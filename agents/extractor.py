"""Extractor agent: one official page -> one scheme's rules, each with the sentence that states it.
Gap and batch lanes.

The model proposes; code decides what survives (ingest/extract.py): fields only from fields.yaml,
every rule's quote must be on the page, unmappable conditions become "check yourself" text. Two
checks run after it here and in discovery/discover.py:
  - benefits_a_person(): village and institution programmes are not schemes a person applies for
  - agents/verifier.py: rules whose quote does not mention their field get a second opinion

No cache: pages are already skipped when their content_hash has not changed (crawl_url + store).
Degrade: none; the page stays in the queue and is read again later.
"""
from __future__ import annotations

from core.benefit import is_personal
from ingest.extract import ExtractResult, extract_scheme

from .runner import AgentSpec

NOT_FOR_PEOPLE = {"village", "institution"}


def _call(llm, page) -> ExtractResult:
    return extract_scheme(page, llm)


def benefits_a_person(scheme: dict) -> bool:
    """True unless the extractor said the money goes to a village or institution, or (when it gave
    no recipient) the benefit text reads that way (core/benefit.is_personal)."""
    recipient = scheme.get("benefit_recipient")
    if recipient in NOT_FOR_PEOPLE:
        return False
    if recipient in ("person", "family", "group"):
        return True
    return is_personal(scheme)


EXTRACTOR = AgentSpec(name="extractor", role="extractor", lane="batch", call=_call, est_tokens=7000)
