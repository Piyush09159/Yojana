"""Turn one scheme dict (the engine's format) into the structured rows the Postgres schema keeps
beside it. Pure function: no database, no LLM. store/pg.py writes the dict and these rows in one
transaction, so they can never disagree.

The "who is it for" index (`targets`) is derived from the quote-checked eligibility rules, never
tagged by a model. Only conditions that let people *in* count: exclusions ("not an income-tax
payer", value false, not_in) and scope (state, district) say nothing about who a scheme is for,
the same reasoning core/ranking.py uses for match strength.

    gender eq female              -> ("gender", "female")
    social_category in [sc, st]   -> ("social_category", "sc"), ("social_category", "st")
    age gte 60                    -> ("age_band", "60+")
    annual_family_income_inr lte 250000 -> ("income_limit", "up_to_2_5l")
    is_bpl eq true                -> ("is_bpl", "true")

Topic categories (education, housing, pension, ...) come from a fixed keyword list over the name
and benefit text. They describe what a scheme is about, not who qualifies, so a keyword match is
acceptable there; eligibility is still decided only by core/eligibility.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.awareness import awareness, norm_name
from core.benefit import benefit_of
from core.eligibility import SCOPE_FIELDS
from core.fields import load_fields
from core.quality import quality_of

# Field groups that never describe who a scheme is for.
NON_TARGET_GROUPS = {"exclusions", "dependency", "scheme_lists", "location"}
# Location fields that do describe people (rural/urban); state and district are scope.
TARGET_LOCATION_FIELDS = {"residence"}

AGE_BANDS = [("0-17", 0, 17), ("18-39", 18, 39), ("40-59", 40, 59), ("60+", 60, 200)]
INCOME_LIMITS = [(100_000, "up_to_1l"), (250_000, "up_to_2_5l"), (500_000, "up_to_5l"),
                 (800_000, "up_to_8l")]

CATEGORY_WORDS: dict[str, list[str]] = {
    "education": ["education", "school", "college", "student", "hostel", "tuition", "admission",
                  "शिक्षण", "शिक्षा", "विद्यार्थी", "शाळा"],
    "scholarship": ["scholarship", "fellowship", "शिष्यवृत्ती", "छात्रवृत्ति"],
    "housing": ["house", "housing", "awas", "gharkul", "dwelling", "shelter", "घरकुल", "आवास"],
    "health": ["health", "hospital", "medical", "treatment", "arogya", "surgery", "आरोग्य", "स्वास्थ्य"],
    "pension": ["pension", "पेन्शन", "पेंशन", "निवृत्तिवेतन"],
    "agriculture": ["farmer", "agricultur", "krishi", "crop", "irrigation", "seed", "tractor", "horticultur",
                    "शेतकरी", "कृषी", "कृषि", "किसान", "शेती"],
    "livestock_fisheries": ["livestock", "dairy", "poultry", "goat", "cattle", "fisher", "fishing",
                            "पशु", "मत्स्य", "दुग्ध"],
    "livelihood": ["self-employment", "self employment", "loan", "entrepreneur", "business", "livelihood",
                   "स्वयंरोजगार", "कर्ज", "रोजगार", "व्यवसाय"],
    "skill": ["skill", "training", "कौशल्य", "प्रशिक्षण"],
    "women_child": ["women", "woman", "girl", "daughter", "mahila", "kanya", "widow", "महिला", "मुलगी",
                    "बालिका", "विधवा"],
    "maternity": ["pregnan", "maternity", "lactating", "matru", "गर्भवती", "मातृ"],
    "disability": ["disab", "divyang", "handicap", "दिव्यांग", "अपंग"],
    "senior": ["senior citizen", "old age", "elderly", "वृद्ध", "ज्येष्ठ"],
    "marriage": ["marriage", "vivah", "विवाह"],
    "food": ["ration", "food grain", "foodgrain", "अन्न", "राशन"],
    "insurance": ["insurance", "bima", "विमा", "बीमा"],
}
# Who a scheme targets also says what it is about.
TARGET_CATEGORIES = {
    ("occupation", "farmer"): "agriculture", ("occupation", "agricultural_labourer"): "agriculture",
    ("occupation", "fisher"): "livestock_fisheries", ("is_student", "true"): "education",
    ("occupation", "student"): "education", ("is_pregnant_or_lactating", "true"): "maternity",
    ("disability", "yes"): "disability", ("age_band", "60+"): "senior",
    ("marital_status", "widowed"): "women_child", ("has_girl_child_under_10", "true"): "women_child",
}


@dataclass
class Derived:
    level: str                                  # central | state | district
    columns: dict                               # scheme-table columns besides the jsonb
    rules: list[dict] = field(default_factory=list)
    targets: list[tuple[str, str]] = field(default_factory=list)
    names: list[tuple[str, str, str]] = field(default_factory=list)   # (name, lang, name_norm)
    unmapped: list[str] = field(default_factory=list)
    documents: list[tuple[str, str]] = field(default_factory=list)    # (name, key)
    sources: list[dict] = field(default_factory=list)


def level_of(scheme: dict) -> str:
    if scheme.get("district"):
        return "district"
    return "state" if scheme.get("state") else "central"


def is_exclusion(rule: dict) -> bool:
    """Rules that keep people out, not let them in. Matches core.ranking.is_exclusion, plus `ne`."""
    return rule.get("value") is False or rule.get("op") in ("not_in", "ne")


def _age_bands(lo: float, hi: float) -> list[str]:
    return [label for label, a, b in AGE_BANDS if a <= hi and b >= lo]


def _income_label(ceiling: float) -> str:
    for limit, label in INCOME_LIMITS:
        if ceiling <= limit:
            return label
    return "above_8l"


def _age_range(rules: list[dict]) -> tuple[float, float] | None:
    lo, hi, seen = 0.0, 200.0, False
    for r in rules:
        v, op = r.get("value"), r.get("op")
        if r.get("field") != "age" or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        seen = True
        if op in ("gte", "gt"):
            lo = max(lo, v + (1 if op == "gt" else 0))
        elif op in ("lte", "lt"):
            hi = min(hi, v - (1 if op == "lt" else 0))
        elif op == "eq":
            lo, hi = max(lo, v), min(hi, v)
    return (lo, hi) if seen and lo <= hi else None


def targets_of(scheme: dict, fields: dict | None = None) -> list[tuple[str, str]]:
    fields = fields or load_fields()
    elig = scheme.get("eligibility") or {}
    all_rules, any_rules = list(elig.get("all") or []), list(elig.get("any") or [])
    out: list[tuple[str, str]] = []

    for rule in all_rules + any_rules:
        name, op, value = rule.get("field"), rule.get("op"), rule.get("value")
        spec = fields.get(name)
        if spec is None or name in SCOPE_FIELDS or is_exclusion(rule):
            continue
        if spec["group"] in NON_TARGET_GROUPS and name not in TARGET_LOCATION_FIELDS:
            continue
        if spec["type"] == "enum" and op in ("eq", "in"):
            for v in (value if isinstance(value, list) else [value]):
                out.append((name, str(v)))
        elif spec["type"] == "bool" and op == "eq" and value is True:
            out.append((name, "true"))
        elif name == "disability_percent" and op in ("gte", "gt") and isinstance(value, (int, float)):
            out.append(("disability", "yes"))
        elif name == "annual_family_income_inr" and op in ("lte", "lt") and isinstance(value, (int, float)):
            out.append(("income_limit", _income_label(value)))

    age = _age_range(all_rules)
    if age:
        out += [("age_band", b) for b in _age_bands(*age)]
    for rule in any_rules:                      # an age alternative stands on its own
        single = _age_range([rule])
        if single:
            out += [("age_band", b) for b in _age_bands(*single)]
    return list(dict.fromkeys(out))


def categories_of(scheme: dict, targets: list[tuple[str, str]]) -> list[str]:
    text = " ".join(str(scheme.get(k) or "") for k in ("name", "name_hi", "name_mr", "benefit_summary")).lower()
    found = {cat for cat, words in CATEGORY_WORDS.items() if any(w in text for w in words)}
    found |= {TARGET_CATEGORIES[t] for t in targets if t in TARGET_CATEGORIES}
    return sorted(found)


def _rule_rows(elig: dict) -> list[dict]:
    rows = []
    for block in ("all", "any"):
        for pos, r in enumerate(elig.get(block) or []):
            rows.append({"block": block, "position": pos, "field": r["field"], "op": r["op"],
                         "value": r["value"], "reason": r.get("reason") or "",
                         "source_quote": r.get("source_quote") or "",
                         "near_miss": r.get("near_miss"), "promoted": bool(r.get("promoted_from_unmapped"))})
    return rows


def derive(scheme: dict) -> Derived:
    fields = load_fields()
    elig = scheme.get("eligibility") or {}
    targets = targets_of(scheme, fields)
    benefit = benefit_of(scheme)
    origin = scheme.get("origin", "discovered")
    verified = bool(scheme.get("verified", origin == "curated"))
    names = [(scheme["name"], "en")]
    names += [(scheme[k], lang) for k, lang in (("name_hi", "hi"), ("name_mr", "mr")) if scheme.get(k)]
    unmapped = [u for u in (scheme.get("unmapped_criteria") or []) if isinstance(u, str) and u.strip()]
    docs = [(d.get("name", ""), d.get("key", "")) for d in (scheme.get("documents") or []) if d.get("name")]
    sources = []
    if scheme.get("source_url"):
        sources.append({"url": scheme["source_url"], "site": scheme.get("source_site") or "",
                        "fetched_at": scheme.get("fetched_at"), "content_hash": scheme.get("content_hash"),
                        "is_primary": True})
    columns = {
        "name": scheme["name"], "name_norm": norm_name(scheme["name"]), "level": level_of(scheme),
        "state_key": scheme.get("state"), "district_key": scheme.get("district"),
        "agency_type": scheme.get("agency_type"), "agency_name": scheme.get("agency_name"),
        "ministry": scheme.get("ministry"), "categories": categories_of(scheme, targets),
        "benefit_kind": benefit.kind, "annual_value_inr": int(benefit.annual_value),
        "benefit_personal": benefit.personal, "origin": origin, "verified": verified,
        "awareness": awareness(scheme), "quality_score": quality_of(scheme).score,
        "rule_count": len(elig.get("all") or []) + len(elig.get("any") or []),
        "unmapped_count": len(unmapped), "source_url": scheme.get("source_url"),
        "source_site": scheme.get("source_site"), "content_hash": scheme.get("content_hash"),
        "last_checked": scheme.get("fetched_at"),
    }
    return Derived(level=columns["level"], columns=columns, rules=_rule_rows(elig), targets=targets,
                   names=[(n, lang, norm_name(n)) for n, lang in names], unmapped=unmapped,
                   documents=docs, sources=sources)
