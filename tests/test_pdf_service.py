"""pdf_service HTTP contract, payload rules, HTML safety, the backend client and the guide.pdf
download route. Offline: a fake renderer stands in for WeasyPrint (the real render is
tests/test_pdf_render_real.py, run inside the Docker image), and httpx.MockTransport stands in
for the network."""
import json
import logging
import threading

import httpx
import pytest

pytest.importorskip('jinja2')
pytest.importorskip('segno')

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pdf_service import render as R
from pdf_service.app import create_app, service_tokens
from pdf_service.i18n import LABELS, inr
from pdf_service.models import Guide
from pdf_service.render import RendererUnavailable, guide_html, official
from web import pdf_api, pdf_client as PC

TOKEN = 'test-token-0123456789abcdefghij'
OTHER = 'second-token-0123456789abcdefgh'
NAME = 'Sunita Synthetic-Person'


class FakeRenderer:
    name = 'fake'

    def __init__(self, fail_load=False, error=None, gate=None):
        self.fail_load, self.error, self.gate = fail_load, error, gate
        self.calls = []

    def load(self):
        if self.fail_load:
            raise RendererUnavailable('OSError')

    def render(self, guides, today=None):
        self.calls.append(guides)
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return b'%PDF-1.7\n% fake\n'


def scheme(**over):
    base = {'id': 'pm_kisan', 'name': 'PM-KISAN', 'status': 'eligible', 'verified': True,
            'benefit': 'Rs 6000 a year in three instalments', 'benefit_annual_inr': 6000,
            'conditions_met': ['Family owns cultivable land'], 'documents': [{'name': 'Land record', 'issued_by': 'Talathi'}],
            'how_to_apply': 'Register on the portal.\nOr visit the CSC.', 'apply_url': 'https://pmkisan.gov.in/registrationform',
            'source_url': 'https://pmkisan.gov.in/', 'source_site': 'pmkisan.gov.in', 'agency': 'Ministry of Agriculture',
            'last_verified': '2026-10-21'}
    return {**base, **over}


def guide(**over):
    return {'language': 'en', 'recipient_name': NAME, 'schemes': [scheme()], **over}


def client(renderer=None, **env):
    env = {'PDF_SERVICE_TOKENS': TOKEN, **env}
    return TestClient(create_app(renderer or FakeRenderer(), env=env))


AUTH = {'Authorization': f'Bearer {TOKEN}'}


# ---------- service ----------

def test_health_reports_renderer_state():
    with client() as c:
        r = c.get('/health')
    assert r.status_code == 200 and r.json() == {'status': 'ok', 'renderer': 'fake', 'version': '1.0.0', 'auth_configured': True}
    assert r.headers['cache-control'].startswith('no-store')
    with client(FakeRenderer(fail_load=True)) as c:
        assert c.get('/health').status_code == 503


def test_generate_returns_a_private_pdf_download():
    renderer = FakeRenderer()
    with client(renderer) as c:
        r = c.post('/api/v1/pdf/generate', json=guide(generated_on='2026-11-11'), headers=AUTH)
        inline = c.post('/api/v1/pdf/generate?disposition=inline', json=guide(), headers=AUTH)
    assert r.status_code == 200 and r.content.startswith(b'%PDF-')
    assert r.headers['content-type'] == 'application/pdf'
    assert r.headers['content-disposition'] == 'attachment; filename="yojana-guide-en-20261111.pdf"'
    assert NAME not in r.headers['content-disposition']                   # no name in the file name
    assert r.headers['cache-control'] == 'no-store, private'
    assert inline.headers['content-disposition'].startswith('inline;')
    assert renderer.calls[0][0].schemes[0].id == 'pm_kisan'


def test_batch_returns_one_pdf_for_several_guides():
    renderer = FakeRenderer()
    body = {'guides': [guide(), guide(language='hi'), guide(language='mr')]}
    with client(renderer) as c:
        r = c.post('/api/v1/pdf/generate-batch', json=body, headers=AUTH)
    assert r.status_code == 200 and r.headers['content-type'] == 'application/pdf'
    assert [g.language for g in renderer.calls[0]] == ['en', 'hi', 'mr']


