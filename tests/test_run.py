"""Offline tests for the run loop: one action per ticket, also after crashes and timeouts."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import run as runner  # noqa: E402
from agent.config import Settings  # noqa: E402
from agent.helpdesk import AlreadyHandled, HelpdeskError, UncertainOutcome  # noqa: E402
from agent.schema import Decision  # noqa: E402

SETTINGS = Settings("https://example.invalid", "nrd_test", "t@example.com", "", "gsk_test", "test-model")


class FakeHelpdesk:
    """In-memory helpdesk.

    fail:             ticket id -> exceptions to raise on POST, in order
    save_before_fail: for these ids the POST is saved, then the connection drops
    get_fail:         ticket id -> exceptions to raise on GET /tickets/{id}, in order
    """

    def __init__(self, ids, fail=None, save_before_fail=(), get_fail=None):
        self.handled: dict[str, str] = {}
        self.ids = list(ids)
        self.posts: list[tuple[str, str]] = []
        self.fail = fail or {}
        self.save_before_fail = set(save_before_fail)
        self.get_fail = get_fail or {}
        self.stats = {"requests": 0}

    def list_open_tickets(self):
        return [{"id": i, "subject": "s", "body": "b"} for i in self.ids if i not in self.handled]

    def get_ticket(self, ticket_id):
        errors = self.get_fail.get(ticket_id) or []
        if errors:
            raise errors.pop(0)
        action = self.handled.get(ticket_id)
        return {"id": ticket_id, "handled": {"action": action} if action else None}

    def is_open(self, ticket_id):
        return runner.ticket_is_open(self.get_ticket(ticket_id))

    def post_action(self, ticket_id, action, payload):
        self.posts.append((ticket_id, action))
        if ticket_id in self.handled:
            raise AlreadyHandled(409, "already_handled")
        errors = self.fail.get(ticket_id) or []
        if errors:
            if ticket_id in self.save_before_fail:
                self.handled[ticket_id] = action
            raise errors.pop(0)
        self.handled[ticket_id] = action


def _handoff(ticket, settings):
    return Decision("nl", "question", "P4", "handoff", "How-to question. Handed off in this test.", handoff_note="note"), "reply"


def _engine_failure(ticket, settings):
    return Decision("nl", "other", "P3", "handoff", "Decision engine failed: Groq API error. Safe default.",
                    handoff_note="not analysed", confidence=0.0), "handoff"


class Crash(BaseException):
    """Stands in for kill -9: not an Exception, so no handler in the agent can swallow it."""


class RunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_dir = Path(tmp.name)
        patcher = mock.patch.object(runner, "decide_ticket", side_effect=_handoff)
        self.decide = patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, hd, **kwargs):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.run(SETTINGS, hd, log_dir=self.log_dir, **kwargs)

    def _lines(self, filename=runner.DECISIONS_LOG):
        path = self.log_dir / filename
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def _pending(self):
        return sorted(p.name for p in (self.log_dir / runner.PENDING_DIR).iterdir())

    # ---------- normal operation

    def test_each_ticket_gets_one_post_and_one_log_line(self):
        hd = FakeHelpdesk(["T1", "T2", "T3"])
        summary = self._run(hd)
        self.assertEqual(hd.posts, [("T1", "handoff"), ("T2", "handoff"), ("T3", "handoff")])
        self.assertEqual([line["ticket_id"] for line in self._lines()], ["T1", "T2", "T3"])
        self.assertEqual(summary["outcomes"], {"posted": 3})
        self.assertEqual(self._pending(), [])

    def test_rerun_processes_only_open_tickets(self):
        hd = FakeHelpdesk(["T1", "T2"])
        self._run(hd, limit=1)
        self._run(hd)
        self._run(hd)
        self.assertEqual(hd.posts, [("T1", "handoff"), ("T2", "handoff")])
        self.assertEqual(len(self._lines()), 2)

    def test_negative_limit_is_rejected(self):
        hd = FakeHelpdesk(["T1", "T2"])
        with self.assertRaises(runner.RunAborted):
            self._run(hd, limit=-1)
        self.assertEqual(hd.posts, [])

    def test_dry_run_posts_nothing_and_does_not_touch_the_decision_log(self):
        hd = FakeHelpdesk(["T1"])
        summary = self._run(hd, dry_run=True)
        self.assertEqual(hd.posts, [])
        self.assertEqual(summary["outcomes"], {"dry_run": 1})
        self.assertEqual(self._lines(), [])
        self.assertEqual(len(self._lines(runner.DRY_RUN_LOG)), 1)
        self._run(hd)  # the real run afterwards writes exactly one decision line
        self.assertEqual([line["outcome"] for line in self._lines()], ["posted"])

    def test_dry_run_leaves_recovery_state_alone(self):
        hd = FakeHelpdesk(["T1"])
        with mock.patch.object(runner, "append_log", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        self._run(hd, dry_run=True)
        self.assertEqual(self._pending(), ["T1.json"])

    def test_ticket_with_strange_id_is_skipped(self):
        hd = FakeHelpdesk(["../../etc/passwd", "T2"])
        summary = self._run(hd)
        self.assertEqual(hd.posts, [("T2", "handoff")])
        self.assertEqual(summary["outcomes"], {"invalid_ticket_id_left_open": 1, "posted": 1})

    # ---------- crashes at each point (fault injection)

    def test_crash_before_post_means_ticket_is_decided_again(self):
        hd = FakeHelpdesk(["T1"])
        with mock.patch.object(runner, "post_decision", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        self.assertEqual(hd.posts, [])
        self.assertEqual(self._pending(), ["T1.json"])
        summary = self._run(hd)
        self.assertEqual(hd.posts, [("T1", "handoff")])
        self.assertEqual(summary["recovered_after_crash"], 0)
        self.assertEqual([line["outcome"] for line in self._lines()], ["posted"])

    def test_crash_after_post_before_log_is_recovered_without_second_post(self):
        hd = FakeHelpdesk(["T1", "T2"])
        with mock.patch.object(runner, "append_log", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        self.assertEqual(hd.posts, [("T1", "handoff")])
        self.assertEqual(self._lines(), [])
        summary = self._run(hd)
        self.assertEqual(hd.posts, [("T1", "handoff"), ("T2", "handoff")])  # T1 not posted again
        self.assertEqual(summary["recovered_after_crash"], 1)
        lines = self._lines()
        self.assertEqual([line["ticket_id"] for line in lines], ["T1", "T2"])
        self.assertEqual(lines[0]["outcome"], "handled_confirmed_after_crash")
        self.assertEqual(self._pending(), [])

    def test_crash_after_log_before_pending_removal_does_not_duplicate_the_line(self):
        hd = FakeHelpdesk(["T1"])
        with mock.patch.object(runner, "clear_pending", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        self.assertEqual(len(self._lines()), 1)
        self.assertEqual(self._pending(), ["T1.json"])
        summary = self._run(hd)
        self.assertEqual(summary["recovered_after_crash"], 0)
        self.assertEqual(len(self._lines()), 1)
        self.assertEqual(self._pending(), [])
        self.assertEqual(hd.posts, [("T1", "handoff")])

    def test_crash_during_recovery_does_not_duplicate_the_line(self):
        hd = FakeHelpdesk(["T1"])
        with mock.patch.object(runner, "append_log", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        real_unlink = Path.unlink
        def crash_on_pending(path, *args, **kwargs):  # recovery wrote the line, dies before removing the file
            if path.parent.name == runner.PENDING_DIR:
                raise Crash()
            return real_unlink(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", crash_on_pending):
            with self.assertRaises(Crash):
                self._run(hd)
        self.assertEqual(len(self._lines()), 1)
        self._run(hd)
        self.assertEqual(len(self._lines()), 1)
        self.assertEqual(hd.posts, [("T1", "handoff")])

    def test_recovery_records_the_action_the_helpdesk_reports(self):
        hd = FakeHelpdesk(["T1"])
        with mock.patch.object(runner, "post_decision", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        hd.handled["T1"] = "reply"  # somebody else answered it in the meantime
        self._run(hd)
        line = self._lines()[0]
        self.assertEqual((line["action"], line["proposed_action"], line["outcome"]), ("reply", "handoff", "handled_confirmed_after_crash"))
        self.assertEqual(hd.posts, [])

    def test_unreadable_pending_file_is_kept_as_evidence(self):
        pending = self.log_dir / runner.PENDING_DIR
        pending.mkdir(parents=True)
        (pending / "T9.json").write_text("{not json")
        hd = FakeHelpdesk(["T1"])
        with self.assertRaises(runner.RunAborted):  # the operator must hear about it once
            self._run(hd)
        self.assertEqual(self._pending(), ["T9.bad"])
        self.assertEqual(hd.posts, [])
        self.assertEqual(self._run(hd)["outcomes"], {"posted": 1})  # the next run continues

    # ---------- uncertain outcomes and helpdesk errors

    def test_timeout_where_post_arrived_is_not_retried(self):
        hd = FakeHelpdesk(["T1"], fail={"T1": [UncertainOutcome("timeout")]}, save_before_fail=["T1"])
        summary = self._run(hd)
        self.assertEqual(hd.posts, [("T1", "handoff")])
        self.assertEqual(summary["outcomes"], {"posted_confirmed_after_timeout": 1})

    def test_timeout_where_nothing_was_saved_is_retried_once(self):
        hd = FakeHelpdesk(["T1"], fail={"T1": [UncertainOutcome("timeout")]})
        summary = self._run(hd)
        self.assertEqual(hd.posts, [("T1", "handoff"), ("T1", "handoff")])
        self.assertEqual(summary["outcomes"], {"posted": 1})

    def test_two_timeouts_leave_ticket_open_for_next_run(self):
        hd = FakeHelpdesk(["T1"], fail={"T1": [UncertainOutcome("a"), UncertainOutcome("b")]})
        summary = self._run(hd)
        self.assertEqual(summary["outcomes"], {"uncertain_left_open": 1})
        self.assertEqual(self._pending(), ["T1.json"])
        self.assertEqual(self._lines(), [])
        self._run(hd)
        self.assertEqual(hd.handled, {"T1": "handoff"})
        self.assertEqual(len(self._lines()), 1)

    def test_state_check_failure_after_timeout_stops_and_keeps_pending(self):
        hd = FakeHelpdesk(["T1", "T2"], fail={"T1": [UncertainOutcome("timeout")]}, save_before_fail=["T1"])
        real_get = hd.get_ticket
        calls = {"n": 0}
        def flaky_get(ticket_id):  # first GET (pre-check) works, the GET after the timeout fails
            calls["n"] += 1
            if calls["n"] == 2:
                raise HelpdeskError(503, "service_unavailable")
            return real_get(ticket_id)
        hd.get_ticket = flaky_get
        with self.assertRaises(runner.RunAborted):
            self._run(hd)
        self.assertEqual(self._pending(), ["T1.json"])
        self.assertEqual(hd.posts, [("T1", "handoff")])
        summary = self._run(hd)  # helpdesk reachable again: the saved POST is found, not repeated
        self.assertEqual(summary["recovered_after_crash"], 1)
        self.assertEqual(hd.posts, [("T1", "handoff"), ("T2", "handoff")])
        self.assertEqual(sorted(line["ticket_id"] for line in self._lines()), ["T1", "T2"])

    def test_409_on_the_retry_after_a_timeout_logs_what_the_helpdesk_has(self):
        hd = FakeHelpdesk(["T1"], fail={"T1": [UncertainOutcome("timeout")]})
        real_post = hd.post_action
        def someone_else_answers_after_our_state_check(ticket_id, action, payload):
            try:
                return real_post(ticket_id, action, payload)
            except UncertainOutcome:
                hd.is_open = lambda _id: True      # the state check after the timeout still says open
                hd.handled[ticket_id] = "reply"    # then another worker replies
                raise
        hd.post_action = someone_else_answers_after_our_state_check
        summary = self._run(hd)
        self.assertEqual(summary["outcomes"], {"handled_after_timeout_unknown_source": 1})
        line = self._lines()[0]
        self.assertEqual((line["ticket_id"], line["action"], line["proposed_action"]), ("T1", "reply", "handoff"))
        self.assertEqual(self._pending(), [])
        self.assertEqual(len(hd.posts), 2)  # the retry got the 409; no third attempt

    def test_pending_file_for_a_ticket_that_no_longer_exists_is_moved_aside(self):
        hd = FakeHelpdesk(["T1"])
        with mock.patch.object(runner, "post_decision", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        hd.get_fail = {"T1": [HelpdeskError(404, "not_found")]}
        with self.assertRaises(runner.RunAborted):
            self._run(hd)
        self.assertEqual(self._pending(), ["T1.bad"])
        self.assertEqual(self._run(hd)["outcomes"], {"posted": 1})

    def test_409_is_logged_and_never_retried(self):
        hd = FakeHelpdesk(["T1"], fail={"T1": [AlreadyHandled(409, "already_handled")]})
        summary = self._run(hd)
        self.assertEqual(len(hd.posts), 1)
        self.assertEqual(summary["outcomes"], {"already_handled_409": 1})
        self.assertEqual(self._lines(), [])
        self.assertEqual(len(self._lines(runner.ATTEMPTS_LOG)), 1)

    def test_ticket_handled_while_deciding_is_skipped(self):
        hd = FakeHelpdesk(["T1"])
        def decide_and_someone_else_handles(ticket, settings):
            hd.handled["T1"] = "reply"
            return _handoff(ticket, settings)
        self.decide.side_effect = decide_and_someone_else_handles
        summary = self._run(hd)
        self.assertEqual(hd.posts, [])
        self.assertEqual(summary["outcomes"], {"skipped_already_handled": 1})

    def test_validation_error_is_logged_and_run_continues(self):
        hd = FakeHelpdesk(["T1", "T2"], fail={"T1": [HelpdeskError(422, "validation_failed")]})
        summary = self._run(hd)
        self.assertEqual(summary["outcomes"], {"error_422_left_open": 1, "posted": 1})
        self.assertEqual(hd.handled, {"T2": "handoff"})
        self.assertEqual(self._pending(), [])

    def test_bad_credentials_abort_the_run(self):
        hd = FakeHelpdesk(["T1", "T2"], fail={"T1": [HelpdeskError(401, "unauthorized")]})
        with self.assertRaises(runner.RunAborted):
            self._run(hd)
        self.assertEqual(len(hd.posts), 1)

    # ---------- engine failures

    def test_isolated_engine_failure_is_handed_off(self):
        self.decide.side_effect = [_engine_failure(None, None), _handoff(None, None), _handoff(None, None)]
        hd = FakeHelpdesk(["T1", "T2", "T3"])
        summary = self._run(hd)
        self.assertEqual(hd.handled, {"T1": "handoff", "T2": "handoff", "T3": "handoff"})
        self.assertEqual(summary["outcomes"], {"posted": 3})

    def test_engine_failure_on_the_last_ticket_is_handed_off(self):
        self.decide.side_effect = [_handoff(None, None), _engine_failure(None, None)]
        hd = FakeHelpdesk(["T1", "T2"])
        self._run(hd)
        self.assertEqual(hd.handled, {"T1": "handoff", "T2": "handoff"})

    def test_engine_down_aborts_without_posting(self):
        self.decide.side_effect = _engine_failure
        hd = FakeHelpdesk(["T1", "T2", "T3", "T4", "T5"])
        with self.assertRaises(runner.RunAborted):
            self._run(hd)
        self.assertEqual(hd.posts, [])
        self.assertEqual(self._lines(), [])
        self.assertEqual(len(self._lines(runner.ATTEMPTS_LOG)), 3)

    # ---------- log hygiene and lock

    def test_log_line_has_no_payment_data_and_no_keys(self):
        def leaky(ticket, settings):
            return Decision("nl", "billing", "P2", "handoff", "Customer anna@cafe.nl (06 12345678) sent IBAN NL91 ABNA 0417 1643 00 and card 4111 1111 1111 1111.",
                            handoff_note="note"), "handoff"
        self.decide.side_effect = leaky
        with mock.patch.object(runner, "clear_pending"):  # keep the pending file to inspect it too
            self._run(FakeHelpdesk(["T1"]))
        raw = (self.log_dir / runner.DECISIONS_LOG).read_text() + (self.log_dir / runner.PENDING_DIR / "T1.json").read_text()
        self.assertNotIn("reason", json.loads((self.log_dir / runner.DECISIONS_LOG).read_text()))
        for secret in ("ABNA", "4111", "nrd_test", "gsk_test", "t@example.com", "anna@cafe.nl", "12345678", "Customer"):
            self.assertNotIn(secret, raw)

    def test_second_run_at_the_same_time_is_refused(self):
        held = runner.acquire_lock(self.log_dir)  # another run holds the lock
        self.addCleanup(held.close)
        hd = FakeHelpdesk(["T1"])
        with self.assertRaises(runner.RunAborted):
            self._run(hd)
        self.assertEqual(hd.posts, [])

    def test_lock_of_a_finished_or_killed_run_does_not_block(self):
        runner.acquire_lock(self.log_dir).close()  # closing (or dying) releases the lock; the file stays
        summary = self._run(FakeHelpdesk(["T1"]))
        self.assertEqual(summary["outcomes"], {"posted": 1})

    def test_half_written_log_line_is_not_taken_for_a_decision(self):
        hd = FakeHelpdesk(["T1"])
        with mock.patch.object(runner, "append_log", side_effect=Crash):
            with self.assertRaises(Crash):
                self._run(hd)
        attempt_id = json.loads((self.log_dir / runner.PENDING_DIR / "T1.json").read_text())["attempt_id"]
        (self.log_dir / runner.DECISIONS_LOG).write_text('{"ticket_id": "T1", "attempt_id": "' + attempt_id + '", "outc')
        summary = self._run(hd)
        self.assertEqual(summary["recovered_after_crash"], 1)
        complete = [line for line in (self.log_dir / runner.DECISIONS_LOG).read_text().splitlines() if line.endswith("}")]
        self.assertEqual([json.loads(line)["ticket_id"] for line in complete], ["T1"])
        self.assertEqual(hd.posts, [("T1", "handoff")])


if __name__ == "__main__":
    unittest.main()
