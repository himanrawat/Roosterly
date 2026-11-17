"""Live crash test (Phase 4): kill the agent mid-run several times, rerun, check for duplicates.

    python scripts/crash_test.py            # uses the practice helpdesk from .env

It posts real decisions to practice tickets (POST /practice/reset re-opens them).
For each kill it starts `python -m agent --limit N`, waits until a pending file exists
in logs/pending (the agent is then between "about to POST" and "log line written"), waits a
little longer so different kills land before, during and after the POST, and sends SIGKILL.
Then it runs the agent to completion and checks:

- every ticket handled since the start has exactly one line in logs/decisions.jsonl;
- no ticket in that log is still open on the helpdesk;
- logs/attempts.jsonl has no 409 (the agent never tried to answer a ticket twice);
- logs/pending is empty;
- no key, candidate email, IBAN or card number appears anywhere in logs/ (counts only).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import load_settings  # noqa: E402
from agent.helpdesk import HelpdeskClient, ticket_is_open  # noqa: E402
from agent.rules import contains_payment_data  # noqa: E402

LOGS = ROOT / "logs"
LIMIT = 9
KILL_DELAYS = (0.0, 0.35, 0.7)  # seconds after the pending file appears
WAIT_TIMEOUT = 120


def handled_ids(hd: HelpdeskClient) -> set[str]:
    return {t["id"] for t in hd.iter_tickets("all") if not ticket_is_open(t)}


def run_and_kill(hd: HelpdeskClient, delay: float, skip: int) -> dict:
    """Start the agent, kill it `delay` seconds after its (skip+1)-th pending file appears."""
    proc = subprocess.Popen([sys.executable, "-m", "agent", "--limit", str(LIMIT)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    seen: list[str] = []
    deadline = time.monotonic() + WAIT_TIMEOUT
    while time.monotonic() < deadline and proc.poll() is None:
        for path in (LOGS / "pending").glob("*.json"):
            if path.stem not in seen:
                seen.append(path.stem)
        if len(seen) > skip:
            time.sleep(delay)
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
            proc.wait()
            ticket_id = seen[-1]
            pending_left = (LOGS / "pending" / f"{ticket_id}.json").exists()
            saved = not hd.is_open(ticket_id)
            window = ("after POST, before log" if saved and pending_left else
                      "before POST was saved" if pending_left else "after log line (too late)")
            return {"killed_on": ticket_id, "delay": delay, "window": window}
        time.sleep(0.005)
    proc.kill()
    proc.wait()
    return {"killed_on": None, "delay": delay, "window": "no pending file seen (nothing open or timeout)"}


def count_secrets() -> dict:
    """Counts only: the secret values themselves are never printed or passed to another program."""
    secrets = {name: os.environ.get(name, "") for name in ("HELPDESK_API_KEY", "GROQ_API_KEY", "CANDIDATE_EMAIL")}
    counts = Counter()
    for path in LOGS.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            for name, value in secrets.items():
                counts[name] += text.count(value) if value else 0
            counts["payment_data_lines"] += sum(contains_payment_data(line) for line in text.splitlines())
    return dict(counts)


def main() -> int:
    hd = HelpdeskClient(load_settings())
    before = handled_ids(hd)
    kills = [run_and_kill(hd, delay, skip) for skip, delay in enumerate(KILL_DELAYS)]
    final = subprocess.run([sys.executable, "-m", "agent", "--limit", str(LIMIT)], cwd=ROOT, capture_output=True, text=True)

    after = handled_ids(hd)
    new = after - before
    lines = [json.loads(line) for line in (LOGS / "decisions.jsonl").read_text().splitlines()]
    per_ticket = Counter(line["ticket_id"] for line in lines if line["ticket_id"] in new)
    attempts_path = LOGS / "attempts.jsonl"
    attempts = [json.loads(line) for line in attempts_path.read_text().splitlines()] if attempts_path.exists() else []
    report = {
        "kills": kills,
        "final_run_exit_code": final.returncode,
        "handled_before": len(before),
        "newly_handled": sorted(new),
        "tickets_with_exactly_one_log_line": sum(1 for t in new if per_ticket[t] == 1),
        "tickets_without_log_line": sorted(t for t in new if per_ticket[t] == 0),
        "tickets_with_duplicate_log_lines": sorted(t for t in new if per_ticket[t] > 1),
        "logged_but_still_open": sorted({line["ticket_id"] for line in lines} - after),
        "recovered_after_crash_lines": sum(line["outcome"] == "handled_confirmed_after_crash" for line in lines),
        "409_attempts": sum(a["outcome"] == "already_handled_409" for a in attempts),
        "pending_files_left": sorted(p.name for p in (LOGS / "pending").iterdir()),
        "secret_and_payment_matches_in_logs": count_secrets(),
    }
    print(json.dumps(report, indent=2))
    ok = (not report["tickets_without_log_line"] and not report["tickets_with_duplicate_log_lines"]
          and not report["logged_but_still_open"] and not report["409_attempts"] and not report["pending_files_left"]
          and not any(report["secret_and_payment_matches_in_logs"].values()))
    if not any(kill["killed_on"] for kill in kills):
        # No open tickets (or nothing reached the POST): the checks above had nothing to check.
        print("NOTHING TESTED: no run was killed. Re-open tickets first (python -m agent --reset).")
        return 2
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
