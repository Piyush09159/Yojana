"""Server-side delivery of the confirmed shortlist's PDF guide over WAHA, plus the call offer.

Runs from the delivery runner (web/delivery_worker.py: an API-startup loop by default, or
`python -m web.delivery_worker` as a separate service). Never inside the chat webhook.

PDF stage, per result_deliveries row (one row per confirmed review version, UNIQUE(chat_hash, version)):
  queued/retrying --lease--> generating --PDF service--> (WAHA POST /api/sendFile) --> sent
                                 |--> retrying (bounded backoff) --> failed (citizen told; GUIDE retries)
  It starts only after WAHA accepted the scheme text (result_review.event_sent). The send happens
  under the chat's lock after re-checking STOP/CHANGE, so a cancelled shortlist is never sent.
  "Accepted by WAHA" is not a handset delivery receipt. A crash between WAHA accepting the file and
  the commit can resend it once after the lease expires (at-least-once, never claimed exactly-once).

One bundled PDF per shortlist (up to 5 schemes), filename yojana-guide-<lang>.pdf. Schemes without
an official source URL are left out (guide_from_cards) and the scheme message says so.
Logs carry only digests, codes and counts: never names, numbers, PDF bytes or tokens.
"""
from __future__ import annotations

import base64
import os
import time
import uuid

import httpx

from . import case_store
from . import result_review as review
from . import lid_resolver
from .channel_security import seal, unseal
from .diagnostics import emit
from .pdf_client import (PdfAuthError, PdfClient, PdfNotConfigured, PdfRejected, PdfServiceError,
                         PdfUnavailable, guide_from_cards)

LEASE = 180
BACKOFF = (30, 120, 600)


class FileRejected(ValueError):
    pass


def max_attempts():
    try:
        return max(1, min(int(os.getenv('PDF_DELIVERY_MAX_ATTEMPTS', '4')), 8))
    except ValueError:
        return 4


def max_bytes():
    try:
        return max(10000, int(os.getenv('WHATSAPP_PDF_MAX_BYTES', str(8 * 1024 * 1024))))
    except ValueError:
        return 8 * 1024 * 1024


def get_client() -> PdfClient:
    """Kept as a module function so tests can replace it."""
    return PdfClient.from_env()


def send_file(url, key, session, chat_id, data: bytes, filename: str, caption: str | None = None):
    """WAHA POST /api/sendFile with base64 data. Validates the document first."""
    if not isinstance(data, (bytes, bytearray)) or not bytes(data[:5]) == b'%PDF-':
        raise FileRejected('not_a_pdf')
    if len(data) > max_bytes():
        raise FileRejected('too_large')
    if not filename.endswith('.pdf') or '/' in filename or '\\' in filename:
        raise FileRejected('bad_filename')
    body = {'session': session, 'chatId': chat_id,
            'file': {'mimetype': 'application/pdf', 'filename': filename,
                     'data': base64.b64encode(bytes(data)).decode('ascii')}}
    if caption:
        body['caption'] = caption[:1000]
    resp = httpx.post(url + '/api/sendFile', headers={'X-Api-Key': key}, json=body, timeout=60)
    resp.raise_for_status()


def _waha():
    from .waha_bridge import _config
    url, key, _ = _config()
    return url, key, os.getenv('WAHA_SESSION', 'default')


def _label(field, language):
    from .pdf_api import _label as label
    return label(field, language)


def _valid(tx, row):
    if row['cancelled_at'] or row['schemes_status'] != 'sent' or not row['address_cipher'] or not row['snapshot']:
        return False
    if not tx.execute('SELECT 1 FROM waha_chats WHERE chat_hash=? AND opted_in=1', (row['chat_hash'],)).fetchone():
        return False
    r = tx.execute('SELECT status FROM result_reviews WHERE id=?', (row['review_id'],)).fetchone()
    return bool(r and r['status'] == 'confirmed')


def _cancel(tx, row, reason):
    tx.execute("UPDATE result_deliveries SET pdf_status='cancelled',cancel_reason=?,lease_owner=NULL,lease_until=NULL,"
               "updated_at=? WHERE id=?", (reason, time.time(), row['id']))


