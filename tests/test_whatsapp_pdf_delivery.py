"""PDF guide over WAHA /api/sendFile: payload format, validation, PDF-service failures with bounded
retries, Render cold start, WAHA send failures, citizen GUIDE retry, schemes without official sources."""
import base64
import time
from unittest.mock import Mock

import httpx
import pytest

from web import case_store, result_review as review, whatsapp_delivery as delivery
from web.pdf_client import PdfAuthError, PdfRejected, PdfServiceError, PdfUnavailable
from test_whatsapp_approval import wa, start, say, texts, rows, run, FakePdf, CHAT  # noqa: F401
from test_deployment_v24 import persistent  # noqa: F401


def confirm(wa):
    start(wa)
    assert say(wa, 'PROCEED', 'm-ok') == 'profile_confirmed'


# ---------- the WAHA adapter ----------

def test_send_file_posts_base64_pdf_with_metadata(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, json=json, timeout=timeout)
        return httpx.Response(201, json={'id': 'x'}, request=httpx.Request('POST', url))
    monkeypatch.setattr(delivery.httpx, 'post', fake_post)
    delivery.send_file('http://waha:3000', 'k', 'default', CHAT, b'%PDF-1.7 x', 'yojana-guide-hi.pdf', 'caption')
    assert seen['url'] == 'http://waha:3000/api/sendFile' and seen['headers'] == {'X-Api-Key': 'k'}
    body = seen['json']
    assert body['session'] == 'default' and body['chatId'] == CHAT and body['caption'] == 'caption'
    assert body['file']['mimetype'] == 'application/pdf' and body['file']['filename'] == 'yojana-guide-hi.pdf'
    assert base64.b64decode(body['file']['data']) == b'%PDF-1.7 x'


@pytest.mark.parametrize('data,code', [(b'<html>not a pdf', 'not_a_pdf'), (b'', 'not_a_pdf')])
def test_send_file_rejects_non_pdf_bytes(monkeypatch, data, code):
    post = Mock()
    monkeypatch.setattr(delivery.httpx, 'post', post)
    with pytest.raises(delivery.FileRejected, match=code):
        delivery.send_file('http://waha', 'k', 'default', CHAT, data, 'yojana-guide-en.pdf')
    post.assert_not_called()


def test_send_file_rejects_oversized_document(monkeypatch):
    monkeypatch.setenv('WHATSAPP_PDF_MAX_BYTES', '10000')
    post = Mock()
    monkeypatch.setattr(delivery.httpx, 'post', post)
    with pytest.raises(delivery.FileRejected, match='too_large'):
        delivery.send_file('http://waha', 'k', 'default', CHAT, b'%PDF-' + b'0' * 20000, 'yojana-guide-en.pdf')
    post.assert_not_called()


# ---------- 7. PDF service failures ----------

@pytest.mark.parametrize('error,code', [
    (PdfAuthError('token mismatch', status=401), 'pdf_auth'),
    (PdfRejected('bad payload', status=422), 'pdf_rejected'),
])
def test_permanent_pdf_errors_fail_once_tell_citizen_and_never_offer_a_call(wa, monkeypatch, error, code):
    monkeypatch.setenv('BOLNA_FOLLOWUP_ENABLED', 'true')
    wa.pdf.results = [error]
    confirm(wa)
    run()
    d = rows('SELECT pdf_status,pdf_error,pdf_attempts,call_offer FROM result_deliveries')[0]
    assert d == {'pdf_status': 'failed', 'pdf_error': code, 'pdf_attempts': 1, 'call_offer': 'none'}
    assert texts(wa)[-1] == review.PDF_FAILED['en']
    wa.files.assert_not_called()
    assert rows('SELECT * FROM followup_calls') == []


@pytest.mark.parametrize('error', [
    PdfUnavailable('down', status=503), PdfUnavailable('gateway', status=504),
    PdfUnavailable('PDF service timed out (ReadTimeout)'), PdfServiceError('not a pdf', status=200, code='bad_response'),
])
def test_transient_pdf_errors_retry_with_backoff_and_stop_after_the_limit(wa, monkeypatch, error):
    monkeypatch.setenv('PDF_DELIVERY_MAX_ATTEMPTS', '3')
    wa.pdf.results = [error, error, error]
    confirm(wa)
    now = time.time()
    run(now)
    d = rows('SELECT pdf_status,pdf_attempts,pdf_next_attempt FROM result_deliveries')[0]
    assert d['pdf_status'] == 'retrying' and d['pdf_attempts'] == 1 and d['pdf_next_attempt'] >= now + 29
    run(now + 1)                                         # not due yet: nothing happens
    assert len(wa.pdf.calls) == 1
    run(now + 31)
    run(now + 31 + 121)
    d = rows('SELECT pdf_status,pdf_attempts FROM result_deliveries')[0]
    assert d == {'pdf_status': 'failed', 'pdf_attempts': 3}
    assert len(wa.pdf.calls) == 3
    run(now + 5000)
    assert len(wa.pdf.calls) == 3                        # bounded: no more attempts
    assert texts(wa).count(review.PDF_FAILED['en']) == 1
    wa.files.assert_not_called()


