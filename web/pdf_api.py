"""Browser download of the application guide: GET /api/v2/conversation/{session_id}/guide.pdf

The browser sends only its session id (plus the HttpOnly owner cookie it already has). The backend
checks ownership exactly like the conversation itself, rebuilds the shortlist deterministically
from the stored profile and the catalog (so the PDF shows what the engine decided, from the
unredacted catalog text), and asks the PDF service for the file with the server-side token.
The PDF is streamed straight back and never stored.

Off unless PDF_SERVICE_URL and PDF_SERVICE_TOKEN are set (then 404 pdf_disabled).
"""
from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from core.assessment import relevant_catalog
from . import case_store
from . import conversation_api as api
from .access import owner, require_memory
from .pdf_client import (PdfAuthError, PdfClient, PdfNotConfigured, PdfRejected, PdfServiceError,
                         PdfUnavailable, guide_from_cards, shortlist_cards)

router = APIRouter(tags=['Application guide'])
log = logging.getLogger('yojana.pdf_api')
SESSION_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
NO_STORE = {'Cache-Control': 'no-store, private', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff'}


def get_client() -> PdfClient:
    """Kept as a module function so tests can replace it."""
    return PdfClient.from_env()


def _state(session_id: str, token):
    if case_store.enabled():
        with case_store.transaction(session_id) as tx:
            return tx.session(session_id, token)['state']
    with api._lock:
        state = api._sessions.get(session_id)
    if not state or time.time() - state.get('updated', 0) > api._TTL:
        raise HTTPException(404, 'Session expired; start a new conversation')
    require_memory(state, token)
    return state


def _label(field: str, language: str) -> str:
    asked = api.QUESTION_TEXT.get(field, {}).get(language)
    if asked:
        return asked
    from core.fields import load_fields
    return (load_fields().get(field) or {}).get('description') or field.replace('_', ' ')


def _refuse(status, code, message, **headers):
    return JSONResponse({'code': code, 'message': message}, status_code=status, headers={**NO_STORE, **headers})


@router.get('/api/v2/conversation/{session_id}/guide.pdf')
def guide_pdf(session_id: str, request: Request, lang: str | None = None):
    if not SESSION_ID.match(session_id):
        raise HTTPException(404, 'Session not found')
    try:
        client = get_client()
    except PdfNotConfigured:
        return _refuse(404, 'pdf_disabled', 'Application guide downloads are not enabled on this server.')
    state = _state(session_id, owner(request))
    language = lang if lang in ('en', 'hi', 'mr') else (state.get('lang') if state.get('lang') in ('en', 'hi', 'mr') else 'en')
    profile = {**(state.get('profile') or {}), '_intent': state.get('intent_context') or {}}
    catalog = relevant_catalog(profile, api.load_catalog())
    shortlist = api._shortlist_public(profile, catalog) if catalog else {}
    guide = guide_from_cards(shortlist_cards(shortlist), catalog, language=language, field_label=_label)
    if not guide:
        return _refuse(404, 'no_schemes', 'There are no shortlisted schemes to put in a guide yet.')
    try:
        pdf = client.generate(guide)
    except PdfUnavailable as exc:
        log.warning('pdf service unavailable: %s', exc.code or exc.status or type(exc).__name__)
        return _refuse(503, 'pdf_unavailable', 'The guide service is starting up or busy. Try again in a minute.',
                       **{'Retry-After': '30'})
    except (PdfAuthError, PdfRejected, PdfServiceError) as exc:
        log.error('pdf service error: %s %s', type(exc).__name__, exc.status)
        return _refuse(502, 'pdf_failed', 'The guide could not be created. Please try again later.')
    name = f'yojana-guide-{language}.pdf'
    return Response(pdf, media_type='application/pdf',
                    headers={**NO_STORE, 'Content-Disposition': f'attachment; filename="{name}"'})
