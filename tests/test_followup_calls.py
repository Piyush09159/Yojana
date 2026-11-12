"""Consented follow-up calls after a delivered shortlist: separate opt-in, trusted numbers only (never
from @lid digits), one call per shortlist across retries and restarts, quiet hours, revocation, Bolna
status webhooks that never touch the profile."""
import time
from unittest.mock import Mock

import httpx
import pytest

from web import bolna, case_store, followup_calls as calls, result_review as review
from test_whatsapp_approval import wa, start, say, tap, texts, rows, run, CHAT, FULL  # noqa: F401
from test_deployment_v24 import persistent  # noqa: F401

SECRET = 'fixture-bolna-webhook-secret-24-chars'
LID = '123456789012345@lid'


def ist_10(base):
    """10:00 IST on the IST day containing `base`."""
    return base - (base + 19800) % 86400 + 36000


FIXED10 = ist_10(1_800_000_000)


def _daytime():
    """A runner clock outside quiet hours (21:00-09:00 IST) and within a few hours of the real
    clock, so consent written with the real time is never older than its 24 h limit."""
    now = time.time()
    minute = ((now + 19800) % 86400) // 60
    if 9 * 60 <= minute < 18 * 60:
        return now + 600          # margin: queued work is due a few seconds after the real clock
    ten = ist_10(now)
    return ten if ten > now else ten + 86400


DAYTIME = _daytime()


@pytest.fixture
def phone(wa, monkeypatch):
    monkeypatch.setenv('BOLNA_FOLLOWUP_ENABLED', 'true')
    monkeypatch.setenv('BOLNA_API_KEY', 'fixture-bolna-key')
    monkeypatch.setenv('BOLNA_FOLLOWUP_AGENT_ID', 'fixture-followup-agent')
    monkeypatch.setenv('BOLNA_WEBHOOK_SECRET', SECRET)
    placed = Mock(return_value='exec-1')
    monkeypatch.setattr(bolna, 'place_call', placed)
    wa.placed = placed
    return wa


def delivered(wa, chat=CHAT, now=None):
    start(wa, chat=chat)
    assert say(wa, 'PROCEED', 'm-ok', chat) == 'profile_confirmed'
    run(now)                   # PDF
    run(now)                   # call offer


def calls_rows():
    return rows('SELECT status,detail,phone_cipher,execution_id FROM followup_calls')


# ---------- consent is separate from PROCEED ----------

def test_proceed_alone_never_schedules_a_call(phone):
    delivered(phone, now=DAYTIME)
    assert rows('SELECT call_offer FROM result_deliveries')[0]['call_offer'] == 'offered'
    offer = texts(phone)[-1]
    for words in ('automated', 'not a government office', 'ending in 3210', 'never ask for Aadhaar, OTP or bank',
                  '1. Call me', '2. No call', 'Nothing is scheduled unless'):
        assert words in offer
    run(DAYTIME + 60)
    assert calls_rows() == [] and rows('SELECT * FROM call_consents') == []
    phone.placed.assert_not_called()


def test_call_me_records_consent_and_places_exactly_one_call(phone):
    delivered(phone, now=DAYTIME)
    assert say(phone, '1', 'm-call') == 'call_scheduled'
    assert 'ending in 3210' in texts(phone)[-1] and 'NO CALL' in texts(phone)[-1]
    consent = rows('SELECT purpose,decision,event_id,revoked_at FROM call_consents')[0]
    assert consent['purpose'] == 'followup_call' and consent['decision'] == 'granted' and consent['event_id']
    row = rows('SELECT due_at,created_at,phone_cipher FROM followup_calls')[0]
    assert 5 <= row['due_at'] - row['created_at'] <= 10
    assert row['phone_cipher'].startswith('sealed:') and '9876543210' not in row['phone_cipher']
    run(row['due_at'] - 1)
    phone.placed.assert_not_called()                     # a durable delayed task, not a sleep
    run(max(row['due_at'], DAYTIME) + 1)
    phone.placed.assert_called_once()
    agent, number, user_data = phone.placed.call_args.args
    assert agent == 'fixture-followup-agent' and number == '+919876543210'
    assert set(user_data) == {'service_name', 'language', 'scheme_count', 'guide_sent'}
    assert calls_rows() == [{'status': 'started', 'detail': None, 'phone_cipher': '', 'execution_id': 'exec-1'}]
    # duplicates: same event again, a second CALL ME, a list tap, another runner pass
    assert say(phone, '1', 'm-call') == 'duplicate'
    assert say(phone, 'call me', 'm-call-2') == 'call_already_started'
    assert tap(phone, 'call:yes:1', 'Call me', 'm-call-3') == 'call_already_started'
    run(DAYTIME + 3600)
    phone.placed.assert_called_once()


