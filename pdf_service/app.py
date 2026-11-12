"""GET /health, POST /api/v1/pdf/generate, POST /api/v1/pdf/generate-batch.

Request:  JSON (see pdf_service/models.py and docs/PDF_SERVICE.md)
          Authorization: Bearer <one of PDF_SERVICE_TOKENS>
Response: application/pdf bytes; Content-Disposition attachment (or ?disposition=inline)
Errors:   JSON {"code": ...}: 401 unauthorized, 413 too_large, 415 unsupported_media_type,
          422 invalid_request (+ field locations, never the submitted values),
          503 not_configured | not_ready | busy, 500 render_failed

Privacy: the PDF is built in memory and returned; nothing is written to disk or kept after the
response. Logs are one JSON line per request with status, timings and counts only, never names,
scheme content or any other payload value.
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
from datetime import date

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .models import Batch, Guide
from .render import RendererUnavailable, WeasyRenderer

VERSION = '1.0.0'
MIN_TOKEN_LENGTH = 24
PRIVATE_HEADERS = {'Cache-Control': 'no-store, private', 'Pragma': 'no-cache',
                   'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'}

log = logging.getLogger('yojana.pdf')
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter('%(levelname)s pdf %(message)s'))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False
for noisy in ('weasyprint', 'fontTools', 'fontTools.subset', 'fontTools.ttLib'):
    logging.getLogger(noisy).setLevel(logging.ERROR)


def _event(level, **fields):
    log.log(level, json.dumps(fields, sort_keys=True, ensure_ascii=True))


def _error(status, code, **extra):
    return JSONResponse({'code': code, **extra}, status_code=status, headers=PRIVATE_HEADERS)


def _int(env, name, default):
    try:
        return max(1, int(env.get(name, default)))
    except ValueError:
        return default


def service_tokens(env) -> list[str]:
    """Comma-separated so a new token can be added before the old one is removed (rotation).
    Tokens shorter than MIN_TOKEN_LENGTH are ignored: a weak secret is treated as no secret."""
    return [t for t in (x.strip() for x in env.get('PDF_SERVICE_TOKENS', '').split(',')) if len(t) >= MIN_TOKEN_LENGTH]


def _authorized(header: str, tokens: list[str]) -> bool:
    supplied = header[7:].strip() if header[:7].lower() == 'bearer ' else ''
    ok = False
    for token in tokens:                                   # compare against every token: constant work
        ok |= hmac.compare_digest(supplied.encode(), token.encode())
    return ok and bool(supplied)


def _errors(exc: ValidationError) -> list[dict]:
    """Where and why validation failed, without echoing what was submitted."""
    out = []
    for e in exc.errors(include_url=False, include_input=False, include_context=False)[:20]:
        out.append({'loc': [str(p)[:60] for p in e.get('loc', ())], 'type': e.get('type'), 'msg': str(e.get('msg'))[:200]})
    return out


class Busy(Exception):
    pass


def create_app(renderer=None, *, env=os.environ):
    renderer = renderer if renderer is not None else WeasyRenderer(env)
    tokens = service_tokens(env)
    max_body = _int(env, 'PDF_MAX_BODY_BYTES', 256 * 1024)
    queue_wait = float(env.get('PDF_QUEUE_WAIT_SECONDS', '15'))
    slots = threading.BoundedSemaphore(_int(env, 'PDF_CONCURRENCY', 2))
    state = {'ready': False, 'error': None}

    def load():
        try:
            renderer.load()
            state.update(ready=True, error=None)
        except RendererUnavailable as exc:
            state.update(ready=False, error=str(exc))
            _event(logging.ERROR, event='renderer_unavailable', renderer=renderer.name, detail=str(exc))

    @asynccontextmanager
    async def lifespan(_app):
        load()
        if not tokens:
            _event(logging.WARNING, event='not_configured', detail='PDF_SERVICE_TOKENS is empty or too short')
        yield

    app = FastAPI(title='yojana-pdf', version=VERSION, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.pdf = state

    @app.get('/health')
    def health():
        body = {'status': 'ok' if state['ready'] else 'unavailable', 'renderer': renderer.name,
                'version': VERSION, 'auth_configured': bool(tokens)}
        return JSONResponse(body, status_code=200 if state['ready'] else 503, headers=PRIVATE_HEADERS)

    async def read_json(request: Request):
        ctype = request.headers.get('content-type', '').split(';')[0].strip().lower()
        if ctype != 'application/json':
            raise _Reject(415, 'unsupported_media_type')
        try:
            declared = int(request.headers.get('content-length') or 0)
        except ValueError:
            raise _Reject(400, 'bad_content_length')
        if declared > max_body:
            raise _Reject(413, 'too_large', max_bytes=max_body)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_body:
                raise _Reject(413, 'too_large', max_bytes=max_body)
        return bytes(body)

    def render(guides):
        if not slots.acquire(timeout=queue_wait):
            raise Busy()
        try:
            return renderer.render(guides)
        finally:
            slots.release()

    async def handle(request: Request, model, kind: str):
        started = time.perf_counter()
        status, code, info = 200, None, {'kind': kind}
        try:
            if not tokens:
                status, code = 503, 'not_configured'
                return _error(status, code)
            if not _authorized(request.headers.get('authorization', ''), tokens):
                status, code = 401, 'unauthorized'
                return _error(status, code)
            try:
                raw = await read_json(request)
                payload = model.model_validate_json(raw)
            except _Reject as rej:
                status, code = rej.status, rej.code
                return _error(status, code, **rej.extra)
            except ValidationError as exc:
                status, code = 422, 'invalid_request'
                return _error(status, code, errors=_errors(exc))
            guides = payload.guides if isinstance(payload, Batch) else [payload]
            info.update(guides=len(guides), schemes=sum(len(g.schemes) for g in guides),
                        languages=sorted({g.language for g in guides}))
            if not state['ready']:
                status, code = 503, 'not_ready'
                return _error(status, code)
            try:
                pdf = await anyio.to_thread.run_sync(render, guides)
            except Busy:
                status, code = 503, 'busy'
                return JSONResponse({'code': code}, status_code=503, headers={**PRIVATE_HEADERS, 'Retry-After': '5'})
            except Exception as exc:                         # never leak a traceback or payload
                status, code = 500, 'render_failed'
                info['error'] = type(exc).__name__
                return _error(status, code)
            info['bytes'] = len(pdf)
            stamp = (guides[0].generated_on or date.today()).strftime('%Y%m%d')
            name = f'yojana-guide-{guides[0].language}-{stamp}.pdf' if kind == 'single' else f'yojana-guides-{stamp}.pdf'
            disposition = 'inline' if request.query_params.get('disposition') == 'inline' else 'attachment'
            headers = {**PRIVATE_HEADERS, 'Content-Disposition': f'{disposition}; filename="{name}"'}
            return Response(pdf, media_type='application/pdf', headers=headers)
        finally:
            _event(logging.INFO if status < 500 else logging.ERROR, event='generate', status=status, code=code,
                   ms=int((time.perf_counter() - started) * 1000), **info)

    @app.post('/api/v1/pdf/generate')
    async def generate(request: Request):
        return await handle(request, Guide, 'single')

    @app.post('/api/v1/pdf/generate-batch')
    async def generate_batch(request: Request):
        return await handle(request, Batch, 'batch')

    return app


class _Reject(Exception):
    def __init__(self, status, code, **extra):
        self.status, self.code, self.extra = status, code, extra
