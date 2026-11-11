"""yojana-api with STT_PROVIDER=local: WhatsApp voice notes go WAHA -> API -> private stt_service,
never to Sarvam. The STT service runs in-process (stt_service.app with a fake engine) behind an httpx
mock transport, fed with real FLEURS audio. No model, no network."""
import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from stt_service.app import create_app as create_stt
from web import speech, waha_bridge as waha, waha_media as media
from test_voice_pipeline import channel, post, voice, CHAT  # noqa: F401
from test_deployment_v24 import persistent, mock_extraction, signed  # noqa: F401
from test_stt_service import FakeEngine

AUDIO = Path(__file__).parent / 'fixtures' / 'audio'


@pytest.fixture
def local_stt(monkeypatch):
    """STT_PROVIDER=local with Sarvam fully configured too, to prove it is never used."""
    monkeypatch.setenv('STT_PROVIDER', 'local')
    monkeypatch.setenv('LOCAL_STT_URL', 'http://yojana-stt:8001')
    monkeypatch.setenv('LOCAL_STT_TOKEN', 'k' * 32)
    monkeypatch.setenv('SARVAM_STT_ENABLED', 'true')
    monkeypatch.setenv('SARVAM_API_KEY', 'fixture-sarvam-key')
    state = {'engine': FakeEngine(text='मी 62 वर्षांचा शेतकरी आहे', language='mr'), 'audio': 'mr_5.wav',
             'calls': {'waha': [], 'stt': [], 'sarvam': []}, 'stt_handler': None}
    app_env = {'LOCAL_STT_TOKEN': 'k' * 32, 'STT_QUEUE_WAIT_SECONDS': '1'}

    def route(request):
        host = request.url.host
        if host == 'api.sarvam.ai':
            state['calls']['sarvam'].append(request)
            return httpx.Response(500)
        if host == 'yojana-stt':
            state['calls']['stt'].append(request)
            if state['stt_handler']:
                return state['stt_handler'](request)
            with TestClient(create_stt(state['engine'], env=app_env, load_in_background=False)) as stt:
                r = stt.request(request.method, request.url.path, params=request.url.params,
                                content=request.content, headers=dict(request.headers))
            return httpx.Response(r.status_code, content=r.content, headers={'content-type': 'application/json'})
        state['calls']['waha'].append(request)
        return httpx.Response(200, content=(AUDIO / state['audio']).read_bytes())

    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(route), **kw))
    monkeypatch.setattr(media, 'decode', lambda audio: audio)   # fixtures are already 16 kHz PCM16 WAV
    speech._blocked.clear()
    return state


def wav_b64(name):
    return base64.b64encode((AUDIO / name).read_bytes()).decode()


# ---------- speech.stt contract ----------

def test_local_request_shape_and_language_hint(local_stt):
    out = speech.stt(wav_b64('mr_5.wav'), 'mr')
    assert out == {'text': 'मी 62 वर्षांचा शेतकरी आहे', 'language': 'mr'}
    sent = local_stt['calls']['stt'][0]
    assert str(sent.url) == 'http://yojana-stt:8001/transcribe?language=mr'
    assert sent.headers['content-type'] == 'audio/wav' and sent.headers['authorization'] == 'Bearer ' + 'k' * 32
    assert sent.content == (AUDIO / 'mr_5.wav').read_bytes()     # bytes, never a URL
    assert local_stt['calls']['sarvam'] == []


def test_auto_language_sends_no_hint(local_stt):
    speech.stt(wav_b64('en_4.wav'), 'auto')
    assert 'language' not in str(local_stt['calls']['stt'][0].url)


