"""Guide -> HTML (Jinja2, autoescaped) -> PDF (WeasyPrint, in memory).

Nothing is written to disk and nothing outside this package is fetched: the HTML has no external
resources (CSS is inline, QR codes are inline SVG data URIs), and the URL fetcher refuses every
URL that is not a data: URI, so a link in the payload can never make the service call out.
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from .i18n import LABELS, inr, labels
from .models import Guide

TEMPLATES = Path(__file__).parent / 'templates'
DEFAULT_QR_DOMAINS = ('gov.in', 'nic.in')


class RendererUnavailable(RuntimeError):
    """The PDF engine (WeasyPrint and its system libraries) cannot be loaded."""


def qr_domains(env=os.environ) -> tuple[str, ...]:
    raw = env.get('PDF_QR_DOMAINS', '')
    found = tuple(d.strip().lower().lstrip('.') for d in raw.split(',') if d.strip())
    return found or DEFAULT_QR_DOMAINS


def official(url: str | None, domains=DEFAULT_QR_DOMAINS) -> bool:
    """An https link on an official government domain: only these get a QR code."""
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    return parts.scheme == 'https' and any(host == d or host.endswith('.' + d) for d in domains)


def qr_data_uri(url: str) -> str:
    import segno
    return segno.make(url, error='m').svg_data_uri(scale=4, border=2, dark='#000000', light='#ffffff')


def _css_string(value: str) -> Markup:
    """A CSS string literal for our own labels inside <style>, where HTML escaping does not apply."""
    escaped = re.sub(r'(["\\\n<>])', lambda m: '\\a ' if m.group(1) == '\n' else '\\%x ' % ord(m.group(1)), value)
    return Markup('"' + escaped + '"')


def _day(value: date) -> str:
    return value.strftime('%d-%m-%Y')


_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(['html', 'j2']),
                   undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
_env.filters['css_string'] = _css_string
_env.filters['day'] = _day
_env.filters['inr'] = inr


def _scheme_view(scheme, domains):
    links = []
    for kind, url in (('apply_link', scheme.apply_url), ('source_link', scheme.source_url)):
        if url and url not in [link['url'] for link in links]:
            links.append({'kind': kind, 'url': url, 'qr': qr_data_uri(url) if official(url, domains) else None})
    steps = [line.strip() for line in scheme.how_to_apply.splitlines() if line.strip()]
    return {'s': scheme, 'links': links, 'steps': steps}


def guide_context(guides: list[Guide], *, today: date | None = None, env=os.environ) -> dict:
    domains = qr_domains(env)
    today = today or date.today()
    views = []
    for guide in guides:
        views.append({'g': guide, 'L': labels(guide.language), 'day': guide.generated_on or today,
                      'schemes': [_scheme_view(s, domains) for s in guide.schemes]})
    first = labels(guides[0].language)
    return {'guides': views, 'all_labels': LABELS, 'languages': sorted({g.language for g in guides}),
            'doc_title': first['title'], 'doc_lang': guides[0].language}


def guide_html(guides: list[Guide], *, today: date | None = None, env=os.environ) -> str:
    return _env.get_template('guide.html.j2').render(**guide_context(guides, today=today, env=env))


ALLOWED_PROTOCOLS = ('data',)


def safe_fetcher():
    """Only data: URIs (our own QR codes) may load; http, https, file and every other URL is
    refused, never fetched (WeasyPrint >= 68 URLFetcher API)."""
    from weasyprint.urls import URLFetcher
    return URLFetcher(allowed_protocols=ALLOWED_PROTOCOLS, allow_redirects=False, timeout=5)


class WeasyRenderer:
    name = 'weasyprint'

    def __init__(self, env=os.environ):
        self.env = env
        self._html = None

    def load(self):
        """Import WeasyPrint once; its system libraries (Pango, HarfBuzz) load here."""
        if self._html is None:
            try:
                from weasyprint import HTML
            except (ImportError, OSError) as exc:
                raise RendererUnavailable(type(exc).__name__) from exc
            self._html = HTML
        return self._html

    def render(self, guides: list[Guide], *, today: date | None = None) -> bytes:
        HTML = self.load()
        html = guide_html(guides, today=today, env=self.env)
        return HTML(string=html, base_url=str(TEMPLATES), url_fetcher=safe_fetcher()).write_pdf()
