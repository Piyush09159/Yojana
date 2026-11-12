"""Opt-in: the real faster-whisper model on real FLEURS audio, through the HTTP service and through a
signed WhatsApp voice note. Skipped unless requirements-stt.txt is installed and STT_TEST_MODEL_DIR
points at a directory containing faster-whisper-<LOCAL_STT_MODEL> (download_model.py). No network.

    STT_TEST_MODEL_DIR=/models python -m pytest -q tests/test_stt_real_audio.py
"""
import json
import os
from pathlib import Path

import pytest

pytest.importorskip('faster_whisper')
MODEL_DIR = os.getenv('STT_TEST_MODEL_DIR')
MODEL = os.getenv('LOCAL_STT_MODEL', 'small')
if not MODEL_DIR or not (Path(MODEL_DIR) / f'faster-whisper-{MODEL}').exists():
    pytest.skip('STT_TEST_MODEL_DIR with downloaded weights not set', allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from stt_service.app import create_app  # noqa: E402
from stt_service.engines import FasterWhisperEngine  # noqa: E402
from test_stt_service import pcm_wav  # noqa: E402
from stt_service.textmetrics import cer  # noqa: E402
from test_local_stt import local_stt  # noqa: E402,F401
from test_voice_pipeline import channel  # noqa: E402,F401
from test_deployment_v24 import persistent  # noqa: E402,F401

AUDIO = Path(__file__).parent / 'fixtures' / 'audio'
MANIFEST = {x['file']: x for x in json.loads((AUDIO / 'manifest.json').read_text(encoding='utf-8'))}


@pytest.fixture(scope='module')
def engine():
    e = FasterWhisperEngine(MODEL_DIR, model=MODEL, cpu_threads=int(os.getenv('STT_CPU_THREADS', '2')))
    e.load()
    return e


@pytest.fixture(scope='module')
def stt(engine):
    with TestClient(create_app(engine, env={'LOCAL_STT_TOKEN': 'k' * 32}, load_in_background=False)) as c:
        yield c


def transcribe(stt, name, hint=None):
    params = {'language': hint} if hint else {}
    r = stt.post('/transcribe', content=(AUDIO / name).read_bytes(), params=params,
                 headers={'Content-Type': 'audio/wav', 'Authorization': 'Bearer ' + 'k' * 32})
    assert r.status_code == 200
    return r.json()


# CER bounds come from the 2026-11-11 benchmark on these clips (small, int8, beam 1), with headroom.
@pytest.mark.parametrize('name,hint,cer_max', [
    ('en_4.wav', None, 0.15),
    ('hi_3.wav', None, 0.45),
    ('mr_5.wav', 'mr', 0.60),        # Whisper small often hears Marathi as Hindi without the chat hint
])
def test_real_model_transcribes_in_the_spoken_language(stt, name, hint, cer_max):
    out = transcribe(stt, name, hint)
    expected = MANIFEST[name]
    assert out['language'] == expected['language']
    assert out['text'] and not out['no_speech']
    if expected['language'] != 'en':
        assert any('ऀ' <= ch <= 'ॿ' for ch in out['text'])   # Devanagari, not translated
    assert cer(expected['reference'], out['text']) <= cer_max


def test_real_model_mixed_note_stays_in_devanagari(stt):
    out = transcribe(stt, 'mix_hi_en.wav')
    assert out['language'] == 'hi' and any('ऀ' <= ch <= 'ॿ' for ch in out['text'])


def test_real_model_silence_returns_no_speech(stt):
    r = stt.post('/transcribe', content=pcm_wav(3, value=0),
                 headers={'Content-Type': 'audio/wav', 'Authorization': 'Bearer ' + 'k' * 32})
    assert r.json()['no_speech'] is True


def test_real_whatsapp_voice_note_end_to_end(channel, local_stt, stt, monkeypatch):
    """Signed WAHA webhook -> download -> real model over HTTP -> conversation -> one WhatsApp reply."""
    import httpx
    from test_voice_pipeline import post, voice
    from test_deployment_v24 import mock_extraction
    from web import waha_bridge as waha
    client, sender = channel
    mock_extraction(monkeypatch)
    seen = []
    original = waha.conversation

    def spy(message, **kw):
        seen.append((message.text, message.lang))
        return original(message, **kw)
    monkeypatch.setattr(waha, 'conversation', spy)

    def forward(request):
        r = stt.post(request.url.path, params=request.url.params, content=request.content,
                     headers={'Content-Type': 'audio/wav', 'Authorization': request.headers.get('authorization', '')})
        return httpx.Response(r.status_code, content=r.content, headers={'content-type': 'application/json'})
    local_stt.update(audio='hi_3.wav', stt_handler=forward)
    r = post(client, voice('REAL1'))
    assert r.json()['status'] == 'replied' and sender.call_count == 1
    text, lang = seen[0]
    assert lang == 'hi' and any('ऀ' <= ch <= 'ॿ' for ch in text)
    assert cer(MANIFEST['hi_3.wav']['reference'], text) <= 0.45
    assert local_stt['calls']['sarvam'] == []
