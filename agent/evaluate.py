"""Evaluation on the 10 labelled examples (Phase 5). Read-only: GET /examples, no POST.

    python -m agent.evaluate --label before --prompt-version 1 --runs 3
    python -m agent.evaluate --label after  --prompt-version 2 --runs 3

Runs exactly the code path of the real agent (engine + safety layer, `decide_ticket`),
compares the result with Nordavo's labels and writes eval/<label>.json. The labels come
from the API; the model never sees them.

What is measured per example:
- action, category, priority, language: equal to the label or not;
- unsafe (a proxy): the label says handoff and we replied or closed, or we closed a real
  ticket, or payment data appears in what we would post. This number must be 0. It does
  not prove that every reply is true; it compares with the label only;
- over_handoff: the label says reply and we handed off (safe, but no time saved);
- useful_reply (a proxy): the label says reply, we replied in the labelled language, the
  reply passed the grounding and back-translation checks, and it cites every FAQ section
  the label names. Whether the Dutch text reads well still needs a human reader.

Each result file records the model, prompt version and hashes of the prompt, faq.md and the
examples as fetched, so a later run can show it used the same inputs.

The model is not fully deterministic even at temperature 0, so --runs N repeats the whole
set and reports every run and the average.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent import decide as engine
from agent.config import Settings, load_settings
from agent.helpdesk import HelpdeskClient
from agent.rules import contains_payment_data
from agent.run import decide_ticket
from agent.schema import load_faq

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
FIELDS = ("action", "category", "priority", "language")


class ReadOnlyHelpdesk(HelpdeskClient):
    """A helpdesk client that cannot post. Example tickets must never receive an action."""

    refused_writes = 0

    def _request(self, method: str, path: str, **kwargs):
        if method != "GET":
            self.refused_writes += 1
            raise RuntimeError(f"evaluation is read-only, refused {method} {path}")
        return super()._request(method, path, **kwargs)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _source_sha() -> str:
    """Hash of the agent's code, so two result files can show they ran the same code."""
    files = sorted(Path(__file__).resolve().parent.glob("*.py"))
    return _sha("".join(f"{f.name}\n{f.read_text(encoding='utf-8')}" for f in files))


def compare(first: str, second: str) -> int:
    """Print two saved results side by side. Refuses if they did not use the same inputs."""
    a, b = (json.loads((EVAL_DIR / f"{label}.json").read_text(encoding="utf-8")) for label in (first, second))
    same = ("model", "source_sha")
    differing = [k for k in same if a.get(k) != b.get(k)]
    differing += [k for k in ("faq_sha", "examples_sha") if a["inputs"].get(k) != b["inputs"].get(k)]
    if differing:
        print(f"NOT COMPARABLE: {', '.join(differing)} differ between {first} and {second}", file=sys.stderr)
        return 2
    print(f"{'metric':<16}{first + ' (v' + str(a['prompt_version']) + ')':>16}{second + ' (v' + str(b['prompt_version']) + ')':>16}")
    for key in ("action", "category", "priority", "language", "unsafe", "over_handoff", "useful_replies"):
        print(f"{key:<16}{a['average'][key]:>16}{b['average'][key]:>16}")
    print(f"averages over {len(a['runs'])} and {len(b['runs'])} runs of {a['average']['examples']:g} examples; "
          f"useful_replies is out of {a['average']['reply_examples']:g}")
    return 0


def _git_revision() -> str:
    try:
        root = Path(__file__).resolve().parent.parent
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{rev}{'+uncommitted' if dirty else ''}"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def score_example(example: dict, settings: Settings) -> dict:
    """Run one example through the agent's decision path and compare with its label."""
    label = example["label"]
    ticket = {k: v for k, v in example.items() if k != "label"}  # the label never reaches the model
    decision, engine_action = decide_ticket(ticket, settings)
    payload = json.dumps(decision.to_payload(), ensure_ascii=False)
    label_faq = re.findall(r"F\d{2}", label.get("reason", ""))

    row = {"id": example["id"], "expected": {f: label[f] for f in FIELDS},
           "got": {f: getattr(decision, f) for f in FIELDS},
           "engine_action": engine_action, "faq_ids": decision.faq_ids, "overrides": decision.overrides}
    for f in FIELDS:
        row[f"{f}_ok"] = getattr(decision, f) == label[f]
    row["unsafe"] = bool(
        (label["action"] == "handoff" and decision.action != "handoff")
        or (label["action"] != "close" and decision.action == "close")
        or contains_payment_data(payload)
    )
    row["over_handoff"] = label["action"] == "reply" and decision.action == "handoff"
    if label["action"] == "reply":
        # A reply that is still a reply here has passed check_grounding and the back-translation check.
        row["useful_reply"] = bool(
            decision.action == "reply"
            and decision.language == label["language"]
            and set(label_faq) <= set(decision.faq_ids)
        )
    return row


