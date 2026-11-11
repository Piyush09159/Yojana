"""Bolna voice-agent adapter. Bolna talks; core/ decides.

Two uses, two separate Bolna agents:
  * Verification calls (browser, POST /api/bolna/verify): ported from feature/call-agent. The agent
    re-asks a few declared facts; code validates them with core.fields and flags mismatches. The
    declared profile is never overwritten. Off unless BOLNA_VERIFICATION_ENABLED=true and
    BOLNA_AGENT_ID is set.
  * Follow-up notification calls after a confirmed WhatsApp shortlist: web/followup_calls.py, with
    its own agent (BOLNA_FOLLOWUP_AGENT_ID) and flag (BOLNA_FOLLOWUP_ENABLED).

Bolna API (docs.bolna.ai, checked 2026-11-12): POST /call {agent_id, recipient_phone_number (E.164),
from_phone_number?, user_data?, retry_config?} -> {message, status: "queued", execution_id};
GET /executions/{id} -> {id, status, ...}. Auth: Authorization: Bearer <BOLNA_API_KEY>.
Webhook bodies are never trusted: the execution is fetched again from Bolna before acting.

Never log or return the API key, phone numbers, transcripts or extracted data.
"""
from __future__ import annotations

import hmac
import json
import os
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from core.eligibility import evaluate_scheme
from core.fields import validate_profile
from discovery.catalog import load_catalog
from . import case_store
from .access import owner
from .channel_security import seal

router = APIRouter(prefix='/api/bolna', tags=['Bolna calls'])
API = 'https://api.bolna.ai'
E164 = r'^\+[1-9]\d{9,14}$'
# Fields the verification agent's extraction prompt must return, named exactly as in fields.yaml.
VERIFY_FIELDS = ('age', 'state', 'district', 'annual_family_income_inr', 'occupation',
                 'owns_cultivable_land', 'land_acres', 'is_bpl')

# Bolna execution statuses -> our terminal outcome. Anything else is still in progress.
COMPLETED = {'completed', 'call-disconnected'}
NO_ANSWER = {'no-answer', 'busy'}
FAILED = {'failed', 'error', 'canceled', 'stopped', 'balance-low'}


class BolnaError(RuntimeError):
    """`retryable` is True only when Bolna certainly did not accept the call (connection refused,
    429/5xx answer). A timeout is ambiguous: the phone might already be ringing, so never retry it."""
    def __init__(self, detail, *, status=None, retryable=False):
        super().__init__(detail)
        self.detail, self.status, self.retryable = detail, status, retryable


def flag(name):
    return os.getenv(name, 'false').strip().lower() == 'true'


def api_key():
    return os.getenv('BOLNA_API_KEY', '').strip()


def webhook_secret_ok(secret: str) -> bool:
    expected = os.getenv('BOLNA_WEBHOOK_SECRET', '')
    return len(expected) >= 24 and hmac.compare_digest(secret.encode(), expected.encode())


def place_call(agent_id: str, phone: str, user_data: dict, *, timeout: float = 15.0) -> str:
    """Start one outbound call; returns Bolna's execution_id. Bolna's own retries are disabled:
    no extra call ever happens without renewed opt-in."""
    key = api_key()
    if not key or not agent_id:
        raise BolnaError('not_configured')
    body = {'agent_id': agent_id, 'recipient_phone_number': phone, 'user_data': user_data,
            'retry_config': {'enabled': False}}
    if os.getenv('BOLNA_FROM_PHONE_NUMBER'):
        body['from_phone_number'] = os.environ['BOLNA_FROM_PHONE_NUMBER']
    try:
        r = httpx.post(API + '/call', headers={'Authorization': 'Bearer ' + key}, json=body, timeout=timeout)
    except httpx.ConnectError as exc:
        raise BolnaError('connect_error', retryable=True) from exc
    except httpx.HTTPError as exc:                       # timeouts etc.: Bolna may have accepted it
        raise BolnaError(type(exc).__name__) from exc
    if r.status_code == 429 or r.status_code >= 500:
        raise BolnaError('http_error', status=r.status_code, retryable=True)
    if r.status_code >= 400:
        raise BolnaError('rejected', status=r.status_code)
    try:
        execution_id = r.json().get('execution_id')
    except ValueError:
        execution_id = None
    if not isinstance(execution_id, str) or not execution_id:
        raise BolnaError('no_execution_id', status=r.status_code)
    return execution_id


