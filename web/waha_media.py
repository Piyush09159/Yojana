"""Bounded in-memory WAHA media adapter. Never follows redirects or stores audio."""
import base64
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


class _NoMediaURLs(logging.Filter):
    def filter(self, record):
        # HTTPX INFO records include the full filename, which may contain a
        # citizen phone number. Never emit those request URLs to service logs.
        return '/api/files/' not in record.getMessage()


logging.getLogger('httpx').addFilter(_NoMediaURLs())


def media_url(media):
    if not isinstance(media, dict) or media.get('error'):
        raise ValueError('Media unavailable')
    mime = str(media.get('mimetype', '')).split(';')[0].lower()
    if mime not in {'audio/ogg','audio/opus','audio/wav','audio/x-wav','audio/mpeg','audio/mp4','audio/aac','audio/webm'}:
        raise ValueError('Not a supported voice note')
    candidate = urlsplit(str(media.get('url') or ''))
    trusted = urlsplit(os.environ['WAHA_BASE_URL'].rstrip('/'))
    if (candidate.scheme not in ('http','https') or candidate.scheme != trusted.scheme
            or candidate.netloc != trusted.netloc or candidate.username or candidate.password
            or candidate.query or candidate.fragment
            or not re.fullmatch(r'/api/files/[A-Za-z0-9_@.%-]+',candidate.path)
            or '..' in unquote(candidate.path) or '/' in unquote(candidate.path[len('/api/files/'):])):
        raise ValueError('Untrusted media location')
    return candidate.geturl()


def download(media):
    url = media_url(media)
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(8,connect=3),follow_redirects=False,trust_env=False) as client:
        with client.stream('GET',url,headers={'X-Api-Key':os.environ['WAHA_API_KEY']}) as response:
            response.raise_for_status()
            if int(response.headers.get('content-length','0')) > MAX_DOWNLOAD:
                raise ValueError('Voice note too large')
            audio = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                audio.extend(chunk)
                if len(audio)>MAX_DOWNLOAD or time.monotonic()-started>12:
                    raise ValueError('Voice note exceeds download limits')
    if not audio:
        raise ValueError('Empty voice note')
    return bytes(audio)


def decode(audio):
    if not audio or len(audio)>MAX_DOWNLOAD:
        raise ValueError('Voice note too large or empty')
    # pipe input only, no network/playlist demuxers; bounded 16s output rejected by
    # the shared 15s PCM validator. Single decoder thread + wall timeout bound CPU.
    try:
        result = subprocess.run([
            os.getenv('FFMPEG_BINARY','ffmpeg'), '-hide_banner','-loglevel','error',
            '-nostdin','-threads','1','-protocol_whitelist','pipe',
            '-format_whitelist','ogg,wav,mp3,mov,aac,matroska,webm',
            '-i','pipe:0','-t','16','-vn','-ac','1','-ar','16000',
            '-threads','1','-f','s16le','pipe:1'],input=audio,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,timeout=8,check=True)
    except (subprocess.SubprocessError,OSError):
        raise ValueError('Voice note cannot be decoded') from None
    pcm = result.stdout
    if not pcm or len(pcm)>15*16000*2 or len(pcm)%2:
        raise ValueError('Voice note must be under 15 seconds')
    import io
    import wave
    output=io.BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
    return output.getvalue()


def transcribe(media):
    if os.getenv('SARVAM_STT_ENABLED','false').lower() != 'true':
        raise HTTPException(503,'Voice transcription unavailable; please type')
    if not _slots.acquire(blocking=False):
        raise HTTPException(503,'Voice processing busy; retry or type')
    try:
        wav=decode(download(media))
        return speech.stt(base64.b64encode(wav).decode('ascii'),'auto')
    finally:
        _slots.release()
