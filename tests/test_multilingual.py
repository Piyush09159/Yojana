"""Multilingual conversation: preferred language vs turn language, explicit switches at any point,
code-mixed text, voice notes followed by text, read-back of uncertain spoken numbers, and the
browser voice page's language change, STT/TTS settings and handoff language. No network."""
import base64
import json
import logging
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from core.language_detect import detect, switch_request, mentions_number
from llm.providers.mock_provider import MockProvider
from web import case_store, speech, voice as voice_api, waha_bridge as waha, waha_media as media, conversation_api as api
from web.app import create_app
from test_deployment_v24 import persistent, configure_waha, signed, mock_extraction  # noqa: F401
from test_whatsapp_final import note
from test_whatsapp_menus import wa, case_state, menu_row, last_text  # noqa: F401
from test_citizen_v25 import linked_call, pcm


# ---------- detection units ----------

@pytest.mark.parametrize('text,lang,sure', [
    ('I am 62 years old and need a pension', 'en', True),
    ('मेरी उम्र 62 साल है', 'hi', True),
    ('मी 62 वर्षांचा आहे', 'mr', True),
    ('मुझे pension चाहिए, मैं farmer हूँ', 'hi', True),           # Hindi-English code-mixed
    ('मला pension पाहिजे, मी farmer आहे', 'mr', True),            # Marathi-English code-mixed
    ('mujhe pension chahiye', 'hi', True),                       # romanised Hindi
    ('mala pension pahije aahe', 'mr', True),                    # romanised Marathi
    ('I am kisan, mujhe pension chahiye', 'en', False),          # evenly split: not sure
    ('ok', 'en', False), ('62', 'en', False), ('होय', 'mr', True), ('हाँ', 'hi', True),
])
def test_turn_language_detection(text, lang, sure):
    assert detect(text) == (lang, sure)


@pytest.mark.parametrize('text,lang', [
    ('Hindi', 'hi'), ('मराठी', 'mr'), ('English please', 'en'), ('speak in Marathi', 'mr'),
    ('reply in hindi', 'hi'), ('हिंदी में बात करो', 'hi'), ('मराठीत बोला', 'mr'), ('मराठी मध्ये बोला', 'mr'),
    ('hindi me baat karo', 'hi'), ('change language to english', 'en'), ('भाषा मराठी', 'mr'),
    # answers that only mention a language are not switches
    ('Marathi medium school', None), ('मी मराठी आहे', None), ('I speak Hindi at home and work in a farm', None),
    ('my son studies English', None), ('62', None), ('', None)])
def test_switch_request_only_matches_whole_requests(text, lang):
    assert switch_request(text) == lang


def test_number_mentions():
    assert mentions_number('मी 62 वर्षांचा') and mentions_number('साठ हजार') and mentions_number('दोन एकर')
    assert mentions_number('१२ वर्ष') and not mentions_number('मला पेन्शन हवी आहे')


# ---------- WhatsApp: switching keeps the case ----------

def spy_conversation(monkeypatch):
    seen = []
    original = waha.conversation

    def spy(message, **kw):
        seen.append((message.text, message.lang))
        return original(message, **kw)
    monkeypatch.setattr(waha, 'conversation', spy)
    return seen


def test_english_to_marathi_to_hindi_keeps_profile_question_and_case(wa, monkeypatch):
    client, sender, _ = wa
    mock_extraction(monkeypatch, {'age': 62, 'state': 'maharashtra'})
    signed(client, 'YOJANA', 'a'); signed(client, '1', 'en')
    assert signed(client, 'I am 62 and live in Maharashtra', 'facts').json()['status'] == 'replied'
    sid, start = case_state()
    assert start['pending']
    for word, lang, needle in (('मराठीत बोला', 'mr', 'ठीक आहे'), ('हिंदी में बात करो', 'hi', 'ठीक है'),
                               ('speak in English', 'en', 'Okay')):
        assert signed(client, word, 'switch-' + lang).json()['status'] == 'language_selected'
        assert needle in last_text(sender)
        sid_now, state = case_state()
        assert sid_now == sid and state['profile'] == start['profile'] and state['pending'] == start['pending']
        assert state['question_count'] == start['question_count'] and state['preferred_lang'] == lang
        assert menu_row()['preferred_language'] == lang


