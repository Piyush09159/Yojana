"""One optional automated follow-up call after a confirmed WhatsApp shortlist (BOLNA_FOLLOWUP_ENABLED).

Order of events, each a separate, auditable step:
  1. PROCEED approves the facts and recommendations. It is NOT telephony consent.
  2. Scheme text accepted by WAHA, then the PDF guide accepted by WAHA (web/whatsapp_delivery.py).
  3. Only then a separate offer: "Call me / No call", naming the number (last 4 digits) and saying it is
     an automated assistant, independent guidance, not a government office.
  4. CALL ME stores consent evidence (call_consents: purpose, time, message-event digest) and queues
     ONE call (followup_calls.delivery_id is UNIQUE) due BOLNA_FOLLOWUP_DELAY_SECONDS (default 8) later.
     It is a durable row processed by the delivery runner, not a sleep in the webhook. Provider and
     network delays mean the phone may ring later than that.
  5. The runner re-checks everything at dial time (flag, consent not revoked, chat still opted in,
     review still confirmed, PDF sent, quiet hours, consent age) and then calls Bolna once.

Phone numbers: only a WhatsApp phone JID (<digits>@c.us) gives a number WhatsApp itself verified;
it is shown back to the citizen (last 4 digits) before they consent. An opaque @lid is NEVER turned
into a number, so @lid chats are not offered a call (a future owner-bound OTP form could add one).
The number is sealed at rest and wiped as soon as Bolna accepts or refuses the call.

Crash safety: a row is set to 'dialing' and committed before the HTTP request. A 'dialing' row whose
lease expired (worker died mid-request) is marked failed, never dialled again. A timeout is treated
as possibly-ringing, so it is not retried either. Only a certain refusal (connect error, 429/5xx) is
retried, at most twice. Bolna's own retries are disabled.

The call never changes the profile: the status webhook updates only this table.
"""
from __future__ import annotations

import os
import re
import time
import uuid

from fastapi import APIRouter, HTTPException

from . import bolna, case_store
from .channel_security import seal, unseal
from .diagnostics import emit
from . import result_review as review

router = APIRouter(prefix='/api/bolna/followup', tags=['Bolna follow-up calls'])

IST_OFFSET = 330 * 60
CONSENT_TTL = 24 * 3600
OFFER_TTL = 24 * 3600
LEASE = 120
TERMINAL = ('completed', 'no_answer', 'failed', 'cancelled', 'skipped')


def enabled():
    return bolna.flag('BOLNA_FOLLOWUP_ENABLED')


def configured():
    return bool(bolna.api_key() and os.getenv('BOLNA_FOLLOWUP_AGENT_ID'))


def delay():
    try:
        return max(5, min(int(os.getenv('BOLNA_FOLLOWUP_DELAY_SECONDS', '8')), 600))
    except ValueError:
        return 8


def trusted_phone(chat):
    """E.164 number for a WhatsApp phone JID, or None. Never for @lid or anything else."""
    m = re.fullmatch(r'([1-9]\d{9,14})@c\.us', chat or '')
    if not m:
        return None
    phone = '+' + m.group(1)
    codes = [c.strip() for c in os.getenv('BOLNA_ALLOWED_COUNTRY_CODES', '91').split(',') if c.strip().isdigit()]
    for code in codes:
        if m.group(1).startswith(code):
            if code == '91' and len(m.group(1)) != 12:
                return None
            return phone
    return None


# ---------------------------------------------------------------- quiet hours (India, IST)

def _window():
    raw = os.getenv('CALL_QUIET_HOURS', '21:00-09:00')
    try:
        start, end = raw.split('-')
        sh, sm = (int(x) for x in start.split(':'))
        eh, em = (int(x) for x in end.split(':'))
        return sh * 60 + sm, eh * 60 + em
    except ValueError:
        return 21 * 60, 9 * 60


