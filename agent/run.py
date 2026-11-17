"""The one command (Phase 4): fetch open tickets, decide, check rules, post one action each.

    python -m agent              process every open ticket
    python -m agent --dry-run    decide and log, but post nothing
    python -m agent --limit 5    stop after 5 tickets

How a customer never gets two answers:

1. Only tickets the helpdesk lists as open are processed. The helpdesk is the source of
   truth, not a local file, so a rerun on another machine is just as safe.
2. Right before posting, the ticket is fetched again; if it was handled meanwhile it is skipped.
3. Before the POST a small "pending" file is written to disk. After the POST the log line
   is written and the pending file removed. If the process dies in between, the next run
   finds the pending file and asks the helpdesk whether that ticket was handled. It then
   writes the missing log line, or drops the file. It never re-posts from a pending file.
4. A POST that times out has an unknown outcome. The ticket is fetched: still open means
   nothing was saved, so one retry is safe; handled means the POST did arrive. If that
   check itself fails, the run stops and keeps the pending file.
5. A 409 already_handled is logged and never retried as another action. The helpdesk's
   409 is also the last guard if two machines run at once.
6. A lock file stops two runs on this machine from working at the same time.

Files in logs/:
    decisions.jsonl   exactly one line per ticket the helpdesk accepted an action for
    attempts.jsonl    everything that did not end in a saved action (errors, skips, 409s)
    dry_run.jsonl     decisions of --dry-run
    pending/          write-ahead files, normally empty
They hold ids, the validated fields and rule names only. The free-text `reason` goes to
the helpdesk but not into logs/: the model could copy a name, an address or any other
customer text into it, and no filter can promise to remove all of that.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agent import decide as engine
from agent.config import ConfigError, Settings, load_settings
from agent.helpdesk import AlreadyHandled, HelpdeskClient, HelpdeskError, UncertainOutcome, ticket_is_open
from agent.rules import apply_rules, sanitize_for_log, scrub_payment_data
from agent.schema import Decision

log = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
DECISIONS_LOG = "decisions.jsonl"
ATTEMPTS_LOG = "attempts.jsonl"
DRY_RUN_LOG = "dry_run.jsonl"
PENDING_DIR = "pending"
LOCK_FILE = ".run.lock"

# Outcomes that mean "the helpdesk has saved an action for this ticket".
SAVED_OUTCOMES = ("posted", "posted_confirmed_after_timeout", "handled_confirmed_after_crash",
                  "handled_after_timeout_unknown_source")

# A dead model or a wrong Groq key makes every ticket fail the same way. One failure is
# handed to a human; this many in a row stops the run before anything is posted for them.
MAX_CONSECUTIVE_ENGINE_FAILURES = 3

# Ticket ids become file names and are printed, so they must look like ids.
_TICKET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")


class RunAborted(RuntimeError):
    """The run cannot continue safely (bad credentials, engine down, helpdesk unreachable)."""


# --------------------------------------------------------------------------
# Log files
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_record(ticket_id: str, decision: Decision, *, run_id: str, outcome: str, engine_action: str,
               tokens: int = 0, attempt_id: str = "") -> dict:
    """The log line for a ticket: ids, enum values, numbers and rule names. No free text."""
    return {
        "ts": _now(),
        "run_id": run_id,
        "attempt_id": attempt_id,
        "ticket_id": ticket_id,
        "outcome": outcome,
        "action": decision.action,
        "engine_action": engine_action,
        "language": decision.language,
        "category": decision.category,
        "priority": decision.priority,
        "confidence": decision.confidence,
        "faq_ids": decision.faq_ids,
        "overrides": [sanitize_for_log(name, 60) for name in decision.overrides],
        "tokens": tokens,
    }


def append_log(log_dir: Path, filename: str, record: dict) -> None:
    """Append one JSON line and force it to disk, so a crash right after cannot lose it."""
    line = scrub_payment_data(json.dumps(record, ensure_ascii=False))
    path = log_dir / filename
    # A crash in the middle of an earlier write leaves a half line; start on a fresh line.
    prefix = "\n" if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b"\n") else ""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(prefix + line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def log_has_attempt(log_dir: Path, attempt_id: str) -> bool:
    """True if decisions.jsonl already has the line for this attempt (crash after logging)."""
    path = log_dir / DECISIONS_LOG
    if not attempt_id or not path.exists():
        return False
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                if json.loads(line).get("attempt_id") == attempt_id:
                    return True
            except (ValueError, AttributeError):
                continue  # a half-written line is not a log line
    return False


# --------------------------------------------------------------------------
# Pending files (write-ahead) and lock
# --------------------------------------------------------------------------

def _pending_path(log_dir: Path, ticket_id: str) -> Path:
    return log_dir / PENDING_DIR / f"{ticket_id}.json"


def write_pending(log_dir: Path, record: dict) -> None:
    """Write the pending file so that it is on disk, complete, before the POST is sent."""
    path = _pending_path(log_dir, record["ticket_id"])
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(scrub_payment_data(json.dumps(record, ensure_ascii=False)))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # atomic: the file is either absent or complete
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def clear_pending(log_dir: Path, ticket_id: str) -> None:
    _pending_path(log_dir, ticket_id).unlink(missing_ok=True)


def recover_pending(hd: HelpdeskClient, log_dir: Path, run_id: str) -> int:
    """Finish what an interrupted run left behind. Returns how many log lines were written.

    A pending file means a POST was about to be sent. For each one:
    - its log line already exists  -> the crash came after logging; just remove the file;
    - ticket handled on the helpdesk -> write the missing log line with the action the
      helpdesk reports (the API cannot tell us whose POST it was, so we record what is there);
    - ticket still open -> nothing was saved; remove the file, the ticket is decided again.
    If the helpdesk cannot be reached the error propagates and the files stay.
    An unreadable pending file is moved aside and the run stops: that ticket may have been
    answered without a log line, and the operator should know. The next run continues.
    """
    recovered = 0
    unreadable = []
    for path in sorted((log_dir / PENDING_DIR).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            ticket_id, attempt_id = record["ticket_id"], record["attempt_id"]
        except (ValueError, KeyError, TypeError):
            # Cannot happen with the atomic write above; keep the evidence, do not guess.
            path.rename(path.with_suffix(".bad"))
            unreadable.append(path.with_suffix(".bad").name)
            continue
        if not log_has_attempt(log_dir, attempt_id):
            try:
                ticket = hd.get_ticket(ticket_id)
            except HelpdeskError as exc:
                if exc.status != 404:
                    raise
                # The ticket is no longer in our set (for example after a reset to a new set).
                path.rename(path.with_suffix(".bad"))
                unreadable.append(path.with_suffix(".bad").name)
                continue
            if not ticket_is_open(ticket):
                handled = ticket.get("handled")
                saved_action = handled.get("action") if isinstance(handled, dict) else handled
                record.update(ts=_now(), run_id=run_id, outcome="handled_confirmed_after_crash",
                              proposed_action=record.get("action"), action=str(saved_action or record.get("action")))
                append_log(log_dir, DECISIONS_LOG, record)
                recovered += 1
        path.unlink(missing_ok=True)
    if unreadable:
        raise RunAborted(f"unusable pending file(s) moved to {', '.join(unreadable)} in logs/{PENDING_DIR}; "
                         "check those tickets on the helpdesk by hand, then run again")
    return recovered


def acquire_lock(log_dir: Path):
    """One run at a time on this machine. Returns the open lock file; keep it until the run ends.

    flock is held by the operating system for as long as this process lives, so a killed
    run releases it automatically and there is no stale lock to clean up.
    """
    fh = open(log_dir / LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise RunAborted(f"another run is active (lock {log_dir / LOCK_FILE})") from exc
    return fh


# --------------------------------------------------------------------------
# One ticket
# --------------------------------------------------------------------------

def decide_ticket(ticket: dict, settings: Settings) -> tuple[Decision, str]:
    """Engine proposal, then the safety layer. Returns (final decision, engine's own action)."""
    proposal = engine.decide(ticket, settings=settings)
    engine_action = proposal.action
    final = apply_rules(ticket, proposal, translate=lambda text, lang: engine.back_translate(text, lang, settings=settings))
    return final, engine_action


def _is_open_or_abort(hd: HelpdeskClient, ticket_id: str) -> bool:
    """Ticket state check. If the helpdesk cannot answer we must not guess: stop the run."""
    try:
        return hd.is_open(ticket_id)
    except HelpdeskError as exc:
        raise RunAborted(f"could not check the state of {ticket_id} ({exc.status} {exc.error}); "
                         "its pending file is kept and the next run checks again") from exc


def _saved_action_or_abort(hd: HelpdeskClient, ticket_id: str) -> str:
    """The action the helpdesk has stored for a handled ticket. Stops the run (pending file
    kept) if the helpdesk cannot be asked."""
    try:
        handled = hd.get_ticket(ticket_id).get("handled")
    except HelpdeskError as exc:
        raise RunAborted(f"could not read the saved action of {ticket_id} ({exc.status} {exc.error}); "
                         "its pending file is kept and the next run checks again") from exc
    return str(handled.get("action") if isinstance(handled, dict) else handled)


def post_decision(hd: HelpdeskClient, ticket_id: str, decision: Decision) -> str:
    """Post exactly one action. Returns the outcome word for the log; never posts twice.

    A received 503 is retried inside HelpdeskClient: the API guarantees nothing was saved.
    A timeout is different (UncertainOutcome): the state is checked before any repeat.
    """
    if not _is_open_or_abort(hd, ticket_id):
        return "skipped_already_handled"
    timed_out = False
    for _ in (1, 2):
        try:
            hd.post_action(ticket_id, decision.action, decision.to_payload())
            return "posted"
        except AlreadyHandled:
            # A 409 on the retry after a timeout: the ticket is handled, but the API cannot
            # tell us whether by our first POST or by someone else. Log what the helpdesk has.
            return "handled_after_timeout_unknown_source" if timed_out else "already_handled_409"
        except UncertainOutcome:
            timed_out = True
            if not _is_open_or_abort(hd, ticket_id):
                return "posted_confirmed_after_timeout"
    return "uncertain_left_open"


class Runner:
    """State of one run: where to log, what was counted."""

    def __init__(self, settings: Settings, hd: HelpdeskClient, log_dir: Path, *, dry_run: bool):
        self.settings = settings
        self.hd = hd
        self.log_dir = log_dir
        self.dry_run = dry_run
        self.run_id = uuid.uuid4().hex[:8]
        self.outcomes: Counter = Counter()
        self.actions: Counter = Counter()

    def _finish(self, record: dict, filename: str) -> None:
        append_log(self.log_dir, filename, record)
        self.outcomes[record["outcome"]] += 1
        if record["outcome"] in SAVED_OUTCOMES or record["outcome"] == "dry_run":
            self.actions[record["action"]] += 1
        print(f"{record['ticket_id']}  {record['outcome']:<34} {record['action']}", flush=True)

    def left_open(self, ticket_id: str, decision: Decision, engine_action: str, tokens: int, outcome: str) -> None:
        self._finish(log_record(ticket_id, decision, run_id=self.run_id, outcome=outcome,
                                engine_action=engine_action, tokens=tokens), ATTEMPTS_LOG)

    def post_and_log(self, ticket_id: str, decision: Decision, engine_action: str, tokens: int) -> None:
        """Send one decision for one ticket and write its log line."""
        def record(outcome: str, attempt_id: str = "") -> dict:
            return log_record(ticket_id, decision, run_id=self.run_id, outcome=outcome,
                              engine_action=engine_action, tokens=tokens, attempt_id=attempt_id)

        if decision.problems():
            # apply_rules repairs invalid decisions, so this should not happen; never post one.
            log.error("ticket %s: final decision has %d contract problems, not posted", ticket_id, len(decision.problems()))
            return self._finish(record("invalid_left_open"), ATTEMPTS_LOG)
        if self.dry_run:
            return self._finish(record("dry_run"), DRY_RUN_LOG)

        attempt_id = uuid.uuid4().hex
        write_pending(self.log_dir, record("pending", attempt_id))
        try:
            outcome = post_decision(self.hd, ticket_id, decision)
        except HelpdeskError as exc:
            # The helpdesk answered the POST with an error, so nothing was saved.
            # 400/404/422 = our request is wrong; 401/403 = credentials. Not retried unchanged.
            clear_pending(self.log_dir, ticket_id)
            # Only the status code is logged: the server's error text is free text.
            self._finish(record(f"error_{exc.status}_left_open"), ATTEMPTS_LOG)
            if exc.status in (401, 403):
                raise RunAborted(f"helpdesk rejected the credentials ({exc.status})") from exc
            return None
        if outcome == "uncertain_left_open":
            # Keep the pending file: the next run asks the helpdesk before doing anything.
            return self._finish(record(outcome, attempt_id), ATTEMPTS_LOG)
        if outcome == "handled_after_timeout_unknown_source":
            line = record(outcome, attempt_id)
            line.update(proposed_action=decision.action, action=_saved_action_or_abort(self.hd, ticket_id))
            self._finish(line, DECISIONS_LOG)
            clear_pending(self.log_dir, ticket_id)
            return None
        # Log first, then remove the pending file. A crash in between is harmless:
        # recovery finds the attempt_id in the log and only removes the file.
        self._finish(record(outcome, attempt_id), DECISIONS_LOG if outcome in SAVED_OUTCOMES else ATTEMPTS_LOG)
        clear_pending(self.log_dir, ticket_id)
        return None


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def run(settings: Settings, hd: HelpdeskClient, *, log_dir: Path = LOG_DIR, limit: int | None = None,
        dry_run: bool = False) -> dict:
    """Process every open ticket once. Returns a summary dict."""
    if limit is not None and limit < 0:
        raise RunAborted("--limit must be 0 or more")
    (log_dir / PENDING_DIR).mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    runner = Runner(settings, hd, log_dir, dry_run=dry_run)
    # A dry run posts nothing, so it neither needs the lock nor touches recovery state.
    lock = None if dry_run else acquire_lock(log_dir)
    try:
        recovered = 0 if dry_run else recover_pending(hd, log_dir, runner.run_id)
        tickets = hd.list_open_tickets()  # follows next_cursor until it is null
        if limit is not None:
            tickets = tickets[:limit]

        # Engine failures are held back until we know whether they are isolated.
        held: list[tuple[str, Decision, str, int]] = []
        for position, ticket in enumerate(tickets, start=1):
            ticket_id = str(ticket.get("id", ""))
            if not _TICKET_ID_RE.fullmatch(ticket_id):
                # Not printed or logged: the id is untrusted text. The exit code is non-zero.
                runner.outcomes["invalid_ticket_id_left_open"] += 1
                print(f"(open ticket number {position} has an invalid id and was left open)", flush=True)
                continue
            tokens_before = engine.usage_totals()["total_tokens"]
            decision, engine_action = decide_ticket(ticket, settings)
            tokens = engine.usage_totals()["total_tokens"] - tokens_before

            if engine.is_engine_failure(decision):
                held.append((ticket_id, decision, engine_action, tokens))
                if len(held) >= MAX_CONSECUTIVE_ENGINE_FAILURES:
                    for item in held:
                        runner.left_open(*item, outcome="engine_failed_left_open")
                    raise RunAborted(
                        f"decision engine failed on {len(held)} tickets in a row; check GROQ_API_KEY "
                        "and GROQ_MODEL. Nothing was posted for those tickets; rerun when fixed."
                    )
                continue
            # The engine works, so earlier failures were isolated: a human gets those tickets.
            for item in held:
                runner.post_and_log(*item)
            held.clear()
            runner.post_and_log(ticket_id, decision, engine_action, tokens)
        for item in held:
            runner.post_and_log(*item)

        return {
            "run_id": runner.run_id,
            "dry_run": dry_run,
            "open_at_start": len(tickets),
            "recovered_after_crash": recovered,
            "outcomes": dict(runner.outcomes),
            "actions": dict(runner.actions),
            "seconds": round(time.monotonic() - started, 1),
            "groq_usage": engine.usage_totals(),
            "helpdesk_requests": dict(hd.stats),
        }
    finally:
        if lock is not None:
            lock.close()  # releases the flock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent", description="Roosterly support ticket agent")
    parser.add_argument("--dry-run", action="store_true", help="decide and log, but post nothing to the helpdesk")
    parser.add_argument("--limit", type=int, default=None, help="process at most N open tickets")
    parser.add_argument("--status", action="store_true", help="show how many tickets are open and handled, then exit")
    parser.add_argument("--reset", action="store_true", help="re-open all practice tickets (POST /practice/reset), then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings(require_groq=not (args.status or args.reset))
        if args.status or args.reset:
            hd = HelpdeskClient(settings)
            if args.reset:
                hd.practice_reset()
            status = hd.status()
            print(f"mode {status.get('mode')}: {status.get('open_tickets')} open, {status.get('handled_tickets')} handled")
            return 0
        summary = run(settings, HelpdeskClient(settings), limit=args.limit, dry_run=args.dry_run)
    except (ConfigError, RunAborted) as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        return 2
    except HelpdeskError as exc:
        print(f"ABORTED: helpdesk error {exc.status} {exc.error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    left_open = sum(n for outcome, n in summary["outcomes"].items() if outcome.endswith("left_open"))
    return 1 if left_open else 0


if __name__ == "__main__":
    sys.exit(main())
