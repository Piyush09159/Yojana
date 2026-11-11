"""Consent-based Bolna verification calls. Bolna talks; core/ decides."""
import json, os, re, time, uuid
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from core.eligibility import evaluate_scheme
from core.fields import validate_profile
from discovery.catalog import load_catalog
from . import case_store
from .access import owner
from .channel_security import seal, unseal

router = APIRouter(prefix='/api/bolna', tags=['Bolna verification calls'])
API = 'https://api.bolna.ai'
# Fields the Bolna agent's extraction prompt must return, named exactly as in fields.yaml.
VERIFY_FIELDS = ('age', 'state', 'district', 'annual_family_income_inr', 'occupation',
                 'owns_cultivable_land', 'land_acres', 'is_bpl')


def _headers():
    key = os.getenv('BOLNA_API_KEY')
    if not key or not os.getenv('BOLNA_AGENT_ID'):
        raise HTTPException(503, 'Bolna is not configured')
    return {'Authorization': 'Bearer ' + key}


class VerifyRequest(BaseModel):
    case_id: str = Field(max_length=64)
    scheme_id: str = Field(max_length=120)
    phone: str = Field(pattern=r'^\+[1-9]\d{9,14}$')   # E.164
    consent: StrictBool


@router.post('/verify')
def start_verification(body: VerifyRequest, request: Request):
    if not body.consent:
        raise HTTPException(422, 'Explicit consent to a verification call is required')
    headers = _headers()
    scheme = load_catalog().get(body.scheme_id)
    if scheme is None:
        raise HTTPException(404, 'Unknown scheme')
    vid, now = uuid.uuid4().hex, time.time()
    with case_store.transaction(body.case_id) as tx:
        row = tx.session(body.case_id, owner(request))
        declared = {k: v for k, v in row['state'].get('profile', {}).items() if k in VERIFY_FIELDS}
        # One active call per case, so a double-click cannot ring the farmer twice.
        if tx.execute("SELECT id FROM verification_calls WHERE session_id=? AND status IN ('queued','calling')",
                      (body.case_id,)).fetchone():
            raise HTTPException(409, 'A verification call is already in progress')
        tx.execute('INSERT INTO verification_calls VALUES(?,?,?,?,?,?,?,?,?)',
                   (vid, body.case_id, body.scheme_id, seal(body.phone), 'queued', None, None, now, now))
    try:
        r = httpx.post(API + '/call', headers=headers, timeout=15, json={
            'agent_id': os.environ['BOLNA_AGENT_ID'],
            'recipient_phone_number': body.phone,
            # Variables the agent prompt can use, e.g. {scheme_name}; keep these minimal.
            'user_data': {'scheme_name': scheme['name'], 'declared_facts': json.dumps(declared)}})
        r.raise_for_status()
        execution_id = r.json().get('execution_id')
    except httpx.HTTPError:
        with case_store.transaction(body.case_id) as tx:
            tx.execute("UPDATE verification_calls SET status='failed',phone_cipher='',updated_at=? WHERE id=?",
                       (time.time(), vid))
        raise HTTPException(502, 'Could not place the call; try again later')
    with case_store.transaction(body.case_id) as tx:
        tx.execute("UPDATE verification_calls SET status='calling',execution_id=?,updated_at=? WHERE id=?",
                   (execution_id, time.time(), vid))
    return {'verification_id': vid, 'status': 'calling'}


@router.post('/webhook/{secret}', include_in_schema=False)
def webhook(secret: str, payload: dict):
    expected = os.getenv('BOLNA_WEBHOOK_SECRET', '')
    import hmac
    if len(expected) < 24 or not hmac.compare_digest(secret, expected):
        raise HTTPException(403, 'Forbidden')
    # Check the field name in your first test call: it is the execution id.
    execution_id = payload.get('id') or payload.get('execution_id')
    if not isinstance(execution_id, str):
        return {'status': 'ignored'}
    # Never trust the webhook body: ask Bolna for the authoritative record.
    try:
        r = httpx.get(f'{API}/executions/{execution_id}', headers=_headers(), timeout=15)
        r.raise_for_status()
        execution = r.json()
    except httpx.HTTPError:
        raise HTTPException(503, 'Retry later')      # Bolna sends further status events
    if execution.get('status') not in ('completed', 'call-disconnected'):
        return {'status': 'in_progress'}
    return finish(execution_id, execution)


def finish(execution_id, execution):
    with case_store.transaction('bolna:' + execution_id) as tx:
        call = tx.execute('SELECT * FROM verification_calls WHERE execution_id=?', (execution_id,)).fetchone()
        if not call or call['status'] not in ('calling', 'queued'):
            return {'status': 'duplicate'}               # idempotent
        tx.lock(call['session_id'])
        row = tx.session(call['session_id'], trusted=True)
        state = row['state']
        extracted = execution.get('extracted_data') or {}
        spoken, errors = validate_profile({k: v for k, v in extracted.items()
                                           if k in VERIFY_FIELDS and v is not None})
        declared = state.get('profile', {})
        mismatches = {k: {'declared': declared[k], 'on_call': v}
                      for k, v in spoken.items() if k in declared and declared[k] != v}
        scheme = load_catalog().get(call['scheme_id'])
        # Declared facts stay authoritative until the farmer confirms a correction.
        result = evaluate_scheme(scheme, {**declared, **{k: v for k, v in spoken.items() if k not in declared}})
        outcome = {'status': 'verified' if spoken and not mismatches else 'needs_review',
                   'call_facts': spoken, 'mismatches': mismatches, 'rejected_values': errors,
                   'eligibility': {'status': result.status, 'why': result.explain(),
                                   'missing': result.missing_fields},
                   'government_submission': False}
        state['verification'] = {**state.get('verification', {}), call['scheme_id']: outcome}
        tx.save(call['session_id'], state, kind=row['kind'])
        # Drop the phone number as soon as it has been used.
        tx.execute("UPDATE verification_calls SET status=?,phone_cipher='',result=?,updated_at=? WHERE id=?",
                   (outcome['status'], json.dumps(outcome, ensure_ascii=False), time.time(), call['id']))
    return {'status': outcome['status']}


@router.get('/{case_id}')
def verification_status(case_id: str, request: Request):
    with case_store.transaction(case_id) as tx:
        tx.session(case_id, owner(request))
        rows = tx.execute('SELECT id,scheme_id,status,result,updated_at FROM verification_calls WHERE session_id=? ORDER BY created_at DESC',
                          (case_id,)).fetchall()
    return [{**{k: r[k] for k in ('id', 'scheme_id', 'status', 'updated_at')},
             'result': json.loads(r['result']) if r['result'] else None} for r in rows]