def test_no_call_declines_and_cancels(phone):
    delivered(phone, now=DAYTIME)
    assert say(phone, 'no call', 'm-no') == 'call_declined'
    assert say(phone, 'call me', 'm-yes') == 'call_unavailable'        # a decline is final for this shortlist
    run(DAYTIME + 60)
    phone.placed.assert_not_called()


def test_revocation_before_dialing_cancels_the_call(phone):
    delivered(phone, now=DAYTIME)
    assert say(phone, 'CALL ME', 'm-yes') == 'call_scheduled'
    assert say(phone, 'NO CALL', 'm-no') == 'call_cancelled'
    assert rows('SELECT revoked_at IS NOT NULL AS revoked FROM call_consents')[0]['revoked'] == 1
    run(DAYTIME + 3600)
    phone.placed.assert_not_called()
    assert calls_rows()[0]['status'] == 'cancelled' and calls_rows()[0]['phone_cipher'] == ''


def test_stop_and_change_cancel_a_queued_call(phone):
    delivered(phone, now=DAYTIME)
    say(phone, 'CALL ME', 'm-yes')
    assert say(phone, 'CHANGE', 'm-change') == 'change_requested'
    run(DAYTIME + 3600)
    phone.placed.assert_not_called()
    assert calls_rows()[0]['status'] == 'cancelled'
    assert say(phone, 'STOP', 'm-stop') == 'stopped'
    assert rows("SELECT COUNT(*) AS n FROM followup_calls WHERE phone_cipher!=''")[0]['n'] == 0


# ---------- 10. @lid and trusted numbers ----------

def lids(monkeypatch, response):
    """Stub WAHA GET /api/{session}/lids/{lid}. response: httpx.Response or an exception."""
    from web import lid_resolver
    get = Mock(side_effect=response if isinstance(response, BaseException) else None,
               return_value=None if isinstance(response, BaseException) else response)
    monkeypatch.setattr(lid_resolver.httpx, 'get', get)
    return get


def lid_reply(pn, lid=LID, status=200):
    return httpx.Response(status, json={'lid': lid, 'pn': pn}, request=httpx.Request('GET', 'http://waha:3000/x'))


def test_lid_without_mapping_is_skipped_safely(phone, monkeypatch):
    get = lids(monkeypatch, lid_reply(None))
    delivered(phone, chat=LID, now=DAYTIME)
    assert rows('SELECT call_offer,call_phone_cipher FROM result_deliveries')[0] == {'call_offer': 'unavailable', 'call_phone_cipher': None}
    assert get.call_args.args == (f'http://waha:3000/api/default/lids/{LID}',)
    assert get.call_args.kwargs['headers'] == {'X-Api-Key': 'fixture-api-key'}
    assert say(phone, 'call me', 'm-yes', LID) == 'call_unavailable'
    run(DAYTIME + 60)
    phone.placed.assert_not_called()
    assert calls_rows() == []


def test_lid_with_waha_mapping_needs_separate_call_me_consent(phone, monkeypatch):
    lids(monkeypatch, lid_reply('919876543210@c.us'))
    delivered(phone, chat=LID, now=DAYTIME)
    assert rows('SELECT call_offer FROM result_deliveries')[0]['call_offer'] == 'offered'
    cipher = rows('SELECT call_phone_cipher FROM result_deliveries')[0]['call_phone_cipher']
    assert cipher.startswith('sealed:') and '9876543210' not in cipher and '900000' not in cipher
    assert 'ending in 3210' in texts(phone)[-1]
    run(DAYTIME + 60)                                      # PROCEED alone never dials
    phone.placed.assert_not_called()
    assert rows('SELECT * FROM call_consents') == [] and calls_rows() == []
    assert say(phone, 'call me', 'm-yes', LID) == 'call_scheduled'
    assert rows('SELECT call_phone_cipher FROM result_deliveries')[0]['call_phone_cipher'] is None
    run(DAYTIME + 3600)
    phone.placed.assert_called_once()
    assert phone.placed.call_args.args[1] == '+919876543210'
    assert say(phone, 'call me', 'm-yes2', LID) == 'call_already_started'
    run(DAYTIME + 7200)
    phone.placed.assert_called_once()


