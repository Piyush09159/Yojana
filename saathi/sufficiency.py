"""Is the database enough for this person? Deterministic, no LLM.

A match is "good" only when the engine says the person qualifies AND the record is solid enough to
stand behind:
    status      eligible or eligible_check_manually
    quality     core/quality score >= QUALITY_FLOOR (the record says enough to act on)
    match       ranking match strength >= MIN_MATCH (the scheme is aimed at someone like them,
                not matched on a single generic condition such as "SC" alone)

The orchestrator shows good matches first and asks the gap lane for more when it has fewer than
TARGET. A segment (profile group) is what "people like this" means for caching and for searching
the web at most once a day: state, district, occupation, gender, social category and age band.
"""
from __future__ import annotations

from core.quality import quality_of

GOOD_STATUSES = ("eligible", "eligible_check_manually")
TARGET = 5
QUALITY_FLOOR = 30
MIN_MATCH = 2.0


def age_band(age) -> str:
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return "?"
    return "0-17" if age < 18 else "18-39" if age < 40 else "40-59" if age < 60 else "60+"


def segment(profile: dict) -> tuple[str, dict]:
    dims = {k: profile.get(k) for k in ("state", "district", "occupation", "gender", "social_category")}
    dims["age_band"] = age_band(profile.get("age"))
    return "|".join(str(v or "?") for v in dims.values()), dims


def is_good(card: dict, scheme: dict) -> tuple[bool, str]:
    if card.get("status") not in GOOD_STATUSES:
        return False, f"status {card.get('status')}"
    q = quality_of(scheme).score
    if q < QUALITY_FLOOR:
        return False, f"record too thin (quality {q})"
    if (card.get("match") or 0) < MIN_MATCH:
        return False, "matches on too little (e.g. one generic condition)"
    return True, ""


def pick(shortlist: dict, catalog: dict, n: int = TARGET) -> tuple[list[dict], int]:
    """Up to n cards, good matches first in rank order, then the best of the rest marked weak.
    Returns (cards, number of good ones). Each card gets 'good' and, when weak, 'weak_reason'."""
    ranked = ([shortlist["start_with"]] if shortlist.get("start_with") else []) + list(shortlist.get("others") or [])
    good, weak = [], []
    for card in ranked:
        ok, why = is_good(card, catalog[card["id"]])
        (good if ok else weak).append({**card, "good": ok, **({} if ok else {"weak_reason": why})})
    cards = (good + weak)[:n]
    return cards, min(len(good), n)
