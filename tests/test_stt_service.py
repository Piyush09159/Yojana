"""stt_service HTTP contract with real FLEURS audio and a fake engine (no model, no network).
The real-model test is tests/test_stt_real_audio.py (opt-in)."""
import io
import json
import logging
import threading
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stt_service import audio as wav
from stt_service.app import create_app
from stt_service.engines import EngineUnavailable, choose_language

AUDIO = Path(__file__).parent / 'fixtures' / 'audio'
MANIFEST = {x['file']: x for x in json.loads((AUDIO / 'manifest.json').read_text(encoding='utf-8'))}


def pcm_wav(seconds=1.0, value=1000, rate=16000, channels=1, width=2):
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(channels); f.setsampwidth(width); f.setframerate(rate)
        f.writeframes((value).to_bytes(width, 'little', signed=True) * int(seconds * rate) * channels)
    return out.getvalue()


class FakeEngine:
    name = 'fake'

    def __init__(self, text='मुझे पेंशन चाहिए', language='hi', fail_load=False, gate=None, error=None):
        self.text, self.language, self.fail_load, self.gate, self.error = text, language, fail_load, gate, error
        self.calls = []

    def load(self):
        if self.fail_load:
            raise EngineUnavailable('model_weights_missing')

    def transcribe(self, samples, hint=None):
        self.calls.append((len(samples), hint))
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return {'text': self.text, 'language': hint or self.language, 'language_probability': 0.9}


def client(engine=None, **env):
    app = create_app(engine or FakeEngine(), env={'STT_CONCURRENCY': '1', 'STT_QUEUE_WAIT_SECONDS': '5', **env},
                     load_in_background=False)
    return TestClient(app)


def post(c, body, **kw):
    headers = {'Content-Type': 'audio/wav', **kw.pop('headers', {})}
    return c.post('/transcribe', content=body, headers=headers, **kw)


@pytest.mark.parametrize('file,lang', [('en_4.wav', 'en'), ('hi_3.wav', 'hi'), ('mr_5.wav', 'mr')])
def test_real_five_second_notes_reach_the_engine_with_the_chat_hint(file, lang):
    engine = FakeEngine(text='transcript')
    with client(engine) as c:
        r = post(c, (AUDIO / file).read_bytes(), params={'language': lang})
    assert r.status_code == 200
    body = r.json()
    assert body['text'] == 'transcript' and body['language'] == lang and body['no_speech'] is False
    assert abs(body['seconds'] - MANIFEST[file]['seconds']) < 0.05
    assert engine.calls == [(int(MANIFEST[file]['seconds'] * 16000 + 0.5), lang)]


def test_mixed_language_note_is_accepted_and_unknown_hint_ignored():
    engine = FakeEngine()
    with client(engine) as c:
        r = post(c, (AUDIO / 'mix_hi_en.wav').read_bytes(), params={'language': 'fr'})
    assert r.status_code == 200 and engine.calls[0][1] is None


@pytest.mark.parametrize('body,code,status', [
    (b'not a wav', 'invalid_wav', 422),
    (b'RIFF\x00\x00\x00\x00WAVEfmt ', 'invalid_wav', 422),
    (pcm_wav(1, rate=8000), 'requires_mono_16khz_pcm16_wav', 422),
    (pcm_wav(1, channels=2), 'requires_mono_16khz_pcm16_wav', 422),
    (pcm_wav(0), 'empty_audio', 422),
    (pcm_wav(15.5), 'too_large', 413),            # exceeds the byte bound before parsing
])
def test_invalid_audio_rejected_before_the_model(body, code, status):
    engine = FakeEngine()
    with client(engine) as c:
        r = post(c, body)
    assert (r.status_code, r.json()['code']) == (status, code)
    assert engine.calls == []


def test_longer_than_fifteen_seconds_rejected():
    with pytest.raises(wav.AudioError) as err:
        wav.read_pcm16(pcm_wav(15.2))
    assert err.value.code in ('too_long', 'too_large')
    header_says_short = bytearray(pcm_wav(15.01))
    with pytest.raises(wav.AudioError):
        wav.read_pcm16(bytes(header_says_short))


def test_wrong_content_type_rejected():
    with client() as c:
        r = c.post('/transcribe', content=pcm_wav(1), headers={'Content-Type': 'text/plain'})
    assert r.status_code == 415