def test_lid_call_can_be_cancelled_and_stop_scrubs(phone, monkeypatch):
    lids(monkeypatch, lid_reply('919876543210@c.us'))
    delivered(phone, chat=LID, now=DAYTIME)
    assert say(phone, 'no call', 'm-no', LID) == 'call_declined'
    assert rows('SELECT call_phone_cipher FROM result_deliveries')[0]['call_phone_cipher'] is None
    phone.placed.assert_not_called()


def test_stop_before_consent_scrubs_resolved_number(phone, monkeypatch):
    lids(monkeypatch, lid_reply('919876543210@c.us'))
    delivered(phone, chat=LID, now=DAYTIME)
    assert say(phone, 'STOP', 'm-stop', LID) == 'stopped'
    assert rows("SELECT COUNT(*) AS n FROM result_deliveries WHERE call_phone_cipher IS NOT NULL")[0]['n'] == 0
    assert say(phone, 'call me', 'm-yes', LID) != 'call_scheduled'
    phone.placed.assert_not_called()


@pytest.mark.parametrize('pn', ['900000000000001@c.us', '14155550100@c.us', '919876543210@lid', '+919876543210',
                                '919876543210@g.us', 12345, {'x': 1}])
def test_lid_mapping_must_be_an_allowed_phone_jid(phone, monkeypatch, pn):
    lids(monkeypatch, lid_reply(pn))
    delivered(phone, chat=LID, now=DAYTIME)
    assert rows('SELECT call_offer,call_phone_cipher FROM result_deliveries')[0] == {'call_offer': 'unavailable', 'call_phone_cipher': None}
    assert say(phone, 'call me', 'm-yes', LID) == 'call_unavailable'
    phone.placed.assert_not_called()


def test_lid_mapping_for_a_different_lid_is_rejected(phone, monkeypatch):
    lids(monkeypatch, lid_reply('919876543210@c.us', lid='111111111111111@lid'))
    delivered(phone, chat=LID, now=DAYTIME)
    assert rows('SELECT call_offer FROM result_deliveries')[0]['call_offer'] == 'unavailable'


def test_lid_lookup_errors_retry_then_give_up(phone, monkeypatch):
    get = lids(monkeypatch, httpx.ConnectError('down'))
    delivered(phone, chat=LID, now=DAYTIME)
    assert rows('SELECT call_offer,call_offer_attempts FROM result_deliveries')[0] == {'call_offer': 'pending', 'call_offer_attempts': 2}
    run(DAYTIME + 1)
    assert rows('SELECT call_offer FROM result_deliveries')[0]['call_offer'] == 'unavailable' and get.call_count == 3
    lids(monkeypatch, lid_reply('919876543210@c.us', status=500))
    from web import lid_resolver
    assert lid_resolver.resolve('http://waha:3000', 'k', 'default', LID) == ('error', None)
    lids(monkeypatch, lid_reply(None, status=404))
    assert lid_resolver.resolve('http://waha:3000', 'k', 'default', LID) == ('unmapped', None)


def test_lid_lookup_never_runs_for_phone_jid_or_bad_ids(phone, monkeypatch):
    get = lids(monkeypatch, lid_reply('919876543210@c.us'))
    delivered(phone, now=DAYTIME)
    get.assert_not_called()
    from web import lid_resolver
    for bad in ('abc@lid', '../x@lid', '9876543210@c.us', ''):
        assert lid_resolver.resolve('http://waha:3000', 'k', 'default', bad) == ('invalid', None)
    assert lid_resolver.resolve('http://waha:3000', 'k', '../x', LID) == ('invalid', None)
    get.assert_not_called()


@pytest.mark.parametrize('chat,expected', [
    ('919876543210@c.us', '+919876543210'), ('123456789012345@lid', None), ('9876543210@c.us', None),
    ('14155550100@c.us', None), ('91987654321@c.us', None), ('919876543210@g.us', None), ('', None),
])
def test_trusted_phone_only_from_phone_jids(chat, expected):
    assert calls.trusted_phone(chat) == expected


def test_country_allow_list_is_configurable(monkeypatch):
    monkeypatch.setenv('BOLNA_ALLOWED_COUNTRY_CODES', '91,1')
    assert calls.trusted_phone('14155550100@c.us') == '+14155550100'


# ---------- 11. quiet hours, flags, restarts ----------

