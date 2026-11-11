"""WhatsApp voice notes end to end through the signed webhook, with WAHA and Sarvam mocked at the
HTTP layer (httpx.MockTransport). Covers the "voice note gets no reply" causes found in the
2026-11-11 audit: body null, session-nested /api/files paths, WAHA advertising another origin,
media.url null, inline base64, STT off, provider failures, overlong notes and retried turns."""
import base64
import hashlib
import hmac
import json
import os
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from web.app import create_app
from web import case_store, speech, waha_bridge as waha, waha_media as media
from test_deployment_v24 import persistent, configure_waha, signed, mock_extraction  # noqa: F401

CHAT = '900000000000@c.us'
OGG = b'OggS-synthetic-voice-note'


@pytest.fixture
def channel(persistent, monkeypatch):  # noqa: F811
    configure_waha(monkeypatch)
    monkeypatch.setenv('SARVAM_STT_ENABLED', 'true')
    monkeypatch.setenv('SARVAM_API_KEY', 'fixture-sarvam-key')
    sender = Mock()
    monkeypatch.setattr(waha, '_send', sender)
    client = TestClient(create_app())
    signed(client, 'YOJANA', 'activate')
    signed(client, 'Chat', 'chat')
    sender.reset_mock()
    return client, sender


def post(client, payload):
    """A signed WAHA webhook exactly as the HTTP endpoint receives it."""
    raw = json.dumps({'event': 'message', 'session': 'default', 'payload': payload}).encode()
    mac = hmac.new(os.environ['WAHA_HMAC_KEY'].encode(), raw, hashlib.sha512).hexdigest()
    return client.post('/api/webhooks/waha', content=raw, headers={
        'Content-Type': 'application/json', 'X-Webhook-Hmac-Algorithm': 'sha512', 'X-Webhook-Hmac': mac})


def voice(mid, body=None, **media_fields):
    """NOWEB-shaped voice note: body is null, media.url points at WAHA's files API."""
    m = {'url': 'http://waha:3000/api/files/default/false_900000000000@c.us_' + mid + '.oga',
         'mimetype': 'audio/ogg; codecs=opus', 'filename': None, **media_fields}
    return {'from': CHAT, 'id': 'false_' + mid, 'body': body, 'hasMedia': True, 'fromMe': False, 'media': m}


def fake_network(monkeypatch, waha_handler=None, sarvam_handler=None):
    """Route every httpx.Client the media adapter and Sarvam client open through mock transports."""
    calls = {'waha': [], 'sarvam': []}

    def waha_default(request):
        return httpx.Response(200, content=OGG)

    def sarvam_default(request):
        return httpx.Response(200, json={'transcript': 'I am 62 years old', 'language_code': 'en-IN'})

    def route(request):
        if request.url.host == 'api.sarvam.ai':
            calls['sarvam'].append(request)
            return (sarvam_handler or sarvam_default)(request)
        calls['waha'].append(request)
        return (waha_handler or waha_default)(request)

    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(route), **kw))
    monkeypatch.setattr(media, 'decode', lambda audio: _wav())     # ffmpeg is covered separately
    return calls


def _wav(seconds=1):
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\x01\x00' * 16000 * seconds)
    return out.getvalue()


# ---------- the root cause: body null ----------

def test_noweb_voice_note_with_null_body_gets_a_reply(channel, monkeypatch):
    client, sender = channel
    mock_extraction(monkeypatch)
    calls = fake_network(monkeypatch)
    response = post(client, voice('A1'))
    assert response.status_code == 200 and response.json()['status'] == 'replied'
    assert sender.call_count == 1
    assert len(calls['waha']) == 1 and calls['waha'][0].headers['X-Api-Key'] == 'fixture-api-key'
    sent = calls['sarvam'][0]
    assert sent.headers['api-subscription-key'] == 'fixture-sarvam-key'
    assert b'name="language_code"\r\n\r\nunknown' in sent.content     # auto-detect


def test_text_with_null_body_is_still_ignored(channel):
    client, sender = channel
    assert post(client, {'from': CHAT, 'id': 'nb', 'body': None}).json()['status'] == 'ignored'
    sender.assert_not_called()


# ---------- media location ----------

@pytest.mark.parametrize('url', ['http://waha:3000/api/files/voice.oga',
                                 'http://waha:3000/api/files/default/false_1@c.us_AB.oga'])
def test_flat_and_session_nested_file_paths_accepted(monkeypatch, url):
    configure_waha(monkeypatch)
    assert media.media_url({'url': url, 'mimetype': 'audio/ogg'}) == url


@pytest.mark.parametrize('url', ['http://waha:3000/api/files/othersession/a.oga',
                                 'http://waha:3000/api/files/default/../a.oga',
                                 'http://waha:3000/api/files/default%2Fa.oga',
                                 'http://localhost:3000/api/files/a.oga'])
