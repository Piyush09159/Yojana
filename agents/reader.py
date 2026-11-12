"""Reader agent: one citizen message -> profile facts + intent. Real-time lane.

The model only labels what the person said (saathi/understand.py); core/fields.validate_profile
keeps or drops each fact. The orchestrator calls this agent only when the code short-answer parser
(saathi/short_answer.py) cannot read the reply, so most WhatsApp replies cost nothing.

Degrade: none. If every model fails, the orchestrator asks the pending question again rather than
guessing facts.
"""
from __future__ import annotations

from dataclasses import asdict

from saathi.understand import Understood, understand
from store.runtime import DAY, key_of

from .runner import AgentSpec


def _call(llm, inp: dict) -> Understood:
    return understand(llm, inp["text"], inp.get("asked"))


def _key(inp: dict) -> str:
    asked = inp.get("asked") or {}
    # same words in answer to the same question -> same facts (the hash never stores the words)
    return key_of(" ".join(inp["text"].lower().split()), asked.get("qid") or asked.get("id"))


READER = AgentSpec(
    name="reader", role="reader", lane="realtime", call=_call,
    cache_key=_key, cache_ttl=30 * DAY,
    dump=asdict, load=lambda d: Understood(**d),
    est_tokens=2500,
)