def test_quiet_hours_defer_then_call(phone):
    delivered(phone, now=DAYTIME)
    assert say(phone, 'call me', 'm-yes') == 'call_scheduled'
    night = ist_10(DAYTIME) + 12 * 3600                    # 22:00 IST
    with case_store.transaction() as tx:                   # consent given just before 22:00
        tx.execute('UPDATE call_consents SET created_at=?', (night - 30,))
        tx.execute('UPDATE followup_calls SET due_at=?', (night - 10,))
    run(night + 60)
    phone.placed.assert_not_called()
    due = rows('SELECT due_at,detail FROM followup_calls')[0]
    assert due['detail'] == 'deferred_quiet_hours' and calls.quiet(due['due_at'] - 1) and not calls.quiet(due['due_at'])
    run(due['due_at'] + 1)
    phone.placed.assert_called_once()


def test_quiet_hours_window():
    assert calls.quiet(FIXED10 + 12 * 3600) and calls.quiet(FIXED10 - 2 * 3600)
    assert not calls.quiet(FIXED10) and not calls.quiet(FIXED10 + 10 * 3600)
    assert calls.next_open(FIXED10 + 12 * 3600) == FIXED10 + 23 * 3600      # 09:00 IST next day


def test_flag_off_at_dial_time_skips(phone, monkeypatch):
    delivered(phone, now=DAYTIME)
    say(phone, 'call me', 'm-yes')
    monkeypatch.setenv('BOLNA_FOLLOWUP_ENABLED', 'false')
    run(DAYTIME + 60)
    phone.placed.assert_not_called()
    assert calls_rows()[0]['status'] == 'skipped' and calls_rows()[0]['detail'] == 'disabled'


def test_flag_off_means_no_offer(wa, monkeypatch):
    monkeypatch.setenv('BOLNA_FOLLOWUP_ENABLED', 'false')
    delivered(wa, now=DAYTIME)
    assert rows('SELECT call_offer FROM result_deliveries')[0]['call_offer'] == 'disabled'
    assert say(wa, 'call me', 'm-yes') == 'call_unavailable'


def test_interrupted_dial_is_never_retried(phone):
    delivered(phone, now=DAYTIME)
    say(phone, 'call me', 'm-yes')
    with case_store.transaction() as tx:     # worker died after committing 'dialing'
        tx.execute("UPDATE followup_calls SET status='dialing',lease_until=?", (DAYTIME - 1,))
    run(DAYTIME + 60)
    run(DAYTIME + 120)
    phone.placed.assert_not_called()
    assert calls_rows()[0]['status'] == 'failed' and calls_rows()[0]['detail'] == 'interrupted'
    assert calls_rows()[0]['phone_cipher'] == ''


def test_ambiguous_timeout_is_not_retried_but_refusal_is(phone):
    delivered(phone, now=DAYTIME)
    say(phone, 'call me', 'm-yes')
    phone.placed.side_effect = bolna.BolnaError('ReadTimeout')
    run(DAYTIME + 60)
    run(DAYTIME + 600)
    assert phone.placed.call_count == 1 and calls_rows()[0]['status'] == 'failed'


def test_connect_refusal_retries_at_most_twice(phone):
    delivered(phone, now=DAYTIME)
    say(phone, 'call me', 'm-yes')
    phone.placed.side_effect = bolna.BolnaError('connect_error', retryable=True)
    for step in range(6):
        run(DAYTIME + 60 + step * 40)
    assert phone.placed.call_count == 3 and calls_rows()[0]['status'] == 'failed'


# ---------- 12. Bolna webhooks ----------

def hook(client, execution_id='exec-1', secret=SECRET):
    return client.post(f'/api/bolna/followup/webhook/{secret}', json={'id': execution_id})


def started(phone, monkeypatch, status):
    delivered(phone, now=DAYTIME)
    say(phone, 'call me', 'm-yes')
    run(DAYTIME + 60)
    lookup = Mock(return_value={'id': 'exec-1', 'status': status, 'extracted_data': {'age': 20},
                                'transcript': 'secret talk'})
    monkeypatch.setattr(bolna, 'get_execution', lookup)
    return lookup


@pytest.mark.parametrize('provider,ours', [('no-answer', 'no_answer'), ('busy', 'no_answer'), ('failed', 'failed'),
                                           ('error', 'failed'), ('completed', 'completed'),
                                           ('call-disconnected', 'completed')])
def test_status_webhook_maps_terminal_statuses(phone, monkeypatch, provider, ours):
    started(phone, monkeypatch, provider)
    assert hook(phone.client).json()['status'] == ours
    assert calls_rows()[0]['status'] == ours