def test_other_sessions_traversal_and_unlisted_origins_rejected(monkeypatch, url):
    configure_waha(monkeypatch)
    with pytest.raises(media.VoiceFailure) as err:
        media.media_url({'url': url, 'mimetype': 'audio/ogg'})
    assert err.value.code == 'WAHA_MEDIA_UNTRUSTED'


def test_advertised_origin_alias_downloads_from_trusted_origin_only(channel, monkeypatch):
    client, sender = channel
    mock_extraction(monkeypatch)
    monkeypatch.setenv('WAHA_MEDIA_ORIGINS', 'http://localhost:3000')
    calls = fake_network(monkeypatch)
    payload = voice('A2', url='http://localhost:3000/api/files/default/A2.oga')
    assert post(client, payload).json()['status'] == 'replied'
    assert calls['waha'][0].url.host == 'waha'                  # never the host from the payload


def test_inline_base64_media_needs_no_download(channel, monkeypatch):
    client, sender = channel
    mock_extraction(monkeypatch)
    calls = fake_network(monkeypatch)
    payload = voice('A3', url=None, data=base64.b64encode(OGG).decode())
    assert post(client, payload).json()['status'] == 'replied'
    assert calls['waha'] == []


# ---------- every failure gets a helpful text reply, once ----------

@pytest.mark.parametrize('case,expected', [
    ('url_null', "couldn't transcribe"),
    ('no_media', "couldn't transcribe"),
    ('image', "couldn't transcribe"),
    ('waha_404', "couldn't transcribe"),
    ('stt_off', 'not available right now'),
    ('sarvam_429', 'not available right now'),
    ('sarvam_401', 'not available right now'),
    ('sarvam_empty', "couldn't transcribe"),
    ('too_long', 'longer than 15 seconds'),
])
def test_failures_reply_with_reason_and_do_not_retry(channel, monkeypatch, case, expected):
    client, sender = channel
    payload = voice('F-' + case)
    waha_handler = sarvam_handler = None
    if case == 'url_null':
        payload['media']['url'] = None
    elif case == 'no_media':
        payload['media'] = None
    elif case == 'image':
        payload['media']['mimetype'] = 'image/jpeg'
    elif case == 'waha_404':
        waha_handler = lambda r: httpx.Response(404)  # noqa: E731
    elif case == 'stt_off':
        monkeypatch.setenv('SARVAM_STT_ENABLED', 'false')
    elif case == 'sarvam_429':
        sarvam_handler = lambda r: httpx.Response(429)  # noqa: E731
    elif case == 'sarvam_401':
        sarvam_handler = lambda r: httpx.Response(401)  # noqa: E731
    elif case == 'sarvam_empty':
        sarvam_handler = lambda r: httpx.Response(200, json={'transcript': '  ', 'language_code': 'en-IN'})  # noqa: E731
    calls = fake_network(monkeypatch, waha_handler, sarvam_handler)
    if case == 'too_long':
        monkeypatch.setattr(media, 'decode', Mock(side_effect=media.VoiceFailure('VOICE_TOO_LONG')))
    speech._blocked.clear()
    first = post(client, payload)
    assert first.status_code == 200 and first.json()['status'] == 'transcription_failed'
    assert expected in sender.call_args.args[-1]
    again = post(client, payload)                       # WAHA retry of the same event
    assert again.json()['status'] == 'duplicate'
    assert sender.call_count == 1
    assert len(calls['sarvam']) <= 1
    speech._blocked.clear()


@pytest.mark.parametrize('lang,word,needle', [('hi', 'Hindi', 'लंबा'), ('mr', 'Marathi', 'मोठा')])
def test_failure_reply_in_chat_language(channel, monkeypatch, lang, word, needle):
    client, sender = channel
    signed(client, word, 'lang-' + lang)
    fake_network(monkeypatch)
    monkeypatch.setattr(media, 'decode', Mock(side_effect=media.VoiceFailure('VOICE_TOO_LONG')))
    assert post(client, voice('L-' + lang)).json()['status'] == 'transcription_failed'
    assert needle in sender.call_args.args[-1]


def test_spoken_identity_number_is_refused_with_explanation(channel, monkeypatch):
    client, sender = channel
    fake_network(monkeypatch, sarvam_handler=lambda r: httpx.Response(
        200, json={'transcript': 'my aadhaar is 1234 5678 9012', 'language_code': 'en-IN'}))
    assert post(client, voice('S1')).json()['status'] == 'transcription_failed'
    assert 'without the number' in sender.call_args.args[-1]


# ---------- language paths ----------

