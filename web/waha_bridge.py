"""Opt-in WAHA bridge for Yojana-Pilot conversation endpoint.

The signed production path stores mapping/deduplication/outbox state in PostgreSQL.
The legacy URL-secret route is development-only. Only explicitly opted-in 1:1 text and bounded voice notes are handled;
external delivery remains at-least-once and requires live account validation.
"""
from __future__ import annotations
import hmac
import hashlib
import json
import os
import re
import time
import unicodedata
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .conversation_api import Message, conversation
from . import case_store, notifications
from .privacy import redact
from .channel_security import seal, unseal

router = APIRouter(tags=["WhatsApp (opt-in beta)"])


def command_text(text):
    value = unicodedata.normalize('NFKC', text).strip().casefold()
    while value and (value[0].isspace() or unicodedata.category(value[0]).startswith(('P', 'S'))):
        value = value[1:]
    while value and (value[-1].isspace() or unicodedata.category(value[-1]).startswith(('P', 'S'))):
        value = value[:-1]
    return ' '.join(value.split())

ACTIVATE = {'yojana', 'yojna', 'योजना'}
STOP = {'stop', 'unsubscribe', 'बंद', 'थांबा'}
CHAT = {'chat', 'चैट', 'चॅट', 'गप्पा'}
CALL = {'call', 'कॉल', 'बोलणे'}
LANGUAGE = {'english':'en', 'hindi':'hi', 'हिंदी':'hi', 'हिन्दी':'hi', 'marathi':'mr', 'मराठी':'mr'}

def welcome(lang):
    lead = {'en':'Welcome to Yojana Saathi!\n\nHow would you like to continue?',
            'hi':'योजना साथी में आपका स्वागत है!\n\nआप कैसे आगे बढ़ना चाहते हैं?',
            'mr':'योजना साथीमध्ये आपले स्वागत आहे!\n\nपुढे कसे जायचे?'}[lang]
    options = {'en':'• *Chat* — Send text or WhatsApp voice notes (up to 15 seconds).\n• *Call* — Open a secure browser voice conversation.\n\nReply *Chat* or *Call*.',
               'hi':'• *चैट* — संदेश या WhatsApp वॉइस नोट भेजें (15 सेकंड तक)।\n• *कॉल* — सुरक्षित ब्राउज़र में बात करें।\n\n*Chat* / *चैट* या *Call* / *कॉल* लिखें।',
               'mr':'• *चॅट* — संदेश किंवा WhatsApp व्हॉइस नोट पाठवा (15 सेकंदांपर्यंत).\n• *कॉल* — सुरक्षित ब्राउझरमध्ये बोला.\n\n*Chat* / *चॅट* किंवा *Call* / *कॉल* लिहा.'}[lang]
    return lead+'\n\n'+options+notifications.option_notice(lang)+'\nSTOP: unsubscribe. Never send Aadhaar, bank or identity numbers. Independent guidance; not a government service.'

def voice_failure(lang):
    return {'en':"I couldn't transcribe that voice note. Please retry with up to 15 seconds or type instead. Do not share identity or bank numbers.",
            'hi':'वॉइस नोट समझ नहीं आया। कृपया 15 सेकंड तक का नोट फिर भेजें या लिखें। पहचान या बैंक नंबर न भेजें।',
            'mr':'व्हॉइस नोट समजला नाही. कृपया 15 सेकंदांपर्यंतचा नोट पुन्हा पाठवा किंवा लिहा. ओळख किंवा बँक क्रमांक पाठवू नका.'}.get(lang, voice_failure('en') if lang != 'en' else '')


def _config():
    url = os.getenv("WAHA_BASE_URL", "").rstrip("/")
    key = os.getenv("WAHA_API_KEY", "")
    secret = os.getenv("YOJANA_WAHA_WEBHOOK_SECRET", "") or os.getenv('WAHA_HMAC_KEY', '')
    if not url or not key or len(secret) < 24:
        raise HTTPException(503, "WhatsApp bridge not configured")
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise HTTPException(503, "WAHA_BASE_URL must be an HTTP(S) URL")
    return url, key, secret


class WAHAEvent(BaseModel):
    event: str = ""
    session: str | None = None
    payload: dict = {}
    timestamp: int | None = None


def _send(url: str, key: str, session: str, chat_id: str, message: str):
    resp = httpx.post(url + "/api/sendText",
                      headers={"X-Api-Key": key},
                      json={"session": session, "chatId": chat_id, "text": message[:3500]},
                      timeout=12)
    resp.raise_for_status()



