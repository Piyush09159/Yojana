"""Server-only speech integrations. No audio, transcript or provider payload is logged/stored.

STT providers (STT_PROVIDER):
  local   self-hosted stt_service on the private network (LOCAL_STT_URL); never falls back to Sarvam
  sarvam  Sarvam REST (opt-in; also selected when STT_PROVIDER is unset and SARVAM_STT_ENABLED=true)
  off     voice input disabled (default)
TTS stays on Sarvam and is unaffected.
"""
import base64
import io
import os
import wave
import json
import hashlib
import time
from threading import Lock
import httpx
from fastapi import HTTPException
from core.languages import normalize, provider_code, TTS
from .metrics import record

MAX_AUDIO = 512000
_blocked={}
_lock=Lock()


def request(path, **kwargs):
    started=time.perf_counter();outcome='error'
    stage='tts' if path=='text-to-speech' else 'stt'
    key = os.getenv('SARVAM_API_KEY')
    if not key:
        record(stage,time.perf_counter()-started,'fallback')
        raise HTTPException(503, {'code': 'speech_not_configured'})
    identity=hashlib.sha256(key.encode()).hexdigest()
    try:
        with _lock:
            blocked=_blocked.get(identity)
        if blocked and blocked[1]>time.monotonic():
            raise HTTPException(blocked[2],{'code':blocked[0],'fallback':'device_voice_or_text'})
        with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False) as client:
            with client.stream('POST', 'https://api.sarvam.ai/' + path,
                               headers={'api-subscription-key': key}, **kwargs) as response:
                if response.status_code in (402,429):
                    credit=response.status_code==402
                    code='speech_credits_exhausted' if credit else 'speech_rate_limited'
                    with _lock:
                        if len(_blocked)>=8:
                            _blocked.clear()
                        _blocked[identity]=(code,float('inf') if credit else time.monotonic()+60,response.status_code)
                    raise HTTPException(response.status_code, {'code':code,'fallback':'device_voice_or_text'})
                if response.status_code in (400,422):
                    raise HTTPException(422,{'code':'speech_unsupported_request','fallback':'device_voice_or_text'})
                if response.status_code in (401,403):
                    raise HTTPException(503,{'code':'speech_auth_unavailable','fallback':'device_voice_or_text'})
                if response.status_code != 200:
                    raise HTTPException(502, {'code': 'speech_provider_unavailable'})
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 2000000:
                        raise HTTPException(502, {'code': 'speech_response_too_large'})
                result=json.loads(content)
                outcome='ok'
                return result
    except httpx.TimeoutException:
        raise HTTPException(504,{'code':'speech_timeout','fallback':'device_voice_or_text'})
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, {'code': 'speech_provider_unavailable'})
    finally:
        record(stage,time.perf_counter()-started,outcome)


def tts(text, language):
    if normalize(language) not in TTS:
        record('tts',0,'fallback')
        raise HTTPException(422, {'code': 'tts_language_unavailable', 'fallback': 'read_text'})
    data = request('text-to-speech', json={'text': text, 'language_code': provider_code(language),
                   'model': os.getenv('SARVAM_TTS_MODEL', 'bulbul:v3'), 'output_audio_codec': 'wav'})
    try:
        audio = base64.b64decode(data['audios'][0], validate=True)
        if len(audio) > 1500000 or not audio.startswith(b'RIFF'):
            raise ValueError()
        with wave.open(io.BytesIO(audio),'rb') as wav:
            if (wav.getnchannels() not in (1,2) or not 8000<=wav.getframerate()<=48000
                    or not 0<wav.getnframes()<=45*wav.getframerate()):
                raise ValueError()
            frames=wav.readframes(wav.getnframes())
            if len(frames)!=wav.getnframes()*wav.getnchannels()*wav.getsampwidth():
                raise ValueError()
            if not any(frames) or (wav.getsampwidth()==1 and all(b==128 for b in frames)):
                record('tts',0,'fallback')
                raise HTTPException(502,{'code':'silent_speech_response','fallback':'device_voice_or_text'})
        return audio
    except (KeyError, IndexError, ValueError, TypeError, wave.Error, EOFError):
        raise HTTPException(502, {'code': 'invalid_speech_response'})


