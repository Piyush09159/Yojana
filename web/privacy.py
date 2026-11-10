"""Small data minimization boundary shared by stored cases and channel adapters."""
import re

IDENTIFIERS = re.compile(r'(?<!\d)(?:\d[ -]?){9,18}(?!\d)')
EMAIL = re.compile(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b')


def redact(text: str) -> str:
    return EMAIL.sub('[email removed]', IDENTIFIERS.sub('[number removed]', text))[:4000]


def minimize(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [minimize(v) for v in value]
    if isinstance(value, dict):
        structural = {'id', 'session_id', 'owner_hash', 'last_request_id', 'request_hash', 'facts_hash', 'source_url'}
        return {k: v if k in structural else minimize(v) for k, v in value.items()
                if k not in {'records', 'access_token', 'session_token', 'phone', 'account_number', 'aadhaar_number'}}
    return value