@pytest.mark.parametrize('handler,status,code', [
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout('slow', request=r)), 504, 'speech_timeout'),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError('down', request=r)), 503, 'speech_provider_unavailable'),
    (lambda r: httpx.Response(503, json={'code': 'not_ready'}), 503, 'speech_provider_unavailable'),
    (lambda r: httpx.Response(503, json={'code': 'busy'}), 503, 'speech_busy'),
    (lambda r: httpx.Response(500, json={'code': 'engine_error'}), 503, 'speech_provider_unavailable'),
    (lambda r: httpx.Response(401, json={'code': 'unauthorized'}), 503, 'speech_auth_unavailable'),
    (lambda r: httpx.Response(200, json={'text': '', 'no_speech': True}), 422, 'no_speech'),
])
def test_local_failures_never_fall_back_to_sarvam(local_stt, handler, status, code):
    local_stt['stt_handler'] = handler
    with pytest.raises(HTTPException) as err:
        speech.stt(wav_b64('hi_3.wav'), 'hi')
    assert (err.value.status_code, err.value.detail['code']) == (status, code)
    assert local_stt['calls']['sarvam'] == []


def test_timeout_setting_is_used(local_stt, monkeypatch):
    seen = {}
    original = httpx._client.Client                              # the real class, not the fixture's patch

    def capture(**kw):
        seen['timeout'] = kw['timeout']
        return original(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'text': 'ok', 'language': 'en'})), **kw)
    monkeypatch.setattr(httpx, 'Client', capture)
    monkeypatch.setenv('LOCAL_STT_TIMEOUT_SECONDS', '12')
    speech.stt(wav_b64('en_4.wav'), 'en')
    assert seen['timeout'].read == 12


def test_sensitive_numbers_still_refused_with_local_stt(local_stt):
    local_stt['engine'].text = 'my aadhaar is 1234 5678 9012'
    with pytest.raises(HTTPException) as err:
        speech.stt(wav_b64('en_4.wav'), 'en')
    assert err.value.status_code == 422


def test_provider_selection(monkeypatch):
    for key in ('STT_PROVIDER', 'SARVAM_STT_ENABLED'):
        monkeypatch.delenv(key, raising=False)
    assert speech.stt_provider() == 'off'
    monkeypatch.setenv('SARVAM_STT_ENABLED', 'true')
    assert speech.stt_provider() == 'sarvam'                      # legacy deployments unchanged
    monkeypatch.setenv('STT_PROVIDER', 'local')
    assert speech.stt_provider() == 'local'
    monkeypatch.setenv('STT_PROVIDER', 'openai')
    assert speech.stt_provider() == 'off'                          # unknown -> off, never a guess


def test_production_validates_local_stt_settings(monkeypatch):
    from web import runtime
    monkeypatch.setenv('DEPLOYMENT_MODE', 'production')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u@db.invalid/cases')
    monkeypatch.setenv('STT_PROVIDER', 'local')
    monkeypatch.setenv('LOCAL_STT_URL', '')
    with pytest.raises(RuntimeError, match='LOCAL_STT_URL'):
        runtime.validate_configuration()
    monkeypatch.setenv('STT_PROVIDER', 'whisper')
    with pytest.raises(RuntimeError, match='STT_PROVIDER'):
        runtime.validate_configuration()


def test_voice_readiness_for_local_provider(monkeypatch):
    from test_deployment_v24 import configure_waha
    configure_waha(monkeypatch)
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/ffmpeg')
    monkeypatch.setenv('STT_PROVIDER', 'local')
    monkeypatch.delenv('LOCAL_STT_URL', raising=False)
    monkeypatch.delenv('SARVAM_API_KEY', raising=False)
    monkeypatch.setenv('SARVAM_STT_ENABLED', 'false')
    missing = waha.voice_missing()
    assert 'LOCAL_STT_URL' in missing                              # Sarvam settings no longer required
    assert not any('SARVAM' in name for name in missing)
    monkeypatch.setenv('LOCAL_STT_URL', 'http://yojana-stt:8001')
    assert 'LOCAL_STT_URL' not in waha.voice_missing() and 'STT_PROVIDER=local|sarvam' not in waha.voice_missing()


# ---------- WhatsApp voice notes end to end ----------

