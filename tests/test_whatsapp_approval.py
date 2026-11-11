"""WhatsApp result review gate: no scheme is sent before PROCEED; CHANGE/HELP/STOP; stale approvals;
list row ids, numbered fallback and words in English, Hindi and Marathi.

Offline: SQLite case store, mock LLM, WAHA send functions replaced by mocks, fake PDF client."""
import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from llm.providers.mock_provider import MockProvider
from web import case_store, conversation_api as api, result_review as review, waha_bridge as waha
from web import whatsapp_delivery as delivery, delivery_worker
from web.app import create_app
from test_deployment_v24 import persistent, configure_waha, signed  # noqa: F401
from test_assessment_completion import catalog
from test_voice_pipeline import post

CHAT = '919876543210@c.us'
FULL = {'age': 62, 'occupation': 'retired', 'state': 'maharashtra', 'annual_family_income_inr': 40000}
SCHEME_NAME = 'Old Age Pension'


class FakePdf:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls, self.warmed = [], 0

    def warm_up(self):
        self.warmed += 1
        return True

    def generate(self, guide):
        self.calls.append(guide)
        if self.results:
            item = self.results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return b'%PDF-1.7 synthetic guide'


@pytest.fixture
def wa(persistent, monkeypatch):  # noqa: F811
    configure_waha(monkeypatch)
    monkeypatch.setenv('SCHEME_REVIEW_CONFIRMATION_ENABLED', 'true')
    monkeypatch.setenv('WHATSAPP_PDF_SEND_ENABLED', 'true')
    monkeypatch.delenv('BOLNA_FOLLOWUP_ENABLED', raising=False)
    provider = MockProvider(queue=[{'language': 'en', 'facts': FULL, 'intent': 'give_info',
                                    'requested_category': 'income'}] * 6)
    monkeypatch.setattr(api, 'get_llm', lambda _: provider)
    monkeypatch.setattr(api, 'load_catalog', catalog)
    sender, lister, files = Mock(), Mock(), Mock()
    monkeypatch.setattr(waha, '_send', sender)
    monkeypatch.setattr(waha, '_send_list', lister)
    monkeypatch.setattr(delivery, 'send_file', files)
    pdf = FakePdf()
    monkeypatch.setattr(delivery, 'get_client', lambda: pdf)
    client = TestClient(create_app())
    return type('WA', (), {'client': client, 'sender': sender, 'lister': lister, 'files': files, 'pdf': pdf,
                           'provider': provider})


def say(wa, text, mid, chat=CHAT):
    return signed(wa.client, text, mid, chat=chat).json()['status']


def tap(wa, row_id, title, mid, chat=CHAT):
    message = {'listResponseMessage': {'title': title, 'listType': 1, 'singleSelectReply': {'selectedRowId': row_id}}}
    return post(wa.client, {'from': chat, 'id': mid, 'body': title, 'fromMe': False, 'hasMedia': False,
                            '_data': {'key': {'id': mid}, 'message': message}}).json()['status']


def texts(wa):
    return [c.args[-1] for c in wa.sender.call_args_list]


def start(wa, language='1', chat=CHAT, intro='I am 62, retired in Maharashtra, income 40000'):
    assert say(wa, 'YOJANA', 'm-start', chat) == 'started'
    assert say(wa, language, 'm-lang', chat) == 'language_selected'
    return say(wa, intro, 'm-intro', chat)


def rows(sql, params=()):
    with case_store.transaction() as tx:
        return [dict(r) for r in tx.execute(sql, params)]


def run(now=None):
    return delivery_worker.run_once(now)


# ---------- 1. assessment complete -> review only ----------

