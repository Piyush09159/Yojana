"""Authorized web voice, one-time channel handoff, retained summary and consent queue."""
import json
import os
import secrets
import time
import uuid
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictBool, field_validator
from core.languages import registry
from discovery.catalog import load_catalog
from saathi.shortlist import build_shortlist
from . import case_store, speech
from .access import owner, issue
from .privacy import redact, minimize
from .channel_security import seal, unseal

router = APIRouter(prefix='/api/voice', tags=['Browser voice'])


def require_store():
    if not case_store.enabled():
        raise HTTPException(503, 'Voice case storage is not configured; typed chat remains available')
    if not case_store.auth_required():
        raise HTTPException(503,'Voice requires session access authorization to be enabled')


def new_channel_case(tx):
    sid = uuid.uuid4().hex
    tx.save(sid, {'profile': {}, 'asked': [], 'pending': None, 'lang': 'en', 'updated': time.time()})
    return sid


def handoff(tx, sid, chat_hash):
    origin = os.getenv('PUBLIC_FRONTEND_ORIGIN', '')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise HTTPException(503, 'An exact HTTPS PUBLIC_FRONTEND_ORIGIN is required')
    row = tx.session(sid, trusted=True)
    tx.execute('DELETE FROM voice_links WHERE session_id=?', (sid,))
    raw = secrets.token_urlsafe(32)
    tx.execute('INSERT INTO voice_links VALUES(?,?,?,?,NULL)',
               (case_store.digest(raw), sid, chat_hash, min(time.time()+600, row['expires_at'])))
    # Fragment is not sent in HTTP requests or Referer. Redemption requires an explicit POST.
    return origin.rstrip('/') + '/voice#handoff=' + raw


class Redemption(BaseModel):
    token: str = Field(min_length=32, max_length=100)


@router.post('/redeem')
def redeem(body: Redemption, request: Request, response: Response):
    require_store()
    token_hash = case_store.digest(body.token)
    with case_store.transaction('voice-redemption') as tx:
        link = tx.execute('SELECT * FROM voice_links WHERE token_hash=?', (token_hash,)).fetchone()
        if not link or link['redeemed_at'] or link['expires_at'] <= time.time():
            raise HTTPException(410, 'Link expired or already used. Request a new Call link in WhatsApp.')
        tx.lock(link['session_id'])
        mapping = tx.execute('SELECT * FROM waha_chats WHERE chat_hash=?', (link['chat_hash'],)).fetchone()
        if not mapping or not mapping['opted_in'] or mapping['session_id'] != link['session_id']:
            raise HTTPException(403, 'Channel consent is no longer active')
        row = tx.session(link['session_id'], trusted=True)
        token = issue(request, response)
        tx.execute('INSERT INTO case_grants VALUES(?,?,?) ON CONFLICT(session_id,owner_hash) DO UPDATE SET expires_at=excluded.expires_at',
                   (link['session_id'], case_store.digest(token), row['expires_at']))
        tx.execute('UPDATE voice_links SET redeemed_at=? WHERE token_hash=?', (time.time(), token_hash))
        return {'session_id': link['session_id']}


@router.get('/capabilities')
def capabilities():
    return {'languages': registry(), 'tts_configured': bool(os.getenv('SARVAM_API_KEY')),
            'stt_configured': bool(os.getenv('SARVAM_API_KEY')) and os.getenv('SARVAM_STT_ENABLED') == 'true',
            'speech_live_verified': False, 'kind': 'browser_voice_not_phone_call'}


class Start(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)


@router.post('/start')
def start(body: Start, request: Request, response: Response):
    require_store()
    token = owner(request)
    with case_store.transaction(body.session_id or 'new-voice-case') as tx:
        if body.session_id:
            row = tx.session(body.session_id, token)
            sid = row['id']
        else:
            token = issue(request, response)
            sid = uuid.uuid4().hex
            tx.save(sid, {'profile': {}, 'asked': [], 'pending': None, 'lang': 'en', 'updated': time.time()}, token)
        if tx.session(sid, token)['status'] == 'closed':
            raise HTTPException(409, 'Reopen the case before starting voice')
        active = tx.execute("SELECT id FROM voice_calls WHERE session_id=? AND status='active'", (sid,)).fetchone()
        if active:
            return {'call_id': active['id'], 'session_id': sid, 'resumed': True}
        state=tx.session(sid,token)['state']
        if state.get('assessment',{}).get('assessment_complete'):
            state.update(asked=[],pending=None,question_count=0,pending_repeats=0)
            tx.save(sid,state,token)
        if tx.execute('SELECT COUNT(*) AS n FROM voice_calls WHERE session_id=?', (sid,)).fetchone()['n'] >= 10:
            raise HTTPException(409, 'Case voice-history limit reached')
        cid = uuid.uuid4().hex
        tx.execute('INSERT INTO voice_calls VALUES(?,?,?,?,?,?,?,?)', (cid,sid,'active','[]',None,0,time.time(),None))
        return {'call_id': cid, 'session_id': sid, 'resumed': False}