def stt_provider():
    """'local', 'sarvam' or 'off'. Unknown values mean 'off' so audio is never sent somewhere unintended."""
    chosen = os.getenv('STT_PROVIDER', '').strip().lower()
    if chosen:
        return chosen if chosen in ('local', 'sarvam', 'off') else 'off'
    return 'sarvam' if os.getenv('SARVAM_STT_ENABLED', 'false').lower() == 'true' else 'off'


def stt_configured():
    provider = stt_provider()
    if provider == 'local':
        return bool(os.getenv('LOCAL_STT_URL'))
    return provider == 'sarvam' and bool(os.getenv('SARVAM_API_KEY'))


def _local_stt(raw, language):
    """POST the validated WAV to the private STT service. Errors map to the same codes as Sarvam's."""
    started = time.perf_counter(); outcome = 'error'
    url = os.getenv('LOCAL_STT_URL', '').rstrip('/')
    if not url.startswith(('http://', 'https://')):
        record('stt', 0, 'fallback')
        raise HTTPException(503, {'code': 'speech_not_configured'})
    headers = {'Content-Type': 'audio/wav'}
    if os.getenv('LOCAL_STT_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['LOCAL_STT_TOKEN']
    hint = normalize(language)
    params = {'language': hint} if hint in ('en', 'hi', 'mr') else {}
    timeout = float(os.getenv('LOCAL_STT_TIMEOUT_SECONDS', '30'))
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=3), follow_redirects=False,
                          trust_env=False) as client:
            response = client.post(url + '/transcribe', content=raw, headers=headers, params=params)
        try:
            data = response.json() if len(response.content) <= 200000 else {}
        except ValueError:
            data = {}
        code = data.get('code') if isinstance(data, dict) else None
        if response.status_code == 200 and isinstance(data, dict):
            outcome = 'ok'
            return data
        if response.status_code == 503 and code == 'busy':
            raise HTTPException(503, {'code': 'speech_busy', 'fallback': 'type'})
        if response.status_code in (413, 422):
            raise HTTPException(422, {'code': 'speech_unsupported_request', 'fallback': 'type'})
        if response.status_code == 401:
            raise HTTPException(503, {'code': 'speech_auth_unavailable', 'fallback': 'type'})
        raise HTTPException(503, {'code': 'speech_provider_unavailable', 'fallback': 'type'})
    except httpx.TimeoutException:
        raise HTTPException(504, {'code': 'speech_timeout', 'fallback': 'type'})
    except httpx.HTTPError:
        raise HTTPException(503, {'code': 'speech_provider_unavailable', 'fallback': 'type'})
    finally:
        record('stt', time.perf_counter() - started, outcome)


def stt(encoded, language):
    provider = stt_provider()
    if provider == 'off':
        record('stt',0,'fallback')
        raise HTTPException(503, {'code': 'stt_not_enabled', 'fallback': 'type'})
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_AUDIO:
            raise ValueError()
        with wave.open(io.BytesIO(raw)) as wav:
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000
                    or wav.getnframes() > 15 * 16000 or wav.getnframes() == 0):
                raise ValueError()
            if len(wav.readframes(wav.getnframes())) != wav.getnframes()*2:
                raise ValueError()
    except (ValueError, wave.Error, EOFError):
        raise HTTPException(422, {'code': 'audio_requires_mono_16khz_pcm_wav_under_15_seconds'})
    if provider == 'local':
        data = _local_stt(raw, language)
        text = data.get('text')
        if data.get('no_speech') or (isinstance(text, str) and not text.strip()):
            raise HTTPException(422, {'code': 'no_speech', 'fallback': 'type'})
        detected = data.get('language')
    else:
        data = request('speech-to-text', files={'file': ('speech.wav', raw, 'audio/wav')},
                       data={'model': os.getenv('SARVAM_STT_MODEL', 'saaras:v3'), 'mode': 'transcribe',
                             'language_code': provider_code(language)})
        text = data.get('transcript')
        detected = data.get('language_code')
    if not isinstance(text, str) or not text.strip() or len(text) > 1800:
        raise HTTPException(502, {'code': 'invalid_transcript'})
    from .conversation_api import _safe_message
    return {'text': _safe_message(text), 'language': normalize(detected)}