def test_marathi_reply_language_follows_preference_and_hints_switch_once(wa, monkeypatch):
    client, sender, _ = wa
    seen = spy_conversation(monkeypatch)
    signed(client, 'YOJANA', 'a'); signed(client, '3', 'mr')
    mock_extraction(monkeypatch)
    signed(client, 'I am 62 years old', 'en1')
    assert seen[-1][1] == 'mr' and 'ENGLISH' in last_text(sender)
    mock_extraction(monkeypatch)
    signed(client, 'I need a pension for my family', 'en2')
    assert seen[-1][1] == 'mr' and 'ENGLISH' not in last_text(sender)      # hinted once only


@pytest.mark.parametrize('text,lang', [('मुझे pension चाहिए, मैं farmer हूँ', 'hi'),
                                       ('मला pension पाहिजे, मी farmer आहे', 'mr'),
                                       ('I am a widow and need help', 'en')])
def test_code_mixed_text_without_preference_replies_in_that_language(wa, monkeypatch, text, lang):
    client, sender, _ = wa
    seen = spy_conversation(monkeypatch)
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a')
    assert signed(client, text, 'mix').json()['status'] == 'replied'
    assert seen[-1][1] == lang


def test_uncertain_language_keeps_previous(wa, monkeypatch):
    client, sender, _ = wa
    seen = spy_conversation(monkeypatch)
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a'); signed(client, 'मला मदत हवी आहे', 'mr-first')
    mock_extraction(monkeypatch)
    signed(client, 'ok', 'unsure')
    assert [lang for _, lang in seen] == ['mr', 'mr']


def test_marathi_voice_note_then_english_text(wa, monkeypatch):
    client, sender, _ = wa
    seen = spy_conversation(monkeypatch)
    signed(client, 'YOJANA', 'a')
    mock_extraction(monkeypatch)
    monkeypatch.setattr(media, 'transcribe', Mock(return_value={'text': 'मला पेन्शन हवी आहे', 'language': 'mr', 'confidence': 0.7}))
    assert waha.persistent_event(note('v-mr'))['status'] == 'replied'
    mock_extraction(monkeypatch)
    assert signed(client, 'I also have a ration card', 'en-after').json()['status'] == 'replied'
    assert seen == [('मला पेन्शन हवी आहे', 'mr'), ('I also have a ration card', 'en')]


def test_spoken_switch_request_switches(wa, monkeypatch):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a')
    monkeypatch.setattr(media, 'transcribe', Mock(return_value={'text': 'हिंदी में बात करो', 'language': 'hi', 'confidence': 0.9}))
    assert waha.persistent_event(note('v-switch'))['status'] == 'language_selected'
    assert menu_row()['preferred_language'] == 'hi'


def test_language_mention_inside_an_answer_is_not_a_switch(wa, monkeypatch):
    client, sender, _ = wa
    seen = spy_conversation(monkeypatch)
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a'); signed(client, '1', 'en')
    assert signed(client, 'My daughter studies in a Marathi medium school', 'answer').json()['status'] == 'replied'
    assert menu_row()['preferred_language'] == 'en' and seen[-1][1] == 'en'


def test_unknown_and_repeated_messages_get_replies(wa, monkeypatch):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a'); signed(client, '1', 'en')
    for i in range(2):
        mock_extraction(monkeypatch, {})
        assert signed(client, 'hmm', f'unknown-{i}').json()['status'] == 'replied'
    assert sender.call_count == 4


# ---------- read-back of uncertain spoken numbers ----------

