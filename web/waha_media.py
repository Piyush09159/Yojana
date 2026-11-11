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
_FILE = r'[A-Za-z0-9_@.:+=%-]+'  # NOWEB: false_<jid>_<id>.<ext>


class VoiceFailure(ValueError):
    """A voice note could not be turned into text. `code` is a diagnostics code (web/diagnostics)."""

    def __init__(self, code, detail='', status=None):
        super().__init__(code if not detail else f'{code}: {detail}')
        self.code, self.detail, self.status = code, detail, status
        self.flags = {}


class _NoMediaURLs(logging.Filter):
    def filter(self, record):
        # HTTPX INFO records include the full filename, which may contain a
        # citizen phone number. Never emit those request URLs to service logs.
        return '/api/files/' not in record.getMessage()


logging.getLogger('httpx').addFilter(_NoMediaURLs())


def mimetype(media):
    return str((media or {}).get('mimetype') or '').split(';')[0].strip().lower()


_DEFAULT_PORTS = {'http': 80, 'https': 443}
# WAHA's own default when its WAHA_BASE_URL is unset: http://{WHATSAPP_API_HOSTNAME=localhost}:{PORT=3000}
_WAHA_DEFAULT_HOSTS = {'localhost', '127.0.0.1'}


def _endpoint(url):
    """(scheme, host, port, path prefix) with default ports filled in and case normalised."""
    parts = urlsplit(str(url).strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = None
    return scheme, (parts.hostname or '').lower(), port, parts.path.rstrip('/')


def _host_kind(host):
    if host in _WAHA_DEFAULT_HOSTS:
        return 'localhost'
    if not host:
        return 'none'
    if '.' not in host or host.endswith(('.internal', '.local')) or re.fullmatch(r'[\d.]+', host):
        return 'private'
    return 'public'


def _untrusted(check, **flags):
    """Reject with the name of the failed check. Flags are categorical only: never URLs or names."""
    err = VoiceFailure('WAHA_MEDIA_UNTRUSTED', f'check={check}')
    err.flags = {'check': check, **flags}
    return err


def _trusted_endpoints():
    base = _endpoint(os.environ['WAHA_BASE_URL'])
    trusted = [('base', base)]
    trusted += [('media_origins', _endpoint(v)) for v in os.getenv('WAHA_MEDIA_ORIGINS', '').split(',') if v.strip()]
    # WAHA advertises http://localhost:<port> unless its own WAHA_BASE_URL is set. That name can only
    # mean "the WAHA instance itself"; the download still goes to the backend's WAHA_BASE_URL below.
    trusted += [('waha_default', ('http', h, port, '')) for h in _WAHA_DEFAULT_HOSTS
                for port in {3000, base[2]} if port]
    return base, trusted


def media_url(media):
    """The URL to download this note from: always on the trusted WAHA origin.

    Only the validated `/api/files/[<session>/]<file>` part of media.url is kept; scheme, host and
    port always come from WAHA_BASE_URL, so the payload can never steer the request or the API key."""
    if not isinstance(media, dict) or media.get('error'):
        raise VoiceFailure('WAHA_MEDIA_UNAVAILABLE', 'media missing or download error in WAHA')
    if mimetype(media) not in AUDIO_TYPES:
        raise VoiceFailure('WAHA_MEDIA_UNSUPPORTED', mimetype(media) or 'no mimetype')
    if not media.get('url'):
        raise VoiceFailure('WAHA_MEDIA_UNAVAILABLE', 'media.url is null (WAHA_EVENTS_DOWNLOAD_MEDIA)')
    raw = str(media['url']).strip()
    candidate = urlsplit(raw)
    scheme, host, port, path = _endpoint(raw)
    base, trusted = _trusted_endpoints()
    facts = {'scheme': scheme or 'none', 'origin_kind': _host_kind(host),
             'host_match': host == base[1], 'port_match': port == base[2]}
    if scheme not in ('http', 'https'):
        raise _untrusted('scheme', **facts)
    if not host or port is None:
        raise _untrusted('origin', **facts)            # malformed host/port never matches anything
    if candidate.username or candidate.password:
        raise _untrusted('userinfo', **facts)
    if candidate.query:
        raise _untrusted('query', **facts)        # e.g. S3 presigned URLs: not supported
    if candidate.fragment:
        raise _untrusted('fragment', **facts)
    match = next(((name, prefix) for name, (s, h, p, prefix) in trusted
                  if (s, h, p) == (scheme, host, port) and (path + '/').startswith(prefix + '/api/files/')), None)
    if match is None:
        same_origin = any((s, h, p) == (scheme, host, port) for _, (s, h, p, _) in trusted)
        raise _untrusted('path_prefix' if same_origin else 'origin', **facts)
    facts['origin_kind'] = match[0]
    rest = candidate.path[len(match[1]):]                  # '/api/files/...'
    # WAHA does not percent-encode; tolerate only an encoded '@' (%40) from intermediaries.
    if re.search(r'%(?!40)', rest, re.I):
        raise _untrusted('encoded_separator', **facts)
    if '..' in unquote(rest).split('/') or '\\' in unquote(rest):
        raise _untrusted('traversal', **facts)
    session = os.getenv('WAHA_SESSION', 'default')
    segments = rest[len('/api/files/'):].split('/')
    if len(segments) == 2 and segments[0] != session:
        raise _untrusted('session', **facts)
    if len(segments) not in (1, 2):
        raise _untrusted('path_depth', **facts)
    if not re.fullmatch(_FILE, segments[-1]) or segments[-1].startswith('.'):
        raise _untrusted('filename', **facts)
    return f'{base[0]}://{urlsplit(os.environ["WAHA_BASE_URL"].strip()).netloc}{base[3]}{rest}'


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


def transcribe(media, hint=None):
    """Download, decode and transcribe one note. `hint` is the chat's stored language (en/hi/mr)."""
    provider = speech.stt_provider()
    if provider == 'off':
        raise VoiceFailure('STT_DISABLED', 'set STT_PROVIDER=local')
    if not _slots.acquire(blocking=False):
        raise VoiceFailure('VOICE_BUSY', 'two notes already decoding')
    try:
        wav = decode(download(media))
        try:
            # The chat's language is only a tie-breaker for the local engine. Sarvam would treat it as
            # a fixed language, so Sarvam keeps auto-detection as before.
            language = (hint or 'auto') if provider == 'local' else 'auto'
            result = speech.stt(base64.b64encode(wav).decode('ascii'), language)
            return {**result, 'provider': provider}
        except HTTPException as exc:
            detail = exc.detail.get('code') if isinstance(exc.detail, dict) else 'transcript_rejected'
            if exc.status_code == 422 and not isinstance(exc.detail, dict):
                # _safe_message refused the transcript (spoken identity/bank number).
                raise VoiceFailure('VOICE_SENSITIVE_NUMBER', 'transcript contained a long number', 422) from None
            prefix = 'LOCAL' if provider == 'local' else 'SARVAM'
            if detail == 'no_speech':
                code = 'VOICE_NO_SPEECH'
            elif detail == 'speech_not_configured':
                code = f'{prefix}_STT_DISABLED'
            else:
                code = f'{prefix}_STT_FAILED'
            raise VoiceFailure(code, detail, exc.status_code) from None
    finally:
        _slots.release()
