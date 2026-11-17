"""Shared types for the decision engine (Phase 2) and the safety layer (Phase 3).

Both layers exchange a `Decision`. The engine proposes one; the safety layer may
override it. Only `to_payload()` output is ever sent to the helpdesk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

LANGUAGES = ("nl", "en", "de")
CATEGORIES = ("bug", "billing", "feature_request", "question", "other")
PRIORITIES = ("P1", "P2", "P3", "P4")
ACTIONS = ("reply", "handoff", "close")

FAQ_PATH = Path(__file__).resolve().parent.parent / "faq.md"
_SECTION_RE = re.compile(r"^## (F\d{2}) · (.+)$", re.MULTILINE)


@dataclass
class Decision:
    language: str
    category: str
    priority: str
    action: str
    reason: str
    draft_reply: str = ""
    handoff_note: str = ""
    confidence: float | None = None
    # FAQ sections the reply is based on, e.g. ["F02"]. Empty for handoff/close.
    faq_ids: list[str] = field(default_factory=list)
    # Raw language code the model detected; may be outside LANGUAGES (e.g. "fr").
    detected_language: str = ""
    # Names of safety-layer rules that changed this decision, e.g. ["mandatory_handoff:refund"].
    overrides: list[str] = field(default_factory=list)

    def problems(self) -> list[str]:
        """Return a list of contract violations; empty means the decision can be posted."""
        out: list[str] = []
        if self.language not in LANGUAGES:
            out.append(f"language {self.language!r} not in {LANGUAGES}")
        if self.category not in CATEGORIES:
            out.append(f"category {self.category!r} not in {CATEGORIES}")
        if self.priority not in PRIORITIES:
            out.append(f"priority {self.priority!r} not in {PRIORITIES}")
        if self.action not in ACTIONS:
            out.append(f"action {self.action!r} not in {ACTIONS}")
        if not self.reason.strip():
            out.append("reason is empty")
        if self.action == "reply" and not self.draft_reply.strip():
            out.append("reply without draft_reply")
        if self.action == "handoff" and not self.handoff_note.strip():
            out.append("handoff without handoff_note")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            out.append("confidence outside 0..1")
        return out

    def to_payload(self) -> dict:
        """Request body for POST /tickets/{id}/{action}. Internal fields are left out."""
        body: dict = {
            "language": self.language,
            "category": self.category,
            "priority": self.priority,
            "reason": self.reason,
        }
        if self.action == "reply":
            body["draft_reply"] = self.draft_reply
        elif self.action == "handoff":
            body["handoff_note"] = self.handoff_note
        if self.confidence is not None:
            body["confidence"] = round(self.confidence, 2)
        return body


@lru_cache(maxsize=1)
def load_faq() -> str:
    """Full faq.md text, including the client's rules at the bottom."""
    return FAQ_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def faq_sections() -> dict[str, str]:
    """Map FAQ id to its text ("F02" -> "Adding a new employee\\nManagers go to ...").

    Only the help-centre knowledge sections; the client's rules block is excluded.
    """
    knowledge = load_faq().split("\n---\n", 1)[0]
    matches = list(_SECTION_RE.finditer(knowledge))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(knowledge)
        sections[m.group(1)] = f"{m.group(2).strip()}\n{knowledge[m.end():end].strip()}"
    return sections