def test_marathi_note_with_number_is_read_back_then_used_on_yes(wa, monkeypatch, caplog):
    client, sender, _ = wa
    seen = spy_conversation(monkeypatch)
    signed(client, 'YOJANA', 'a')
    transcriber = Mock(return_value={'text': 'मी 62 वर्षांचा आहे', 'language': 'mr', 'confidence': 0.9})
    monkeypatch.setattr(media, 'transcribe', transcriber)
    with caplog.at_level(logging.DEBUG):
        assert waha.persistent_event(note('v1'))['status'] == 'voice_confirm'
    assert 'मी ऐकले: “मी 62 वर्षांचा आहे”' in last_text(sender) and seen == []
    assert 'VOICE_CONFIRMATION_REQUESTED' in caplog.text and 'वर्षांचा' not in caplog.text and 'मी ऐकले' not in caplog.text
    with case_store.transaction() as tx:   # transcript sealed at rest, never stored as plain text
        stored = tx.execute("SELECT reply FROM waha_events WHERE result='voice_confirm'").fetchone()['reply']
        pending = tx.execute('SELECT pending_voice FROM channel_menus').fetchone()['pending_voice']
        assert stored.startswith('sealed:') and pending.startswith('sealed:')
        assert 'वर्षांचा' not in stored + pending                 # ciphertext only
    assert waha.persistent_event(note('v1'))['status'] == 'duplicate' and transcriber.call_count == 1
    mock_extraction(monkeypatch, {'age': 62})
    assert signed(client, 'होय', 'yes').json()['status'] == 'replied'
    assert seen == [('मी 62 वर्षांचा आहे', 'mr')]
    assert menu_row()['pending_voice'] is None


def test_read_back_rejected_or_replaced(wa, monkeypatch):
    client, sender, _ = wa
    seen = spy_conversation(monkeypatch)
    signed(client, 'YOJANA', 'a')
    monkeypatch.setattr(media, 'transcribe', Mock(return_value={'text': 'मी 26 वर्षांचा आहे', 'language': 'mr'}))
    assert waha.persistent_event(note('v1'))['status'] == 'voice_confirm'
    assert signed(client, 'नाही', 'no').json()['status'] == 'voice_rejected'
    assert waha.persistent_event(note('v2'))['status'] == 'voice_confirm'
    mock_extraction(monkeypatch, {'age': 62})
    assert signed(client, 'मी 62 वर्षांचा आहे', 'typed').json()['status'] == 'replied'
    assert seen == [('मी 62 वर्षांचा आहे', 'mr')]          # the typed text, not the misheard note


@pytest.mark.parametrize('result,status', [
    ({'text': 'मेरी उम्र 62 साल है', 'language': 'hi', 'confidence': 0.64}, 'voice_confirm'),   # unsure Hindi number
    ({'text': 'मेरी उम्र 62 साल है', 'language': 'hi', 'confidence': 0.92}, 'replied'),
    ({'text': 'I am 62 years old', 'language': 'en', 'confidence': 0.95}, 'replied'),
    ({'text': 'मला पेन्शन हवी आहे', 'language': 'mr', 'confidence': 0.5}, 'replied'),        # no number: no read-back
])
def test_read_back_only_for_uncertain_numbers(wa, monkeypatch, result, status):
    client, sender, _ = wa
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a')
    monkeypatch.setattr(media, 'transcribe', Mock(return_value=result))
    assert waha.persistent_event(note('v'))['status'] == status


def test_stt_timeout_gets_localized_reply_without_sarvam(wa, monkeypatch):
    from web.waha_media import VoiceFailure
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a'); signed(client, '2', 'hi')
    monkeypatch.setattr(media, 'transcribe', Mock(side_effect=VoiceFailure('LOCAL_STT_FAILED', 'speech_timeout', 504)))
    assert waha.persistent_event(note('t'))['status'] == 'transcription_failed'
    assert 'वॉइस नोट उपलब्ध नहीं' in last_text(sender)


# ---------- the shared conversation API (website / browser voice) ----------

