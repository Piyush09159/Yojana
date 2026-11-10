"""Pick the next question that settles the most undecided schemes.

For each candidate question we count how many still-undecided schemes have a rule
on one of its fields. Ties go to the question listed first in questions.yaml.

Fields that no hand-written question covers (common once web-discovered schemes are
loaded) get a synthetic question built from fields.yaml, id "field:<name>", text in
English only. The conversation layer rewords it in the user's language. Hand-written
questions come first, so they win ties.
"""
from __future__ import annotations

import math

from .awareness import OBSCURITY, awareness
from .eligibility import check_eligibility
from .fields import load_fields
from .loader import load_questions, load_schemes
from .relevance import relevance

SYNTHETIC_TYPES = {"bool": "yes_no", "int": "number", "number": "number", "enum": "choice", "str": "text"}


def synthetic_questions(questions: tuple, fields: dict | None = None) -> list[dict]:
    fields = fields or load_fields()
    covered = {f for q in questions for f in q["fields"]}
    out = []
    for name, spec in fields.items():
        if name in covered or spec["group"] == "dependency":
            continue
        text = spec["description"]
        out.append({"id": f"field:{name}", "fields": [name], "type": SYNTHETIC_TYPES[spec["type"]],
                    "choices": list(spec.get("values") or []), "synthetic": True,
                    "text_en": text, "text_hi": text, "text_mr": text})
    return out


def _applies(question: dict, profile: dict) -> bool:
    cond = question.get("only_if") or {}
    return all(profile.get(k) == v for k, v in cond.items())


def next_best_question(profile: dict, schemes: dict | None = None,
                       questions: tuple | None = None) -> dict | None:
    """Return the next question dict, or None if nothing useful is left to ask."""
    schemes = schemes or load_schemes()
    if profile.get('_intent') or (profile.get('age') or 0) >= 60 or profile.get('occupation') == 'retired':
        candidates = question_candidates(profile, schemes, questions, limit=1)
        return candidates[0]['question'] if candidates else None
    questions = questions or load_questions()
    results = check_eligibility(profile, schemes)
    undecided = [r for r in results if r.status == "needs_info"]
    if not undecided:
        return None

    missing_by_scheme = [set(r.missing_fields) for r in undecided]
    best, best_score = None, 0
    for q in list(questions) + synthetic_questions(questions):
        if not _applies(q, profile) or q["id"] in profile.get("_asked", []):
            continue
        unanswered = {f for f in q["fields"] if profile.get(f) is None}
        if not unanswered:
            continue
        s = sum(1 for missing in missing_by_scheme if missing & unanswered)
        if s > best_score:
            best, best_score = q, s
    return best


def apply_answer(profile: dict, question: dict, answer) -> dict:
    """Return a new profile with the answer stored. `answer` is already parsed (bool / number)."""
    updated = dict(profile)
    updated["_asked"] = list(profile.get("_asked", [])) + [question["id"]]
    if question["type"] == "yes_no" and answer is False and question.get("on_no"):
        updated.update(question["on_no"])
    elif question["type"] == "yes_no" and answer is True and len(question["fields"]) > 1:
        # combined question answered "yes": the agent must ask which one; mark nothing yet
        updated["_needs_followup"] = question["id"]
    else:
        for f in question["fields"]:
            updated[f] = answer
    return updated


def scheme_weight(scheme: dict) -> float:
    """How much settling this scheme is worth: money, usefulness, and how little known it is."""
    benefit = scheme.get("benefit_annual_inr") or 0
    money = math.log10(1 + benefit / 1000) if benefit else 0.0      # Rs 1k -> 0.3, 1 lakh -> 2
    return 1.0 + money + 0.3 * scheme["rank"]["value_score"] + 0.75 * OBSCURITY[awareness(scheme)]


def question_candidates(profile: dict, schemes: dict, questions: tuple | None = None,
                        limit: int = 5) -> list[dict]:
    """Unknown fields ranked by value of information: for each field, the summed weight of the
    still-undecided schemes that need it. The planner LLM chooses among these; it cannot ask
    about anything else."""
    questions = questions or load_questions()
    by_field = {}
    for q in list(questions) + synthetic_questions(questions):
        if _applies(q, profile) and q["id"] not in profile.get("_asked", []):
            for f in q["fields"]:
                by_field.setdefault(f, q)
    results = check_eligibility(profile, schemes)
    gains: dict[str, dict] = {}
    for r in results:
        if r.status != "needs_info":
            continue
        tier = relevance(schemes[r.scheme_id], profile)
        if tier == 0:
            continue
        w = scheme_weight(schemes[r.scheme_id])
        for f in set(r.missing_fields):
            q = by_field.get(f)
            if q is None or profile.get(f) is not None:
                continue
            g = gains.setdefault(f, {"field": f, "question": q, "score": 0.0, "schemes": [], 'relevance': 0})
            g['relevance'] = max(g['relevance'], tier)
            g["score"] += w
            g["schemes"].append(r.name)
    ranked = sorted(gains.values(), key=lambda g: (-g['relevance'], -g["score"], g["field"]))
    for g in ranked:
        g["score"] = round(g["score"], 2)
    return ranked[:limit]