def test_in_progress_then_late_webhook_after_final(phone, monkeypatch):
    lookup = started(phone, monkeypatch, 'ringing')
    assert hook(phone.client).json()['status'] == 'in_progress'
    lookup.return_value = {'id': 'exec-1', 'status': 'no-answer'}
    assert hook(phone.client).json()['status'] == 'no_answer'
    lookup.return_value = {'id': 'exec-1', 'status': 'completed'}
    assert hook(phone.client).json()['status'] == 'duplicate'          # late: no overwrite
    assert calls_rows()[0]['status'] == 'no_answer'
    run(DAYTIME + 7200)
    phone.placed.assert_called_once()                                   # no-answer never redials


def test_completed_call_never_changes_the_profile(phone, monkeypatch):
    started(phone, monkeypatch, 'completed')
    assert hook(phone.client).json()['status'] == 'completed'
    with case_store.transaction() as tx:
        sid = tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
        state = tx.session(sid, trusted=True)['state']
    assert state['profile']['age'] == FULL['age'] and 'verification' not in state
    stored = rows('SELECT * FROM followup_calls')[0]
    assert 'secret talk' not in str(stored)


def test_webhook_rejects_bad_secret_and_ignores_unknown_ids(phone, monkeypatch):
    lookup = Mock()
    monkeypatch.setattr(bolna, 'get_execution', lookup)
    assert hook(phone.client, secret='wrong-secret-wrong-secret-wrong').status_code == 403
    assert hook(phone.client, execution_id='unknown').json()['status'] == 'ignored'
    lookup.assert_not_called()


def test_bolna_unavailable_does_not_break_whatsapp_help(phone, monkeypatch):
    delivered(phone, now=DAYTIME)
    say(phone, 'call me', 'm-yes')
    phone.placed.side_effect = bolna.BolnaError('http_error', status=503, retryable=True)
    run(DAYTIME + 60)
    assert say(phone, 'HELP', 'm-help') == 'review_help'
    assert 'GUIDE' in texts(phone)[-1]


# ---------- the Bolna HTTP adapter ----------

def test_place_call_body_and_error_classes(monkeypatch):
    monkeypatch.setenv('BOLNA_API_KEY', 'k')
    seen = {}

    def ok(url, headers, json, timeout):
        seen.update(url=url, headers=headers, json=json)
        return httpx.Response(200, json={'message': 'done', 'status': 'queued', 'execution_id': 'e-1'},
                              request=httpx.Request('POST', url))
    monkeypatch.setattr(bolna.httpx, 'post', ok)
    assert bolna.place_call('agent', '+919876543210', {'language': 'en'}) == 'e-1'
    assert seen['url'] == 'https://api.bolna.ai/call' and seen['headers'] == {'Authorization': 'Bearer k'}
    assert seen['json']['retry_config'] == {'enabled': False}
    assert seen['json']['recipient_phone_number'] == '+919876543210'
    for response, retryable in ((httpx.Response(503), True), (httpx.Response(429), True), (httpx.Response(400), False)):
        monkeypatch.setattr(bolna.httpx, 'post', lambda *a, r=response, **k: httpx.Response(
            r.status_code, request=httpx.Request('POST', 'https://api.bolna.ai/call')))
        with pytest.raises(bolna.BolnaError) as err:
            bolna.place_call('agent', '+919876543210', {})
        assert err.value.retryable is retryable

    def timeout(*a, **k):
        raise httpx.ReadTimeout('slow')
    monkeypatch.setattr(bolna.httpx, 'post', timeout)
    with pytest.raises(bolna.BolnaError) as err:
        bolna.place_call('agent', '+919876543210', {})
    assert err.value.retryable is False


def test_outcome_mapping():
    assert bolna.outcome('ringing') is None and bolna.outcome('in-progress') is None
    assert bolna.outcome('balance-low') == 'failed' and bolna.outcome('canceled') == 'failed'


def test_voice_note_saying_call_me_is_not_consent(phone, monkeypatch):
    from web import waha_bridge as waha, waha_media as media
    delivered(phone, now=DAYTIME)
    monkeypatch.setattr(media, 'transcribe', Mock(return_value={'text': 'call me', 'language': 'en'}))
    event = waha.WAHAEvent(event='message', session='default', payload={
        'from': CHAT, 'id': 'voice-1', 'body': '', 'hasMedia': True,
        'media': {'url': 'http://waha:3000/api/files/voice.ogg', 'mimetype': 'audio/ogg; codecs=opus'}})
    assert waha.persistent_event(event)['status'] != 'call_scheduled'
    assert rows('SELECT * FROM call_consents') == [] and calls_rows() == []