@router.post("/api/integrations/waha/{webhook_secret}", include_in_schema=False)
def receive_waha(webhook_secret: str, body: WAHAEvent):
    if os.getenv('DEPLOYMENT_MODE') == 'production':
        raise HTTPException(404, 'Use the signed webhook endpoint')
    if os.getenv("YOJANA_WAHA_ENABLED", "false").lower() != "true":
        raise HTTPException(503, "WhatsApp bridge is disabled")
    url, key, secret = _config()
    if not hmac.compare_digest(webhook_secret, secret):
        raise HTTPException(403, "Forbidden")
    if not case_store.enabled():
        raise HTTPException(503, 'Persistent case storage required for WhatsApp')
    return persistent_event(body)


@router.get("/api/integrations/waha/status")
def waha_status():
    return {"configured": bool(os.getenv("WAHA_BASE_URL") and os.getenv("WAHA_API_KEY")
                              and len(os.getenv("YOJANA_WAHA_WEBHOOK_SECRET", "") or os.getenv('WAHA_HMAC_KEY', '')) >= 24),
            "enabled": os.getenv("YOJANA_WAHA_ENABLED", "false").lower() == "true",
            "mode": "YOJANA opt-in text and bounded voice notes", "production_ready": False,
            'persistent': case_store.enabled(), 'connectivity': 'unverified'}


@router.post('/api/webhooks/waha', include_in_schema=False)
async def signed_webhook(request: Request):
    if os.getenv('YOJANA_WAHA_ENABLED', 'false').lower() != 'true':
        raise HTTPException(503, 'WhatsApp bridge is disabled')
    _config()
    secret = os.getenv('WAHA_HMAC_KEY', '')
    if len(secret) < 32 or not case_store.enabled():
        raise HTTPException(503, 'Signed persistent webhook is not configured')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 131072:
            raise HTTPException(413, 'Webhook too large')
    signature = request.headers.get('x-webhook-hmac', '')
    if request.headers.get('x-webhook-hmac-algorithm', '').lower() != 'sha512' or not hmac.compare_digest(
            signature, hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()):
        raise HTTPException(403, 'Invalid webhook signature')
    try:
        body = WAHAEvent.model_validate_json(raw)
    except ValueError:
        raise HTTPException(422, 'Invalid webhook payload')
    if os.getenv('DEPLOYMENT_MODE') == 'production' and (
            body.timestamp is None or abs(time.time() - body.timestamp / 1000) > 86400):
        raise HTTPException(403, 'Webhook timestamp is missing or stale')
    # Provider calls and DBAPI transactions must not block the ASGI event loop.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(persistent_event, body)