def quiet(ts):
    start, end = _window()
    if start == end:
        return False
    minute = int(((ts + IST_OFFSET) % 86400) // 60)
    return (minute >= start or minute < end) if start > end else (start <= minute < end)


def next_open(ts):
    """The next moment the quiet window ends (IST)."""
    _, end = _window()
    local = ts + IST_OFFSET
    day = local - (local % 86400)
    candidate = day + end * 60
    if candidate <= local:
        candidate += 86400
    return candidate - IST_OFFSET


def _hours_text(lang):
    start, end = _window()
    fmt = lambda m: f'{m // 60}:{m % 60:02d}'          # noqa: E731
    return {'en': f'We do not call between {fmt(start)} and {fmt(end)}, so the call will come after {fmt(end)}.',
            'hi': f'हम {fmt(start)} से {fmt(end)} के बीच कॉल नहीं करते, इसलिए कॉल {fmt(end)} के बाद आएगी।',
            'mr': f'आम्ही {fmt(start)} ते {fmt(end)} दरम्यान कॉल करत नाही, म्हणून कॉल {fmt(end)} नंतर येईल.'}[lang]


# ---------------------------------------------------------------- texts

OFFER = {
    'en': ('Would you like one automated follow-up phone call from Yojana Saathi about these results?\n\n'
           'An automated voice assistant (independent guidance, not a government office) will call your WhatsApp '
           'number ending in {tail} once. It will never ask for Aadhaar, OTP or bank details.'),
    'hi': ('क्या आप इन नतीजों के बारे में योजना साथी से एक स्वचालित फ़ॉलो-अप फ़ोन कॉल चाहते हैं?\n\n'
           'एक स्वचालित वॉइस सहायक (स्वतंत्र मार्गदर्शन, सरकारी कार्यालय नहीं) आपके {tail} पर ख़त्म होने वाले '
           'WhatsApp नंबर पर एक बार कॉल करेगा। वह कभी आधार, OTP या बैंक की जानकारी नहीं माँगेगा।'),
    'mr': ('या निकालांबद्दल योजना साथीकडून एक स्वयंचलित फॉलो-अप फोन कॉल हवा आहे का?\n\n'
           'एक स्वयंचलित व्हॉइस सहायक (स्वतंत्र मार्गदर्शन, सरकारी कार्यालय नाही) तुमच्या {tail} ने संपणाऱ्या '
           'WhatsApp क्रमांकावर एकदा कॉल करेल. तो कधीही आधार, OTP किंवा बँक तपशील विचारणार नाही.'),
}
OFFER_ROWS = {
    'en': (('Call me', 'One automated call'), ('No call', 'No phone call')),
    'hi': (('मुझे कॉल करें', 'एक स्वचालित कॉल'), ('कॉल नहीं', 'कोई फ़ोन कॉल नहीं')),
    'mr': (('मला कॉल करा', 'एक स्वयंचलित कॉल'), ('कॉल नको', 'फोन कॉल नको')),
}
OFFER_FOOT = {
    'en': 'Reply 1 or 2. Nothing is scheduled unless you choose Call me.',
    'hi': '1 या 2 लिखें। “मुझे कॉल करें” चुने बिना कोई कॉल तय नहीं होगी।',
    'mr': '1 किंवा 2 पाठवा. “मला कॉल करा” निवडल्याशिवाय कोणताही कॉल ठरणार नाही.',
}
SCHEDULED = {
    'en': ('Okay. One automated follow-up call from Yojana Saathi will come to the number ending in {tail} shortly. '
           'It is not a government call and will never ask for Aadhaar, OTP or bank details. Reply NO CALL to cancel.'),
    'hi': ('ठीक है। योजना साथी की एक स्वचालित फ़ॉलो-अप कॉल जल्द ही {tail} पर ख़त्म होने वाले नंबर पर आएगी। '
           'यह सरकारी कॉल नहीं है और कभी आधार, OTP या बैंक जानकारी नहीं माँगेगी। रद्द करने के लिए NO CALL लिखें।'),
    'mr': ('ठीक आहे. योजना साथीचा एक स्वयंचलित फॉलो-अप कॉल लवकरच {tail} ने संपणाऱ्या क्रमांकावर येईल. '
           'हा सरकारी कॉल नाही आणि कधीही आधार, OTP किंवा बँक तपशील विचारणार नाही. रद्द करण्यासाठी NO CALL पाठवा.'),
}
DECLINED = {
    'en': 'Okay, no call will be made. Reply HELP any time for next steps.',
    'hi': 'ठीक है, कोई कॉल नहीं की जाएगी। आगे के कदमों के लिए कभी भी HELP लिखें।',
    'mr': 'ठीक आहे, कोणताही कॉल केला जाणार नाही. पुढील पायऱ्यांसाठी कधीही HELP पाठवा.',
}
CANCELLED = {
    'en': 'Okay, the follow-up call is cancelled. No call will be made.',
    'hi': 'ठीक है, फ़ॉलो-अप कॉल रद्द कर दी गई। कोई कॉल नहीं की जाएगी।',
    'mr': 'ठीक आहे, फॉलो-अप कॉल रद्द केला. कोणताही कॉल केला जाणार नाही.',
}
ALREADY = {
    'en': 'Your follow-up call is already scheduled. Reply NO CALL to cancel it.',
    'hi': 'आपकी फ़ॉलो-अप कॉल पहले से तय है। रद्द करने के लिए NO CALL लिखें।',
    'mr': 'तुमचा फॉलो-अप कॉल आधीच ठरला आहे. रद्द करण्यासाठी NO CALL पाठवा.',
}
STARTED_ALREADY = {
    'en': 'The follow-up call has already been placed. No other call will be made.',
    'hi': 'फ़ॉलो-अप कॉल पहले ही की जा चुकी है। कोई और कॉल नहीं होगी।',
    'mr': 'फॉलो-अप कॉल आधीच केला आहे. दुसरा कोणताही कॉल होणार नाही.',
}
UNAVAILABLE = {
    'en': 'A follow-up call is offered only after your shortlist and PDF guide have been sent, and only for this WhatsApp number.',
    'hi': 'फ़ॉलो-अप कॉल केवल आपकी सूची और PDF गाइड भेजे जाने के बाद, और केवल इसी WhatsApp नंबर के लिए दी जाती है।',
    'mr': 'फॉलो-अप कॉल फक्त तुमची यादी आणि PDF मार्गदर्शिका पाठवल्यानंतर, आणि फक्त याच WhatsApp क्रमांकासाठी दिला जातो.',
}
STALE_OFFER = {
    'en': 'That call offer is out of date. Nothing was scheduled.',
    'hi': 'वह कॉल प्रस्ताव पुराना है। कुछ भी तय नहीं किया गया।',
    'mr': 'तो कॉल प्रस्ताव जुना आहे. काहीही ठरवले नाही.',
}

CALL_YES_WORDS = {'call me', 'yes call', 'yes call me', 'please call', 'call me please', 'मुझे कॉल करें', 'मुझे कॉल करो',
                  'कॉल करें', 'कॉल करो', 'फोन करें', 'फ़ोन करें', 'मला कॉल करा', 'कॉल करा', 'फोन करा'}
CALL_NO_WORDS = {'no call', 'no calls', 'dont call', "don't call", 'do not call', 'cancel call', 'कॉल नहीं', 'कॉल मत करो',
                 'कॉल न करें', 'फोन नहीं', 'कॉल नको', 'फोन नको', 'कॉल रद्द', 'कॉल रद्द करा'}


def offer_text(lang, tail):
    lang = lang if lang in OFFER else 'en'
    rows = OFFER_ROWS[lang]
    return (OFFER[lang].format(tail=tail) + '\n\n' +
            '\n'.join(f'{i}. {t} — {d}' for i, (t, d) in enumerate(rows, 1)) + '\n\n' + OFFER_FOOT[lang])


# ---------------------------------------------------------------- citizen choice (inside the webhook tx)

def choose(tx, chat_hash, chat, lang, event_id, accept, version=None):
    """CALL ME / NO CALL. Returns (status, reply)."""
    lang = lang if lang in OFFER else 'en'
    d = review.latest_delivery(tx, chat_hash)
    r = review.latest(tx, chat_hash)
    valid = (d and r and r['status'] == 'confirmed' and r['version'] == d['version'] and d['pdf_status'] == 'sent'
             and d['call_offer'] in ('offered', 'accepted', 'declined'))
    if not valid:
        return 'call_unavailable', UNAVAILABLE[lang]
    if version is not None and version != d['version']:
        return 'call_stale', STALE_OFFER[lang]
    now = time.time()
    call = tx.execute('SELECT * FROM followup_calls WHERE delivery_id=?', (d['id'],)).fetchone()
    if not accept:
        if call and call['status'] == 'queued':
            tx.execute("UPDATE followup_calls SET status='cancelled',phone_cipher='',detail='citizen_cancelled',updated_at=? WHERE id=?",
                       (now, call['id']))
            tx.execute('UPDATE call_consents SET revoked_at=?,revoke_reason=? WHERE id=? AND revoked_at IS NULL',
                       (now, 'citizen_cancelled', call['consent_id']))
            tx.execute("UPDATE result_deliveries SET call_offer='declined',updated_at=? WHERE id=?", (now, d['id']))
            emit('CALL_FOLLOWUP_SKIPPED', event=event_id, detail='citizen_cancelled')
            return 'call_cancelled', CANCELLED[lang]
        if call and call['status'] in ('dialing', 'started'):
            return 'call_already_started', STARTED_ALREADY[lang]
        if not call:
            tx.execute('INSERT INTO call_consents VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                       (uuid.uuid4().hex, chat_hash, d['id'], 'followup_call', 'declined', event_id, now, None, None))
        tx.execute("UPDATE result_deliveries SET call_offer='declined',updated_at=? WHERE id=?", (now, d['id']))
        return 'call_declined', DECLINED[lang]
    # accept
    if call:
        if call['status'] == 'queued':
            return 'call_already_scheduled', ALREADY[lang]
        if call['status'] in ('dialing', 'started', 'completed', 'no_answer', 'failed'):
            return 'call_already_started', STARTED_ALREADY[lang]
        return 'call_unavailable', UNAVAILABLE[lang]          # cancelled/skipped: renewed offer needed
    if d['call_offer'] == 'declined':
        existing = tx.execute("SELECT id FROM call_consents WHERE delivery_id=? AND purpose='followup_call'",
                              (d['id'],)).fetchone()
        if existing:                                          # a decline is final for this shortlist
            return 'call_unavailable', UNAVAILABLE[lang]
    phone = trusted_phone(chat)
    if not enabled() or not configured() or not phone or now - (d['call_offered_at'] or 0) > OFFER_TTL:
        return 'call_unavailable', UNAVAILABLE[lang]
    if not review.mark_once(tx, chat_hash, d['version'], 'call_consent'):
        return 'call_already_scheduled', ALREADY[lang]
    consent_id = uuid.uuid4().hex
    tx.execute('INSERT INTO call_consents VALUES(?,?,?,?,?,?,?,?,?)',
               (consent_id, chat_hash, d['id'], 'followup_call', 'granted', event_id, now, None, None))
    tx.execute('INSERT INTO followup_calls(id,delivery_id,chat_hash,consent_id,phone_cipher,status,due_at,created_at,updated_at) '
               'VALUES(?,?,?,?,?,?,?,?,?)',
               (uuid.uuid4().hex, d['id'], chat_hash, consent_id, seal(phone), 'queued', now + delay(), now, now))
    tx.execute("UPDATE result_deliveries SET call_offer='accepted',updated_at=? WHERE id=?", (now, d['id']))
    emit('CALL_FOLLOWUP_QUEUED', event=event_id, version=d['version'])
    reply = SCHEDULED[lang].format(tail=phone[-4:])
    if quiet(now + delay()):
        reply += '\n\n' + _hours_text(lang)
    return 'call_scheduled', reply


# ---------------------------------------------------------------- runner pass

def _skip(tx, row, reason):
    tx.execute("UPDATE followup_calls SET status='skipped',phone_cipher='',detail=?,lease_owner=NULL,lease_until=NULL,"
               "updated_at=? WHERE id=?", (reason, time.time(), row['id']))
    emit('CALL_FOLLOWUP_SKIPPED', detail=reason)


def _skip_reason(tx, row, now):
    if not enabled():
        return 'disabled'
    if not configured():
        return 'not_configured'
    if not row['phone_cipher']:
        return 'no_phone'
    if not tx.execute('SELECT 1 FROM waha_chats WHERE chat_hash=? AND opted_in=1', (row['chat_hash'],)).fetchone():
        return 'opted_out'
    consent = tx.execute("SELECT * FROM call_consents WHERE id=? AND decision='granted' AND revoked_at IS NULL",
                         (row['consent_id'],)).fetchone()
    if not consent:
        return 'no_consent'
    d = tx.execute('SELECT * FROM result_deliveries WHERE id=?', (row['delivery_id'],)).fetchone()
    r = tx.execute('SELECT status FROM result_reviews WHERE id=?', (d['review_id'],)).fetchone() if d else None
    if not d or d['cancelled_at'] or d['pdf_status'] != 'sent' or not r or r['status'] != 'confirmed':
        return 'stale'
    if now - consent['created_at'] > CONSENT_TTL:
        return 'expired'
    return None


def run_due(now=None, limit=5):
    """One bounded pass. Returns the number of calls placed."""
    if not case_store.enabled():
        return 0
    now = now or time.time()
    placed = 0
    with case_store.transaction('followup-recovery') as tx:
        stuck = tx.execute("SELECT id FROM followup_calls WHERE status='dialing' AND lease_until<?", (now,)).fetchall()
        for row in stuck:
            tx.execute("UPDATE followup_calls SET status='failed',phone_cipher='',detail='interrupted',updated_at=? "
                       "WHERE id=? AND status='dialing'", (now, row['id']))
            emit('CALL_FOLLOWUP_FAILED', detail='interrupted')
        due = [dict(r) for r in tx.execute(
            "SELECT id,chat_hash FROM followup_calls WHERE status='queued' AND due_at<=? "
            "AND (lease_until IS NULL OR lease_until<?) ORDER BY due_at LIMIT ?", (now, now, limit))]
    for candidate in due:
        lease = uuid.uuid4().hex
        with case_store.transaction('waha:' + candidate['chat_hash']) as tx:
            row = tx.execute('SELECT * FROM followup_calls WHERE id=?', (candidate['id'],)).fetchone()
            if not row or row['status'] != 'queued' or row['due_at'] > now:
                continue
            reason = _skip_reason(tx, row, now)
            if reason:
                _skip(tx, row, reason)
                continue
            if quiet(now):
                resume = next_open(now)
                consent = tx.execute('SELECT created_at FROM call_consents WHERE id=?', (row['consent_id'],)).fetchone()
                if resume - consent['created_at'] > CONSENT_TTL:
                    _skip(tx, row, 'quiet_hours')
                else:
                    tx.execute('UPDATE followup_calls SET due_at=?,detail=?,updated_at=? WHERE id=?',
                               (resume, 'deferred_quiet_hours', now, row['id']))
                    emit('CALL_FOLLOWUP_SKIPPED', detail='deferred_quiet_hours')
                continue
            d = tx.execute('SELECT language,scheme_count,guide_count FROM result_deliveries WHERE id=?',
                           (row['delivery_id'],)).fetchone()
            phone = unseal(row['phone_cipher'])
            attempts = row['attempts'] + 1
            tx.execute("UPDATE followup_calls SET status='dialing',attempts=?,lease_owner=?,lease_until=?,updated_at=? WHERE id=?",
                       (attempts, lease, now + LEASE, now, row['id']))
        # Minimal user_data: no name, profile, scheme names or number. The agent's prompt holds the script.
        user_data = {'service_name': 'Yojana Saathi', 'language': d['language'],
                     'scheme_count': d['scheme_count'], 'guide_sent': 'yes'}
        try:
            execution_id = bolna.place_call(os.environ.get('BOLNA_FOLLOWUP_AGENT_ID', ''), phone, user_data)
        except bolna.BolnaError as exc:
            with case_store.transaction('waha:' + candidate['chat_hash']) as tx:
                current = tx.execute('SELECT status,lease_owner FROM followup_calls WHERE id=?', (row['id'],)).fetchone()
                if not current or current['status'] != 'dialing' or current['lease_owner'] != lease:
                    continue
                if exc.retryable and attempts < 3:
                    tx.execute("UPDATE followup_calls SET status='queued',due_at=?,detail=?,lease_owner=NULL,lease_until=NULL,"
                               "updated_at=? WHERE id=?", (time.time() + 30, 'retry_' + exc.detail, time.time(), row['id']))
                else:
                    tx.execute("UPDATE followup_calls SET status='failed',phone_cipher='',detail=?,lease_owner=NULL,"
                               "lease_until=NULL,updated_at=?,finished_at=? WHERE id=?",
                               (exc.detail[:60], time.time(), time.time(), row['id']))
            emit('CALL_FOLLOWUP_FAILED', status=exc.status, detail=exc.detail, attempt=attempts)
            continue
        finally:
            phone = None
        with case_store.transaction('waha:' + candidate['chat_hash']) as tx:
            tx.execute("UPDATE followup_calls SET status='started',execution_id=?,provider_status='queued',phone_cipher='',"
                       "lease_owner=NULL,lease_until=NULL,started_at=?,updated_at=? WHERE id=? AND status='dialing'",
                       (execution_id, time.time(), time.time(), row['id']))
        emit('CALL_FOLLOWUP_STARTED', attempt=attempts)
        placed += 1
    return placed


# ---------------------------------------------------------------- Bolna status webhook

@router.post('/webhook/{secret}', include_in_schema=False)
def followup_webhook(secret: str, payload: dict):
    if not bolna.webhook_secret_ok(secret):
        raise HTTPException(403, 'Forbidden')
    execution_id = payload.get('id') or payload.get('execution_id')
    if not isinstance(execution_id, str) or not execution_id or len(execution_id) > 100:
        return {'status': 'ignored'}
    with case_store.transaction('bolna-followup:' + execution_id) as tx:
        row = tx.execute('SELECT status FROM followup_calls WHERE execution_id=?', (execution_id,)).fetchone()
    if not row:
        return {'status': 'ignored'}           # unknown ids never trigger a Bolna lookup
    if row['status'] in TERMINAL:
        return {'status': 'duplicate'}         # late webhook after a final status: no change
    try:
        execution = bolna.get_execution(execution_id)
    except bolna.BolnaError:
        raise HTTPException(503, 'Retry later')
    provider = str(execution.get('status') or '')[:40]
    result = bolna.outcome(provider)
    with case_store.transaction('bolna-followup:' + execution_id) as tx:
        current = tx.execute('SELECT id,status FROM followup_calls WHERE execution_id=?', (execution_id,)).fetchone()
        if not current or current['status'] in TERMINAL:
            return {'status': 'duplicate'}
        if result is None:
            tx.execute('UPDATE followup_calls SET provider_status=?,updated_at=? WHERE id=?', (provider, time.time(), current['id']))
            return {'status': 'in_progress'}
        # Only this row changes. Transcript/extracted data are not stored and never touch the profile.
        tx.execute('UPDATE followup_calls SET status=?,provider_status=?,phone_cipher=\'\',finished_at=?,updated_at=? WHERE id=?',
                   (result, provider, time.time(), time.time(), current['id']))
    emit('CALL_FOLLOWUP_FINISHED' if result == 'completed' else 'CALL_FOLLOWUP_FAILED', detail=result)
    return {'status': result}
