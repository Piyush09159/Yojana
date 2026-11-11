"""How complete and trustworthy one scheme record is, 0-100. Deterministic, no LLM.

The score answers "can the engine judge this scheme well, and can the person act on it?":

    up to 30  qualifying rules the engine can check (3 or more = full marks)
    up to 20  share of the scheme's conditions that are checkable rules, not free text
    up to 15  a benefit amount is stated (cash or insurance 15, free goods or services 8)
       10     documents are listed
       10     how to apply, or an apply link, is given
       10     a person has checked it
        5     the source was read in the last year

`state` and `district` are not qualifying rules: they say where a scheme applies, not who
qualifies (see core/eligibility.py). The orchestrator counts a match as "good" only above a
quality floor, and the review queue starts with low scores on schemes people actually see.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .benefit import annual_value
from .eligibility import SCOPE_FIELDS

MAX_RULE_POINTS, RULES_FOR_FULL_MARKS = 30.0, 3
CHECKABLE_POINTS = 20.0
BENEFIT_POINTS = {"cash": 15.0, "insurance": 15.0, "in_kind": 8.0, "unknown": 0.0}
DOCUMENT_POINTS = APPLY_POINTS = VERIFIED_POINTS = 10.0
FRESH_POINTS, FRESH_DAYS = 5.0, 365


@dataclass
class Quality:
    score: int
    parts: dict[str, float] = field(default_factory=dict)


def qualifying_rules(scheme: dict) -> list[dict]:
    elig = scheme.get("eligibility") or {}
    rules = list(elig.get("all") or []) + list(elig.get("any") or [])
    return [r for r in rules if r.get("field") not in SCOPE_FIELDS]


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def quality_of(scheme: dict, today: date | None = None) -> Quality:
    today = today or date.today()
    rules = len(qualifying_rules(scheme))
    unmapped = len(scheme.get("unmapped_criteria") or [])
    _, kind, _ = annual_value(scheme)
    seen = _as_date(scheme.get("last_verified"))
    parts = {
        "rules": MAX_RULE_POINTS * min(rules, RULES_FOR_FULL_MARKS) / RULES_FOR_FULL_MARKS,
        "checkable": CHECKABLE_POINTS * rules / (rules + unmapped) if rules + unmapped else 0.0,
        "benefit": BENEFIT_POINTS.get(kind, 0.0),
        "documents": DOCUMENT_POINTS if scheme.get("documents") else 0.0,
        "how_to_apply": APPLY_POINTS if (scheme.get("how_to_apply") or scheme.get("apply_url")) else 0.0,
        "verified": VERIFIED_POINTS if scheme.get("verified", scheme.get("origin", "curated") == "curated") else 0.0,
        "fresh": FRESH_POINTS if seen and (today - seen).days <= FRESH_DAYS else 0.0,
    }
    return Quality(score=round(sum(parts.values())), parts={k: round(v, 1) for k, v in parts.items()})
