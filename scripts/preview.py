"""Show the full text the agent produces for a ticket: reply or handoff note. Posts nothing.

    python scripts/preview.py T005 T003      # specific practice tickets
    python scripts/preview.py --all          # every practice ticket (open or handled)
    python scripts/preview.py --examples     # the 10 labelled examples

The helpdesk stores which action a ticket got, but does not give the posted text back,
and logs/ holds no free text on purpose. This script decides the ticket again and prints
the result, plus an English translation of a Dutch or German reply. Because the model is
not fully deterministic, the wording can differ slightly from what was posted earlier.
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import decide as engine  # noqa: E402
from agent.config import load_settings  # noqa: E402
from agent.evaluate import ReadOnlyHelpdesk  # noqa: E402
from agent.run import decide_ticket  # noqa: E402


def _block(label: str, text: str) -> None:
    print(f"  {label}:")
    for paragraph in (text or "(empty)").splitlines() or [""]:
        for line in textwrap.wrap(paragraph, 96) or [""]:
            print(f"      {line}")


def show(ticket: dict, settings) -> None:
    decision, engine_action = decide_ticket(ticket, settings)
    print("=" * 100)
    print(f"{ticket.get('id')}  |  {ticket.get('subject', '')}")
    _block("Customer wrote", ticket.get("body", ""))
    print(f"  Decision: {decision.action.upper()}   category={decision.category}  priority={decision.priority}  "
          f"language={decision.language}  confidence={decision.confidence}")
    if engine_action != decision.action or decision.overrides:
        print(f"  Model proposed: {engine_action}; changed by: {', '.join(decision.overrides) or 'engine rule'}")
    _block("Reason", decision.reason)
    if decision.action == "reply":
        _block(f"Reply to the customer ({decision.language}), based on FAQ {', '.join(decision.faq_ids)}", decision.draft_reply)
        if decision.language != "en":
            _block("Same reply in English (back-translation)", engine.back_translate(decision.draft_reply, decision.language, settings=settings))
    elif decision.action == "handoff":
        _block("Handoff note for the support team", decision.handoff_note)
    if "label" in ticket:
        label = ticket["label"]
        print(f"  Nordavo's label: {label['action']} / {label['category']} / {label['priority']} / {label['language']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the agent's full output for tickets, without posting")
    parser.add_argument("ids", nargs="*", help="ticket ids, e.g. T005 T003")
    parser.add_argument("--all", action="store_true", help="all practice tickets")
    parser.add_argument("--examples", action="store_true", help="the 10 labelled examples")
    args = parser.parse_args()
    if not (args.ids or args.all or args.examples):
        parser.error("give ticket ids, --all or --examples")
    settings = load_settings()
    hd = ReadOnlyHelpdesk(settings)  # refuses every non-GET request
    if args.examples:
        tickets = sorted(hd.examples(), key=lambda e: e["id"])
    elif args.all:
        tickets = list(hd.iter_tickets("all"))
    else:
        tickets = [hd.get_ticket(ticket_id) for ticket_id in args.ids]
    for ticket in tickets:
        show(ticket, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
