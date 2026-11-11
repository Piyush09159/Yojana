"""Bounded in-memory WAHA media adapter. Never follows redirects or stores audio.

Audio is only ever fetched from the configured WAHA origin (WAHA_BASE_URL) with the WAHA API key.
WAHA builds `media.url` from its *own* WAHA_BASE_URL; if that differs from the backend's private
origin (e.g. WAHA still advertises http://localhost:3000), list the advertised origin in
WAHA_MEDIA_ORIGINS. Only the validated `/api/files/...` path is reused; the request still goes to the
trusted WAHA_BASE_URL, so a payload can never steer the API key to another host.

Every failure raises VoiceFailure (a ValueError) carrying a diagnostic code, so the bridge can send
a helpful text reply instead of staying silent.
"""
import base64
import binascii
import logging
import os
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit, unquote

import httpx
from fastapi import HTTPException
from . import speech

_slots = threading.BoundedSemaphore(2)
MAX_DOWNLOAD = 2_000_000
MAX_SECONDS = 15
# WhatsApp voice notes are audio/ogg; codecs=opus. Other entries cover forwarded audio files.
AUDIO_TYPES = {'audio/ogg', 'audio/opus', 'audio/oga', 'application/ogg', 'audio/wav', 'audio/x-wav',
               'audio/mpeg', 'audio/mp3', 'audio/mp4', 'audio/m4a', 'audio/x-m4a', 'audio/aac',
               'audio/webm', 'audio/amr', 'audio/3gpp'}
_FILE = r'[A-Za-z0-9_@.%-]+'


class VoiceFailure(ValueError):
    """A voice note could not be turned into text. `code` is a diagnostics code (web/diagnostics)."""

    def __init__(self, code, detail='', status=None):
        super().__init__(code if not detail else f'{code}: {detail}')
        self.code, self.detail, self.status = code, detail, status


class _NoMediaURLs(logging.Filter):
    def filter(self, record):
        # HTTPX INFO records include the full filename, which may contain a
        # citizen phone number. Never emit those request URLs to service logs.
        return '/api/files/' not in record.getMessage()


logging.getLogger('httpx').addFilter(_NoMediaURLs())


def mimetype(media):
    return str((media or {}).get('mimetype') or '').split(';')[0].strip().lower()


def _origin(url):
    parts = urlsplit(url.strip().rstrip('/'))
    return parts.scheme.lower(), parts.netloc.lower()


def media_url(media):
    """The URL to download this note from: always on the trusted WAHA origin."""
    if not isinstance(media, dict) or media.get('error'):
        raise VoiceFailure('WAHA_MEDIA_UNAVAILABLE', 'media missing or download error in WAHA')
    if mimetype(media) not in AUDIO_TYPES:
        raise VoiceFailure('WAHA_MEDIA_UNSUPPORTED', mimetype(media) or 'no mimetype')
    if not media.get('url'):
        raise VoiceFailure('WAHA_MEDIA_UNAVAILABLE', 'media.url is null (WAHA_EVENTS_DOWNLOAD_MEDIA)')
    candidate = urlsplit(str(media['url']))
    base = urlsplit(os.environ['WAHA_BASE_URL'].rstrip('/'))
    allowed = {(base.scheme.lower(), base.netloc.lower())}
    allowed |= {_origin(v) for v in os.getenv('WAHA_MEDIA_ORIGINS', '').split(',') if v.strip()}
    session = re.escape(os.getenv('WAHA_SESSION', 'default'))
    path = unquote(candidate.path)
    if (candidate.scheme not in ('http', 'https') or (candidate.scheme.lower(), candidate.netloc.lower()) not in allowed
            or candidate.username or candidate.password or candidate.query or candidate.fragment
            # WAHA serves files at /api/files/<file>; newer builds nest them per session.
            or not re.fullmatch(rf'/api/files/(?:{session}/)?{_FILE}', candidate.path)
            or '..' in path or path.count('/') > candidate.path.count('/')):
        raise VoiceFailure('WAHA_MEDIA_UNTRUSTED', 'media.url origin or path not allowed')
    return f'{base.scheme}://{base.netloc}{base.path.rstrip("/")}{candidate.path}'


def _inline(media):
    """Some engines/configurations deliver the file inline as base64 instead of a URL."""
    data = media.get('data') if isinstance(media, dict) else None
    if not isinstance(data, str) or not data:
        return None
    if data.startswith('data:'):
        data = data.split(',', 1)[-1]
    if len(data) > (MAX_DOWNLOAD * 4) // 3 + 8:
        raise VoiceFailure('VOICE_TOO_LONG', 'inline media too large')
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise VoiceFailure('WAHA_MEDIA_UNAVAILABLE', 'inline media is not base64') from None


