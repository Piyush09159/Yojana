"""Dashboard routing and real API integration regression coverage."""
from fastapi.testclient import TestClient
from web.app import create_app
from web import conversation_api


def test_public_landing_dashboard_and_legacy_are_distinct():
    app = create_app()
    client = TestClient(app)
    home = client.get('/')
    dashboard = client.get('/app')
    legacy = client.get('/legacy')
    assert home.status_code == 200
    assert 'data-site="public-landing"' in home.text
    assert 'id="hero-title"' in home.text
    assert 'href="/app"' in home.text
    assert 'id="how-it-works"' in home.text
    assert 'id="faq"' in home.text
    assert 'dashboard.js' not in home.text
    assert 'id="rescue-form"' not in home.text
    assert 'id="profile-form"' not in home.text
    assert dashboard.status_code == legacy.status_code == 200
    assert 'V2.2 dashboard' in dashboard.text
    assert 'id="rescue-form"' in dashboard.text
    assert 'Upcoming LiveKit' in dashboard.text
    assert 'data-site="public-landing"' not in dashboard.text
    assert 'id="profile-form"' not in dashboard.text
    assert 'id="profile-form"' in legacy.text
    assert len({home.text, dashboard.text, legacy.text}) == 3
    for path in ['/', '/app', '/chat', '/legacy', '/docs']:
        assert sum(getattr(r, 'path', None) == path for r in app.routes) == 1


def test_chat_redirects_to_canonical_app():
    client = TestClient(create_app())
    response = client.get('/chat', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == '/app'
    assert client.get('/chat').text == client.get('/app').text


def test_dashboard_and_legacy_assets_and_docs():
    client = TestClient(create_app())
    for path, media in [('/static/landing.css', 'text/css'),
                        ('/static/landing.js', 'javascript'),
                        ('/static/brand.css', 'text/css'),
                        ('/static/favicon.svg', 'image/svg+xml'),
                        ('/static/dashboard.css', 'text/css'),
                        ('/static/dashboard.js', 'javascript'),
                        ('/static/style.css', 'text/css'),
                        ('/static/app.js', 'javascript'), ('/openapi.json', 'application/json'), ('/docs', 'text/html')]:
        r = client.get(path)
        assert r.status_code == 200
        assert media in r.headers['content-type']


def test_public_html_is_revalidated_and_does_not_boot_conversation():
    client = TestClient(create_app())
    assert client.get('/').headers['cache-control'] == 'no-cache'
    assert client.get('/app').headers['cache-control'] == 'no-cache'
    assert '/api/v2/conversation' not in client.get('/').text


def test_dashboard_catalog_rescue_and_waha_routes():
    client = TestClient(create_app())
    catalog = client.get('/api/catalog').json()
    assert any(s['id'] == 'pm_kisan' and s['rescue_supported'] for s in catalog)
    body = {'scheme_id': 'pm_kisan', 'facts': {'ekyc_done': False}}
    pre = client.post('/api/rescue/preflight', json=body)
    assert pre.status_code == 200
    assert 'ekyc_not_done' in pre.json()['flags']
    diagnosis = client.post('/api/rescue/diagnose', json={**body, 'status_text': 'eKYC pending'})
    assert diagnosis.status_code == 200
    assert diagnosis.json()['government_status_verified'] is False
    assert client.get('/api/integrations/waha/status').status_code == 200
    assert client.post('/api/integrations/waha/invalid', json={}).status_code in (403, 503)


def test_dashboard_conversation_roundtrip(monkeypatch):
    from llm.providers.mock_provider import MockProvider
    from saathi.orchestrator import Orchestrator
    reader = MockProvider(responses={"Person's message": {
        "language": "en", "facts": {"age": 60, "state": "maharashtra"}, "intent": "give_info", "scheme_name": None}})
    monkeypatch.setattr(conversation_api, "_orchestrator", Orchestrator(reader_llm=reader, enqueue_gaps=False))
    client = TestClient(create_app())
    r = client.post('/api/v2/conversation', json={'text': 'I am 60 in Maharashtra', 'lang': 'en'})
    assert r.status_code == 200
    result = r.json()
    assert result['profile']['age'] == 60
    assert result['results']['schemes_checked'] > 0
    assert result['question']['reason']
    assert client.delete('/api/v2/conversation/' + result['session_id']).status_code == 200


def test_rescue_ayushman_dependency_can_be_reported():
    client = TestClient(create_app())
    result = client.post('/api/rescue/preflight', json={
        'scheme_id': 'pmjay', 'facts': {'ayushman_card_made': False}})
    assert result.status_code == 200
    assert result.json()['fixes'][0]['action'] == 'make_card'