def test_no_or_weak_token_configured_refuses_generation():
    with TestClient(create_app(FakeRenderer(), env={})) as c:
        assert c.post('/api/v1/pdf/generate', json=guide(), headers=AUTH).json() == {'code': 'not_configured'}
    assert service_tokens({'PDF_SERVICE_TOKENS': 'short, ' + TOKEN}) == [TOKEN]


def test_token_is_required_and_rotation_accepts_either():
    with client(PDF_SERVICE_TOKENS=f'{TOKEN},{OTHER}') as c:
        assert c.post('/api/v1/pdf/generate', json=guide()).status_code == 401
        assert c.post('/api/v1/pdf/generate', json=guide(), headers={'Authorization': 'Bearer ' + 'x' * 31}).status_code == 401
        assert c.post('/api/v1/pdf/generate', json=guide(), headers={'Authorization': TOKEN}).status_code == 401
        assert c.post('/api/v1/pdf/generate', json=guide(), headers={'Authorization': 'Bearer ' + OTHER}).status_code == 200


def test_media_type_and_size_limits():
    with client(PDF_MAX_BODY_BYTES='600') as c:
        r = c.post('/api/v1/pdf/generate', content=b'{}', headers={**AUTH, 'Content-Type': 'text/plain'})
        assert r.status_code == 415
        big = guide(schemes=[scheme(benefit='x' * 900)])
        r = c.post('/api/v1/pdf/generate', json=big, headers=AUTH)
        assert r.status_code == 413 and r.json()['code'] == 'too_large'


@pytest.mark.parametrize('body', [
    guide(language='ta'),
    guide(extra='field'),
    guide(schemes=[]),
    guide(schemes=[scheme(status='ineligible')]),                       # never a guide for a refusal
    guide(schemes=[scheme(source_url='javascript:alert(1)')]),
    guide(schemes=[scheme(apply_url='ftp://example.gov.in/x')]),
    guide(schemes=[scheme(source_url='https://user:pw@pmkisan.gov.in/')]),
    guide(schemes=[scheme(), scheme()]),                                # duplicate scheme
    guide(schemes=[scheme(id=f's{i}') for i in range(11)]),             # over PDF_MAX_SCHEMES
    guide(schemes=[scheme(name='bad\x00name')]),
    guide(schemes=[scheme(benefit_annual_inr=-5)]),
    guide(reference='has spaces'),
    guide(deadline='2026-12-31'),                                       # no field for invented deadlines
])
def test_invalid_payloads_are_rejected_without_echoing_values(body):
    with client() as c:
        r = c.post('/api/v1/pdf/generate', json=body, headers=AUTH)
    assert r.status_code == 422 and r.json()['code'] == 'invalid_request'
    assert r.json()['errors'] and NAME not in r.text and 'alert(1)' not in r.text and 'pw@' not in r.text


def test_invalid_json_and_batch_limits():
    with client() as c:
        r = c.post('/api/v1/pdf/generate', content=b'{"language": ', headers={**AUTH, 'Content-Type': 'application/json'})
        assert r.status_code == 422
        many = {'guides': [guide(schemes=[scheme(id=f'g{g}s{i}') for i in range(6)]) for g in range(5)]}
        assert c.post('/api/v1/pdf/generate-batch', json=many, headers=AUTH).status_code == 422   # 30 > 25
        assert c.post('/api/v1/pdf/generate-batch', json={'guides': [guide()] * 6}, headers=AUTH).status_code == 422


