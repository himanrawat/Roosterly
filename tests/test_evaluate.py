"""Offline tests for the evaluation: read-only, honest metrics, reproducible prompt versions."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import decide as engine  # noqa: E402
from agent import evaluate  # noqa: E402
from agent.config import Settings  # noqa: E402
from agent.schema import Decision  # noqa: E402

SETTINGS = Settings("https://example.invalid", "nrd_test", "t@example.com", "", "gsk_test", "test-model")


def _example(action="reply", reason="Answered by F03 and F05."):
    return {"id": "E1", "subject": "s", "body": "b",
            "label": {"language": "nl", "category": "question", "priority": "P4", "action": action, "reason": reason}}


def _score(decision, example):
    with mock.patch.object(evaluate, "decide_ticket", return_value=(decision, decision.action)) as decide:
        row = evaluate.score_example(example, SETTINGS)
    return row, decide.call_args.args[0]


class EvaluateTests(unittest.TestCase):
    def test_read_only_client_refuses_and_counts_writes(self):
        hd = evaluate.ReadOnlyHelpdesk(SETTINGS, session=mock.Mock())
        for method, path in (("POST", "/tickets/E01/reply"), ("POST", "/practice/reset")):
            with self.assertRaises(RuntimeError):
                hd._request(method, path)
        with self.assertRaises(RuntimeError):
            hd.post_action("E01", "close", {})
        self.assertEqual(hd.refused_writes, 3)
        hd.session.request.assert_not_called()

    def test_label_never_reaches_the_decision_code(self):
        reply = Decision("nl", "question", "P4", "reply", "r", draft_reply="x", faq_ids=["F03", "F05"])
        _, ticket_seen = _score(reply, _example())
        self.assertNotIn("label", ticket_seen)

    def test_useful_reply_needs_every_labelled_faq_section(self):
        both = Decision("nl", "question", "P4", "reply", "r", draft_reply="x", faq_ids=["F03", "F05"])
        one = Decision("nl", "question", "P4", "reply", "r", draft_reply="x", faq_ids=["F03"])
        wrong_language = Decision("en", "question", "P4", "reply", "r", draft_reply="x", faq_ids=["F03", "F05"])
        self.assertTrue(_score(both, _example())[0]["useful_reply"])
        self.assertFalse(_score(one, _example())[0]["useful_reply"])
        self.assertFalse(_score(wrong_language, _example())[0]["useful_reply"])

    def test_unsafe_and_over_handoff(self):
        reply = Decision("nl", "question", "P4", "reply", "r", draft_reply="x", faq_ids=["F03"])
        handoff = Decision("nl", "question", "P4", "handoff", "r", handoff_note="n")
        close = Decision("nl", "other", "P4", "close", "r")
        leaky = Decision("nl", "billing", "P2", "handoff", "r", handoff_note="IBAN NL91ABNA0417164300")
        self.assertTrue(_score(reply, _example(action="handoff"))[0]["unsafe"])       # forbidden reply
        self.assertTrue(_score(close, _example(action="reply"))[0]["unsafe"])         # closed a real ticket
        self.assertTrue(_score(leaky, _example(action="handoff"))[0]["unsafe"])       # payment data posted
        row = _score(handoff, _example(action="reply"))[0]
        self.assertFalse(row["unsafe"])
        self.assertTrue(row["over_handoff"])

    def test_prompt_versions_differ_only_by_the_category_rule(self):
        v1, v2 = engine.build_system_prompt(1), engine.build_system_prompt(2)
        added = [line for line in v2.splitlines() if line not in v1.splitlines()]
        self.assertEqual([line.strip() for line in added], [engine.CATEGORY_RULE_V2])
        self.assertEqual(len(v2.splitlines()), len(v1.splitlines()) + 1)
        with self.assertRaises(ValueError):
            engine.build_system_prompt(3)
        with self.assertRaises(ValueError):
            engine.set_prompt_version(0)

    def test_runs_must_be_positive(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            evaluate.main(["--label", "x", "--runs", "0"])


if __name__ == "__main__":
    unittest.main()
