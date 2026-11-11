"""POST /transcribe, GET /health, GET /ready. One model instance, one request at a time by default.

Request:  body = WAV bytes (mono, 16 kHz, PCM16, <=15 s); Content-Type audio/wav
          optional query ?language=en|hi|mr (hint from the chat's stored language)
          Authorization: Bearer <LOCAL_STT_TOKEN> when that variable is set
Response: {"text", "language", "language_probability", "seconds", "ms", "engine", "no_speech"}
Errors:   JSON {"code": ...}: 401 unauthorized, 413 too_large, 415 unsupported_media_type,
          422 invalid_wav|too_long|..., 503 not_ready|busy, 500 engine_error

Logs are one JSON line per request with status, timings, duration and language only. Audio and
transcripts are never logged, written to disk or kept after the response.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import audio as wav
from .engines import EngineUnavailable, LANGUAGES, build_engine

log = logging.getLogger('yojana.stt')
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter('%(levelname)s stt %(message)s'))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False
for noisy in ('faster_whisper', 'httpx', 'huggingface_hub'):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def _event(level, **fields):
    log.log(level, json.dumps(fields, sort_keys=True, ensure_ascii=True))


def _error(status, code):
    return JSONResponse({'code': code}, status_code=status)


def create_app(engine=None, *, env=os.environ, load_in_background=True):
    engine = engine if engine is not None else build_engine(env)
    concurrency = max(1, int(env.get('STT_CONCURRENCY', '1')))
    queue_wait = float(env.get('STT_QUEUE_WAIT_SECONDS', '20'))
    token = env.get('LOCAL_STT_TOKEN', '')
    slots = threading.BoundedSemaphore(concurrency)
    state = {'ready': False, 'error': None, 'loaded_ms': None}

    def load():
        started = time.perf_counter()
        try:
            engine.load()
            state.update(ready=True, error=None, loaded_ms=int((time.perf_counter() - started) * 1000))
            _event(logging.INFO, event='model_loaded', engine=engine.name, ms=state['loaded_ms'])
        except EngineUnavailable as exc:
            state.update(ready=False, error=str(exc))
            _event(logging.ERROR, event='model_unavailable', engine=engine.name, detail=str(exc))
        except Exception as exc:                                  # never crash the health endpoint
            state.update(ready=False, error=type(exc).__name__)
            _event(logging.ERROR, event='model_unavailable', engine=engine.name, detail=type(exc).__name__)

    @asynccontextmanager
    async def lifespan(_app):
        if load_in_background:
            threading.Thread(target=load, name='stt-load', daemon=True).start()
        else:
            load()
        yield

    app = FastAPI(title='yojana-stt', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.stt = state

    @app.get('/health')
    def health():
        return {'status': 'alive'}

    @app.get('/ready')
    def ready():
        if state['ready']:
            return {'status': 'ready', 'engine': engine.name}
        return JSONResponse({'status': 'not_ready', 'detail': state['error'] or 'loading'}, status_code=503)

    @app.post('/transcribe')
    async def transcribe(request: Request):
        started = time.perf_counter()
        status, code, info = 200, None, {}
        try:
            if token and not hmac.compare_digest(request.headers.get('authorization', ''), f'Bearer {token}'):
                status, code = 401, 'unauthorized'
                return _error(status, code)
            ctype = request.headers.get('content-type', '').split(';')[0].strip().lower()
            if ctype not in ('audio/wav', 'audio/x-wav', 'application/octet-stream'):
                status, code = 415, 'unsupported_media_type'
                return _error(status, code)
            if int(request.headers.get('content-length') or 0) > wav.MAX_BYTES:
                status, code = 413, 'too_large'
                return _error(status, code)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > wav.MAX_BYTES:
                    status, code = 413, 'too_large'
                    return _error(status, code)
            try:
                samples = wav.read_pcm16(bytes(body))
            except wav.AudioError as exc:
                status, code = exc.status, exc.code
                return _error(status, code)
            finally:
                body.clear()
            info['seconds'] = round(wav.seconds(samples), 2)
            hint = request.query_params.get('language')
            hint = hint if hint in LANGUAGES else None
            if not state['ready']:
                status, code = 503, 'not_ready'
                return _error(status, code)
            if wav.is_silent(samples):
                info['no_speech'] = True
                return {'text': '', 'language': hint, 'language_probability': None, 'no_speech': True,
                        'seconds': info['seconds'], 'ms': int((time.perf_counter() - started) * 1000),
                        'engine': engine.name}
            if not await anyio.to_thread.run_sync(lambda: slots.acquire(timeout=queue_wait)):
                status, code = 503, 'busy'
                return _error(status, code)
            try:
                result = await anyio.to_thread.run_sync(engine.transcribe, samples, hint)
            except Exception as exc:
                status, code = 500, 'engine_error'
                info['detail'] = type(exc).__name__
                return _error(status, code)
            finally:
                slots.release()
            text = str(result.get('text') or '').strip()
            info.update(language=result.get('language'), no_speech=not text)
            return {'text': text, 'language': result.get('language'),
                    'language_probability': result.get('language_probability'), 'no_speech': not text,
                    'seconds': info['seconds'], 'ms': int((time.perf_counter() - started) * 1000),
                    'engine': engine.name}
        finally:
            _event(logging.WARNING if status >= 500 else logging.INFO, event='transcribe', status=status,
                   code=code, ms=int((time.perf_counter() - started) * 1000), **info)

    return app
