"""Request contract for the PDF service. Every field is bounded and unknown fields are rejected.

The service only formats what the backend sends. It never decides eligibility: `status` is the
engine's own status string (core/eligibility.py) and the guide words it as "conditions we could
check", never as "you are eligible". Schemes the engine found ineligible cannot be sent at all.
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

LANGUAGES = ('en', 'hi', 'mr')
STATUSES = ('eligible', 'eligible_check_manually', 'check_manually', 'near_miss', 'needs_info')


def limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


MAX_SCHEMES = limit('PDF_MAX_SCHEMES', 10)              # per guide
MAX_GUIDES = limit('PDF_MAX_GUIDES', 5)                 # per batch
MAX_BATCH_SCHEMES = limit('PDF_MAX_BATCH_SCHEMES', 25)  # across one batch

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _clean(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError('control characters are not allowed')
    return value


def _url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('only absolute http(s) URLs are allowed')
    if parts.username or parts.password:
        raise ValueError('URLs with credentials are not allowed')
    if any(c.isspace() for c in value):
        raise ValueError('URLs may not contain spaces')
    return value


def Text(max_length: int, min_length: int = 0):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length),
                     AfterValidator(_clean)]


Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=2000), AfterValidator(_url)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Document(Strict):
    name: Text(400, 1)
    issued_by: Text(200) | None = None


class Scheme(Strict):
    id: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.\-]{1,120}$')]
    name: Text(300, 1)
    name_local: Text(300) | None = None          # name_hi / name_mr from the catalog, when it exists
    status: Literal[STATUSES]
    verified: bool = False                         # checked by a person (curated); False = found on a website
    benefit: Text(1500) = ''                       # as published; no computed yearly amount (catalog
                                                   # amounts mix one-time grants and yearly payments)
    conditions_met: Annotated[list[Text(400, 1)], Field(max_length=12)] = []
    check_yourself: Annotated[list[Text(600, 1)], Field(max_length=12)] = []
    missing_information: Annotated[list[Text(120, 1)], Field(max_length=20)] = []
    documents: Annotated[list[Document], Field(max_length=25)] = []
    how_to_apply: Text(4000) = ''
    apply_url: Url | None = None
    source_url: Url                                # every scheme carries its official source
    source_site: Text(200) = ''
    agency: Text(300) = ''
    last_verified: date | None = None


class Guide(Strict):
    language: Literal[LANGUAGES] = 'en'
    recipient_name: Text(80) | None = None         # optional "Prepared for"; never logged or put in metadata
    reference: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9\-]{1,40}$')] | None = None
    generated_on: date | None = None
    schemes: Annotated[list[Scheme], Field(min_length=1, max_length=MAX_SCHEMES)]

    @model_validator(mode='after')
    def _unique(self):
        ids = [s.id for s in self.schemes]
        if len(ids) != len(set(ids)):
            raise ValueError('each scheme may appear only once in a guide')
        return self


class Batch(Strict):
    guides: Annotated[list[Guide], Field(min_length=1, max_length=MAX_GUIDES)]

    @model_validator(mode='after')
    def _total(self):
        if sum(len(g.schemes) for g in self.guides) > MAX_BATCH_SCHEMES:
            raise ValueError(f'a batch may hold at most {MAX_BATCH_SCHEMES} schemes in total')
        return self