def download(media):
    if isinstance(media, dict) and not media.get('url') and mimetype(media) in AUDIO_TYPES:
        audio = _inline(media)
        if audio:
            return audio
    url = media_url(media)
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url, headers={'X-Api-Key': os.environ['WAHA_API_KEY']}) as response:
                if response.status_code != 200:
                    raise VoiceFailure('WAHA_MEDIA_DOWNLOAD_FAILED', 'http status', response.status_code)
                if int(response.headers.get('content-length', '0') or 0) > MAX_DOWNLOAD:
                    raise VoiceFailure('VOICE_TOO_LONG', 'file larger than download limit')
                audio = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    audio.extend(chunk)
                    if len(audio) > MAX_DOWNLOAD:
                        raise VoiceFailure('VOICE_TOO_LONG', 'file larger than download limit')
                    if time.monotonic() - started > 12:
                        raise VoiceFailure('WAHA_MEDIA_DOWNLOAD_FAILED', 'download too slow')
    except httpx.HTTPError as exc:
        raise VoiceFailure('WAHA_MEDIA_DOWNLOAD_FAILED', type(exc).__name__) from None
    if not audio:
        raise VoiceFailure('WAHA_MEDIA_UNAVAILABLE', 'empty file')
    return bytes(audio)


def decode(audio):
    if not audio:
        raise VoiceFailure('WAHA_AUDIO_DECODE_FAILED', 'empty audio')
    if len(audio) > MAX_DOWNLOAD:
        raise VoiceFailure('VOICE_TOO_LONG', 'audio larger than limit')
    # pipe input only, no network/playlist demuxers; bounded 16s output rejected by
    # the shared 15s PCM validator. Single decoder thread + wall timeout bound CPU.
    try:
        result = subprocess.run([
            os.getenv('FFMPEG_BINARY', 'ffmpeg'), '-hide_banner', '-loglevel', 'error',
            '-nostdin', '-threads', '1', '-protocol_whitelist', 'pipe',
            '-format_whitelist', 'ogg,wav,mp3,mov,aac,matroska,webm,amr',
            '-i', 'pipe:0', '-t', '16', '-vn', '-ac', '1', '-ar', '16000',
            '-threads', '1', '-f', 's16le', 'pipe:1'], input=audio, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=8, check=True)
    except FileNotFoundError:
        raise VoiceFailure('WAHA_AUDIO_DECODE_FAILED', 'ffmpeg not installed') from None
    except subprocess.TimeoutExpired:
        raise VoiceFailure('WAHA_AUDIO_DECODE_FAILED', 'ffmpeg timeout') from None
    except (subprocess.SubprocessError, OSError):
        raise VoiceFailure('WAHA_AUDIO_DECODE_FAILED', 'ffmpeg could not decode') from None
    pcm = result.stdout
    if not pcm or len(pcm) % 2:
        raise VoiceFailure('WAHA_AUDIO_DECODE_FAILED', 'no audio samples')
    if len(pcm) > MAX_SECONDS * 16000 * 2:
        raise VoiceFailure('VOICE_TOO_LONG', f'longer than {MAX_SECONDS}s')
    import io
    import wave
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(pcm)
    return output.getvalue()


def transcribe(media):
    if os.getenv('SARVAM_STT_ENABLED', 'false').lower() != 'true':
        raise VoiceFailure('SARVAM_STT_DISABLED', 'set SARVAM_STT_ENABLED=true')
    if not _slots.acquire(blocking=False):
        raise VoiceFailure('VOICE_BUSY', 'two notes already decoding')
    try:
        wav = decode(download(media))
        try:
            return speech.stt(base64.b64encode(wav).decode('ascii'), 'auto')
        except HTTPException as exc:
            detail = exc.detail.get('code') if isinstance(exc.detail, dict) else 'transcript_rejected'
            if exc.status_code == 422 and not isinstance(exc.detail, dict):
                # _safe_message refused the transcript (spoken identity/bank number).
                raise VoiceFailure('VOICE_SENSITIVE_NUMBER', 'transcript contained a long number', 422) from None
            code = 'SARVAM_STT_DISABLED' if detail == 'speech_not_configured' else 'SARVAM_STT_FAILED'
            raise VoiceFailure(code, detail, exc.status_code) from None
    finally:
        _slots.release()