@pytest.mark.parametrize('audio,text,lang', [
    ('en_4.wav', 'I am 62 years old farmer in Beed', 'en'),
    ('hi_3.wav', 'मेरी उम्र 62 साल है, मैं बीड में किसान हूँ', 'hi'),
    ('mr_5.wav', 'माझे वय 62 आहे, मी बीडमध्ये शेतकरी आहे', 'mr'),
    ('mix_hi_en.wav', 'मेरी age 62 है and I am a farmer', 'hi'),
])
def test_voice_note_transcribed_locally_and_answered(channel, local_stt, monkeypatch, audio, text, lang):
    client, sender = channel
    mock_extraction(monkeypatch)
    local_stt.update(audio=audio, engine=FakeEngine(text=text, language=lang))
    r = post(client, voice('LOC-' + lang + audio[:2]))
    assert r.status_code == 200 and r.json()['status'] == 'replied'
    assert sender.call_count == 1
    assert len(local_stt['calls']['stt']) == 1 and local_stt['calls']['sarvam'] == []
    assert local_stt['engine'].calls and local_stt['engine'].calls[0][0] > 16000 * 4


def test_chat_language_is_sent_as_hint(channel, local_stt, monkeypatch):
    client, sender = channel
    mock_extraction(monkeypatch)
    signed(client, 'Marathi', 'lang-mr-local')
    assert post(client, voice('HINT1')).json()['status'] == 'replied'
    assert local_stt['calls']['stt'][0].url.params['language'] == 'mr'


def test_silent_note_gets_a_text_reply(channel, local_stt):
    client, sender = channel
    local_stt['stt_handler'] = lambda r: httpx.Response(200, json={'text': '', 'no_speech': True})
    r = post(client, voice('SIL1'))
    assert r.json()['status'] == 'transcription_failed'
    assert "couldn't transcribe" in sender.call_args.args[-1]


def test_stt_service_down_says_voice_unavailable_and_does_not_retry(channel, local_stt, caplog):
    client, sender = channel
    local_stt['stt_handler'] = lambda r: (_ for _ in ()).throw(httpx.ConnectError('down', request=r))
    payload = voice('DOWN1')
    with caplog.at_level('INFO', logger='yojana.diagnostics'):
        assert post(client, payload).json()['status'] == 'transcription_failed'
        assert post(client, payload).json()['status'] == 'duplicate'
    assert 'not available right now' in sender.call_args.args[-1] and sender.call_count == 1
    assert len(local_stt['calls']['stt']) == 1 and local_stt['calls']['sarvam'] == []
    line = next(r.getMessage() for r in caplog.records if 'LOCAL_STT_FAILED' in r.getMessage())
    assert json.loads(line)['detail'] == 'speech_provider_unavailable'


def test_transcript_ready_log_names_local_provider(channel, local_stt, monkeypatch, caplog):
    client, _ = channel
    mock_extraction(monkeypatch)
    with caplog.at_level('INFO', logger='yojana.diagnostics'):
        post(client, voice('SRC1'))
    line = next(r.getMessage() for r in caplog.records if 'VOICE_TRANSCRIPT_READY' in r.getMessage())
    assert json.loads(line)['source'] == 'local'
    assert 'शेतकरी' not in caplog.text and CHAT.split('@')[0] not in caplog.text


def test_webhook_retry_during_slow_transcription_is_deferred_not_duplicated(channel, local_stt, monkeypatch):
    """Local STT takes seconds; a WAHA retry arriving meanwhile gets 503 (retry later), not a second
    transcription. The later retry is a duplicate."""
    import threading
    client, sender = channel
    mock_extraction(monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        release.wait(5)
        return httpx.Response(200, json={'text': 'I am 62', 'language': 'en'})
    local_stt['stt_handler'] = slow
    payload = voice('SLOW1')
    first = []
    t = threading.Thread(target=lambda: first.append(post(client, payload)))
    t.start()
    assert entered.wait(5)
    retry = post(client, payload)
    release.set()
    t.join(10)
    assert retry.status_code == 503
    assert first[0].json()['status'] == 'replied'
    assert post(client, payload).json()['status'] == 'duplicate'
    assert len(local_stt['calls']['stt']) == 1 and sender.call_count == 1
