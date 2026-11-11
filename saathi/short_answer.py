"""Read short replies to the question just asked, in code, so most WhatsApp turns cost no model call.

    parse(pending_question, "ho", "mr")        -> Parsed("value", True)
    parse(pending_question, "45", "en")        -> Parsed("value", 45)
    parse(pending_question, "2", "hi")         -> Parsed("value", <2nd listed choice>)
    parse(pending_question, "माहित नाही", "mr") -> Parsed("skip")
    parse(pending_question, "I am a widow and I work on farms", "en") -> Parsed("none")  (Reader agent)

Only clear, short answers are taken: anything longer than a few words, or anything ambiguous, goes
to the Reader agent, which can pick several facts out of one sentence. The parsed value is still
validated by core/fields.check_value before it is stored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.fields import load_fields

MAX_WORDS = 4
YES = {"y", "yes", "yeah", "yep", "ha", "haan", "haa", "han", "ho", "hoy", "ji", "sure", "ok",
       "होय", "हो", "हाँ", "हां", "हा", "जी", "जी हाँ", "हो जी"}
NO = {"n", "no", "nope", "nahi", "nahin", "nai", "na", "nako", "नाही", "नहीं", "नही", "ना", "नको", "जी नहीं"}
SKIP = {"skip", "dont know", "don't know", "not sure", "pata nahi", "pata nahin", "maahit nahi", "mahit nahi",
        "पता नहीं", "पता नही", "माहित नाही", "माहिती नाही", "सांगता येत नाही", "नहीं पता"}
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
# words people use for an enum value, in en/hi/mr
SYNONYMS = {
    "gender": {"male": ["male", "man", "m", "purush", "पुरुष", "आदमी", "मर्द"],
               "female": ["female", "woman", "f", "mahila", "महिला", "स्त्री", "औरत", "बाई"],
               "transgender": ["transgender", "trans", "तृतीयपंथी", "ट्रांसजेंडर"]},
    "social_category": {"sc": ["sc", "एससी", "अनुसूचित जाति", "अनुसूचित जाती"],
                        "st": ["st", "एसटी", "अनुसूचित जनजाति", "अनुसूचित जमाती"],
                        "obc": ["obc", "ओबीसी"], "ews": ["ews", "ईडब्ल्यूएस"],
                        "general": ["general", "open", "सामान्य", "खुला", "खुली", "ओपन"]},
    "residence": {"rural": ["rural", "village", "gaon", "gav", "ग्रामीण", "गाव", "गांव", "गाँव"],
                  "urban": ["urban", "city", "town", "shahar", "शहरी", "शहर"]},
    "marital_status": {"widowed": ["widow", "widowed", "vidhwa", "विधवा"],
                       "married": ["married", "विवाहित", "शादीशुदा"],
                       "never_married": ["unmarried", "single", "अविवाहित"]},
}
MONEY_UNITS = {"lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "लाख": 100_000,
               "hazar": 1_000, "hazaar": 1_000, "thousand": 1_000, "k": 1_000, "हजार": 1_000, "हज़ार": 1_000}
NUMBER = re.compile(r"^(\d+(?:\.\d+)?)\s*([^\d\s.]*)$")
# number questions where "no" / "none" means zero ("Any disability?" "No" -> 0 percent)
ZERO_IF_NO = {"disability_percent", "num_children", "land_acres", "family_monthly_pension_inr"}
NONE_WORDS = NO | {"none", "nil", "zero", "koi nahi", "कोई नहीं", "काहीही नाही", "शून्य"}
PERIODIC = re.compile(r"month|mahina|mahine|महिना|महीने|महीना|दरमहा|week|day", re.I)


@dataclass
class Parsed:
    kind: str                    # value | skip | none
    value: Any = None


def _norm(text: str) -> str:
    t = (text or "").strip().lower().translate(DEVANAGARI_DIGITS)
    t = re.sub(r"[.!?,;:।]+$", "", t)
    return re.sub(r"\s+", " ", t)


def choices_of(question: dict) -> list[str]:
    if question.get("choices"):
        return list(question["choices"])
    fields = question.get("fields") or [question.get("field")]
    spec = load_fields().get(fields[0]) if fields and fields[0] else None
    return list((spec or {}).get("values") or [])


def _number(t: str, field: str):
    if PERIODIC.search(t):
        return None                  # "10 hazar mahina" is monthly: the Reader converts it
    m = NUMBER.match(t.replace(",", ""))
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2).strip()
    if unit and unit not in MONEY_UNITS and unit not in ("years", "year", "yrs", "वर्ष", "साल", "%"):
        return None
    if unit in MONEY_UNITS:
        if field != "annual_family_income_inr":
            return None
        value *= MONEY_UNITS[unit]
    return int(value) if value == int(value) else value


def parse(question: dict | None, text: str, lang: str = "en") -> Parsed:
    if not question:
        return Parsed("none")
    t = _norm(text)
    if not t:
        return Parsed("none")
    if t in SKIP:
        return Parsed("skip")
    if len(t.split()) > MAX_WORDS:
        return Parsed("none")
    qtype = question.get("type")
    field = (question.get("fields") or [question.get("field")])[0]

    if qtype in ("yes_no", "bool"):
        if t in YES:
            return Parsed("value", True)
        if t in NO:
            return Parsed("value", False)
        return Parsed("none")
    if qtype in ("number", "int"):
        if field in ZERO_IF_NO and t in NONE_WORDS:
            return Parsed("value", 0)
        v = _number(t, field)
        return Parsed("value", v) if v is not None else Parsed("none")
    if qtype in ("choice", "enum"):
        choices = choices_of(question)
        if t.isdigit() and 1 <= int(t) <= len(choices) and len(choices) <= 12:
            return Parsed("value", choices[int(t) - 1])
        key = t.replace(" ", "_")
        if key in choices:
            return Parsed("value", key)
        for value, words in (SYNONYMS.get(field) or {}).items():
            if value in choices and t in words:
                return Parsed("value", value)
        # a single word that uniquely starts one choice ("farm" -> farmer is ambiguous with labourer: no)
        starts = [c for c in choices if c.replace("_", " ").startswith(t) and len(t) >= 4]
        if len(starts) == 1:
            return Parsed("value", starts[0])
        return Parsed("none")
    return Parsed("none")
