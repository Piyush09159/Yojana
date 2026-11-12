"""Scout agent: a profile group -> the person's needs and scheme names worth looking up. Gap lane.

One call replaces two older ones (discovery brainstorm_names and the investigator's analyse step).
The Scout only says WHERE to look; code turns its needs and names into searches on official sites,
and the engine decides eligibility. Cached per segment for a week: people like this have the same
needs, so the Scout runs at most once per profile group.

Degrade: needs read straight from the profile by code (saathi/investigator.fallback_analysis), no
names. The gap lane still runs on those.
"""
from __future__ import annotations

import json

from saathi.investigator import NEED_TAGS, fallback_analysis
from saathi.sufficiency import segment
from store.runtime import DAY, key_of

from .runner import AgentSpec

MAX_NEEDS, MAX_NAMES, MAX_TERMS = 5, 8, 2

SYSTEM = """You are an experienced social worker in rural India who knows how government help really
reaches people. You get one profile group (state, district, work, gender, social category, age band).
1. needs: their most important needs, including non-obvious ones that lesser-known schemes cover
   (children's education and hostels, house repair, livestock or tools, self-employment loans,
   skill training, funeral or marriage help, help for their caste/tribe/minority group, gender or
   occupation). For each: need (short), why (one sentence), tags (allowed list only), search_terms
   (1-2 short web search phrases, English or the state's language, no site names).
2. scheme_names: up to 8 real, currently running government schemes (central, state, district,
   welfare board, development corporation) worth looking up, official names.
3. summary: two sentences on the situation.
Never decide eligibility; you only suggest where to look."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "needs": {"type": "array", "minItems": 1, "maxItems": MAX_NEEDS, "items": {
            "type": "object",
            "properties": {
                "need": {"type": "string"}, "why": {"type": "string"},
                "tags": {"type": "array", "items": {"enum": NEED_TAGS}},
                "search_terms": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_TERMS},
            },
            "required": ["need", "tags", "search_terms"],
        }},
        "scheme_names": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_NAMES},
    },
    "required": ["summary", "needs", "scheme_names"],
}


def _call(llm, profile: dict) -> dict:
    _, dims = segment(profile)
    r = llm.complete([{"role": "user", "content": "Profile group: " + json.dumps(dims, ensure_ascii=False)}],
                     system=SYSTEM, json_schema=SCHEMA, max_tokens=1200)
    return r.data


def _validate(out: dict, profile: dict) -> dict:
    names = list(dict.fromkeys(n.strip() for n in out.get("scheme_names") or [] if n and n.strip()))[:MAX_NAMES]
    needs = []
    for n in (out.get("needs") or [])[:MAX_NEEDS]:
        terms = [t.strip() for t in n.get("search_terms") or [] if t and t.strip()][:MAX_TERMS]
        needs.append({"need": n["need"].strip(), "why": (n.get("why") or "").strip(),
                      "tags": list(dict.fromkeys(n.get("tags") or [])), "search_terms": terms})
    return {"summary": (out.get("summary") or "").strip(), "needs": needs, "scheme_names": names}


def _degrade(profile: dict) -> dict:
    a = fallback_analysis(profile)
    return {"summary": a["summary"], "needs": a["needs"][:MAX_NEEDS], "scheme_names": []}


SCOUT = AgentSpec(name="scout", role="scout", lane="gap", call=_call, validate=_validate, degrade=_degrade,
                  cache_key=lambda p: key_of(segment(p)[0]), cache_ttl=7 * DAY, est_tokens=2000)
