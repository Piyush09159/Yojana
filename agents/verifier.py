"""Verifier agent: a second opinion on rules whose quote may not say what the rule claims.
Gap and batch lanes. Runs on a different vendor from the Extractor (VERIFIER_PROVIDER, falling
back to JUDGE_*), so a model never grades its own work.

Flow, all for one scheme:
  1. code: ingest/extract.flagged_rules() flags rules whose quote mentions none of the field's words
     ("Residing within the Kerala State..." -> residence = rural is flagged; "aged 60 or above" ->
     age >= 60 is not). Unflagged rules never reach a model.
  2. Verifier: for each flagged rule, does the sentence itself state that condition about the applicant?
  3. code: unsupported rules move to "check yourself" text (ingest/extract.drop_unsupported), the safe
     direction: a doubtful rule may cost a person a "check this" note, never a wrongful rejection.

Degrade (no model available): every flagged rule counts as unsupported, for the same reason.
Set VERIFY_RULES=0 to skip the model and keep flagged rules unchanged (tests; offline runs).
"""
from __future__ import annotations

import json
import os

from core.fields import load_fields
from ingest.extract import drop_unsupported, flagged_rules
from store.runtime import key_of

from .runner import AgentInvalid, AgentSpec, run_agent

SYSTEM = """You check rules that were extracted from an Indian government welfare scheme page.
For each rule you get: the profile field and what it means, the condition (op and value), and the exact
sentence quoted from the page. Say supported=true if that sentence states this condition about the
person who applies, in any words. A sentence that names who may apply supports what it clearly implies
about them: "only Lady Health Visitors can apply" implies gender female; "women whose husbands have
died" implies marital status widowed; "pupils enrolled in ashram schools get uniforms" implies the
applicant is a student.
Say supported=false if the sentence is about something else: another person (a child, a daughter, a
husband), a different condition, a residence or domicile rule mistaken for rural/urban, an exclusion
read as a requirement (for example "not eligible if receiving another pension" is not "must be BPL"),
or only a loose association. When it is truly unclear, false. Give a short reason for each."""

SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "supported": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["i", "supported"],
    }}},
    "required": ["verdicts"],
}


def _payload(inp: dict) -> list[dict]:
    fields = load_fields()
    return [{"i": n, "field": r["field"], "meaning": fields.get(r["field"], {}).get("description", ""),
             "op": r["op"], "value": r["value"], "sentence": r["quote"]} for n, r in enumerate(inp["rules"])]


def _call(llm, inp: dict) -> dict:
    content = json.dumps({"scheme": inp["scheme"], "rules": _payload(inp)}, ensure_ascii=False, default=str)
    return llm.complete([{"role": "user", "content": content}], system=SYSTEM, json_schema=SCHEMA,
                        max_tokens=800).data


def _validate(out: dict, inp: dict) -> list[dict]:
    by_index = {v["i"]: v for v in out.get("verdicts") or [] if isinstance(v.get("i"), int)}
    if not by_index:
        raise AgentInvalid("no verdicts")
    # a rule the model skipped is unsupported: the safe direction
    return [{"supported": bool(by_index.get(n, {}).get("supported", False)),
             "reason": by_index.get(n, {}).get("reason", "not answered")} for n in range(len(inp["rules"]))]


def _degrade(inp: dict) -> list[dict]:
    return [{"supported": False, "reason": "verifier unavailable; left for a person to check"} for _ in inp["rules"]]


VERIFIER = AgentSpec(
    name="verifier", role="verifier", lane="gap", call=_call, validate=_validate, degrade=_degrade,
    cache_key=lambda inp: key_of([(r["field"], r["op"], r["value"], r["quote"]) for r in inp["rules"]]),
    est_tokens=1200,
)


def review(scheme: dict, *, llm=None, lane: str = "gap") -> dict:
    """Check one extracted scheme in place. Returns {flagged, dropped, degraded, source}."""
    flagged = flagged_rules(scheme)
    report = {"flagged": len(flagged), "dropped": 0, "degraded": False, "source": ""}
    if not flagged or os.getenv("VERIFY_RULES", "1") == "0":
        return report
    res = run_agent(VERIFIER, {"scheme": scheme.get("name", ""), "rules": flagged}, llm=llm, lane=lane)
    verdicts = res.data if res.ok else _degrade({"rules": flagged})
    unsupported = [f for f, v in zip(flagged, verdicts) if not v["supported"]]
    report.update(dropped=drop_unsupported(scheme, unsupported), degraded=res.degraded, source=res.source)
    return report
