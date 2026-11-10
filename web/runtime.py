"""Production lifecycle, request boundaries and safe readiness diagnostics."""
from contextlib import asynccontextmanager
import asyncio
import os
import time
from collections import OrderedDict
from fastapi.responses import JSONResponse
from . import case_store, inference


def production():
    return os.getenv('DEPLOYMENT_MODE') == 'production'


def validate_configuration():
    if not production():
        return
    if not os.getenv('DATABASE_URL', '').startswith(('postgresql://', 'postgres://')):
        raise RuntimeError('Production requires a PostgreSQL DATABASE_URL')
    if not case_store.auth_required():
        raise RuntimeError('Production requires SESSION_AUTH_REQUIRED=true')
    origins = [v.strip() for v in os.getenv('APP_ORIGINS', '').split(',') if v.strip()]
    if not origins or any(not v.startswith('https://') or '*' in v for v in origins):
        raise RuntimeError('APP_ORIGINS requires exact HTTPS frontend origins')
    if len(os.getenv('SESSION_SIGNING_KEY', '')) < 32:
        raise RuntimeError('SESSION_SIGNING_KEY must have at least 32 characters')
    from llm.config import resolve
    from urllib.parse import urlsplit
    if any(resolve(role)[0] in ('local', 'mock') for role in ('extractor', 'planner', 'agent')):
        raise RuntimeError('Production requires hosted inference; local and mock providers are disabled')
    if os.getenv('OPENAI_BASE_URL'):
        endpoint = urlsplit(os.environ['OPENAI_BASE_URL'])
        if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password:
            raise RuntimeError('Hosted OPENAI_BASE_URL must be HTTPS without URL credentials')


@asynccontextmanager
async def lifespan(app):
    validate_configuration()
    if case_store.enabled():
        await asyncio.to_thread(case_store.migrate)
        await asyncio.to_thread(case_store.purge)
    async def retention():
        while True:
            await asyncio.sleep(3600)
            try:
                await asyncio.to_thread(case_store.purge)
            except Exception:
                # No connection strings or citizen content in operational logs.
                import logging
                logging.getLogger(__name__).error('Retention cleanup failed; check database readiness')
    async def summaries():
        from .voice import deliver_summaries
        while True:
            await asyncio.sleep(10)
            try:
                await asyncio.to_thread(deliver_summaries)
            except Exception:
                import logging
                logging.getLogger(__name__).error('Summary queue pass failed; inspect service readiness')
    task = asyncio.create_task(retention()) if case_store.enabled() else None
    summary_task = asyncio.create_task(summaries()) if case_store.enabled() else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if summary_task:
            summary_task.cancel()
            try:
                await summary_task
            except asyncio.CancelledError:
                pass


def install(app):
    clients = OrderedDict()
    @app.middleware('http')
    async def boundaries(request, call_next):
        path = request.url.path
        if path.startswith('/api/'):
            max_body = 750000 if path == '/api/voice/stt' else 131072 if path == '/api/webhooks/waha' else 32768
            length = request.headers.get('content-length', '0')
            try:
                if int(length) > max_body:
                    return JSONResponse({'detail': 'Request too large'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Invalid content length'}, status_code=400)
            if request.method in ('POST', 'PATCH', 'PUT'):
                chunks = bytearray()
                async for chunk in request.stream():
                    chunks.extend(chunk)
                    if len(chunks) > max_body:
                        return JSONResponse({'detail': 'Request too large'}, status_code=413)
                request._body = bytes(chunks)  # Starlette's cached request replays this body downstream.
            if production() and request.method not in ('GET', 'HEAD', 'OPTIONS'):
                origin = request.headers.get('origin')
                allowed = os.getenv('APP_ORIGINS', '').split(',')
                if origin and origin not in [v.strip() for v in allowed]:
                    return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
            if production() and (path in ('/api/v2/conversation', '/api/sessions') or path.startswith('/api/voice/')):
                # Defense in depth; use EasyPanel/edge limits too. Ignore spoofable forwarded IPs.
                key = request.client.host if request.client else 'unknown'
                now = time.monotonic()
                window = [t for t in clients.pop(key, []) if now - t < 60]
                if len(window) >= int(os.getenv('REQUESTS_PER_MINUTE', '120')):
                    return JSONResponse({'detail': 'Request limit reached'}, status_code=429,
                                        headers={'Retry-After': '60', 'Cache-Control': 'no-store'})
                clients[key] = window + [now]
                if len(clients) > 2000:
                    clients.popitem(last=False)
        response = await call_next(request)
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Vercel-CDN-Cache-Control'] = 'no-store'
            response.headers['CDN-Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        return response

    @app.get('/healthz', include_in_schema=False)
    @app.get('/api/health/live')
    def live():
        return {'status': 'alive'}

    @app.get('/readyz', include_in_schema=False)
    @app.get('/api/health/ready')
    def ready():
        try:
            from discovery.catalog import load_catalog
            catalog_ok = bool(load_catalog())
            database_ok = case_store.ready() if case_store.enabled() else not production()
            ok = catalog_ok and database_ok
        except Exception:
            ok = False
        return JSONResponse({'status': 'ready' if ok else 'not_ready'}, status_code=200 if ok else 503)

    @app.get('/api/inference/status')
    def provider_status():
        return inference.status()