def persistent_event(body):
    expected_session = os.getenv('WAHA_SESSION', 'default')
    p = body.payload
    chat = p.get('from', '')
    text, mid = p.get('body', ''), p.get('id')
    if isinstance(mid, dict):
        mid = mid.get('_serialized') or mid.get('id')
    if (body.event != 'message' or body.session != expected_session or not isinstance(chat, str)
            # NOWEB emits personal senders as either phone JIDs or opaque LIDs.
            # Keep the exact address (including suffix) for hashing and replies;
            # a LID must never be guessed/rewritten into a phone-number JID.
            or not re.fullmatch(r'(?:[0-9]{6,16}@c\.us|[0-9]{1,20}@lid)', chat)
            or p.get('fromMe') or p.get('participant') or p.get('isGroup') or p.get('broadcast')
            or not isinstance(text, str) or (not text.strip() and not p.get('hasMedia')) or not isinstance(mid, str) or not mid):
        return {'status': 'ignored'}
    if len(text) > 1800 or len(mid) > 500:
        raise HTTPException(422, 'Message too large')
    chat_hash = case_store.chat_digest(expected_session + ':' + chat)
    event_id = case_store.chat_digest(expected_session + ':' + mid)
    lock = 'waha:' + chat_hash
    # Media I/O runs outside the case transaction, so STOP can revoke in flight.
    media_error = False
    media_version = None
    media_language = None
    if p.get('hasMedia'):
        with case_store.transaction(lock) as pre:
            prior = pre.execute('SELECT * FROM waha_events WHERE id=?', (event_id,)).fetchone()
            mapping = pre.execute('SELECT * FROM waha_chats WHERE chat_hash=?', (chat_hash,)).fetchone()
            if not prior:
                if not mapping or not mapping['opted_in']:
                    return {'status':'not_opted_in'}
                media_version = mapping['updated_at']
        if not prior:
            try:
                from .waha_media import transcribe
                transcript = transcribe(p.get('media'))
                text, media_language = transcript['text'], transcript['language']
            except (HTTPException, httpx.HTTPError, OSError, ValueError):
                media_error = True
    with case_store.transaction(lock) as tx:
        event = tx.execute('SELECT * FROM waha_events WHERE id=?', (event_id,)).fetchone()
        if event and event['chat_hash'] != chat_hash:
            raise HTTPException(409, 'Event identity conflict')
        if not event:
            mapping = tx.execute('SELECT * FROM waha_chats WHERE chat_hash=?', (chat_hash,)).fetchone()
            if p.get('hasMedia') and (not mapping or not mapping['opted_in'] or mapping['updated_at'] != media_version):
                return {'status':'cancelled'}
            language_row = tx.execute('SELECT language FROM channel_languages WHERE chat_hash=?',(chat_hash,)).fetchone()
            lang = language_row['language'] if language_row else ('hi' if command_text(text)=='योजना' else 'en')
            if lang not in ('en','hi','mr'):
                lang = 'en'  # Registered languages need an explicit, safe demo fallback.
            command = command_text(text) if not p.get('hasMedia') else ''
            reply = None
            if command in STOP:
                # No unsolicited STOP acknowledgement to a chat that never opted in.
                status = 'stopped' if mapping and mapping['opted_in'] else 'not_opted_in'
                if mapping and mapping['opted_in']:
                    if mapping['session_id']:
                        tx.lock(mapping['session_id'])
                        tx.execute('DELETE FROM case_sessions WHERE id=?', (mapping['session_id'],))
                    notifications.remove(tx, chat_hash)
                    tx.execute('DELETE FROM waha_chats WHERE chat_hash=?', (chat_hash,))
                    tx.execute("UPDATE waha_events SET status='sent',reply=NULL,result='cancelled' WHERE chat_hash=?", (chat_hash,))
                    reply = 'Yojana-Pilot stopped. The local case was cleared. Send YOJANA to opt in again. WAHA/provider logs have separate retention policies.'
            elif command in ACTIVATE:
                if not mapping or not mapping['opted_in']:
                    tx.execute('''INSERT INTO waha_chats VALUES(?,?,?,?) ON CONFLICT(chat_hash)
                        DO UPDATE SET opted_in=1,updated_at=excluded.updated_at''', (chat_hash, None, 1, time.time()))
                status = 'started'
                tx.execute("INSERT INTO channel_preferences VALUES(?,'awaiting') ON CONFLICT(chat_hash) DO UPDATE SET mode='awaiting'",(chat_hash,))
                reply = welcome(lang)
            elif not mapping or not mapping['opted_in']:
                status = 'not_opted_in'
            elif media_error:
                status, reply = 'transcription_failed', voice_failure(lang)
            elif command in notifications.UPDATES_ON | notifications.UPDATES_OFF | notifications.UPDATES_STATUS:
                status, reply = notifications.handle_command(tx, chat_hash, chat, lang, command)
                if status == 'alerts_on' and mapping and mapping['session_id']:
                    try:
                        existing = tx.session(mapping['session_id'], trusted=True)
                        notifications.update_profile(tx, chat_hash, chat, existing['state'].get('profile', {}), lang)
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
            elif command in LANGUAGE:
                lang = LANGUAGE[command]
                tx.execute('INSERT INTO channel_languages VALUES(?,?) ON CONFLICT(chat_hash) DO UPDATE SET language=excluded.language',(chat_hash,lang))
                if mapping['session_id']:
                    row = tx.session(mapping['session_id'], trusted=True)
                    row['state']['lang'] = lang
                    tx.save(mapping['session_id'], row['state'], kind=row['kind'])
                status, reply = 'language_selected', welcome(lang)
            elif command in CHAT:
                tx.execute("INSERT INTO channel_preferences VALUES(?,'chat') ON CONFLICT(chat_hash) DO UPDATE SET mode='chat'",(chat_hash,))
                status, reply = 'chat_selected', {'en':'Chat selected. Describe your situation or send a voice note. Reply Call for browser voice; STOP to leave.', 'hi':'चैट चुना। अपनी स्थिति लिखें या वॉइस नोट भेजें। ब्राउज़र के लिए Call; बंद करने के लिए STOP।', 'mr':'चॅट निवडले. तुमची परिस्थिती लिहा किंवा व्हॉइस नोट पाठवा. ब्राउझरसाठी Call; बंद करण्यासाठी STOP.'}[lang]
            elif command in CALL:
                tx.execute("INSERT INTO channel_preferences VALUES(?,'voice') ON CONFLICT(chat_hash) DO UPDATE SET mode='voice'",(chat_hash,))
                from .voice import new_channel_case, handoff
                sid = mapping['session_id']
                if sid:
                    tx.lock(sid)
                    try:
                        tx.session(sid,trusted=True)
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
                        sid = None
                sid = sid or new_channel_case(tx)
                row = tx.session(sid, trusted=True)
                row['state']['lang'] = lang
                tx.save(sid, row['state'], kind=row['kind'])
                tx.execute('UPDATE waha_chats SET session_id=?,updated_at=? WHERE chat_hash=?',(sid,time.time(),chat_hash))
                tx.execute('INSERT INTO channel_routes VALUES(?,?) ON CONFLICT(chat_hash) DO UPDATE SET address_cipher=excluded.address_cipher',
                           (chat_hash,seal(chat)))
                link = handoff(tx,sid,chat_hash)
                status,reply = 'voice_link', {'en':'Open your private one-use browser voice link within 10 minutes. Do not forward it:', 'hi':'10 मिनट के अंदर अपना निजी एक बार उपयोग होने वाला ब्राउज़र लिंक खोलें। इसे साझा न करें:', 'mr':'10 मिनिटांत तुमची खाजगी एकदाच वापरता येणारी ब्राउझर लिंक उघडा. ती शेअर करू नका:'}[lang]+'\n'+link
            else:
                tx.execute("INSERT INTO channel_preferences VALUES(?,'chat') ON CONFLICT(chat_hash) DO UPDATE SET mode='chat'",(chat_hash,))
                sid = mapping['session_id']
                if sid:
                    try:
                        tx.session(sid, trusted=True)
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
                        sid = None
                if any('\u0900' <= c <= '\u097f' for c in text):
                    lang = 'mr' if any(w in text for w in ('मी ', 'आहे', 'माझ', 'मला', 'वर्षांचा')) else ('hi' if lang != 'mr' else lang)
                elif lang == 'en' and re.search(r'\b(I am|my|need|help|years old)\b',text,re.I):
                    lang = 'en'
                try:
                    other_script = any(c.isalpha() and ord(c)>127 and not '\u0900'<=c<='\u097f' for c in text)
                    result = conversation(Message(text=text, session_id=sid, request_id=event_id, lang=media_language if media_language in ('en','hi','mr') else lang, language_override=not other_script), trusted=True, tx=tx)
                except HTTPException as exc:
                    if exc.status_code in (429, 503):
                        # Roll back: WAHA's retry may safely process this event later.
                        raise
                    status, reply = 'rejected', 'Please retry without sharing identity, bank or phone numbers.'
                else:
                    tx.execute('UPDATE waha_chats SET session_id=?,updated_at=? WHERE chat_hash=?',
                               (result['session_id'], time.time(), chat_hash))
                    status, reply = 'replied', redact(result['message'])
                    lang = result.get('language', lang)
                    notifications.update_profile(tx, chat_hash, chat, result.get('profile', {}), lang)
                    if lang not in ('en','hi','mr'):
                        reply = 'This language is registered but not verified for this demo. Please use English, Hindi or Marathi.\n'+reply
            if mapping or status == 'started':
                if status != 'stopped':
                    tx.execute('INSERT INTO channel_languages VALUES(?,?) ON CONFLICT(chat_hash) DO UPDATE SET language=excluded.language',(chat_hash,lang))
                    tx.execute('UPDATE scheme_alert_recipients SET language=?,updated_at=? WHERE chat_hash=?', (lang,time.time(),chat_hash))
            now = time.time()
            tx.execute('INSERT INTO waha_events VALUES(?,?,?,?,?,?,?)',
                       (event_id, chat_hash, 'pending' if reply else 'sent', status,
                        seal(reply) if reply and '#handoff=' in reply else reply, now, now))
    # The response outbox is committed before delivery. A retry reuses it without
    # repeating extraction, fact mutation or opt-in transitions.
    with case_store.transaction(lock) as tx:
        event = tx.execute('SELECT * FROM waha_events WHERE id=?', (event_id,)).fetchone()
        if event['status'] == 'sent':
            return {'status': 'duplicate' if event['reply'] else event['result']}
        url, key, _ = _config()
        try:
            _send(url, key, expected_session, chat, unseal(event['reply']) if event['result']=='voice_link' else event['reply'])
        except (httpx.HTTPError, OSError):
            raise HTTPException(503, 'Delivery pending; retry this event')
        tx.execute('UPDATE waha_events SET status=?,updated_at=? WHERE id=?', ('sent', time.time(), event_id))
        return {'status': event['result']}
