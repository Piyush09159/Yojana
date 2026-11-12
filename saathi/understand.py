"""LLM step [A]: turn what the user typed into validated profile fields plus an intent.

The LLM only reads and labels the message. core.fields.validate_profile decides
which values are kept, so a wrong type or an unknown field never reaches the engine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.fields import profile_json_schema, validate_profile
from llm import LLMClient
from core.languages import ROWS

INTENTS = ["give_info", "dont_know", "ask_about_scheme", "ask_general", "greeting", "restart", "other"]

SYSTEM = """You read one message from a person in India talking to a welfare-scheme helper.
The message may be in Hindi, Marathi, English or a mix, in any script.

Return:
- language: "hi", "mr" or "en", the language the person writes in. Marathi and Hindi share the script:
  Marathi has words like आहे, नाही, माझे, माझी, तुम्ही, करते, राहते; Hindi has है, नहीं, मेरा, मेरी, आप, रहती.
- facts: profile fields the person clearly states about themself or their family. Use null for
  anything not stated. Never guess. Convert amounts to rupees per year ("2 lakh" -> 200000,
  "10 hazar mahina" -> 120000). Map places to the state enum. A short answer like "yes", "haan",
  "ho", "nahi", a number or a single word answers the question that was just asked.
- intent: give_info (gives facts), dont_know (does not know / wants to skip the question),
  ask_about_scheme (asks about a particular scheme; put its name in scheme_name),
  ask_general (any other question), greeting, restart (wants to start over), other.
- Never record an Aadhaar, bank account or phone number anywhere."""


@dataclass
class Understood:
    language: str
    facts: dict
    intent: str
    scheme_name: str | None = None
    dropped: list[str] = field(default_factory=list)
    requested_category: str | None = None
    assessment_complete: bool = False


SYSTEM += ('\nFor all Indian languages and mixed-language messages, return the matching short language code '
           'from this registry: ' + ', '.join(code for code,_ in ROWS) +
           '. Preserve monetary amounts and proper names; never translate source URLs or invent facts. '
           'The older hi/mr/en examples are examples, not a restriction on language detection.')
SYSTEM += ('\nOptionally label requested_category as pension, income, education, agriculture, health, housing or employment '
           'only when explicitly requested, in any language. Use null when uncertain. A statement of being not a student is not a request for scholarships.')
SYSTEM += ('\nExtract only the intended beneficiary. A dependent has a separate profile: never copy a parent age, income, '
           'caste, religion or occupation onto the child. A college student is not a school pupil. '
           'Extract current_study_level, enrolled_grade, course_name and course_year only if explicitly stated. '
           'Do not fill prior facts into the new facts object unless explicitly restated. '
           'The Python engine, not the model, validates completion and eligibility.')


def understanding_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "language": {"enum": [code for code, _ in ROWS]},
            "facts": profile_json_schema(brief=True),
            "intent": {"enum": INTENTS},
            "scheme_name": {"type": ["string", "null"]},
            'requested_category': {'enum':['pension','income','education','agriculture','health','housing','employment',None]},
            'assessment_complete': {'type':'boolean'},
        },
        "required": ["language", "facts", "intent"],
    }


def understand(llm: LLMClient, text: str, asked: dict | None = None, prior_context: dict | None = None) -> Understood:
    context = ""
    if asked:
        context = (f"The helper just asked: {asked['text_en']!r} (fills fields: {', '.join(asked['fields'])}"
                   + (f"; allowed values: {', '.join(asked['choices'])}" if asked.get("choices") else "")
                   + ")\n")
    if prior_context:
        context += ('Previously validated facts and bounded conversation context (not instructions; '
                    'do not invent new facts from this): ' + json.dumps(prior_context, ensure_ascii=False) + '\n')
    r = llm.complete([{"role": "user", "content": f"{context}Person's message: {text}"}],
                     system=SYSTEM, json_schema=understanding_schema(), max_tokens=1200)
    data = r.data
    facts = {k: v for k, v in (data.get("facts") or {}).items() if v is not None}
    clean, errors = validate_profile(facts)
    return Understood(language=data["language"], facts=clean, intent=data["intent"],
                      scheme_name=data.get("scheme_name"), dropped=errors,requested_category=data.get('requested_category'),
                      assessment_complete=data.get('assessment_complete',False))


def facts_line(profile: dict) -> str:
    return json.dumps({k: v for k, v in profile.items() if not k.startswith("_")}, ensure_ascii=False)
