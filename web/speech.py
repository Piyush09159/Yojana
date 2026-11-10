"""Server-only Sarvam REST integration. No audio or provider payload is logged/stored."""
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


def stt(encoded, language):
    if os.getenv('SARVAM_STT_ENABLED', 'false').lower() != 'true':
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
    data = request('speech-to-text', files={'file': ('speech.wav', raw, 'audio/wav')},
                   data={'model': os.getenv('SARVAM_STT_MODEL', 'saaras:v3'), 'mode': 'transcribe',
                         'language_code': provider_code(language)})
    text = data.get('transcript')
    if not isinstance(text, str) or not text.strip() or len(text) > 1800:
        raise HTTPException(502, {'code': 'invalid_transcript'})
    from .conversation_api import _safe_message
    return {'text': _safe_message(text), 'language': normalize(data.get('language_code'))}