def test_invalid_bytes_from_service_are_never_sent(wa):
    wa.pdf.results = [b'<html>error page</html>']
    confirm(wa)
    run()
    assert rows('SELECT pdf_status,pdf_error FROM result_deliveries')[0] == {'pdf_status': 'failed',
                                                                             'pdf_error': 'invalid_pdf'}
    wa.files.assert_not_called()


def test_guide_command_retries_a_failed_pdf(wa):
    wa.pdf.results = [PdfAuthError('token', status=401)]
    confirm(wa)
    run()
    assert say(wa, 'GUIDE', 'm-guide') == 'guide_retry'
    run()
    wa.files.assert_called_once()
    assert say(wa, 'pdf', 'm-guide-2') == 'guide_sent'
    run()
    wa.files.assert_called_once()


def test_guide_before_confirmation_explains_how_to_get_one(wa):
    start(wa)
    assert say(wa, 'guide', 'm-guide') == 'guide_none'
    assert texts(wa)[-1] == review.GUIDE_NONE['en']


# ---------- 8. Render cold start and repeated WAHA sends ----------

def test_cold_start_warm_up_then_success_on_retry(wa):
    wa.pdf.warm_up = Mock(return_value=False)            # free Render instance still waking
    wa.pdf.results = [PdfUnavailable('PDF service timed out (ConnectTimeout)')]
    confirm(wa)
    now = time.time()
    run(now)
    assert wa.pdf.warm_up.call_count == 1
    wa.files.assert_not_called()
    run(now + 31)
    wa.files.assert_called_once()
    assert rows('SELECT pdf_status,pdf_attempts FROM result_deliveries')[0] == {'pdf_status': 'sent', 'pdf_attempts': 1}


def test_waha_file_send_failure_retries_and_sends_exactly_one_accepted_file(wa):
    wa.files.side_effect = [httpx.ConnectError('waha restarting'), None]
    confirm(wa)
    now = time.time()
    run(now)
    assert rows('SELECT pdf_status,pdf_error FROM result_deliveries')[0] == {'pdf_status': 'retrying',
                                                                             'pdf_error': 'waha_ConnectError'}
    run(now + 31)
    run(now + 400)
    assert wa.files.call_count == 2                      # one refused, one accepted, nothing after
    assert rows('SELECT pdf_status FROM result_deliveries')[0]['pdf_status'] == 'sent'


def test_stop_while_pdf_is_being_generated_is_not_sent(wa, monkeypatch):
    confirm(wa)

    def generate(guide):
        assert say(wa, 'STOP', 'm-stop') == 'stopped'    # citizen leaves mid-generation
        return b'%PDF-1.7 late'
    wa.pdf.generate = generate
    run()
    wa.files.assert_not_called()
    assert rows('SELECT pdf_status FROM result_deliveries')[0]['pdf_status'] == 'cancelled'


def test_crashed_generation_lease_is_recovered(wa):
    confirm(wa)
    with case_store.transaction() as tx:
        tx.execute("UPDATE result_deliveries SET pdf_status='generating',lease_owner='dead',lease_until=?",
                   (time.time() - 1,))
    run()
    wa.files.assert_called_once()


def test_schemes_without_official_source_are_left_out_and_explained(wa, monkeypatch):
    from web import conversation_api as api
    from test_assessment_completion import catalog

    def partial():
        data = catalog()
        for i in (0, 1):
            data[f'pension{i}']['source_url'] = ''
        return data
    monkeypatch.setattr(api, 'load_catalog', partial)
    confirm(wa)
    assert review.PDF_EXCLUDED['en'].format(n=2) in texts(wa)[-1]
    run()
    guide = wa.pdf.calls[0]
    assert {s['id'] for s in guide['schemes']} == {'pension2', 'pension3', 'pension4'}


def test_pdf_flag_off_sends_text_only(wa, monkeypatch):
    monkeypatch.setenv('WHATSAPP_PDF_SEND_ENABLED', 'false')
    confirm(wa)
    assert 'PDF' not in texts(wa)[-1]
    run()
    wa.files.assert_not_called()
    assert rows('SELECT pdf_status,pdf_error FROM result_deliveries')[0] == {'pdf_status': 'skipped',
                                                                             'pdf_error': 'disabled'}


def test_logs_never_contain_pdf_bytes_or_chat_ids(wa, caplog):
    confirm(wa)
    run()
    joined = caplog.text
    assert '%PDF' not in joined and '919876543210' not in joined and 'Old Age Pension' not in joined
