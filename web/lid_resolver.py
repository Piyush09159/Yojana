"""Resolve a WhatsApp @lid chat to the phone number WhatsApp itself maps it to (WAHA lids API).

WAHA: GET /api/{session}/lids/{lid} (X-Api-Key) -> {"lid": "<digits>@lid", "pn": "<digits>@c.us" | null}.
Only the `pn` WAHA returns is ever used, and only when it passes the same strict phone-JID and
country allow-list check as a direct @c.us chat (followup_calls.trusted_phone). The digits of the LID
are NEVER used to build a number. The chat identity stays the LID: this module only supplies the
number that the call offer shows (last 4 digits) and that CALL ME consent, given from that same LID
chat, may later authorise. Nothing here logs a lid, a number or a response body.

Outcomes: ('ok', '+91...'), ('unmapped', None) when WAHA has no mapping (pn null/404),
('invalid', None) when the answer is malformed, mismatched or not an allowed number, and
('error', None) for transport/5xx/auth problems (the caller may retry those).
"""
from __future__ import annotations

import re

import httpx

LID_RE = re.compile(r'([0-9]{1,20})@lid')
TIMEOUT = 6


def is_lid(chat) -> bool:
    return isinstance(chat, str) and LID_RE.fullmatch(chat) is not None


def resolve(url: str, key: str, session: str, lid: str):
    """Returns (outcome, e164_or_None). Never raises."""
    from .followup_calls import trusted_phone
    m = LID_RE.fullmatch(lid or '')
    if not m or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', session or ''):
        return 'invalid', None
    try:
        # The lid id is digits only (validated above), so it cannot alter the URL path.
        resp = httpx.get(f'{url}/api/{session}/lids/{m.group(1)}@lid',
                         headers={'X-Api-Key': key}, timeout=TIMEOUT)
    except httpx.HTTPError:
        return 'error', None
    if resp.status_code == 404:
        return 'unmapped', None
    if resp.status_code != 200:
        return 'error', None
    try:
        body = resp.json()
    except ValueError:
        return 'invalid', None
    if not isinstance(body, dict):
        return 'invalid', None
    returned_lid = body.get('lid')
    if returned_lid is not None and returned_lid != lid:        # answer for a different identity
        return 'invalid', None
    pn = body.get('pn')
    if pn in (None, ''):
        return 'unmapped', None
    if not isinstance(pn, str):
        return 'invalid', None
    phone = trusted_phone(pn)                                    # strict <digits>@c.us + allow-list
    return ('ok', phone) if phone else ('invalid', None)
