"""Publicly safe provider status; working means an actual complete() succeeded."""
import os
import time
from llm.config import resolve

_status = {'state': 'not_configured', 'verified_at': None}


def configured():
    try:
        provider, model = resolve('extractor')
    except Exception:
        return False
    if provider == 'mock':
        return os.getenv('DEPLOYMENT_MODE') != 'production'
    key = {'openai': 'OPENAI_API_KEY', 'anthropic': 'ANTHROPIC_API_KEY', 'gemini': 'GEMINI_API_KEY'}.get(provider)
    if provider == 'openai' and os.getenv('OPENAI_BASE_URL') and not (os.getenv('EXTRACTOR_MODEL') or os.getenv('LLM_MODEL')):
        return False
    return bool(key and os.getenv(key) and model)


def status():
    if not configured():
        return {'state': 'not_configured', 'verified_at': None}
    return dict(_status) if _status['state'] != 'not_configured' else {'state': 'configured_unverified', 'verified_at': None}


def failure(exc):
    text = str(exc).lower()
    cause = exc
    limited = False
    while cause:
        limited |= getattr(cause, 'status_code', None) == 429
        cause = cause.__cause__
    state = 'rate_limited_or_quota_exhausted' if limited or any(s in text for s in ('429', 'quota', 'rate_limit', 'rate limit')) else 'provider_unavailable'
    if not configured():
        state = 'not_configured'
    _status.update(state=state, verified_at=None)
    return state


def succeeded(client):
    # Fixture providers never establish a successful real hosted connection.
    name = getattr(client, 'name', '')
    _status.update(state='mocked' if not name or name.startswith('mock:') else 'working', verified_at=time.time())
