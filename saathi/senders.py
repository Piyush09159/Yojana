"""Deliver a message to a person outside a request (follow-ups from the gap lane).

Every conversation stores how to reach it (`reply_to` in its state). WhatsApp chats are sent through
WAHA when it is configured; everything else (the /app dashboard, the terminal) gets the message on
its next turn, because the orchestrator shows undelivered follow-ups first.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("senders")


def deliver(state: dict, text: str) -> bool:
    """Try to send now. Returns True when the message left; False means "show it next turn"."""
    target = state.get("reply_to") or {}
    if target.get("channel") == "waha" and target.get("chat_id"):
        if os.getenv("YOJANA_WAHA_ENABLED", "false").lower() != "true":
            return False
        try:
            from web.waha_bridge import _config, _send
            url, key, _ = _config()
            _send(url, key, os.getenv("WAHA_SESSION", "default"), target["chat_id"], text)
            return True
        except Exception as e:                        # sending failed: the next turn will show it
            log.warning("follow-up not sent through WAHA: %s", e)
            return False
    return False
