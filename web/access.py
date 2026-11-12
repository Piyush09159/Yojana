"""Owner capability shared by same-origin fetch and EventSource via HttpOnly cookie."""
import os
import secrets
from fastapi import HTTPException

COOKIE = 'yojana_owner'


def owner(request):
    header = request.headers.get('authorization', '')
    if header.startswith('Bearer '):
        return header[7:]
    return request.cookies.get(COOKIE)


def issue(request, response):
    token = owner(request) or secrets.token_urlsafe(32)
    response.set_cookie(COOKIE, token, httponly=True,
                        secure=os.getenv('DEPLOYMENT_MODE') == 'production',
                        samesite='lax', max_age=86400 * min(365, max(1, int(os.getenv('CASE_RETENTION_DAYS', '30')))))
    return token


def require_memory(state, token):
    from .case_store import auth_required, digest
    import hmac
    if auth_required() and (not token or not hmac.compare_digest(state.get('owner_hash', ''), digest(token))):
        raise HTTPException(403, 'Session access denied')