def test_completion_sends_review_and_never_a_scheme_name(wa):
    assert start(wa) == 'review_requested'
    review_text = texts(wa)[-1]
    for line in ('Before I send scheme matches', '• Age: 62', '• State: Maharashtra', '• Work: Retired',
                 'under ₹1 lakh', '1. Proceed', '2. Change', '3. Help', 'do not send identity or bank numbers'):
        assert line in review_text
    assert '40000' not in review_text                     # income only as a band
    assert not any(SCHEME_NAME in t for t in texts(wa))
    stored = rows('SELECT reply FROM waha_events')
    assert not any(r['reply'] and SCHEME_NAME in r['reply'] for r in stored)
    assert rows('SELECT status,version FROM result_reviews') == [{'status': 'awaiting', 'version': 1}]
    assert rows('SELECT * FROM result_deliveries') == []
    run()
    wa.files.assert_not_called()


def test_gate_off_keeps_existing_behaviour(wa, monkeypatch):
    monkeypatch.setenv('SCHEME_REVIEW_CONFIRMATION_ENABLED', 'false')
    assert start(wa) == 'replied'
    assert SCHEME_NAME in texts(wa)[-1]
    assert rows('SELECT * FROM result_reviews') == []


# ---------- 2. PROCEED -> one scheme message, then one PDF ----------

def test_proceed_list_reply_sends_one_scheme_message_then_one_pdf(wa):
    start(wa)
    before = len(texts(wa))
    assert tap(wa, 'review:proceed:1', 'Proceed', 'm-ok') == 'profile_confirmed'
    sent = texts(wa)[before:]
    assert len(sent) == 1
    assert SCHEME_NAME in sent[0] and 'based on the details you approved' in sent[0]
    assert 'decided by the relevant government office' in sent[0] and 'PDF' in sent[0]
    d = rows('SELECT schemes_status,pdf_status,scheme_count,guide_count FROM result_deliveries')[0]
    assert d == {'schemes_status': 'sent', 'pdf_status': 'queued', 'scheme_count': 5, 'guide_count': 5}
    run()
    wa.files.assert_called_once()
    url, key, session, chat, data, filename, caption = wa.files.call_args.args
    assert chat == CHAT and filename == 'yojana-guide-en.pdf' and data.startswith(b'%PDF-')
    assert 'not government approval' in caption
    guide = wa.pdf.calls[0]
    assert guide['language'] == 'en' and len(guide['schemes']) == 5
    assert all(s['source_url'].startswith('https://') for s in guide['schemes'])
    run()
    wa.files.assert_called_once()                          # never a second PDF
    assert rows('SELECT pdf_status FROM result_deliveries')[0]['pdf_status'] == 'sent'


def test_pdf_waits_until_scheme_text_is_accepted_by_waha(wa):
    start(wa)
    wa.sender.side_effect = httpx.ConnectError('waha down')
    assert signed(wa.client, "PROCEED", "m-ok", chat=CHAT).status_code == 503
    assert rows('SELECT schemes_status,pdf_status FROM result_deliveries')[0] == {'schemes_status': 'queued',
                                                                                  'pdf_status': 'waiting'}
    run()
    wa.files.assert_not_called()
    wa.sender.side_effect = None
    assert say(wa, 'PROCEED', 'm-ok') == 'profile_confirmed'      # WAHA's retry of the same event
    run()
    wa.files.assert_called_once()


# ---------- 3. duplicates ----------

def test_duplicate_proceed_and_webhook_retries_do_not_resend(wa):
    start(wa)
    assert say(wa, 'PROCEED', 'm-ok') == 'profile_confirmed'
    assert say(wa, 'PROCEED', 'm-ok') == 'duplicate'               # same WAHA event again
    assert say(wa, 'proceed', 'm-ok-2') == 'already_confirmed'     # a second, new message
    assert tap(wa, 'review:proceed:1', 'Proceed', 'm-ok-3') == 'already_confirmed'
    run(); run()
    assert wa.files.call_count == 1
    assert len(rows('SELECT id FROM result_deliveries')) == 1
    assert sum(SCHEME_NAME in t for t in texts(wa)) == 1


# ---------- 4. CHANGE ----------

