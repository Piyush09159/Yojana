"""Backend client for the PDF service (pdf_service/, deployed separately on Render).

Only the backend calls the service; the service token never reaches a browser or WhatsApp.

    client = PdfClient.from_env()          # PDF_SERVICE_URL, PDF_SERVICE_TOKEN
    pdf = client.generate(guide_payload)   # bytes starting with %PDF-

Errors are typed so callers can answer the citizen sensibly:
    PdfNotConfigured   no URL/token in this deployment (feature off)
    PdfAuthError       401/403: the two sides hold different tokens
    PdfRejected        400/413/415/422: the payload was refused (a bug on our side, not retried)
    PdfUnavailable     connection error, timeout, 429/5xx after the retries (Render asleep or down)

guide_from_cards() turns engine shortlist cards (saathi/shortlist.card) into the service payload.
It copies only what the catalog already holds and never adds steps, deadlines or eligibility.
"""
from __future__ import annotations

import os
import time
from datetime import date
from urllib.parse import urlsplit

import httpx

GUIDE_STATUSES = ('eligible', 'eligible_check_manually', 'check_manually', 'near_miss', 'needs_info')
RETRY_STATUS = (429, 502, 503, 504)


class PdfServiceError(RuntimeError):
    def __init__(self, message, *, status=None, code=None, errors=None):
        super().__init__(message)
        self.status, self.code, self.errors = status, code, errors or []


class PdfNotConfigured(PdfServiceError):
    pass


class PdfAuthError(PdfServiceError):
    pass


class PdfRejected(PdfServiceError):
    pass


class PdfUnavailable(PdfServiceError):
    pass


def _local(host: str) -> bool:
    return host in ('localhost', '127.0.0.1', '::1') or host.endswith('.internal') or '.' not in host


class PdfClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 60.0, connect_timeout: float = 10.0,
                 retries: int = 2, backoff: float = 1.0, transport=None, sleep=time.sleep):
        base_url = (base_url or '').strip().rstrip('/')
        if not base_url or not token:
            raise PdfNotConfigured('PDF_SERVICE_URL and PDF_SERVICE_TOKEN must both be set')
        parts = urlsplit(base_url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise PdfNotConfigured('PDF_SERVICE_URL must be an http(s) URL')
        if parts.scheme == 'http' and not _local(parts.hostname):
            raise PdfNotConfigured('PDF_SERVICE_URL must use https outside a private network')
        self.base_url, self._token = base_url, token
        self.timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self.retries, self.backoff, self.sleep = max(0, retries), backoff, sleep
        self._transport = transport

    @classmethod
    def from_env(cls, env=os.environ, **kwargs):
        def number(name, default):
            try:
                return float(env.get(name) or default)
            except ValueError:
                return default
        return cls(env.get('PDF_SERVICE_URL', ''), env.get('PDF_SERVICE_TOKEN', ''),
                   timeout=number('PDF_SERVICE_TIMEOUT', 60), retries=int(number('PDF_SERVICE_RETRIES', 2)),
                   **kwargs)

    def __repr__(self):                                   # never show the token
        return f'PdfClient({self.base_url!r})'

    def _client(self, timeout=None):
        return httpx.Client(timeout=timeout or self.timeout, transport=self._transport, follow_redirects=False,
                            headers={'User-Agent': 'yojana-backend/pdf-client'})

    def health(self) -> dict:
        with self._client() as http:
            try:
                r = http.get(self.base_url + '/health')
            except httpx.HTTPError as exc:
                raise PdfUnavailable(f'PDF service unreachable ({type(exc).__name__})') from exc
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200:
            raise PdfUnavailable(f'PDF service not healthy (HTTP {r.status_code})', status=r.status_code,
                                 code=body.get('status'))
        return body

    def warm_up(self) -> bool:
        """Wake a sleeping free Render instance before a citizen asks for a PDF. Never raises."""
        try:
            return self.health().get('status') == 'ok'
        except PdfServiceError:
            return False

    def generate(self, guide: dict, *, inline: bool = False) -> bytes:
        return self._post('/api/v1/pdf/generate', guide, inline)

    def generate_batch(self, guides: list[dict], *, inline: bool = False) -> bytes:
        return self._post('/api/v1/pdf/generate-batch', {'guides': guides}, inline)

    def _post(self, path, payload, inline):
        url = self.base_url + path + ('?disposition=inline' if inline else '')
        headers = {'Authorization': f'Bearer {self._token}', 'Accept': 'application/pdf'}
        last = None
        with self._client() as http:
            for attempt in range(self.retries + 1):
                if attempt:
                    self.sleep(last[1] if last and last[1] else self.backoff * (2 ** (attempt - 1)))
                try:
                    r = http.post(url, json=payload, headers=headers)
                except httpx.TimeoutException as exc:
                    last = (PdfUnavailable(f'PDF service timed out ({type(exc).__name__})'), None)
                    continue
                except httpx.TransportError as exc:
                    last = (PdfUnavailable(f'PDF service unreachable ({type(exc).__name__})'), None)
                    continue
                if r.status_code == 200:
                    if r.headers.get('content-type', '').split(';')[0] != 'application/pdf' or not r.content.startswith(b'%PDF-'):
                        raise PdfServiceError('PDF service returned something that is not a PDF', status=200,
                                              code='bad_response')
                    return r.content
                error = _error_for(r)
                if r.status_code in RETRY_STATUS:
                    last = (error, _retry_after(r))
                    continue
                raise error
        raise last[0]


def _retry_after(r) -> float | None:
    try:
        return min(10.0, max(0.0, float(r.headers.get('retry-after', ''))))
    except ValueError:
        return None


def _error_for(r) -> PdfServiceError:
    try:
        body = r.json()
    except ValueError:
        body = {}
    code = body.get('code') if isinstance(body, dict) else None
    errors = body.get('errors', []) if isinstance(body, dict) else []
    status = r.status_code
    if status in (401, 403):
        return PdfAuthError('PDF service refused the token: PDF_SERVICE_TOKEN here must match one of '
                            'PDF_SERVICE_TOKENS on the service', status=status, code=code)
    if status == 503 and code == 'not_configured':
        return PdfUnavailable('PDF service has no PDF_SERVICE_TOKENS configured', status=status, code=code)
    if status in RETRY_STATUS or status >= 500:
        return PdfUnavailable(f'PDF service unavailable (HTTP {status}, {code or "no code"})', status=status, code=code)
    where = '; '.join('.'.join(str(p) for p in e.get('loc', [])) + ': ' + str(e.get('msg', ''))
                      for e in errors[:3] if isinstance(e, dict))
    return PdfRejected(f'PDF service rejected the request (HTTP {status}, {code or "no code"})' + (f': {where}' if where else ''),
                       status=status, code=code, errors=errors)


# ---------- payload from engine cards ----------

def _http_url(value) -> str | None:
    value = (value or '').strip()
    parts = urlsplit(value)
    return value if parts.scheme in ('http', 'https') and parts.hostname and not any(c.isspace() for c in value) else None


def _cut(text, limit) -> str:
    text = ' '.join(str(text or '').split()) if limit <= 600 else str(text or '').strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _field_label(field: str, language: str) -> str:
    try:
        from core.fields import load_fields
        description = (load_fields().get(field) or {}).get('description')
    except Exception:
        description = None
    return description or field.replace('_', ' ')


def scheme_from_card(card: dict, scheme: dict | None = None, *, language: str = 'en', field_label=None) -> dict | None:
    """One engine card -> one service `Scheme`. None when the card cannot go into a guide
    (ineligible, or no official source to cite)."""
    if card.get('status') not in GUIDE_STATUSES:
        return None
    scheme = scheme or {}
    source = _http_url(card.get('source_url') or scheme.get('source_url'))
    if not source:
        return None
    label = field_label or _field_label
    if scheme.get('documents'):
        documents = [{'name': _cut(d.get('name'), 400), 'issued_by': _cut(d.get('issued_by'), 200) or None}
                     for d in scheme['documents'] if (d.get('name') or '').strip()]
    else:
        documents = [{'name': _cut(name, 400)} for name in card.get('documents') or [] if str(name).strip()]
    local = scheme.get(f'name_{language}') if language != 'en' else None
    last = str(scheme.get('last_verified') or '')[:10]
    try:
        last = date.fromisoformat(last).isoformat()
    except ValueError:
        last = None
    amount = card.get('benefit_annual_inr') or scheme.get('benefit_annual_inr') or 0
    return {
        'id': str(card['id'])[:120],
        'name': _cut(card.get('name') or scheme.get('name'), 300),
        'name_local': _cut(local, 300) or None,
        'status': card['status'],
        'verified': bool(card.get('verified')),
        'benefit': _cut(card.get('benefit') or scheme.get('benefit_summary'), 1500),
        'benefit_annual_inr': int(amount) if isinstance(amount, (int, float)) and 0 < amount <= 100_000_000 else None,
        'conditions_met': [_cut(x, 400) for x in card.get('rules_met') or [] if str(x).strip()][:12],
        'check_yourself': [_cut(x, 600) for x in card.get('check_yourself') or [] if str(x).strip()][:12],
        'missing_information': [_cut(label(f, language), 120) for f in card.get('missing_requirements') or []][:20],
        'documents': documents[:25],
        'how_to_apply': _cut(card.get('how_to_apply') or scheme.get('how_to_apply'), 4000),
        'apply_url': _http_url(card.get('apply_url') or scheme.get('apply_url')),
        'source_url': source,
        'source_site': _cut(card.get('source_site') or scheme.get('source_site'), 200),
        'agency': _cut(card.get('agency') or scheme.get('agency_name') or scheme.get('ministry'), 300),
        'last_verified': last,
    }


def shortlist_cards(shortlist: dict, limit: int = 5) -> list[dict]:
    """The schemes the citizen was shown first: start_with, then the others, in order."""
    cards = ([shortlist['start_with']] if shortlist.get('start_with') else []) + list(shortlist.get('others') or [])
    return cards[:limit]


def guide_from_cards(cards: list[dict], catalog: dict | None = None, *, language: str = 'en',
                     recipient_name: str | None = None, reference: str | None = None, field_label=None,
                     max_schemes: int = 10) -> dict | None:
    """Service `Guide` payload, or None when no card can go into a guide."""
    catalog = catalog or {}
    language = language if language in ('en', 'hi', 'mr') else 'en'
    schemes, seen = [], set()
    for card in cards:
        item = scheme_from_card(card, catalog.get(card.get('id')), language=language, field_label=field_label)
        if item and item['id'] not in seen:
            seen.add(item['id'])
            schemes.append(item)
        if len(schemes) >= max_schemes:
            break
    if not schemes:
        return None
    guide = {'language': language, 'schemes': schemes}
    if recipient_name:
        guide['recipient_name'] = _cut(recipient_name, 80)
    if reference:
        guide['reference'] = reference
    return guide