@pytest.mark.parametrize('code,transcript', [
    ('en-IN', 'I am 62 years old'),
    ('hi-IN', 'मेरी उम्र 62 साल है'),
    ('mr-IN', 'माझे वय 62 वर्षे आहे'),
])
def test_transcript_language_reaches_the_conversation(channel, monkeypatch, code, transcript):
    client, sender = channel
    short = code[:2]
    provider = mock_extraction(monkeypatch)
    provider.queue = [{'language': short, 'facts': {'age': 62}, 'intent': 'give_info'}]
    seen = {}
    original = waha.conversation

    def spy(message, **kw):
        seen['lang'], seen['text'] = message.lang, message.text
        return original(message, **kw)
    monkeypatch.setattr(waha, 'conversation', spy)
    fake_network(monkeypatch, sarvam_handler=lambda r: httpx.Response(
        200, json={'transcript': transcript, 'language_code': code}))
    assert post(client, voice('T-' + short)).json()['status'] == 'replied'
    assert seen == {'lang': short, 'text': transcript}


# ---------- retries ----------

def test_model_outage_rolls_back_but_retry_reuses_transcript(channel, monkeypatch):
    client, sender = channel
    calls = fake_network(monkeypatch)
    real = waha.conversation
    monkeypatch.setattr(waha, 'conversation', Mock(side_effect=HTTPException(503, 'model down')))
    assert post(client, voice('R1')).status_code == 503       # WAHA will retry this event
    sender.assert_not_called()
    monkeypatch.setattr(waha, 'conversation', real)
    mock_extraction(monkeypatch)
    assert post(client, voice('R1')).json()['status'] == 'replied'
    assert len(calls['sarvam']) == 1                            # Sarvam paid once, not per retry
    assert sender.call_count == 1


def test_delivery_failure_retries_without_duplicate_messages(channel, monkeypatch):
    client, sender = channel
    mock_extraction(monkeypatch)
    calls = fake_network(monkeypatch)
    sender.side_effect = [httpx.ConnectError('waha down'), None]
    assert post(client, voice('D1')).status_code == 503
    assert post(client, voice('D1')).json()['status'] == 'replied'
    assert post(client, voice('D1')).json()['status'] == 'duplicate'
    assert sender.call_count == 2 and len(calls['sarvam']) == 1


# ---------- diagnostics are useful and private ----------

def test_diagnostics_codes_without_personal_data(channel, monkeypatch, caplog):
    client, sender = channel
    mock_extraction(monkeypatch)
    fake_network(monkeypatch, sarvam_handler=lambda r: httpx.Response(429))
    speech._blocked.clear()
    with caplog.at_level('INFO', logger='yojana.diagnostics'):
        post(client, voice('P1'))
    text = caplog.text
    for code in ('WAHA_WEBHOOK_RECEIVED', 'SARVAM_STT_FAILED', 'WAHA_REPLY_SENT'):
        assert code in text
    for secret in ('900000000000', 'fixture-api-key', 'fixture-sarvam-key', '/api/files/', 'P1.oga'):
        assert secret not in text
    speech._blocked.clear()


def test_ignored_events_say_why(channel, caplog):
    client, _ = channel
    with caplog.at_level('INFO', logger='yojana.diagnostics'):
        raw = json.dumps({'event': 'message', 'session': 'other', 'payload': voice('X1')}).encode()
        mac = hmac.new(os.environ['WAHA_HMAC_KEY'].encode(), raw, hashlib.sha512).hexdigest()
        r = client.post('/api/webhooks/waha', content=raw, headers={
            'Content-Type': 'application/json', 'X-Webhook-Hmac-Algorithm': 'sha512', 'X-Webhook-Hmac': mac})
    assert r.json()['status'] == 'ignored'
    assert 'session_mismatch' in caplog.text


def test_voice_readiness_lists_missing_settings_by_name(monkeypatch):
    configure_waha(monkeypatch)
    monkeypatch.setenv('SARVAM_STT_ENABLED', 'false')
    monkeypatch.delenv('SARVAM_API_KEY', raising=False)
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/ffmpeg')
    missing = waha.voice_missing()
    assert 'SARVAM_STT_ENABLED=true' in missing and 'SARVAM_API_KEY' in missing
    assert all('fixture' not in name for name in missing)


# ---------- Sarvam request contract ----------

def test_sarvam_stt_request_shape_and_language_normalisation(monkeypatch):
    monkeypatch.setenv('SARVAM_STT_ENABLED', 'true')
    monkeypatch.setenv('SARVAM_API_KEY', 'fixture-sarvam-key')
    monkeypatch.delenv('SARVAM_STT_MODEL', raising=False)
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'transcript': 'मी शेतकरी आहे', 'language_code': 'mr-IN'})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    out = speech.stt(base64.b64encode(_wav()).decode(), 'auto')
    assert out == {'text': 'मी शेतकरी आहे', 'language': 'mr'}
    body = seen[0].content
    assert str(seen[0].url) == 'https://api.sarvam.ai/speech-to-text'
    assert b'saaras:v3' in body and b'transcribe' in body and b'audio/wav' in body
