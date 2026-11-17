"""Helpdesk API client.

Responsibilities:
- authenticate every request (bearer key + candidate email)
- stay under 5 requests/second
- 429: wait Retry-After, then retry
- 503: short exponential backoff, then retry (the API guarantees nothing was saved)
- 409 already_handled: raise AlreadyHandled; the caller must never retry as another action
- other 4xx: raise HelpdeskError, do not retry
- timeout or dropped connection on a POST: raise UncertainOutcome; the caller must
  re-check the ticket state before any repeat action
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Iterator

import requests

from .config import Settings

ACTIONS = ("reply", "handoff", "close")


class HelpdeskError(Exception):
    def __init__(self, status: int, error: str, payload: Any = None):
        super().__init__(f"helpdesk {status} {error}")
        self.status = status
        self.error = error
        self.payload = payload


class AlreadyHandled(HelpdeskError):
    """409: the ticket already has a decision. Never retry."""


class UncertainOutcome(Exception):
    """A POST timed out or the connection dropped. The action may or may not have been saved."""


class RateLimiter:
    """Sliding window: at most `max_calls` per `period` seconds (default 4/s, under the 5/s limit)."""

    def __init__(self, max_calls: int = 4, period: float = 1.0):
        self.max_calls = max_calls
        self.period = period
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.period:
            self._calls.popleft()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if len(self._calls) >= self.max_calls:
                sleep_for = self.period - (now - self._calls[0]) + 0.01
                time.sleep(max(sleep_for, 0))
                self._prune(time.monotonic())
            self._calls.append(time.monotonic())


class HelpdeskClient:
    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 20.0,
        max_503_retries: int = 6,
        max_429_retries: int = 10,
        session: requests.Session | None = None,
    ):
        self.base_url = settings.helpdesk_base_url
        self.timeout = timeout
        self.max_503_retries = max_503_retries
        self.max_429_retries = max_429_retries
        self.limiter = RateLimiter()
        self.session = session or requests.Session()
        self.stats = {"requests": 0, "writes": 0, "retries_503": 0, "retries_429": 0}
        self._headers = {
            "Authorization": f"Bearer {settings.helpdesk_api_key}",
            "X-Candidate-Email": settings.candidate_email,
            "Accept": "application/json",
        }
        if settings.candidate_name:
            self._headers["X-Candidate-Name"] = settings.candidate_name

    # ---------- low level ----------

    def _request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        attempts_503 = 0
        attempts_429 = 0
        while True:
            self.limiter.wait()
            self.stats["requests"] += 1
            if method != "GET":
                self.stats["writes"] += 1
            try:
                resp = self.session.request(
                    method, url, headers=self._headers, params=params, json=json, timeout=self.timeout
                )
            except requests.RequestException as exc:  # timeout, dropped connection, broken response
                if method == "GET":
                    attempts_503 += 1
                    if attempts_503 > self.max_503_retries:
                        raise HelpdeskError(0, "network", str(exc)) from exc
                    time.sleep(_backoff(attempts_503))
                    continue
                # POST: we do not know whether the server saved it
                raise UncertainOutcome(f"{method} {path}: {type(exc).__name__}") from exc

            if resp.status_code == 429:
                attempts_429 += 1
                self.stats["retries_429"] += 1
                if attempts_429 > self.max_429_retries:
                    raise HelpdeskError(429, "rate_limited", _safe_json(resp))
                time.sleep(_retry_after_seconds(resp))
                continue

            if resp.status_code == 503:
                attempts_503 += 1
                self.stats["retries_503"] += 1
                if attempts_503 > self.max_503_retries:
                    raise HelpdeskError(503, "service_unavailable", _safe_json(resp))
                time.sleep(_backoff(attempts_503))
                continue

            if resp.status_code == 409:
                raise AlreadyHandled(409, "already_handled", _safe_json(resp))

            if resp.status_code >= 400:
                body = _safe_json(resp)
                error = body.get("error", resp.reason) if isinstance(body, dict) else resp.reason
                raise HelpdeskError(resp.status_code, str(error), body)

            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    # ---------- read ----------

    def status(self) -> dict:
        return self._request("GET", "/status")

    def examples(self) -> list[dict]:
        return _unwrap_list(self._request("GET", "/examples"), "examples")

    def get_ticket(self, ticket_id: str) -> dict:
        return self._request("GET", f"/tickets/{ticket_id}")

    def iter_tickets(self, status: str = "open", limit: int = 10) -> Iterator[dict]:
        """Follow next_cursor until it is null. De-duplicates ids across pages."""
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": limit, "status": status}
            if cursor:
                params["cursor"] = cursor
            page = self._request("GET", "/tickets", params=params)
            for t in _unwrap_list(page, "tickets"):
                if t["id"] in seen:
                    continue
                seen.add(t["id"])
                yield t
            cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not cursor:
                break

    def list_open_tickets(self) -> list[dict]:
        return list(self.iter_tickets("open"))

    def is_open(self, ticket_id: str) -> bool:
        """True if the ticket has no decision yet. Call before any possibly repeated action."""
        return ticket_is_open(self.get_ticket(ticket_id))

    # ---------- write ----------

    def post_action(self, ticket_id: str, action: str, decision: dict) -> Any:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        return self._request("POST", f"/tickets/{ticket_id}/{action}", json=decision)

    def practice_reset(self) -> Any:
        return self._request("POST", "/practice/reset")


# ---------- helpers ----------

def _backoff(attempt: int) -> float:
    return min(0.5 * 2 ** (attempt - 1), 8.0)


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}


def _retry_after_seconds(resp: requests.Response, default: float = 1.0) -> float:
    raw: Any = resp.headers.get("Retry-After")
    if raw is None:
        body = _safe_json(resp)
        raw = body.get("retry_after") if isinstance(body, dict) else None
    try:
        return max(float(raw), 0.2)
    except (TypeError, ValueError):
        return default


def _unwrap_list(data: Any, key: str) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in (key, "items", "data", "results"):
            if isinstance(data.get(k), list):
                return data[k]
    raise HelpdeskError(0, "unexpected_shape", data)


def ticket_is_open(t: dict) -> bool:
    status = str(t.get("status", "")).lower()
    if status:
        return status == "open"
    for k in ("handled", "handling", "decision", "action", "handled_at"):
        if t.get(k):
            return False
    return True