def test_busy_renderer_answers_503_with_retry_after():
    gate = threading.Event()
    renderer = FakeRenderer(gate=gate)
    app = create_app(renderer, env={'PDF_SERVICE_TOKENS': TOKEN, 'PDF_CONCURRENCY': '1', 'PDF_QUEUE_WAIT_SECONDS': '0.05'})
    with TestClient(app) as c:
        first = threading.Thread(target=lambda: c.post('/api/v1/pdf/generate', json=guide(), headers=AUTH))
        first.start()
        for _ in range(100):
            if renderer.calls:
                break
            threading.Event().wait(0.01)
        r = c.post('/api/v1/pdf/generate', json=guide(), headers=AUTH)
        gate.set()
        first.join()
    assert r.status_code == 503 and r.json() == {'code': 'busy'} and r.headers['retry-after'] == '5'


def test_render_failure_is_a_500_without_details():
    with client(FakeRenderer(error=RuntimeError('secret detail ' + NAME))) as c:
        r = c.post('/api/v1/pdf/generate', json=guide(), headers=AUTH)
    assert r.status_code == 500 and r.json() == {'code': 'render_failed'}


def test_logs_hold_counts_only(caplog):
    records = []
    handler = logging.Handler()
    handler.emit = lambda rec: records.append(rec.getMessage())
    logger = logging.getLogger('yojana.pdf')
    logger.addHandler(handler)
    try:
        with client() as c:
            c.post('/api/v1/pdf/generate', json=guide(), headers=AUTH)
            c.post('/api/v1/pdf/generate', json=guide(language='xx'), headers=AUTH)
    finally:
        logger.removeHandler(handler)
    events = [json.loads(m) for m in records if m.startswith('{')]
    assert [e['status'] for e in events] == [200, 422]
    assert events[0]['schemes'] == 1 and events[0]['languages'] == ['en'] and events[0]['bytes'] > 0
    text = ' '.join(records)
    assert NAME not in text and 'PM-KISAN' not in text and TOKEN not in text


# ---------- HTML ----------

def test_html_escapes_payload_and_has_no_external_resources():
    g = Guide.model_validate(guide(schemes=[scheme(name='<script>alert(1)</script>', how_to_apply='<img src=x>')]))
    html = guide_html([g])
    assert '<script>alert' not in html and '&lt;script&gt;' in html and '<img src=x>' not in html
    for attr in ('src="http', "src='http", 'url(http', '@import', '<link'):
        assert attr not in html


def test_qr_codes_only_for_official_https_links():
    assert official('https://pmkisan.gov.in/x') and official('https://maharashtra.nic.in')
    assert not official('http://pmkisan.gov.in/') and not official('https://evilgov.in/') and not official('https://example.com')
    g = Guide.model_validate(guide(schemes=[scheme(apply_url='https://example.com/apply')]))
    html = guide_html([g])
    assert html.count('data:image/svg+xml') == 1                          # source (gov.in) only
    assert guide_html([g], env={'PDF_QR_DOMAINS': 'example.com'}).count('data:image/svg+xml') == 1


def test_languages_and_honest_wording():
    assert all(set(LABELS[lang]) == set(LABELS['en']) for lang in LABELS)
    assert all(set(LABELS[lang]['status_text']) == set(LABELS['en']['status_text']) for lang in LABELS)
    for text in LABELS['en']['status_text'].values():
        assert 'you are eligible' not in text.lower() and 'you qualify' not in text.lower()
    hi = guide_html([Guide.model_validate(guide(language='hi'))])
    en = guide_html([Guide.model_validate(guide())])
    assert LABELS['hi']['title'] in hi and LABELS['hi']['not_translated'] in hi and 'lang="hi"' in hi
    assert LABELS['en']['not_translated'] not in en
    assert 'PM-KISAN' in hi                                                # scheme content kept as published


def test_missing_steps_and_documents_say_so_instead_of_inventing():
    g = Guide.model_validate(guide(schemes=[scheme(how_to_apply='', documents=[], verified=False)]))
    html = guide_html([g])
    assert LABELS['en']['no_steps'] in html and LABELS['en']['no_documents'] in html
    assert LABELS['en']['unverified'] in html