def test_change_invalidates_undelivered_pdf_then_fresh_proceed_works(wa):
    start(wa)
    assert say(wa, 'PROCEED', 'm-ok') == 'profile_confirmed'
    assert say(wa, 'CHANGE', 'm-change') == 'change_requested'
    prompt = texts(wa)[-1]
    assert 'Which detail is wrong?' in prompt and '4. Family income (yearly)' in prompt
    assert rows('SELECT pdf_status,cancel_reason FROM result_deliveries')[0] == {'pdf_status': 'cancelled',
                                                                                 'cancel_reason': 'change'}
    run()
    wa.files.assert_not_called()                                   # the old guide is never sent
    assert say(wa, '4', 'm-field') == 'change_field'
    assert 'earn in a year' in texts(wa)[-1]
    assert say(wa, '90000', 'm-value') == 'review_requested'
    assert 'Before I send scheme matches' in texts(wa)[-1]
    reviews = rows('SELECT version,status FROM result_reviews ORDER BY version')
    assert reviews == [{'version': 1, 'status': 'superseded'}, {'version': 2, 'status': 'awaiting'}]
    # A tap on the first summary's list can never approve the new facts.
    assert tap(wa, 'review:proceed:1', 'Proceed', 'm-stale') == 'review_requested'
    assert 'older summary' in texts(wa)[-1]
    assert rows("SELECT status FROM result_reviews WHERE version=2")[0]['status'] == 'awaiting'
    assert tap(wa, 'review:proceed:2', 'Proceed', 'm-ok-2') == 'profile_confirmed'
    run()
    wa.files.assert_called_once()
    with case_store.transaction() as tx:
        sid = tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
        assert tx.session(sid, trusted=True)['state']['profile']['annual_family_income_inr'] == 90000


def test_free_text_correction_and_change_after_delivery_needs_new_proceed(wa):
    start(wa)
    say(wa, 'PROCEED', 'm-ok')
    run()
    assert wa.files.call_count == 1
    assert say(wa, 'change', 'm-change') == 'change_requested'
    wa.provider.queue[:] = [{'language': 'en', 'facts': {'annual_family_income_inr': 60000}, 'intent': 'give_info'}]
    assert say(wa, 'Actually our income is 60000', 'm-fix') == 'review_requested'
    run()
    assert wa.files.call_count == 1                                # no new guide without PROCEED
    assert say(wa, '1', 'm-ok-2') == 'profile_confirmed'           # numbered fallback while review is open
    run()
    assert wa.files.call_count == 2


# ---------- 5. HELP ----------

def test_help_is_context_aware_and_never_confirms(wa):
    start(wa)
    assert say(wa, 'HELP', 'm-help') == 'review_help'
    assert 'Nothing has been sent yet' in texts(wa)[-1]
    assert tap(wa, 'review:help:1', 'Help', 'm-help-2') == 'review_help'
    assert rows('SELECT status FROM result_reviews')[0]['status'] == 'awaiting'
    assert rows('SELECT * FROM result_deliveries') == []
    assert not any(SCHEME_NAME in t for t in texts(wa))
    say(wa, 'PROCEED', 'm-ok')
    assert say(wa, 'help', 'm-help-3') == 'review_help'
    assert 'GUIDE' in texts(wa)[-1] and 'official links' in texts(wa)[-1]
    say(wa, 'CHANGE', 'm-change')
    assert say(wa, 'मदद', 'm-help-4') == 'review_help'
    assert 'correct detail' in texts(wa)[-1]


# ---------- 6. STOP ----------

def test_stop_cancels_pending_work_and_keeps_deletion_semantics(wa):
    start(wa)
    say(wa, 'PROCEED', 'm-ok')
    assert say(wa, 'STOP', 'm-stop') == 'stopped'
    run()
    wa.files.assert_not_called()
    d = rows('SELECT pdf_status,address_cipher,snapshot,cancel_reason FROM result_deliveries')[0]
    assert d == {'pdf_status': 'cancelled', 'address_cipher': None, 'snapshot': None, 'cancel_reason': 'stop'}
    assert rows('SELECT status FROM result_reviews')[0]['status'] == 'cancelled'
    assert rows('SELECT * FROM waha_chats') == [] and rows('SELECT * FROM case_sessions') == []
    assert say(wa, 'PROCEED', 'm-after') == 'not_opted_in'


