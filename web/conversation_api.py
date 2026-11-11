"""Conversation API for the /app dashboard and the WhatsApp bridge: a thin adapter over the shared
orchestrator (saathi/orchestrator.py), which runs the flow and calls the agents.

Conversations persist in the conversation table (store/runtime.py) for 7 days, so restarts and
several app processes do not lose them. No message text is stored and no official application is
submitted. Aadhaar, bank and phone numbers are refused before any model sees the message.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from saathi import reply
from saathi.guard import SENSITIVE, sensitive_kind
from saathi.orchestrator import Orchestrator
from saathi.short_answer import choices_of

router = APIRouter(tags=["Conversation"])
QUESTION_TEXT = reply.QUESTION_TEXT
DISCLAIMER = "Recommendations are informational; verify unreviewed schemes against official sources."

_orchestrator: Orchestrator | None = None


def orchestrator() -> Orchestrator:
    """One orchestrator per process: it holds the catalog cache; agents share one client per role."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


def _safe_message(text: str) -> str:
    kind = sensitive_kind(text)
    if kind:
        raise HTTPException(422, f"Please remove the {kind} before sending your message. "
                                 "Do not share Aadhaar, bank or phone numbers.")
    return text.strip()


def _question_text(q: dict, lang: str) -> str:
    return reply.question_text(q, lang)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=1800)
    lang: str = Field(default="en", pattern="^(en|hi|mr)$")
    session_id: str | None = Field(default=None, max_length=64)


def _question_json(q: dict | None, lang: str) -> dict | None:
    if not q:
        return None
    return {"field": q["field"], "text": reply.question_text(q, lang), "reason": q.get("reason", ""),
            "choices": choices_of(q) if q.get("type") in ("choice", "enum") else [], "type": q.get("type", "text")}


@router.post("/api/v2/conversation")
def conversation(body: Message):
    text = _safe_message(body.text)
    sid = body.session_id or uuid.uuid4().hex
    turn = orchestrator().handle(sid, text, body.lang)
    return {
        "session_id": sid,
        "message": turn.reply,
        "question": _question_json(turn.question, turn.lang),
        "corrections": turn.corrections,
        "profile": turn.profile,
        "results": turn.shortlist or {},
        "top": [{"id": c["id"], "name": c["name"], "good": c.get("good", True)} for c in turn.cards],
        "good_matches": turn.good,
        "searching_more": turn.gap_job is not None,
        "degraded": turn.degraded,
        "lang": turn.lang,
        "disclaimer": DISCLAIMER,
    }


@router.delete("/api/v2/conversation/{session_id}")
def clear_conversation(session_id: str):
    orchestrator().forget(session_id)
    return {"deleted": True}


__all__ = ["router", "conversation", "Message", "SENSITIVE", "QUESTION_TEXT", "orchestrator"]
