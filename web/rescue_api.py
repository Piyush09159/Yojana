"""Stateless Rescue Mode endpoints; does not call an LLM or government portal.

Attach to the FastAPI app with `app.include_router(router)`.
The caller is responsible for HTTPS, authentication and rate limiting in production.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict

from core.preflight import preflight_check
from core.status_decoder import decode_status
from discovery.catalog import load_catalog
from . import case_store
from .access import owner

router = APIRouter(prefix="/api/rescue", tags=["rescue"])

# Do not accept identifying account numbers; the original design uses only names
# and optional birth dates from two documents. Inputs are processed in-memory.
ALLOWED_RECORDS = frozenset({"aadhaar", "bank_passbook", "land_record", "ration_card"})
ALLOWED_FACTS = frozenset({
    "ekyc_done", "bank_aadhaar_npci_seeded", "bank_account_active",
    "land_record_seeded", "ayushman_card_made",
})


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=150)
    dob: str | None = Field(default=None, max_length=32)


class RescueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme_id: str = Field(min_length=1, max_length=120)
    records: dict[str, Record] = Field(default_factory=dict)
    facts: dict[str, bool | None] = Field(default_factory=dict)
    status_text: str | None = Field(default=None, max_length=300)
    case_id: str | None = Field(default=None, max_length=64)


def _save_case(body, result, request):
    if not body.case_id:
        return
    if not case_store.enabled():
        raise HTTPException(503, 'Persistent cases are not configured')
    with case_store.transaction(body.case_id) as tx:
        row = tx.session(body.case_id, owner(request))
        if row['status'] == 'closed':
            raise HTTPException(409, 'Case is closed. Reopen it before continuing.')
        state = row['state']
        # Keep actionable flags, not document names, birth dates, or portal text.
        summary = {'scheme_id': body.scheme_id, 'facts': body.facts,
                   'flags': result.get('flags', []), 'government_status_verified': False}
        changed = state.get('rescue') != summary
        state['rescue'] = summary
        tx.save(body.case_id, state, owner(request), row['kind'])
        if changed:
            tx.execute("UPDATE case_sessions SET status='open' WHERE id=?", (body.case_id,))


def _scheme(request: RescueRequest) -> dict[str, Any]:
    # Every scheme is selected from the catalog; clients cannot submit arbitrary
    # rule definitions or request that the model decide eligibility.
    scheme = load_catalog().get(request.scheme_id)
    if scheme is None:
        raise HTTPException(404, detail="Unknown scheme")
    if not all(k in ALLOWED_RECORDS for k in request.records):
        raise HTTPException(422, detail="Unsupported document type")
    if not all(k in ALLOWED_FACTS for k in request.facts):
        raise HTTPException(422, detail="Unsupported dependency fact")
    # Prevent using unverified scraped rules for automated remediation.
    if scheme.get("origin") == "discovered" and not scheme.get("verified", False):
        raise HTTPException(409, detail="Scheme has not been manually verified for Rescue Mode")
    if not (scheme.get("actions") and scheme.get("dependencies") is not None):
        raise HTTPException(409, detail="Rescue workflow is not configured for this scheme")
    return scheme


def _preflight(request: RescueRequest, scheme: dict[str, Any]):
    records = {k: v.model_dump(exclude_none=True) for k, v in request.records.items()}
    try:
        return preflight_check(request.scheme_id, records, request.facts,
                               schemes={request.scheme_id: scheme})
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, detail="Scheme preflight configuration is incomplete") from exc


@router.post("/preflight")
def preflight(request: RescueRequest, http_request: Request):
    scheme = _scheme(request)
    result = _preflight(request, scheme)
    payload = {
        "scheme_id": request.scheme_id,
        "scheme_name": scheme["name"],
        "verified": True,
        "ready": result.ready,
        "fixes": [asdict(x) for x in result.fixes],
        "checks": [asdict(x) for x in result.checks],
        "unknown_dependencies": result.unknown_dependencies,
        "flags": sorted(result.flags),
        "source_url": scheme.get("source_url"),
        "disclaimer": "These checks use facts supplied by the user; no government records were queried.",
    }
    _save_case(request, payload, http_request)
    return payload


@router.post("/diagnose")
def diagnose(request: RescueRequest, http_request: Request):
    scheme = _scheme(request)
    if not request.status_text or not request.status_text.strip():
        raise HTTPException(422, detail="status_text is required")
    if not scheme.get("status_map"):
        raise HTTPException(409, detail="Status diagnosis is not configured for this scheme")
    pre = _preflight(request, scheme)
    try:
        result = decode_status(request.scheme_id, request.status_text,
                               flags=pre.flags, schemes={request.scheme_id: scheme})
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, detail="Scheme status configuration is incomplete") from exc
    payload = {
        "scheme_id": request.scheme_id,
        "matched_status": result.matched_status,
        "match_score": result.match_score,
        "likely_causes": [asdict(x) for x in result.causes],
        "flags": sorted(pre.flags),
        "unknown_dependencies": pre.unknown_dependencies,
        "note": result.note,
        "source_url": scheme.get("source_url"),
        "government_status_verified": False,
    }
    _save_case(request, payload, http_request)
    return payload
