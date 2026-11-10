"""Deterministic eligibility engine.

check_eligibility(profile) -> list of results, one per scheme:
    status: "eligible" | "eligible_check_manually" | "near_miss" | "needs_info" | "ineligible"
            | "check_manually"
    trace:  every rule with its outcome ("pass" / "fail" / "unknown")

"eligible_check_manually": every mapped rule passes but the scheme also has
unmapped_criteria that a person must check. "check_manually": a web-discovered
scheme with too little evidence, i.e. fewer qualifying rules than open conditions.

Only *qualifying* rules count as evidence. `state` and `district` say where a scheme
applies, not who may have it, so a scheme whose only rules are "Maharashtra, Sangli"
is not understood at all: its real condition (say, "families with girl children")
is sitting in unmapped_criteria. Without this, such a scheme looks eligible to everyone
in the district. Unmapped criteria never count as a pass.
A scheme with a `state` gets an implicit "resident of that state" rule.

The LLM never decides eligibility. It only calls this function and explains the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .loader import load_schemes
from .cohorts import effective_scheme

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
# where a scheme applies, not who qualifies for it
SCOPE_FIELDS = {"state", "district"}


@dataclass
class RuleResult:
    field: str
    op: str
    expected: Any
    actual: Any
    outcome: str            # pass / fail / unknown
    reason: str
    gap: float | None = None    # how far a numeric value is from passing
    near: bool = False          # failed, but within the rule's near_miss tolerance


@dataclass
class SchemeResult:
    scheme_id: str
    name: str
    status: str
    trace: list[RuleResult] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    failed_rules: list[RuleResult] = field(default_factory=list)
    source_url: str = ""
    last_verified: str = ""
    origin: str = "curated"           # curated | discovered
    verified: bool = True             # discovered schemes stay False until a human checks them
    unmapped_criteria: list[str] = field(default_factory=list)

    def explain(self) -> str:
        if self.status == "eligible":
            return f"Eligible for {self.name}."
        if self.status == "eligible_check_manually":
            return (f"Eligibility not confirmed for {self.name}; verify: "
                    f"{'; '.join(self.unmapped_criteria) or 'unreviewed source extraction; confirm all mandatory conditions on the official page'}.")
        if self.status == "check_manually":
            return f"{self.name}: rules could not be read automatically; check the official page."
        if self.status == "near_miss":
            r = self.failed_rules[0]
            return f"Almost eligible for {self.name}: {r.reason} (short by {r.gap:g})."
        if self.status == "needs_info":
            return f"Need more information for {self.name}: {', '.join(self.missing_fields)}."
        return f"Not eligible for {self.name}: {self.failed_rules[0].reason}."


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "lt":
        return actual < expected
    if op == "lte":
        return actual <= expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    raise ValueError(f"unknown op {op}")


def _gap(op: str, actual: Any, expected: Any) -> float | None:
    """Distance to passing, for numeric comparisons only."""
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return None
    if op in ("gte", "gt"):
        return max(0.0, expected - actual + (1 if op == "gt" else 0))
    if op in ("lte", "lt"):
        return max(0.0, actual - expected + (1 if op == "lt" else 0))
    return None


def evaluate_rule(rule: dict, profile: dict) -> RuleResult:
    fname = rule["field"]
    actual = profile.get(fname)
    base = dict(field=fname, op=rule["op"], expected=rule["value"], actual=actual,
                reason=rule.get("reason", ""))
    if actual is None:
        return RuleResult(outcome=UNKNOWN, **base)
    try:
        ok = _compare(rule["op"], actual, rule["value"])
    except TypeError:
        # wrong type in profile (e.g. "sixty" for age) -> treat as unknown, never crash
        return RuleResult(outcome=UNKNOWN, **base)
    if ok:
        return RuleResult(outcome=PASS, **base)
    gap = _gap(rule["op"], actual, rule["value"])
    tolerance = rule.get("near_miss")
    near = gap is not None and tolerance is not None and gap <= tolerance
    return RuleResult(outcome=FAIL, gap=gap, near=near, **base)


def _state_rule(state: str) -> dict:
    return {"field": "state", "op": "eq", "value": state,
            "reason": f"Only for residents of {state.replace('_', ' ').title()}"}


def evaluate_scheme(scheme: dict, profile: dict) -> SchemeResult:
    scheme = effective_scheme(scheme)
    elig = scheme["eligibility"]
    all_specs = list(elig.get("all") or [])
    if scheme.get("district"):
        all_specs.insert(0, {"field": "district", "op": "eq", "value": scheme["district"],
                             "reason": f"Only for residents of {scheme['district'].replace('_', ' ').title()} district"})
    if scheme.get("state"):
        all_specs.insert(0, _state_rule(scheme["state"]))
    all_rules = [evaluate_rule(r, profile) for r in all_specs]
    any_rules = [evaluate_rule(r, profile) for r in elig.get("any") or []]
    trace = all_rules + any_rules

    res = SchemeResult(scheme_id=scheme["id"], name=scheme["name"], status="",
                       trace=trace, source_url=scheme["source_url"],
                       last_verified=str(scheme["last_verified"]),
                       origin=scheme.get("origin", "curated"),
                       verified=bool(scheme.get("verified", scheme.get("origin", "curated") == "curated")),
                       unmapped_criteria=list(scheme.get("unmapped_criteria") or []))
    own_rules = list(elig.get("all") or []) + list(elig.get("any") or [])
    qualifying = [r for r in own_rules if r.get("field") not in SCOPE_FIELDS]
    has_own_rules = bool(own_rules)

    all_failed = [r for r in all_rules if r.outcome == FAIL]
    all_unknown = [r for r in all_rules if r.outcome == UNKNOWN]

    # "any" block: passes if at least one rule passes
    if any_rules:
        any_pass = any(r.outcome == PASS for r in any_rules)
        any_unknown = [r for r in any_rules if r.outcome == UNKNOWN]
        any_failed = [r for r in any_rules if r.outcome == FAIL]
        if not any_pass:
            if any_unknown:
                all_unknown += any_unknown
            else:
                # every alternative failed: only the closest one decides near-miss vs ineligible
                closest = sorted(any_failed, key=lambda r: (not r.near, r.gap if r.gap is not None else 1e9))
                all_failed.append(closest[0])

    res.failed_rules = all_failed
    res.missing_fields = list(dict.fromkeys(r.field for r in all_unknown))

    hard_fails = [r for r in all_failed if not r.near]
    if hard_fails:
        res.status = "ineligible"
        # put the hard failure first so explain() shows it
        res.failed_rules = hard_fails + [r for r in all_failed if r.near]
    elif all_unknown:
        res.status = "needs_info"
    elif all_failed:              # only near misses left
        res.status = "near_miss"
    elif not qualifying or len(qualifying) < len(res.unmapped_criteria):
        # too little machine-readable evidence: more open conditions than rules we could check
        res.status = "check_manually"
    elif res.unmapped_criteria:
        res.status = "eligible_check_manually"
    elif not res.verified:
        res.status = "eligible_check_manually"
    else:
        res.status = "eligible"
    return res


def check_eligibility(profile: dict, schemes: dict | None = None) -> list[SchemeResult]:
    schemes = schemes or load_schemes()
    return [evaluate_scheme(s, profile) for s in schemes.values()]