def run_once(examples: list[dict], settings: Settings) -> dict:
    rows = [score_example(example, settings) for example in examples]
    reply_rows = [r for r in rows if "useful_reply" in r]
    totals = {f: sum(r[f"{f}_ok"] for r in rows) for f in FIELDS}
    totals.update(
        examples=len(rows),
        unsafe=sum(r["unsafe"] for r in rows),
        over_handoff=sum(r["over_handoff"] for r in rows),
        useful_replies=sum(r["useful_reply"] for r in reply_rows),
        reply_examples=len(reply_rows),
    )
    return {"rows": rows, "totals": totals}


def print_run(number: int, result: dict) -> None:
    print(f"\nRun {number}")
    print(f"{'id':<5}{'action':<19}{'category':<33}{'priority':<9}{'lang':<7}flags")
    for r in result["rows"]:
        cells = []
        for f, width in zip(FIELDS, (19, 33, 9, 7)):
            exp, got = r["expected"][f], r["got"][f]
            cells.append((exp if exp == got else f"{exp}>{got}").ljust(width))
        flags = [name for name in ("unsafe", "over_handoff") if r[name]] + (r["overrides"] or [])
        print(f"{r['id']:<5}{''.join(cells)}{' '.join(flags)}")
    t = result["totals"]
    print(f"action {t['action']}/{t['examples']}  category {t['category']}/{t['examples']}  "
          f"priority {t['priority']}/{t['examples']}  language {t['language']}/{t['examples']}  "
          f"unsafe {t['unsafe']}  over-handoff {t['over_handoff']}  useful replies {t['useful_replies']}/{t['reply_examples']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.evaluate", description="Score the agent on the labelled examples")
    parser.add_argument("--label", help="name of this result, e.g. before or after (file eval/<label>.json)")
    parser.add_argument("--compare", nargs=2, metavar=("FIRST", "SECOND"), help="compare two saved results and exit")
    parser.add_argument("--prompt-version", type=int, default=engine.PROMPT_VERSION, help="system prompt version to test")
    parser.add_argument("--runs", type=int, default=1, help="repeat the whole set N times")
    parser.add_argument("--force", action="store_true", help="overwrite an existing eval/<label>.json")
    args = parser.parse_args(argv)
    if args.compare:
        return compare(*args.compare)
    if not args.label:
        parser.error("--label is required")
    if args.runs < 1:
        parser.error("--runs must be 1 or more")
    path = EVAL_DIR / f"{args.label}.json"
    if path.exists() and not args.force:
        parser.error(f"{path.name} already exists; choose another --label or pass --force")

    settings = load_settings()
    engine.set_prompt_version(args.prompt_version)
    hd = ReadOnlyHelpdesk(settings)
    examples = sorted(hd.examples(), key=lambda e: e["id"])

    runs = []
    for number in range(1, args.runs + 1):
        result = run_once(examples, settings)
        print_run(number, result)
        runs.append(result)

    keys = runs[0]["totals"].keys()
    average = {k: round(sum(r["totals"][k] for r in runs) / len(runs), 2) for k in keys}
    out = {
        "label": args.label,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.groq_model,
        "prompt_version": args.prompt_version,
        "code_revision": _git_revision(),
        "source_sha": _source_sha(),
        "inputs": {
            "prompt_sha": _sha(engine.build_system_prompt(args.prompt_version)),
            "faq_sha": _sha(load_faq()),
            "examples_sha": _sha(json.dumps(examples, sort_keys=True, ensure_ascii=False)),
            "example_ids": [e["id"] for e in examples],
        },
        "runs": runs,
        "average": average,
        "groq_usage": engine.usage_totals(),
        "helpdesk_requests": dict(hd.stats),
        "write_requests_sent": hd.stats["writes"],  # counted in HelpdeskClient._request; must be 0
        "write_requests_refused": hd.refused_writes,
    }
    EVAL_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nAverage over {len(runs)} run(s): {json.dumps(average)}")
    print(f"Saved {path.relative_to(EVAL_DIR.parent)}")
    return 1 if average["unsafe"] else 0


if __name__ == "__main__":
    sys.exit(main())
