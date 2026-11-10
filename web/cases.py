"""Authorized case review. Approval records acknowledge guidance, never submit applications."""
import json
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Literal
from . import case_store
from .access import owner

router = APIRouter(prefix='/api/cases', tags=['Cases'])


def persistent():
    if not case_store.enabled():
        raise HTTPException(503, 'Persistent case storage is not configured')


class Review(BaseModel):
    status: Literal['open', 'reviewed', 'closed']


class Approval(BaseModel):
    decision: Literal['approved', 'declined']
    purpose: Literal['review_guidance'] = 'review_guidance'


@router.get('/{sid}')
def get_case(sid: str, request: Request, optional: bool = False):
    try:
        return _get_case(sid, request)
    except HTTPException as exc:
        if optional and exc.status_code in (403, 404, 503):
            return {'available': False}
        raise


def _get_case(sid, request):
    persistent()
    with case_store.transaction(sid) as tx:
        row = tx.session(sid, owner(request))
        from core.assessment import POLICY_VERSION, relevant_catalog, decide
        from discovery.catalog import load_catalog
        from saathi.shortlist import build_shortlist
        assessment=row['state'].get('assessment') or {}
        refreshed=None
        if row['state'].get('profile') and assessment.get('policy_version')!=POLICY_VERSION:
            # Old deployed snapshots must not restore unsafe eligibility labels.
            # Re-evaluate deterministically, without calling an LLM or rewriting history.
            profile={**row['state']['profile'],'_intent':row['state'].get('intent_context',{}),'_asked':row['state'].get('asked',[])}
            catalog=relevant_catalog(profile,load_catalog())
            assessment=decide(profile,catalog,build_shortlist(profile,catalog),row['state'].get('question_count',0))
            assessment.pop('next_candidate',None)
            refreshed={'schemes':assessment['recommendations']}
        completed=tx.execute("SELECT id,summary,ended_at FROM voice_calls WHERE session_id=? AND status='ended' AND summary IS NOT NULL ORDER BY ended_at DESC LIMIT 1",(sid,)).fetchone()
        from .voice import safe_snapshot
        summary=safe_snapshot(json.loads(completed['summary'])) if completed else None
        # A newer chat refinement supersedes an older saved voice assessment.
        use_voice=bool(completed and summary.get('assessment',{}).get('policy_version')==POLICY_VERSION and refreshed is None and (not row['state'].get('last_response',{}).get('results')
                                      or completed['ended_at']>=row['state'].get('updated',0)))
        recommendations=({'schemes':summary.get('schemes',[])} if use_voice else
                         {'schemes':row['state'].get('last_response',{}).get('recommendations',[])} if row['state'].get('last_response',{}).get('recommendations') is not None else row['state'].get('last_response',{}).get('results'))
        if refreshed is not None:
            recommendations=refreshed
        return {'available': True, 'id': sid, 'profile': row['state'].get('profile', {}),
                'recommendations': recommendations,'latest_voice_summary':summary,
                'assessment':assessment,
                'assessment_date':completed['ended_at'] if use_voice else row['state'].get('updated',row['updated_at']),
                'voice_sessions': [dict(r) for r in tx.execute('SELECT id,status,created_at,ended_at FROM voice_calls WHERE session_id=? ORDER BY created_at DESC LIMIT 10',(sid,))],
                'pending_question': row['state'].get('pending'), 'status': row['status'],
                'rescue': row['state'].get('rescue'),
                'created_at': row['created_at'], 'updated_at': row['updated_at'], 'expires_at': row['expires_at'],
                'messages': tx.history(sid), 'approvals': [dict(r) for r in tx.execute(
                    'SELECT id,decision,purpose,facts_hash,created_at FROM case_approvals WHERE session_id=? ORDER BY created_at', (sid,))]}


@router.patch('/{sid}')
def review_case(sid: str, body: Review, request: Request):
    persistent()
    with case_store.transaction(sid) as tx:
        tx.session(sid, owner(request))
        tx.execute('UPDATE case_sessions SET status=?,updated_at=? WHERE id=?', (body.status, time.time(), sid))
    return {'status': body.status}


@router.post('/{sid}/approvals')
def approve(sid: str, body: Approval, request: Request):
    persistent()
    with case_store.transaction(sid) as tx:
        row = tx.session(sid, owner(request))
        facts_hash = case_store.digest(json.dumps({'profile': row['state'].get('profile', {}),
                                                  'rescue': row['state'].get('rescue')}, sort_keys=True))
        # A bounded review log, not unlimited citizen activity tracking.
        if tx.execute('SELECT COUNT(*) AS n FROM case_approvals WHERE session_id=?', (sid,)).fetchone()['n'] >= 20:
            raise HTTPException(409, 'Review history limit reached')
        identifier = uuid.uuid4().hex
        tx.execute('INSERT INTO case_approvals VALUES(?,?,?,?,?,?)',
                   (identifier, sid, body.decision, body.purpose, facts_hash, time.time()))
    return {'id': identifier, 'decision': body.decision, 'facts_hash': facts_hash, 'government_submission': False}