def test_silent_note_skips_the_model():
    engine = FakeEngine()
    with client(engine) as c:
        r = post(c, pcm_wav(3, value=0))
        quiet = post(c, pcm_wav(3, value=3))                     # about -80 dBFS hiss
    assert r.json()['no_speech'] is True and r.json()['text'] == ''
    assert quiet.json()['no_speech'] is True
    assert engine.calls == []


def test_empty_transcript_is_reported_as_no_speech():
    with client(FakeEngine(text='  ')) as c:
        r = post(c, (AUDIO / 'en_4.wav').read_bytes())
    assert r.status_code == 200 and r.json()['no_speech'] is True


def test_model_unavailable_keeps_health_up_and_ready_down():
    with client(FakeEngine(fail_load=True)) as c:
        assert c.get('/health').json() == {'status': 'alive'}
        ready = c.get('/ready')
        assert ready.status_code == 503 and ready.json()['detail'] == 'model_weights_missing'
        r = post(c, (AUDIO / 'en_4.wav').read_bytes())
    assert (r.status_code, r.json()['code']) == (503, 'not_ready')


def test_engine_crash_is_a_500_without_details():
    with client(FakeEngine(error=MemoryError('x' * 50))) as c:
        r = post(c, (AUDIO / 'en_4.wav').read_bytes())
    assert r.status_code == 500 and r.json() == {'code': 'engine_error'}


def test_one_request_at_a_time_and_the_queue_is_bounded():
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    results = []
    with client(engine, STT_QUEUE_WAIT_SECONDS='0.3') as c:
        def call():
            results.append(post(c, (AUDIO / 'hi_3.wav').read_bytes()).status_code)
        first = threading.Thread(target=call)
        first.start()
        for _ in range(100):
            if engine.calls:
                break
            time.sleep(0.01)
        second = post(c, (AUDIO / 'hi_3.wav').read_bytes())      # model busy, waits 0.3 s, gives up
        gate.set()
        first.join(5)
    assert (second.status_code, second.json()['code']) == (503, 'busy')
    assert results == [200] and len(engine.calls) == 1


def test_shared_token_required_when_configured():
    with client(LOCAL_STT_TOKEN='t' * 32) as c:
        assert post(c, pcm_wav(1)).status_code == 401
        assert post(c, pcm_wav(1), headers={'Authorization': 'Bearer ' + 't' * 32}).status_code == 200


def test_logs_have_timings_but_never_audio_or_transcripts(caplog):
    secret = 'मेरा नाम सुनील है'
    with caplog.at_level(logging.INFO, logger='yojana.stt'):
        with client(FakeEngine(text=secret)) as c:
            post(c, (AUDIO / 'hi_3.wav').read_bytes(), params={'language': 'hi'})
    lines = [r.getMessage() for r in caplog.records if r.name == 'yojana.stt']
    entry = json.loads(next(line for line in lines if '"transcribe"' in line))
    assert entry['status'] == 200 and entry['language'] == 'hi' and 'ms' in entry
    assert set(entry) <= {'event', 'status', 'code', 'ms', 'seconds', 'language', 'no_speech', 'detail'}
    assert all(secret not in line and 'RIFF' not in line for line in lines)


@pytest.mark.parametrize('probs,hint,expected', [
    ({'ur': 0.7, 'hi': 0.2, 'en': 0.05}, None, 'hi'),           # Hindi audio labelled Urdu
    ({'hi': 0.8, 'mr': 0.1}, 'mr', 'mr'),                         # hi/mr confusion: chat language wins
    ({'mr': 0.6, 'hi': 0.3}, 'hi', 'hi'),
    ({'en': 0.9, 'hi': 0.05}, 'mr', 'en'),                        # confident English beats a Marathi chat
    ({'en': 0.3, 'hi': 0.25}, 'hi', 'hi'),                        # unsure: use the chat language
    ({'en': 0.3, 'hi': 0.25}, None, 'en'),
])
def test_language_choice_limited_to_en_hi_mr(probs, hint, expected):
    assert choose_language(probs, hint) == expected


def test_confidence_is_token_weighted_probability():
    from types import SimpleNamespace as S
    from stt_service.engines import confidence
    assert confidence([]) is None
    assert confidence([S(avg_logprob=0.0, tokens=[1, 2])]) == 1.0
    mixed = confidence([S(avg_logprob=-0.1, tokens=[1] * 9), S(avg_logprob=-2.0, tokens=[1])])
    assert 0.7 < mixed < 0.75                      # exp(-0.29): the long, sure segment dominates