# ---------- 9. EN / HI / MR: lists, numbered fallback, words ----------

@pytest.mark.parametrize('number,lang,proceed_word', [('1', 'en', 'Proceed'), ('2', 'hi', 'आगे बढ़ें'),
                                                      ('3', 'mr', 'पुढे जा')])
def test_review_list_and_fallback_in_three_languages(wa, monkeypatch, number, lang, proceed_word):
    monkeypatch.setenv('WAHA_INTERACTIVE_LISTS', 'true')
    assert start(wa, language=number) == 'review_requested'
    message = wa.lister.call_args.args[-1]
    assert [r['rowId'] for r in message['sections'][0]['rows']] == ['review:proceed:1', 'review:change:1', 'review:help:1']
    assert message['sections'][0]['rows'][0]['title'] == proceed_word
    assert review.LABELS['age'][lang] + ': 62' in message['description']
    assert not any(SCHEME_NAME in str(c.args) for c in wa.lister.call_args_list)
    # The list fails on this phone/engine: the numbered text menu goes instead.
    wa.lister.side_effect = httpx.HTTPStatusError('refused', request=httpx.Request('POST', 'http://waha'),
                                                   response=httpx.Response(500))
    assert say(wa, 'HELP', 'm-help') == 'review_help'
    assert say(wa, 'x', 'm-x') in ('review_requested', 'replied')
    fallback = texts(wa)[-1]
    assert f'1. {proceed_word}' in fallback and review.REVIEW_FOOT[lang] in fallback
    assert say(wa, proceed_word, 'm-ok') == 'profile_confirmed'
    assert review.RESULTS_LEAD[lang] in texts(wa)[-1]


def test_marathi_words_and_numbers(wa):
    start(wa, language='3')
    assert say(wa, '2', 'm-change') == 'change_requested'
    assert review.CHANGE_LEAD['mr'] in texts(wa)[-1]
    assert say(wa, 'मदत', 'm-help') == 'review_help'
    assert review.HELP_CHANGING['mr'] in texts(wa)[-1]


def test_language_change_reshows_open_review(wa):
    start(wa)
    assert say(wa, 'हिंदी', 'm-hi') == 'review_requested'
    assert 'उम्र: 62' in texts(wa)[-1]
    assert rows('SELECT version,status FROM result_reviews') == [{'version': 1, 'status': 'awaiting'}]


def test_review_list_ids_are_accepted_by_the_parser():
    from web import whatsapp_menu as menu
    payload = {'_data': {'message': {'listResponseMessage': {'singleSelectReply': {'selectedRowId': 'review:proceed:12'}}}}}
    assert menu.selected_row_id(payload) == 'review:proceed:12'
    assert menu.split_versioned('review:proceed:12') == ('review:proceed', 12)
    bad = {'_data': {'message': {'listResponseMessage': {'singleSelectReply': {'selectedRowId': 'review:approve-all'}}}}}
    assert menu.selected_row_id(bad) is None
    assert menu.choice('1', 'review') == 'review:proceed' and menu.choice('2', 'call') == 'call:no'
    assert menu.choice('9', 'review') == 'invalid'


def test_review_shows_only_whitelisted_coarse_facts():
    profile = {'age': 62, 'state': 'maharashtra', 'social_category': 'sc', 'annual_family_income_inr': 900000,
               '_intent': {'categories': ['income']}}
    text = review.review_text(profile, 'en')
    assert 'above ₹8 lakh' in text and '900000' not in text
    assert 'sc' not in text.lower().split() and '+1 more detail' in text
    assert review.facts_hash(profile) == review.facts_hash({k: v for k, v in profile.items() if k != '_intent'})