def test_indian_grouping_and_css_strings():
    assert [inr(n) for n in (0, 999, 1000, 120000, 12345678)] == ['₹0', '₹999', '₹1,000', '₹1,20,000', '₹1,23,45,678']
    assert R._css_string('a"b</style>') == '"a\\22 b\\3c /style\\3e "'


def test_only_inline_data_may_load():
    assert R.ALLOWED_PROTOCOLS == ('data',)            # enforced by WeasyPrint: tests/test_pdf_render_real.py


# ---------- backend client ----------

def transport(*responses, calls=None):
    seq = list(responses)

    def handle(request):
        if calls is not None:
            calls.append(request)
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        return item
    return httpx.MockTransport(handle)


def pdf_client(*responses, calls=None, **kw):
    return PC.PdfClient('https://yojana-pdf.onrender.com', TOKEN, transport=transport(*responses, calls=calls),
                        sleep=lambda s: None, **kw)


PDF = httpx.Response(200, content=b'%PDF-1.7 x', headers={'content-type': 'application/pdf'})


def test_client_returns_pdf_and_sends_token():
    calls = []
    assert pdf_client(PDF, calls=calls).generate(guide()) == b'%PDF-1.7 x'
    assert calls[0].headers['authorization'] == f'Bearer {TOKEN}' and calls[0].url.path == '/api/v1/pdf/generate'
    assert TOKEN not in repr(pdf_client(PDF))


def test_client_retries_cold_start_then_succeeds():
    calls = []
    busy = httpx.Response(503, json={'code': 'busy'}, headers={'retry-after': '1'})
    assert pdf_client(httpx.ConnectError('down'), busy, PDF, calls=calls).generate(guide()) == b'%PDF-1.7 x'
    assert len(calls) == 3


def test_client_errors_are_typed_and_meaningful():
    calls = []
    with pytest.raises(PC.PdfAuthError) as auth:
        pdf_client(httpx.Response(401, json={'code': 'unauthorized'}), calls=calls).generate(guide())
    assert len(calls) == 1 and 'PDF_SERVICE_TOKENS' in str(auth.value)
    bad = httpx.Response(422, json={'code': 'invalid_request', 'errors': [{'loc': ['schemes', 0, 'source_url'], 'msg': 'only absolute http(s) URLs are allowed'}]})
    with pytest.raises(PC.PdfRejected) as rejected:
        pdf_client(bad).generate(guide())
    assert 'schemes.0.source_url' in str(rejected.value) and rejected.value.status == 422
    calls = []
    with pytest.raises(PC.PdfUnavailable):
        pdf_client(httpx.ConnectTimeout('slow'), calls=calls, retries=2).generate(guide())
    assert len(calls) == 3
    with pytest.raises(PC.PdfServiceError, match='not a PDF'):
        pdf_client(httpx.Response(200, text='<html>', headers={'content-type': 'text/html'})).generate(guide())


def test_client_configuration_is_checked():
    with pytest.raises(PC.PdfNotConfigured):
        PC.PdfClient.from_env({})
    with pytest.raises(PC.PdfNotConfigured):
        PC.PdfClient('http://yojana-pdf.onrender.com', TOKEN)                 # token over plain http
    assert PC.PdfClient('http://localhost:8002', TOKEN).base_url == 'http://localhost:8002'
    health = httpx.Response(200, json={'status': 'ok'})
    assert pdf_client(health).warm_up() is True
    assert pdf_client(httpx.ConnectError('asleep')).warm_up() is False


def card(**over):
    base = {'id': 'pm_kisan', 'name': 'PM-KISAN', 'status': 'eligible', 'verified': True, 'benefit': 'Rs 6000',
            'benefit_annual_inr': 6000, 'documents': ['Land record'], 'how_to_apply': 'Apply on the portal',
            'apply_url': '', 'source_url': 'https://pmkisan.gov.in/', 'source_site': 'pmkisan.gov.in', 'agency': '',
            'rules_met': ['Owns land'], 'check_yourself': [], 'missing_requirements': ['annual_family_income_inr']}
    return {**base, **over}


