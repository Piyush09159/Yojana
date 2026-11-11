"""Rank the schemes a person qualifies for, best first, and say why.

A scheme is ranked on four things, all computed in code so the order can be explained and tested:

  match       how squarely it is aimed at this person. A scheme with four conditions they all
              meet (widow, SC, landless, income under Rs 1 lakh) is aimed at them; one with a
              single generic condition is not. Conditions on who they *are* (caste, disability,
              widowhood, occupation) count double, because that is what targeting looks like.
  value       rupees a year they could actually expect (core/benefit.py turns one-time grants,
              insurance covers and monthly payments into one comparable number).
  confidence  do we really understand the rules: checked by a person > every rule read >
              some conditions left for them to check.
  effort      how hard it is to claim: documents to gather, and the extractor's effort guess.

score = 1*match + 2*value_points + confidence - effort   (MATCH_WEIGHT, VALUE_WEIGHT below)

`start_with` is the single best one (the blueprint warns that a long list causes fewer
applications, not more). `lesser_known` is the same ranking limited to schemes people rarely
hear about, which is where the unclaimed money is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .awareness import OBSCURITY, awareness
from .benefit import benefit_of
from .eligibility import PASS, SCOPE_FIELDS, RuleResult, SchemeResult
from .loader import load_schemes

# Conditions about who a person is, rather than what they have. Meeting these means the
# scheme was written for someone like them.
IDENTITY_FIELDS = {"social_category", "is_minority", "disability_percent", "marital_status",
                   "gender", "occupation", "is_ex_serviceman", "is_construction_worker_registered",
                   "has_eshram_card", "is_student", "residence", "age", "is_bpl", "ration_card_type"}
MATCH_WEIGHT = 1.0
VALUE_WEIGHT = 2.0              # among schemes they already qualify for, what they get matters most
MAX_VALUE_POINTS = 6.0          # Rs 1,00,000 a year and above
EFFORT_PER_DOCUMENT = 0.15


def is_exclusion(rule: RuleResult) -> bool:
    """A rule that keeps the wrong people out rather than letting the right people in.
    Passing "you are not an income-tax payer" says nothing about the scheme being meant for you."""
    return rule.expected is False or rule.op == "not_in"


@dataclass
class Scored:
    result: SchemeResult
    score: float
    match: float
    value: int              # rupees a year
    reasons: list[str] = field(default_factory=list)


@dataclass
class Ranking:
    start_with: SchemeResult | None
    queue: list[SchemeResult]
    near_misses: list[SchemeResult]
    pending: list[SchemeResult]
    check_manually: list[SchemeResult] = field(default_factory=list)
    scored: dict = field(default_factory=dict)       # scheme_id -> Scored, for explaining the order


def match_strength(result: SchemeResult) -> tuple[float, int]:
    """How specifically this scheme targets the person: (strength, conditions met).
    Only conditions they actually meet count; where and when a scheme applies does not."""
    met = [t for t in result.trace
           if t.outcome == PASS and t.field not in SCOPE_FIELDS and not is_exclusion(t)]
    strength = sum(2.0 if t.field in IDENTITY_FIELDS else 1.0 for t in met)
    # conditions we could not read mean we may be matching on only part of the real rule
    strength -= 0.5 * len(result.unmapped_criteria)
    return max(0.0, strength), len(met)


def value_points(annual_value: int) -> float:
    """Rupees a year on a flattened scale: more money matters, but ten times more is not ten
    times better for someone choosing what to apply for first."""
    if annual_value <= 0:
        return 0.0
    return min(MAX_VALUE_POINTS, 1.2 * math.log10(1 + annual_value / 100))


def confidence_points(result: SchemeResult) -> float:
    points = 1.0 if result.verified else 0.0
    points += 1.0 if result.status == "eligible" else 0.0
    points -= 0.3 * len(result.unmapped_criteria)
    return points


def effort_points(scheme: dict) -> float:
    documents = len(scheme.get("documents") or [])
    guessed = scheme.get("rank", {}).get("effort", 3)
    return EFFORT_PER_DOCUMENT * documents + 0.4 * (guessed - 1)


def score_scheme(result: SchemeResult, scheme: dict) -> Scored:
    strength, met = match_strength(result)
    benefit = benefit_of(scheme)
    value = value_points(benefit.annual_value)
    confidence = confidence_points(result)
    effort = effort_points(scheme)
    total = MATCH_WEIGHT * strength + VALUE_WEIGHT * value + confidence - effort

    reasons = []
    if met:
        reasons.append(f"you meet {met} of its conditions"
                       + (" including who it is meant for" if strength > met else ""))
    if benefit.annual_value > 0:
        reasons.append(f"worth about Rs {benefit.annual_value:,} a year ({benefit.basis})")
    elif not benefit.personal:
        reasons.append(benefit.basis)
    if result.unmapped_criteria:
        reasons.append(f"{len(result.unmapped_criteria)} condition(s) you must check yourself")
    if not result.verified:
        reasons.append("found on an official site, not yet checked by a person")
    if awareness(scheme) != "high":
        reasons.append("few people apply for this one")
    return Scored(result=result, score=round(total, 2), match=round(strength, 2),
                  value=benefit.annual_value, reasons=reasons)


def score(scheme: dict) -> int:
    """Kept for callers that only want the old value-minus-effort number."""
    r = scheme["rank"]
    return 2 * r["value_score"] - r["effort"]


def rank_schemes(results: list[SchemeResult], schemes: dict | None = None) -> Ranking:
    schemes = schemes or load_schemes()
    scored = {r.scheme_id: score_scheme(r, schemes[r.scheme_id]) for r in results
              if r.status in ("eligible", "eligible_check_manually")}
    order = sorted(scored.values(), key=lambda s: (-s.score, s.result.scheme_id))
    ranked = [s.result for s in order]
    return Ranking(
        start_with=ranked[0] if ranked else None,
        queue=ranked[1:],
        near_misses=sorted([r for r in results if r.status == "near_miss"],
                           key=lambda r: (r.failed_rules[0].gap or 0) if r.failed_rules else 0),
        pending=[r for r in results if r.status == "needs_info"],
        check_manually=[r for r in results if r.status == "check_manually"],
        scored=scored,
    )


def lesser_known(results: list[SchemeResult], schemes: dict, limit: int = 6) -> list[SchemeResult]:
    """The same ranking, limited to schemes that are not widely known. Obscurity is a small
    bonus here: among schemes worth a similar amount, the unclaimed one is the useful tip."""
    ranking = rank_schemes(results, schemes)
    picked = [s for s in ranking.scored.values() if awareness(schemes[s.result.scheme_id]) != "high"]
    picked.sort(key=lambda s: (-(s.score + 0.5 * OBSCURITY[awareness(schemes[s.result.scheme_id])]),
                               s.result.scheme_id))
    return [s.result for s in picked[:limit]]
