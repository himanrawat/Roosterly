"""Offline tests for the helpdesk client error handling. No network."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.config import Settings  # noqa: E402
from agent.helpdesk import AlreadyHandled, HelpdeskClient, HelpdeskError, UncertainOutcome  # noqa: E402
import requests  # noqa: E402


def _settings() -> Settings:
    return Settings(
        helpdesk_base_url="https://example.invalid/helpdesk",
        helpdesk_api_key="nrd_test",
        candidate_email="test@example.com",
        candidate_name="",
        groq_api_key="",
        groq_model="",
    )


class FakeResponse:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()
        self.headers = headers or {}
        self.reason = "reason"

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _client(responses) -> tuple[HelpdeskClient, FakeSession]:
    session = FakeSession(responses)
    c = HelpdeskClient(_settings(), session=session)
    return c, session


class HelpdeskClientTests(unittest.TestCase):
    @mock.patch("agent.helpdesk.time.sleep")
    def test_503_then_success_retries(self, sleep):
        c, s = _client([FakeResponse(503, {"error": "service_unavailable"}), FakeResponse(200, {"ok": True})])
        self.assertEqual(c.status(), {"ok": True})
        self.assertEqual(len(s.calls), 2)
        self.assertEqual(c.stats["retries_503"], 1)

    @mock.patch("agent.helpdesk.time.sleep")
    def test_429_waits_retry_after(self, sleep):
        c, s = _client([FakeResponse(429, {"error": "rate_limited"}, {"Retry-After": "3"}), FakeResponse(200, {})])
        c.status()
        sleep.assert_any_call(3.0)
        self.assertEqual(len(s.calls), 2)

    def test_409_raises_already_handled_without_retry(self):
        c, s = _client([FakeResponse(409, {"error": "already_handled"})])
        with self.assertRaises(AlreadyHandled):
            c.post_action("T001", "reply", {"language": "nl"})
        self.assertEqual(len(s.calls), 1)

    def test_422_and_401_not_retried(self):
        for status, err in ((422, "validation_failed"), (401, "unauthorized"), (400, "bad_request")):
            c, s = _client([FakeResponse(status, {"error": err})])
            with self.assertRaises(HelpdeskError) as ctx:
                c.status()
            self.assertEqual(ctx.exception.status, status)
            self.assertEqual(ctx.exception.error, err)
            self.assertEqual(len(s.calls), 1)

    def test_post_timeout_is_uncertain(self):
        c, s = _client([requests.Timeout("slow")])
        with self.assertRaises(UncertainOutcome):
            c.post_action("T001", "handoff", {})
        self.assertEqual(len(s.calls), 1)

    @mock.patch("agent.helpdesk.time.sleep")
    def test_get_timeout_is_retried(self, sleep):
        c, s = _client([requests.Timeout("slow"), FakeResponse(200, {"ok": 1})])
        self.assertEqual(c.status(), {"ok": 1})
        self.assertEqual(len(s.calls), 2)

    def test_pagination_follows_cursor_until_null(self):
        c, s = _client([
            FakeResponse(200, {"tickets": [{"id": "T001"}, {"id": "T002"}], "next_cursor": "T002"}),
            FakeResponse(200, {"tickets": [{"id": "T003"}], "next_cursor": None}),
        ])
        ids = [t["id"] for t in c.iter_tickets("open")]
        self.assertEqual(ids, ["T001", "T002", "T003"])
        self.assertEqual(s.calls[1][1], "https://example.invalid/helpdesk/tickets")

    def test_is_open_uses_handled_field(self):
        c, _ = _client([FakeResponse(200, {"id": "T001", "handled": None})])
        self.assertTrue(c.is_open("T001"))
        c, _ = _client([FakeResponse(200, {"id": "T001", "handled": {"action": "reply"}})])
        self.assertFalse(c.is_open("T001"))

    def test_headers_sent(self):
        c, s = _client([FakeResponse(200, {})])
        c.status()
        self.assertEqual(c._headers["Authorization"], "Bearer nrd_test")
        self.assertEqual(c._headers["X-Candidate-Email"], "test@example.com")

    def test_unknown_action_rejected(self):
        c, _ = _client([])
        with self.assertRaises(ValueError):
            c.post_action("T001", "delete", {})


if __name__ == "__main__":
    unittest.main()
