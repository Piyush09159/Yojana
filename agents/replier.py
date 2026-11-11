"""Replier agent: a free question ("what documents do I need?") -> a short answer written only from
the engine's JSON. Real-time lane, used only for intents ask_about_scheme / ask_general; the
5-scheme list and the questions are code templates (saathi/reply.py), not model output.

Code check: the answer is plain text and every link in it appears in the data it was given, so the
model cannot invent a source. Degrade: point to the official page of the scheme asked about.
"""
from __future__ import annotations

import json
import re

from saathi.explain import answer

from .runner import AgentInvalid, AgentSpec

URL = re.compile(r"https?://[^\s)\]>\"']+")
FALLBACK = {
    "en": "I cannot answer that right now. The official page has the details: {url}",
    "hi": "मैं अभी इसका उत्तर नहीं दे पा रहा हूँ। पूरी जानकारी आधिकारिक पेज पर है: {url}",
    "mr": "मी आत्ता याचे उत्तर देऊ शकत नाही. संपूर्ण माहिती अधिकृत पानावर आहे: {url}",
}
NO_SOURCE = {
    "en": "I cannot answer that right now. Please ask again in a moment.",
    "hi": "मैं अभी इसका उत्तर नहीं दे पा रहा हूँ। कृपया थोड़ी देर बाद फिर पूछें।",
    "mr": "मी आत्ता याचे उत्तर देऊ शकत नाही. कृपया थोड्या वेळाने पुन्हा विचारा.",
}


def _call(llm, inp: dict) -> str:
    return answer(llm, inp.get("lang", "en"), inp["question"], inp["context"], inp.get("history") or [])


def _validate(text: str, inp: dict) -> str:
    text = (text or "").strip()
    if not text:
        raise AgentInvalid("empty answer")
    known = json.dumps(inp["context"], ensure_ascii=False, default=str)
    invented = [u for u in URL.findall(text) if u.rstrip(".,") not in known]
    if invented:
        raise AgentInvalid(f"link not in the data: {invented[0][:80]}")
    return text[:1500]


def _source_url(context: dict) -> str | None:
    details = context.get("scheme_details")
    if isinstance(details, dict) and details.get("source_url"):
        return details["source_url"]
    first = (context.get("shortlist") or {}).get("start_with") or {}
    return first.get("source_url")


def _degrade(inp: dict) -> str:
    lang = inp.get("lang", "en") if inp.get("lang") in FALLBACK else "en"
    url = _source_url(inp["context"])
    return FALLBACK[lang].format(url=url) if url else NO_SOURCE[lang]


REPLIER = AgentSpec(name="replier", role="agent", lane="realtime", call=_call, validate=_validate,
                    degrade=_degrade, est_tokens=3000)
