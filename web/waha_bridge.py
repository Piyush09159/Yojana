"""Opt-in WAHA bridge for Yojana-Pilot conversation endpoint.

NOT a production messaging service: state/deduplication live in memory; use Redis/Postgres
and a background queue before exposing to public traffic. Only 1:1 incoming text is handled.
"""
from __future__ import annotations
import hmac
import os
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .conversation_api import Message, conversation

router = APIRouter(tags=["WhatsApp (opt-in beta)"])
_guard = threading.RLock()
_chats: dict[str, str] = {}
_seen: OrderedDict[str, float] = OrderedDict()
_MAX_CHATS = 300
_MAX_SEEN = 2000


def _config():
    url = os.getenv("WAHA_BASE_URL", "").rstrip("/")
    key = os.getenv("WAHA_API_KEY", "")
    secret = os.getenv("YOJANA_WAHA_WEBHOOK_SECRET", "")
    if not url or not key or len(secret) < 24:
        raise HTTPException(503, "WhatsApp bridge not configured")
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise HTTPException(503, "WAHA_BASE_URL must be an HTTP(S) URL")
    return url, key, secret


class WAHAEvent(BaseModel):
    event: str = ""
    session: str | None = None
    payload: dict = {}


def _send(url: str, key: str, session: str, chat_id: str, message: str):
    resp = httpx.post(url + "/api/sendText",
                      headers={"X-Api-Key": key},
                      json={"session": session, "chatId": chat_id, "text": message[:3500]},
                      timeout=12)
    resp.raise_for_status()


def _duplicate(msg_id: str) -> bool:
    now = time.time()
    with _guard:
        for key, created in list(_seen.items()):
            if now - created > 7200:
                _seen.pop(key, None)
        if msg_id in _seen:
            return True
        _seen[msg_id] = now
        if len(_seen) > _MAX_SEEN:
            _seen.popitem(last=False)
        return False


@router.post("/api/integrations/waha/{webhook_secret}", include_in_schema=False)
def receive_waha(webhook_secret: str, body: WAHAEvent):
    if os.getenv("YOJANA_WAHA_ENABLED", "false").lower() != "true":
        raise HTTPException(503, "WhatsApp bridge is disabled")
    url, key, secret = _config()
    if not hmac.compare_digest(webhook_secret, secret):
        raise HTTPException(403, "Forbidden")
    expected_session = os.getenv("WAHA_SESSION", "default")
    if body.event != "message" or body.session != expected_session:
        return {"status": "ignored"}
    p = body.payload
    chat_id = p.get("from", "")
    if not isinstance(chat_id, str) or not re.fullmatch(r"\d{6,16}@c\.us", chat_id):
        return {"status": "ignored"}  # never groups, status, newsletters
    if p.get("fromMe") or p.get("hasMedia"):
        return {"status": "ignored"}
    message = p.get("body", "")
    if not isinstance(message, str) or not message.strip():
        return {"status": "ignored"}
    mid = p.get("id")
    if isinstance(mid, dict):
        mid = mid.get("_serialized") or mid.get("id")
    if not isinstance(mid, str) or not mid:
        return {"status": "ignored"}  # reject unidentifiable events to prevent loops
    if _duplicate(f"{expected_session}:{mid}"):
        return {"status": "duplicate"}
    command = message.strip().lower()
    with _guard:
        old = _chats.get(chat_id)
    if command in ("stop", "unsubscribe", "बंद", "थांबा"):
        with _guard:
            _chats.pop(chat_id, None)
        _send(url, key, expected_session, chat_id, "Yojana-Pilot stopped. Send START to opt in again. This does not delete prior service logs.")
        return {"status": "stopped"}
    if command in ("start", "yojana", "योजना", "सुरू"):
        with _guard:
            if len(_chats) >= _MAX_CHATS and chat_id not in _chats:
                raise HTTPException(503, "Chat limit reached")
            _chats[chat_id] = ""
        _send(url, key, expected_session, chat_id,
              "Welcome to Yojana-Pilot! Please describe your situation in English, Hindi or Marathi. Do not send Aadhaar, phone, bank numbers or documents. Reply STOP anytime. This is a demo, not an official government service.")
        return {"status": "started"}
    if old is None:
        return {"status": "not_opted_in"}  # never unsolicited reply
    try:
        # one stable conversation per chat, keyed by a hash of the chat id; the chat id itself is
        # kept in the conversation state only so the gap lane can send a follow-up
        from store.runtime import key_of
        from .conversation_api import orchestrator
        turn = orchestrator().handle(key_of("waha", chat_id), message, "en",
                                     reply_to={"channel": "waha", "chat_id": chat_id})
        result = {"session_id": turn.chat_ref, "message": turn.reply}
    except HTTPException as exc:
        if exc.status_code == 404:  # expired in-memory session: restart on next message
            with _guard:
                _chats[chat_id] = ""
        _send(url, key, expected_session, chat_id,
              "I couldn't process that message. Please try again without sharing sensitive numbers.")
        return {"status": "error", "code": exc.status_code}
    with _guard:
        _chats[chat_id] = result["session_id"]
    _send(url, key, expected_session, chat_id, result["message"])
    return {"status": "replied"}


@router.get("/api/integrations/waha/status")
def waha_status():
    return {"configured": bool(os.getenv("WAHA_BASE_URL") and os.getenv("WAHA_API_KEY")
                              and len(os.getenv("YOJANA_WAHA_WEBHOOK_SECRET", "")) >= 24),
            "enabled": os.getenv("YOJANA_WAHA_ENABLED", "false").lower() == "true",
            "mode": "opt-in text only", "production_ready": False}