def test_guide_from_cards_copies_only_catalog_facts():
    catalog = {'pm_kisan': {'name_hi': 'पीएम-किसान', 'last_verified': '2026-10-21',
                            'documents': [{'name': 'Land record', 'issued_by': 'Talathi', 'key': 'land'}]}}
    cards = [card(), card(id='x', status='ineligible'), card(id='y', source_url='not a url'),
             card(id='z', apply_url='javascript:x', source_url='https://maharashtra.gov.in/z')]
    g = PC.guide_from_cards(cards, catalog, language='hi', reference='case-12')
    assert [s['id'] for s in g['schemes']] == ['pm_kisan', 'z']
    first, second = g['schemes']
    assert first['name_local'] == 'पीएम-किसान' and first['documents'] == [{'name': 'Land record', 'issued_by': 'Talathi'}]
    assert first['last_verified'] == '2026-10-21'
    assert len(first['missing_information']) == 1 and first['missing_information'][0] != 'annual_family_income_inr'
    assert second['apply_url'] is None
    Guide.model_validate(g)                                                # what we send is what the service accepts
    assert PC.guide_from_cards([card(status='ineligible')]) is None


# ---------- backend route ----------

class FakePdfClient:
    def __init__(self, error=None):
        self.error, self.sent = error, []

    def generate(self, guide):
        self.sent.append(guide)
        if self.error:
            raise self.error
        return b'%PDF-1.7 route'


@pytest.fixture
def route(monkeypatch):
    from types import SimpleNamespace
    from web import conversation_api as api

    def fake_understand(llm, text, pending, context=None):
        return SimpleNamespace(facts={'age': 62, 'state': 'maharashtra', 'is_bpl': True, 'gender': 'male'},
                               language='en', intent='give_info', assessment_complete=True)
    monkeypatch.setattr(api, 'get_llm', lambda role='reader': object())
    monkeypatch.setattr(api, 'understand', fake_understand)
    sid = api.conversation(api.Message(text='I am 62, BPL, Maharashtra'))['session_id']
    app = FastAPI()
    app.include_router(pdf_api.router)
    return TestClient(app), sid


def test_route_is_off_until_configured(route, monkeypatch):
    c, sid = route
    monkeypatch.delenv('PDF_SERVICE_URL', raising=False)
    r = c.get(f'/api/v2/conversation/{sid}/guide.pdf')
    assert r.status_code == 404 and r.json()['code'] == 'pdf_disabled'


def test_route_builds_the_guide_on_the_server(route, monkeypatch):
    c, sid = route
    fake = FakePdfClient()
    monkeypatch.setattr(pdf_api, 'get_client', lambda: fake)
    r = c.get(f'/api/v2/conversation/{sid}/guide.pdf?lang=mr')
    assert r.status_code == 200 and r.content == b'%PDF-1.7 route'
    assert r.headers['content-disposition'] == 'attachment; filename="yojana-guide-mr.pdf"'
    sent = fake.sent[0]
    assert sent['language'] == 'mr' and sent['schemes'] and 'recipient_name' not in sent
    assert all(s['status'] != 'ineligible' and s['source_url'].startswith('http') for s in sent['schemes'])
    Guide.model_validate(sent)
    assert c.get('/api/v2/conversation/unknown-session-id/guide.pdf').status_code == 404


def test_route_maps_service_errors(route, monkeypatch):
    c, sid = route
    monkeypatch.setattr(pdf_api, 'get_client', lambda: FakePdfClient(PC.PdfUnavailable('asleep')))
    r = c.get(f'/api/v2/conversation/{sid}/guide.pdf')
    assert r.status_code == 503 and r.json()['code'] == 'pdf_unavailable' and r.headers['retry-after'] == '30'
    monkeypatch.setattr(pdf_api, 'get_client', lambda: FakePdfClient(PC.PdfAuthError('bad token', status=401)))
    r = c.get(f'/api/v2/conversation/{sid}/guide.pdf')
    assert r.status_code == 502 and 'token' not in r.text