def test_conversation_api_switch_keeps_case_without_model_call(persistent, monkeypatch):  # noqa: F811
    client = TestClient(create_app())
    mock_extraction(monkeypatch, {'age': 62, 'state': 'maharashtra'})
    first = client.post('/api/v2/conversation', json={'text': 'I am 62 in Maharashtra'}).json()
    monkeypatch.setattr(api, 'get_llm', lambda _: MockProvider(queue=[]))   # any model call would fail
    switched = client.post('/api/v2/conversation', json={'text': 'मराठीत बोला', 'session_id': first['session_id']}).json()
    assert switched['language_switched'] == 'mr' and switched['language'] == 'mr'
    assert switched['profile'] == first['profile'] and switched['session_id'] == first['session_id']
    assert switched['message'].startswith('ठीक आहे') and switched['speech_language'] == 'mr'
    mock_extraction(monkeypatch, {'is_bpl': False})
    after = client.post('/api/v2/conversation', json={'text': 'I am not BPL', 'session_id': first['session_id']}).json()
    assert after['language'] == 'mr' and after['preferred_language'] == 'mr' and after['turn_language'] == 'en'


# ---------- browser voice ----------

def test_voice_language_change_mid_session(persistent, monkeypatch):  # noqa: F811
    channel, browser, sender, sid, cid, _ = linked_call(monkeypatch)
    mock_extraction(monkeypatch)
    turn = browser.post(f'/api/voice/{cid}/turn', json={'text': 'I am 62 in Maharashtra', 'request_id': 'turn-0001'}).json()
    body = {'lang': 'hi', 'request_id': 'lang-0001'}
    changed = browser.post(f'/api/voice/{cid}/language', json=body)
    assert changed.status_code == 200 and changed.json()['language'] == 'hi'
    assert changed.json()['message'].startswith('ठीक है')
    assert browser.post(f'/api/voice/{cid}/language', json=body).json() == changed.json()       # idempotent
    with case_store.transaction() as tx:
        state = tx.session(sid, trusted=True)['state']
        assert state['lang'] == state['preferred_lang'] == 'hi' and state['profile'] == turn['profile']
    history = browser.get(f'/api/voice/{cid}').json()
    assert history['language'] == 'hi' and history['status'] == 'active'
    monkeypatch.setattr(speech, 'request', lambda *a, **k: {'audios': [base64.b64encode(pcm()).decode()]})
    monkeypatch.setenv('SARVAM_API_KEY', 'fixture')
    spoken = browser.post('/api/voice/tts', json={'call_id': cid, 'text': changed.json()['spoken_text'], 'lang': 'hi-IN',
                                                   'request_id': 'lang-0001'})
    assert spoken.status_code == 200                                    # the new prompt may be spoken
    stale = browser.post('/api/voice/tts', json={'call_id': cid, 'text': changed.json()['spoken_text'], 'lang': 'hi-IN',
                                                  'request_id': 'turn-0001'})
    assert stale.status_code == 409                                     # an older reply is refused
    assert browser.post(f'/api/voice/{cid}/language', json={'lang': 'ta', 'request_id': 'lang-0002'}).status_code == 422
    other = TestClient(create_app())
    assert other.post(f'/api/voice/{cid}/language', json={'lang': 'mr', 'request_id': 'lang-0003'}).status_code in (401, 403, 404)


def test_handoff_opens_in_whatsapp_language(persistent, monkeypatch):  # noqa: F811
    configure_waha(monkeypatch)
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN', 'https://frontend.invalid')
    sender = Mock(); monkeypatch.setattr(waha, '_send', sender)
    client = TestClient(create_app())
    signed(client, 'YOJANA', 'a'); signed(client, '3', 'mr'); signed(client, '2', 'call')
    token = sender.call_args.args[-1].split('#handoff=')[1]
    browser = TestClient(create_app())
    redeemed = browser.post('/api/voice/redeem', json={'token': token}).json()
    assert redeemed['language'] == 'mr'
    assert browser.post('/api/voice/redeem', json={'token': token}).status_code == 410           # one use