def get_execution(execution_id: str) -> dict:
    key = api_key()
    if not key:
        raise BolnaError('not_configured')
    try:
        r = httpx.get(f'{API}/executions/{execution_id}', headers={'Authorization': 'Bearer ' + key}, timeout=15)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BolnaError('lookup_failed', retryable=True) from exc


def outcome(status) -> str | None:
    """'completed' / 'no_answer' / 'failed' for a terminal Bolna status, None while in progress."""
    if status in COMPLETED:
        return 'completed'
    if status in NO_ANSWER:
        return 'no_answer'
    if status in FAILED:
        return 'failed'
    return None


# ---------------------------------------------------------------- verification calls (browser)

def _verification_agent():
    if not flag('BOLNA_VERIFICATION_ENABLED') or not api_key() or not os.getenv('BOLNA_AGENT_ID'):
        raise HTTPException(503, 'Verification calls are not enabled')
    return os.environ['BOLNA_AGENT_ID']


class VerifyRequest(BaseModel):
    case_id: str = Field(max_length=64)
    scheme_id: str = Field(max_length=120)
    phone: str = Field(pattern=E164)
    consent: StrictBool


@router.post('/verify')
def start_verification(body: VerifyRequest, request: Request):
    if not body.consent:
        raise HTTPException(422, 'Explicit consent to a verification call is required')
    agent = _verification_agent()
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
        execution_id = place_call(agent, body.phone, {'scheme_name': scheme['name'],
                                                      'declared_facts': json.dumps(declared)})
    except BolnaError:
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
    if not webhook_secret_ok(secret):
        raise HTTPException(403, 'Forbidden')
    execution_id = payload.get('id') or payload.get('execution_id')
    if not isinstance(execution_id, str) or not execution_id:
        return {'status': 'ignored'}
    try:
        execution = get_execution(execution_id)      # never trust the webhook body
    except BolnaError:
        raise HTTPException(503, 'Retry later')      # Bolna sends further status events
    result = outcome(execution.get('status'))
    if result is None:
        return {'status': 'in_progress'}
    if result != 'completed':
        return fail(execution_id)
    return finish(execution_id, execution)


def fail(execution_id):
    with case_store.transaction('bolna:' + execution_id) as tx:
        call = tx.execute('SELECT * FROM verification_calls WHERE execution_id=?', (execution_id,)).fetchone()
        if not call or call['status'] not in ('calling', 'queued'):
            return {'status': 'duplicate'}
        tx.execute("UPDATE verification_calls SET status='failed',phone_cipher='',updated_at=? WHERE id=?",
                   (time.time(), call['id']))
    return {'status': 'failed'}


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
        # Declared facts stay authoritative until the citizen confirms a correction.
        result = evaluate_scheme(scheme, {**declared, **{k: v for k, v in spoken.items() if k not in declared}})
        outcome_ = {'status': 'verified' if spoken and not mismatches else 'needs_review',
                    'call_facts': spoken, 'mismatches': mismatches, 'rejected_values': errors,
                    'eligibility': {'status': result.status, 'why': result.explain(),
                                    'missing': result.missing_fields},
                    'government_submission': False}
        state['verification'] = {**state.get('verification', {}), call['scheme_id']: outcome_}
        tx.save(call['session_id'], state, kind=row['kind'])
        # Drop the phone number as soon as it has been used.
        tx.execute("UPDATE verification_calls SET status=?,phone_cipher='',result=?,updated_at=? WHERE id=?",
                   (outcome_['status'], json.dumps(outcome_, ensure_ascii=False), time.time(), call['id']))
    return {'status': outcome_['status']}


@router.get('/{case_id}')
def verification_status(case_id: str, request: Request):
    with case_store.transaction(case_id) as tx:
        tx.session(case_id, owner(request))
        rows = tx.execute('SELECT id,scheme_id,status,result,updated_at FROM verification_calls WHERE session_id=? ORDER BY created_at DESC',
                          (case_id,)).fetchall()
    return [{**{k: r[k] for k in ('id', 'scheme_id', 'status', 'updated_at')},
             'result': json.loads(r['result']) if r['result'] else None} for r in rows]