def call(tx, cid, token):
    row = tx.execute('SELECT * FROM voice_calls WHERE id=?', (cid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Voice session not found')
    tx.session(row['session_id'], token)
    return row


class Turn(BaseModel):
    text: str = Field(min_length=1, max_length=1800)
    lang: str = 'auto'
    request_id: str = Field(min_length=8, max_length=80)
    language_override: bool = False

    @field_validator('lang')
    @classmethod
    def language(cls,value):
        from .conversation_api import Message
        return Message.language(value)


@router.post('/{cid}/turn')
def turn(cid: str, body: Turn, request: Request):
    require_store()
    from .conversation_api import conversation, Message
    from core.assessment import POLICY_VERSION
    with case_store.transaction('voice:'+cid) as tx:
        row = call(tx, cid, owner(request))
        transcript = json.loads(row['transcript'])
        if (transcript and transcript[-1].get('request_id') == body.request_id
                and transcript[-1].get('result',{}).get('assessment',{}).get('policy_version')==POLICY_VERSION
                and (row['status']=='active' or transcript[-1].get('result',{}).get('assessment_complete'))):
            if transcript[-2]['text'] != redact(body.text):
                raise HTTPException(409, 'Request ID conflict')
            return transcript[-1]['result']
        if row['status'] != 'active':
            raise HTTPException(409, 'Voice session has ended')
        if len(transcript) >= 80:
            raise HTTPException(409, 'Voice turn limit reached; end the session for your summary')
        result = conversation(Message(text=body.text, lang=body.lang, request_id=body.request_id,
                                      language_override=body.language_override, session_id=row['session_id']),
                              access_token=owner(request), tx=tx)
        for message in transcript:
            message.pop('result',None)  # Only the latest turn needs an idempotent response cache.
        transcript += [{'role':'user','text':redact(body.text)},
                       {'role':'assistant','text':redact(result['message']), 'request_id':body.request_id,'result':minimize(result)}]
        tx.execute('UPDATE voice_calls SET transcript=? WHERE id=?', (json.dumps(transcript,ensure_ascii=False),cid))
        if result.get('assessment_complete'):
            snapshot={'schemes':result['recommendations'],
                      'caveat':'Guidance only; check missing conditions and official sources. No government approval is implied.',
                      'government_submission':False,'assessment':result['assessment']}
            tx.execute("UPDATE voice_calls SET status='ended',summary=?,ended_at=? WHERE id=? AND status='active'",
                       (json.dumps(snapshot,ensure_ascii=False),time.time(),cid))
            result['voice_finalized']=True
            result['summary']=snapshot
            transcript[-1]['result']=minimize(result)
            tx.execute('UPDATE voice_calls SET transcript=? WHERE id=?',(json.dumps(transcript,ensure_ascii=False),cid))
        return result


def safe_snapshot(snapshot):
    """Do not replay unsafe eligibility claims from pre-hotfix saved summaries."""
    from copy import deepcopy
    from core.assessment import POLICY_VERSION
    if not snapshot or snapshot.get('assessment',{}).get('policy_version')==POLICY_VERSION:
        return snapshot
    result=deepcopy(snapshot)
    warning='Historical snapshot predates the eligibility safety update. Reassess this beneficiary; no eligibility or approval is confirmed.'
    result['caveat']=warning
    for card in result.get('schemes',[]):
        if card.get('status') in ('eligible','eligible_check_manually'):
            card.update(status='check_manually',status_label='Needs verification',why=warning)
        card['check_yourself']=list(card.get('check_yourself',[]))+[warning]
    return result


def summary_for(state):
    from core.assessment import POLICY_VERSION, relevant_catalog, decide
    if not state.get('profile'):
        return {'schemes': [], 'caveat':'More information is needed; no scheme eligibility has been confirmed.',
                'government_submission':False}
    if state.get('assessment',{}).get('policy_version')==POLICY_VERSION:
        return {'schemes':state['assessment']['recommendations'],
                'caveat':'Guidance only; check missing conditions and official sources.',
                'assessment':state['assessment'],'government_submission':False}
    profile = {**state.get('profile', {}), '_intent': state.get('intent_context', {})}
    catalog=relevant_catalog(profile,load_catalog())
    assessment=decide(profile,catalog,build_shortlist(profile,catalog),state.get('question_count',0))
    assessment.pop('next_candidate',None)
    return {'schemes': assessment['recommendations'], 'assessment':assessment,'caveat': 'Guidance only; confirm official eligibility and missing requirements before applying.',
            'government_submission': False}


class End(BaseModel):
    consent_whatsapp: StrictBool = False


@router.post('/{cid}/end')
def end(cid: str, body: End, request: Request):
    require_store()
    with case_store.transaction('voice:'+cid) as tx:
        row = call(tx, cid, owner(request))
        tx.lock(row['session_id'])
        if row['summary']:
            snapshot = safe_snapshot(json.loads(row['summary']))
        else:
            state = tx.session(row['session_id'], owner(request))['state']
            snapshot = summary_for(state)
            tx.execute('UPDATE voice_calls SET status=?,summary=?,ended_at=? WHERE id=?',
                       ('ended',json.dumps(snapshot,ensure_ascii=False),time.time(),cid))
        if body.consent_whatsapp:
            mapping = tx.execute('SELECT * FROM waha_chats WHERE session_id=? AND opted_in=1', (row['session_id'],)).fetchone()
            if not mapping or not tx.execute('SELECT chat_hash FROM channel_routes WHERE chat_hash=?', (mapping['chat_hash'],)).fetchone():
                raise HTTPException(409, 'No opted-in WhatsApp route exists for this case')
            tx.execute('UPDATE voice_calls SET consent=1 WHERE id=?', (cid,))
            tx.execute("""INSERT INTO summary_outbox VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(call_id)
                DO UPDATE SET status='pending',attempts=0,next_attempt=excluded.next_attempt
                WHERE summary_outbox.status='cancelled'""",
                       (cid,mapping['chat_hash'],'pending',0,time.time(),time.time()))
        queued = tx.execute('SELECT status,attempts FROM summary_outbox WHERE call_id=?', (cid,)).fetchone()
        return {'summary':snapshot,'whatsapp':dict(queued) if queued else {'status':'not_requested'},
                'recipient_delivery_verified':False}


@router.get('/{cid}')
def history(cid: str, request: Request):
    require_store()
    with case_store.transaction('voice:'+cid) as tx:
        row = call(tx,cid,owner(request))
        queued = tx.execute('SELECT status,attempts FROM summary_outbox WHERE call_id=?',(cid,)).fetchone()
        return {'call_id':cid,'status':row['status'],'transcript':[{'role':m['role'],'text':m['text']} for m in json.loads(row['transcript'])],
                'summary':safe_snapshot(json.loads(row['summary'])) if row['summary'] else None,
                'whatsapp':dict(queued) if queued else {'status':'not_requested'},'recipient_delivery_verified':False}


@router.post('/{cid}/summary/retry')
def retry_summary(cid: str, request: Request):
    require_store()
    with case_store.transaction('voice:'+cid) as tx:
        row = call(tx,cid,owner(request))
        mapping = tx.execute('SELECT chat_hash FROM waha_chats WHERE session_id=? AND opted_in=1',(row['session_id'],)).fetchone()
        if not row['consent'] or not mapping:
            raise HTTPException(409,'Active WhatsApp consent is required')
        tx.execute("UPDATE summary_outbox SET status='retrying',attempts=0,next_attempt=? WHERE call_id=? AND status='failed'",(time.time(),cid))
    return {'status':'retry_requested','recipient_delivery_verified':False}


@router.post('/{cid}/summary/cancel')
def cancel_summary(cid: str, request: Request):
    require_store()
    with case_store.transaction('voice:'+cid) as tx:
        row=call(tx,cid,owner(request))
        queued=tx.execute('SELECT * FROM summary_outbox WHERE call_id=?',(cid,)).fetchone()
        if queued:
            tx.lock('waha:'+queued['chat_hash'])
            tx.session(row['session_id'],owner(request))
            queued=tx.execute('SELECT status FROM summary_outbox WHERE call_id=?',(cid,)).fetchone()
            if queued and queued['status']=='accepted_by_waha':
                raise HTTPException(409,'WAHA already accepted the summary; it cannot be recalled here')
            tx.execute("UPDATE summary_outbox SET status='cancelled' WHERE call_id=?",(cid,))
        tx.execute('UPDATE voice_calls SET consent=0 WHERE id=?',(cid,))
    return {'status':'cancelled','recipient_delivery_verified':False}


class Audio(BaseModel):
    call_id: str = Field(max_length=64)
    audio: str = Field(max_length=700000)
    lang: str = 'auto'


class Speak(BaseModel):
    call_id: str = Field(max_length=64)
    text: str = Field(min_length=1,max_length=1200)
    lang: str = 'en'
    request_id: str | None = Field(default=None,max_length=80)


@router.post('/stt')
def transcribe(body: Audio, request: Request):
    require_store()
    with case_store.transaction('voice:'+body.call_id) as tx:
        row = call(tx,body.call_id,owner(request))
        if row['status'] != 'active':
            raise HTTPException(409,'Voice session has ended')
    return speech.stt(body.audio,body.lang)


@router.post('/tts')
def speak(body: Speak, request: Request):
    require_store()
    with case_store.transaction('voice:'+body.call_id) as tx:
        row = call(tx,body.call_id,owner(request))
        transcript = json.loads(row['transcript'])
        latest=transcript[-1] if transcript else {}
        result=latest.get('result',{})
        allowed={result.get('spoken_text',''),latest.get('text','')[:360]}
        if (not transcript or latest['role'] != 'assistant' or body.text not in allowed or len(body.text)>360
                or (row['status']!='active' and not result.get('assessment_complete'))):
            raise HTTPException(403,'Only the current authorized assistant reply can be synthesized')
        if body.request_id and body.request_id!=latest.get('request_id'):
            raise HTTPException(409,'Stale speech request')
    return Response(speech.tts(body.text,body.lang),media_type='audio/wav',headers={'Cache-Control':'no-store'})


def deliver_summaries():
    """Bounded worker pass. Accepted by WAHA is not a recipient delivery receipt."""
    if not case_store.enabled() or os.getenv('YOJANA_WAHA_ENABLED') != 'true':
        return
    with case_store.transaction() as tx:
        due = [dict(r) for r in tx.execute("SELECT call_id,chat_hash FROM summary_outbox WHERE status IN ('pending','retrying') AND next_attempt<=? LIMIT 10",(time.time(),))]
    from .waha_bridge import _config, _send
    for candidate in due:
        with case_store.transaction('waha:'+candidate['chat_hash']) as tx:
            queued = tx.execute('SELECT * FROM summary_outbox WHERE call_id=?',(candidate['call_id'],)).fetchone()
            if not queued or queued['status'] not in ('pending','retrying') or queued['next_attempt']>time.time():
                continue
            mapping = tx.execute('SELECT * FROM waha_chats WHERE chat_hash=? AND opted_in=1',(candidate['chat_hash'],)).fetchone()
            route = tx.execute('SELECT address_cipher FROM channel_routes WHERE chat_hash=?',(candidate['chat_hash'],)).fetchone()
            row = tx.execute('SELECT * FROM voice_calls WHERE id=?',(candidate['call_id'],)).fetchone()
            if not mapping or not route or not row or mapping['session_id']!=row['session_id'] or not row['consent']:
                tx.execute("UPDATE summary_outbox SET status='cancelled' WHERE call_id=?",(candidate['call_id'],))
                continue
            try:
                tx.session(row['session_id'],trusted=True)
            except HTTPException:
                tx.execute("UPDATE summary_outbox SET status='cancelled' WHERE call_id=?",(row['id'],))
                continue
            snapshot = safe_snapshot(json.loads(row['summary']))
            lines = ['Your Yojana-Pilot shortlist (guidance, not approval):']
            for index, item in enumerate(snapshot['schemes'],1):
                lines += [f"{index}. {item['name']} — {item.get('status_label',item['status'])}", 'Missing: '+', '.join(item.get('missing_requirements',[]))]
                if item.get('benefit'):
                    lines.append(item['benefit'][:300])
                url = item.get('source_url','')
                if urlsplit(url).scheme == 'https':
                    lines.append(url)
                lines.extend(item.get('next_steps',[])[:1])
            lines.append(snapshot['caveat'])
            try:
                url,key,_ = _config()
                _send(url,key,os.getenv('WAHA_SESSION','default'),unseal(route['address_cipher']),'\n'.join(lines)[:3500])
            except Exception:
                attempts = queued['attempts']+1
                tx.execute('UPDATE summary_outbox SET status=?,attempts=?,next_attempt=? WHERE call_id=?',
                           ('failed' if attempts>=5 else 'retrying',attempts,time.time()+min(3600,2**attempts*10),row['id']))
            else:
                tx.execute("UPDATE summary_outbox SET status='accepted_by_waha',attempts=attempts+1,confirmed_at=? WHERE call_id=?",(time.time(),row['id']))