def test_capabilities_separate_stt_and_tts(monkeypatch):
    client = TestClient(create_app())
    for key in ('TTS_PROVIDER', 'BROWSER_STT_MODE', 'SARVAM_API_KEY', 'SARVAM_STT_ENABLED'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('STT_PROVIDER', 'local'); monkeypatch.setenv('LOCAL_STT_URL', 'http://yojana-stt:8001')
    caps = client.get('/api/voice/capabilities').json()
    assert caps['stt_provider'] == 'local' and caps['browser_stt'] == 'server'
    assert caps['tts_provider'] == 'browser' and caps['tts_configured'] is False
    monkeypatch.setenv('SARVAM_API_KEY', 'k'); monkeypatch.setenv('BROWSER_STT_MODE', 'device')
    caps = client.get('/api/voice/capabilities').json()
    assert caps['tts_provider'] == 'sarvam' and caps['tts_configured'] is True and caps['browser_stt'] == 'device'
    monkeypatch.setenv('TTS_PROVIDER', 'off')
    assert client.get('/api/voice/capabilities').json()['tts_configured'] is False
    with pytest.raises(Exception) as error:
        speech.tts('hello', 'en')
    assert error.value.status_code == 503 and error.value.detail['code'] == 'tts_not_enabled'


def test_tts_unavailable_is_reported_not_crashing(persistent, monkeypatch):  # noqa: F811
    channel, browser, sender, sid, cid, _ = linked_call(monkeypatch)
    mock_extraction(monkeypatch)
    reply = browser.post(f'/api/voice/{cid}/turn', json={'text': 'I am 62', 'request_id': 'turn-tts-1'}).json()
    monkeypatch.setenv('TTS_PROVIDER', 'browser')
    r = browser.post('/api/voice/tts', json={'call_id': cid, 'text': reply['spoken_text'], 'lang': 'en-IN'})
    assert r.status_code == 503 and r.json()['detail']['fallback'] == 'device_voice_or_text'


def test_browser_stt_flags_uncertain_numbers(persistent, monkeypatch):  # noqa: F811
    channel, browser, sender, sid, cid, _ = linked_call(monkeypatch)
    monkeypatch.setenv('STT_PROVIDER', 'local'); monkeypatch.setenv('LOCAL_STT_URL', 'http://yojana-stt:8001')
    answers = iter([{'text': 'मी 62 वर्षांचा आहे', 'language': 'mr', 'confidence': 0.9, 'no_speech': False},
                    {'text': 'I am 62', 'language': 'en', 'confidence': 0.95, 'no_speech': False}])
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=next(answers))), **kw))
    audio = base64.b64encode(pcm()).decode()
    first = browser.post('/api/voice/stt', json={'call_id': cid, 'audio': audio, 'lang': 'mr'}).json()
    assert first['needs_confirmation'] is True and first['language'] == 'mr'
    second = browser.post('/api/voice/stt', json={'call_id': cid, 'audio': audio, 'lang': 'auto'}).json()
    assert second['needs_confirmation'] is False


def test_stt_failure_in_browser_maps_to_typed_fallback(persistent, monkeypatch):  # noqa: F811
    channel, browser, sender, sid, cid, _ = linked_call(monkeypatch)
    monkeypatch.setenv('STT_PROVIDER', 'local'); monkeypatch.setenv('LOCAL_STT_URL', 'http://yojana-stt:8001')
    original = httpx.Client

    def slow(request):
        raise httpx.ReadTimeout('synthetic', request=request)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(slow), **kw))
    r = browser.post('/api/voice/stt', json={'call_id': cid, 'audio': base64.b64encode(pcm()).decode()})
    assert r.status_code == 504 and r.json()['detail'] == {'code': 'speech_timeout', 'fallback': 'type'}


def test_voice_turn_retry_after_reconnect_is_idempotent(persistent, monkeypatch):  # noqa: F811
    channel, browser, sender, sid, cid, _ = linked_call(monkeypatch)
    mock_extraction(monkeypatch)
    body = {'text': 'I am 62 in Maharashtra', 'request_id': 'turn-reconnect-1'}
    first = browser.post(f'/api/voice/{cid}/turn', json=body).json()
    monkeypatch.setattr(api, 'get_llm', lambda _: MockProvider(queue=[]))   # a retry must not call the model
    again = browser.post(f'/api/voice/{cid}/turn', json=body).json()
    assert again['message'] == first['message']
    conflict = browser.post(f'/api/voice/{cid}/turn', json={**body, 'text': 'something else'})
    assert conflict.status_code == 409
