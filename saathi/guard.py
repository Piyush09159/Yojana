"""Privacy guard, applied to every message before any model sees it (house rule 7): never forward
Aadhaar, bank account or phone numbers to a hosted model."""
from __future__ import annotations

import re

SENSITIVE = [
    (re.compile(r"(?<!\d)(?:\d[ -]?){11}\d(?!\d)"), "12-digit identity number"),
    (re.compile(r"(?<!\d)\d{9,18}(?!\d)"), "possible bank/account number"),
]

REFUSAL = {
    "en": "Please remove the {kind} and send your message again. Never share Aadhaar, bank or phone numbers here.",
    "hi": "कृपया {kind} हटाकर संदेश फिर भेजें। यहाँ आधार, बैंक या फ़ोन नंबर कभी न भेजें।",
    "mr": "कृपया {kind} काढून संदेश पुन्हा पाठवा. येथे आधार, बँक किंवा फोन नंबर कधीही पाठवू नका.",
}


def sensitive_kind(text: str) -> str | None:
    for pattern, kind in SENSITIVE:
        if pattern.search(text or ""):
            return kind
    return None