def _failure(tx, row, code, retryable):
    """Record one failed attempt: retry with backoff, or give up and tell the citizen once."""
    attempts = row['pdf_attempts'] + 1
    now = time.time()
    if retryable and attempts < max_attempts():
        tx.execute("UPDATE result_deliveries SET pdf_status='retrying',pdf_attempts=?,pdf_next_attempt=?,pdf_error=?,"
                   "lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                   (attempts, now + BACKOFF[min(attempts - 1, len(BACKOFF) - 1)], code, now, row['id']))
        emit('PDF_DELIVERY_RETRY', detail=code, attempt=attempts)
        return
    tx.execute("UPDATE result_deliveries SET pdf_status='failed',pdf_attempts=?,pdf_error=?,lease_owner=NULL,"
               "lease_until=NULL,updated_at=? WHERE id=?", (attempts, code, now, row['id']))
    emit('PDF_DELIVERY_FAILED', detail=code, attempt=attempts)
    try:
        from .waha_bridge import _send
        url, key, session = _waha()
        _send(url, key, session, unseal(row['address_cipher']), review.PDF_FAILED.get(row['language'], review.PDF_FAILED['en']))
    except Exception as exc:                                  # the status stays 'failed'; HELP/GUIDE still work
        emit('WAHA_REPLY_DELIVERY_FAILED', stage='pdf_failure_notice', detail=type(exc).__name__)


def run_pdf_pass(now=None, limit=5):
    """One bounded pass over due PDF deliveries. Returns the number of guides accepted by WAHA."""
    if not case_store.enabled():
        return 0
    now = now or time.time()
    sent = 0
    with case_store.transaction('pdf-due') as tx:
        due = [dict(r) for r in tx.execute(
            "SELECT id,chat_hash FROM result_deliveries WHERE ((pdf_status IN ('queued','retrying') AND pdf_next_attempt<=?) "
            "OR pdf_status='generating') AND (lease_until IS NULL OR lease_until<?) ORDER BY pdf_next_attempt LIMIT ?",
            (now, now, limit))]
    for candidate in due:
        lease = uuid.uuid4().hex
        lock = 'waha:' + candidate['chat_hash']
        with case_store.transaction(lock) as tx:
            row = tx.execute('SELECT * FROM result_deliveries WHERE id=?', (candidate['id'],)).fetchone()
            if not row or row['pdf_status'] not in ('queued', 'retrying', 'generating') or (
                    row['lease_until'] and row['lease_until'] >= now):
                continue
            if not _valid(tx, row):
                _cancel(tx, row, 'stale')
                continue
            if not review.pdf_enabled():
                tx.execute("UPDATE result_deliveries SET pdf_status='skipped',pdf_error='disabled',updated_at=? WHERE id=?",
                           (now, row['id']))
                continue
            tx.execute("UPDATE result_deliveries SET pdf_status='generating',lease_owner=?,lease_until=?,updated_at=? WHERE id=?",
                       (lease, now + LEASE, now, row['id']))
            row = dict(row)
            snapshot = review.unseal_snapshot(row['snapshot'])
        language = row['language']
        code, retryable, pdf = None, False, None
        try:
            from . import conversation_api
            catalog = conversation_api.load_catalog()
            guide = guide_from_cards(snapshot['cards'], catalog, language=language, field_label=_label)
            if not guide:
                code = 'no_official_source'
            else:
                client = get_client()
                client.warm_up()                       # wakes a sleeping free Render instance; never raises
                pdf = client.generate(guide)
                if not pdf.startswith(b'%PDF-') or len(pdf) > max_bytes():
                    code = 'invalid_pdf'
        except PdfNotConfigured:
            code = 'pdf_not_configured'
        except PdfAuthError:
            code = 'pdf_auth'
        except PdfRejected:
            code = 'pdf_rejected'
        except PdfUnavailable as exc:
            code, retryable = 'pdf_unavailable_' + str(exc.status or 'net'), True
        except PdfServiceError:
            code, retryable = 'pdf_bad_response', True
        except Exception as exc:                       # catalog or payload bug: bounded retry, then give up
            code, retryable = 'pdf_' + type(exc).__name__, True
        with case_store.transaction(lock) as tx:
            current = tx.execute('SELECT * FROM result_deliveries WHERE id=?', (row['id'],)).fetchone()
            if not current or current['pdf_status'] != 'generating' or current['lease_owner'] != lease:
                continue
            if not _valid(tx, current):              # STOP / CHANGE while the PDF was being made
                _cancel(tx, current, 'stale')
                continue
            if code == 'no_official_source':
                tx.execute("UPDATE result_deliveries SET pdf_status='skipped',pdf_error=?,lease_owner=NULL,lease_until=NULL,"
                           "updated_at=? WHERE id=?", (code, time.time(), row['id']))
                continue
            if code:
                _failure(tx, current, code, retryable)
                continue
            try:
                url, key, session = _waha()
                send_file(url, key, session, unseal(current['address_cipher']), pdf,
                          f'yojana-guide-{language}.pdf', review.PDF_CAPTION.get(language, review.PDF_CAPTION['en']))
            except FileRejected as exc:
                _failure(tx, current, 'file_' + str(exc), False)
                continue
            except Exception as exc:                 # WAHA down, 4xx/5xx, timeout, not configured
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                _failure(tx, current, f'waha_{status or type(exc).__name__}', True)
                continue
            from .followup_calls import enabled as calls_enabled
            tx.execute("UPDATE result_deliveries SET pdf_status='sent',pdf_sent_at=?,pdf_error=NULL,lease_owner=NULL,"
                       "lease_until=NULL,call_offer=?,updated_at=? WHERE id=?",
                       (time.time(), 'pending' if calls_enabled() else 'disabled', time.time(), row['id']))
            emit('PDF_DELIVERY_SENT', bytes=len(pdf), version=current['version'])
            sent += 1
        pdf = None
    return sent


def run_offer_pass(now=None, limit=5):
    """Send the separate CALL ME / NO CALL offer after a PDF was accepted. Only phone JIDs qualify."""
    if not case_store.enabled():
        return 0
    from . import followup_calls as calls
    from .waha_bridge import _deliver, _menu_state, _with_list
    now = now or time.time()
    offered = 0
    with case_store.transaction('call-offer-due') as tx:
        due = [dict(r) for r in tx.execute(
            "SELECT id,chat_hash FROM result_deliveries WHERE call_offer='pending' AND pdf_status='sent' LIMIT ?", (limit,))]
    for candidate in due:
        with case_store.transaction('waha:' + candidate['chat_hash']) as tx:
            row = tx.execute('SELECT * FROM result_deliveries WHERE id=?', (candidate['id'],)).fetchone()
            if not row or row['call_offer'] != 'pending':
                continue
            if not _valid(tx, row) or not calls.enabled() or not calls.configured():
                tx.execute("UPDATE result_deliveries SET call_offer='disabled',updated_at=? WHERE id=?", (now, row['id']))
                continue
            chat = unseal(row['address_cipher'])
            phone = calls.trusted_phone(chat)
            lid_phone = False
            if not phone and lid_resolver.is_lid(chat):
                # @lid: ask WAHA which number WhatsApp maps it to. The chat identity stays the LID.
                try:
                    url, key, session = _waha()
                    outcome, phone = lid_resolver.resolve(url, key, session, chat)
                except Exception:                  # WAHA not configured
                    outcome, phone = 'error', None
                if outcome == 'error':
                    attempts = row['call_offer_attempts'] + 1
                    tx.execute('UPDATE result_deliveries SET call_offer=?,call_offer_attempts=?,updated_at=? WHERE id=?',
                               ('unavailable' if attempts >= 3 else 'pending', attempts, now, row['id']))
                    emit('CALL_FOLLOWUP_SKIPPED', detail='lid_lookup_failed', attempt=attempts)
                    continue
                if not phone:                      # unmapped / invalid: skip safely, no phone verification flow
                    tx.execute("UPDATE result_deliveries SET call_offer='unavailable',updated_at=? WHERE id=?", (now, row['id']))
                    emit('CALL_FOLLOWUP_SKIPPED', detail='lid_' + outcome)
                    continue
                lid_phone = True
            if not phone:
                tx.execute("UPDATE result_deliveries SET call_offer='unavailable',updated_at=? WHERE id=?", (now, row['id']))
                emit('CALL_FOLLOWUP_SKIPPED', detail='no_trusted_phone')
                continue
            lang = row['language']
            stored = _with_list(f"call.{row['version']}", lang, calls.offer_text(lang, phone[-4:]))
            try:
                url, key, session = _waha()
                _deliver(url, key, session, chat, stored, row['id'])
            except Exception as exc:
                attempts = row['call_offer_attempts'] + 1
                tx.execute('UPDATE result_deliveries SET call_offer=?,call_offer_attempts=?,updated_at=? WHERE id=?',
                           ('failed' if attempts >= 3 else 'pending', attempts, now, row['id']))
                emit('WAHA_REPLY_DELIVERY_FAILED', stage='call_offer', detail=type(exc).__name__)
                continue
            tx.execute("UPDATE result_deliveries SET call_offer='offered',call_offered_at=?,call_phone_cipher=?,updated_at=? WHERE id=?",
                       (now, seal(phone) if lid_phone else None, now, row['id']))
            _menu_state(tx, row['chat_hash'], menu='call', menu_at=now)
            offered += 1
    return offered
