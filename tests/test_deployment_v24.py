"""Deployment boundaries, restart persistence and channel retries; no hosted calls."""
import hashlib
import hmac
import json
from pathlib import Path
import os
import subprocess
import time
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from llm import LLMError
from llm.providers.mock_provider import MockProvider
from web import case_store, conversation_api as api, inference, waha_bridge as waha
from web.app import create_app
from web.privacy import minimize

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def persistent(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'cases.db'))
    monkeypatch.setenv('SESSION_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('SESSION_SIGNING_KEY', 'fixture-signing-key-for-deployment-tests-32')
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    monkeypatch.delenv('DEPLOYMENT_MODE', raising=False)
    case_store.migrate()
    return tmp_path / 'cases.db'


def mock_extraction(monkeypatch, facts=None):
    provider = MockProvider(queue=[{'language': 'en', 'facts': facts or {'age': 62, 'state': 'maharashtra'}, 'intent': 'give_info'}])
    monkeypatch.setattr(api, 'get_llm', lambda _: provider)
    return provider


def create_case(client, text='I am 62 in Maharashtra', **kwargs):
    response = client.post('/api/v2/conversation', json={'text': text, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()['session_id']


def test_migrations_are_idempotent_and_ready(persistent):
    case_store.migrate()
    assert case_store.ready()
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM schema_migrations').fetchone()['n'] == len(list((ROOT/'web/migrations').glob('*.sql')))


def test_restart_persists_facts_pending_history_and_owner(persistent, monkeypatch):
    mock_extraction(monkeypatch)
    first = TestClient(create_app())
    sid = create_case(first)
    api._sessions.clear()  # No in-process conversation state is necessary.
    restarted = TestClient(create_app())
    restarted.cookies.update(first.cookies)
    response = restarted.get('/api/cases/' + sid)
    assert response.status_code == 200
    case = response.json()
    assert case['profile']['age'] == 62 and case['pending_question']
    assert len(case['messages']) == 2 and case['expires_at'] > case['updated_at']
    stranger = TestClient(create_app())
    assert stranger.get('/api/cases/' + sid).status_code == 403
    assert stranger.delete('/api/v2/conversation/' + sid).status_code == 403
    assert restarted.delete('/api/v2/conversation/' + sid).status_code == 200
    assert restarted.get('/api/cases/' + sid).status_code == 404


def test_idempotent_message_does_not_repeat_extraction(persistent, monkeypatch):
    provider = mock_extraction(monkeypatch)
    client = TestClient(create_app())
    request_id = '1000000000000000000000000000000000000000000000000000000000000000'
    sid = create_case(client, request_id=request_id)
    result = client.post('/api/v2/conversation', json={'text': 'I am 62 in Maharashtra', 'session_id': sid, 'request_id': request_id})
    assert result.status_code == 200
    assert len(provider.calls) == 1
    conflict = client.post('/api/v2/conversation', json={'text': 'A different message', 'session_id': sid, 'request_id': request_id})
    assert conflict.status_code == 409


def test_retention_bounded_history_approvals_and_deletion(persistent, monkeypatch):
    mock_extraction(monkeypatch)
    monkeypatch.setenv('CASE_HISTORY_LIMIT', '4')
    client = TestClient(create_app())
    sid = create_case(client)
    with case_store.transaction(sid) as tx:
        for _ in range(10):
            tx.message(sid, 'user', 'Phone 900000000000 and bank 123456789012. test@example.com')
        assert len(tx.history(sid)) == 4
        assert all('900000000000' not in row['content'] and 'test@example.com' not in row['content'] for row in tx.history(sid))
    assert client.patch('/api/cases/' + sid, json={'status': 'reviewed'}).status_code == 200
    approval = client.post('/api/cases/' + sid + '/approvals', json={'decision': 'approved'})
    assert approval.status_code == 200 and approval.json()['government_submission'] is False
    assert len(client.get('/api/cases/' + sid).json()['approvals']) == 1
    mock_extraction(monkeypatch, {'age': 63})
    assert client.post('/api/v2/conversation', json={'session_id': sid, 'text': 'Correction: I am 63'}).status_code == 200
    assert client.get('/api/cases/' + sid).json()['status'] == 'open'
    with case_store.transaction() as tx:
        tx.execute('UPDATE case_sessions SET expires_at=? WHERE id=?', (time.time() - 1, sid))
    assert client.get('/api/cases/' + sid).status_code == 404
    case_store.purge()
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM case_messages').fetchone()['n'] == 0
        assert tx.execute('SELECT COUNT(*) AS n FROM case_approvals').fetchone()['n'] == 0


def test_sensitive_identifiers_never_saved_or_sent(persistent, monkeypatch):
    provider = mock_extraction(monkeypatch)
    client = TestClient(create_app())
    assert client.post('/api/v2/conversation', json={'text': 'Aadhaar 1234 5678 9012'}).status_code == 422
    assert not provider.calls
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM case_sessions').fetchone()['n'] == 0


@pytest.mark.parametrize('lang,text', [('en', 'I am a farmer'), ('hi', 'मैं किसान हूँ'), ('mr', 'मी शेतकरी आहे')])
def test_contextual_multilingual_extraction(persistent, monkeypatch, lang, text):
    provider = MockProvider(queue=[{'language': lang, 'facts': {'age': 62}, 'intent': 'give_info'},
                                   {'language': lang, 'facts': {'occupation': 'farmer'}, 'intent': 'give_info'}])
    monkeypatch.setattr(api, 'get_llm', lambda _: provider)
    client = TestClient(create_app())
    sid = create_case(client, lang=lang)
    answer = client.post('/api/v2/conversation', json={'text': text, 'lang': lang, 'session_id': sid})
    assert answer.status_code == 200
    assert answer.json()['profile'] == {'age': 62, 'occupation': 'farmer'}
    prompt = provider.calls[-1]['messages'][0]['content']
    assert 'Previously validated facts' in prompt and 'The helper just asked' in prompt
    assert inference.status()['state'] == 'mocked'


def configure_waha(monkeypatch):
    for key, value in {'YOJANA_WAHA_ENABLED': 'true', 'WAHA_BASE_URL': 'http://waha:3000',
                       'WAHA_API_KEY': 'fixture-api-key', 'WAHA_HMAC_KEY': 'fixture-hmac-key-that-is-more-than-32-chars'}.items():
        monkeypatch.setenv(key, value)


def signed(client, text, identifier, chat='900000000000@c.us', signature=True):
    raw = json.dumps({'event': 'message', 'session': 'default', 'payload': {'from': chat, 'id': identifier, 'body': text}}).encode()
    mac = hmac.new(os.environ['WAHA_HMAC_KEY'].encode(), raw, hashlib.sha512).hexdigest()
    return client.post('/api/webhooks/waha', content=raw, headers={
        'Content-Type': 'application/json', 'X-Webhook-Hmac-Algorithm': 'sha512',
        'X-Webhook-Hmac': mac if signature else 'invalid'})


def test_signed_waha_no_unsolicited_and_retry_outbox(persistent, monkeypatch):
    configure_waha(monkeypatch)
    sender = Mock(side_effect=[httpx.ConnectError('fixture transport failure'), None])
    monkeypatch.setattr(waha, '_send', sender)
    client = TestClient(create_app())
    assert signed(client, 'YOJANA', 'bad', signature=False).status_code == 403
    assert signed(client, 'hello', 'uninvited').json()['status'] == 'not_opted_in'
    assert signed(client, 'STOP', 'uninvited-stop').json()['status'] == 'not_opted_in'
    assert signed(client, 'YOJANA', 'start').status_code == 503
    restarted = TestClient(create_app())
    assert signed(restarted, 'YOJANA', 'start').json()['status'] == 'started'
    assert signed(restarted, 'YOJANA', 'start').json()['status'] == 'duplicate'
    assert sender.call_count == 2
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM waha_chats').fetchone()['n'] == 1
        rows = [dict(row) for row in tx.execute('SELECT * FROM waha_events')]
        assert '900000000000' not in json.dumps(rows)


def test_waha_mapping_survives_restart_and_stop_clears_case(persistent, monkeypatch):
    configure_waha(monkeypatch)
    mock_extraction(monkeypatch)
    sender = Mock()
    monkeypatch.setattr(waha, '_send', sender)
    client = TestClient(create_app())
    assert signed(client, 'YOJANA', 'start').json()['status'] == 'started'
    restarted = TestClient(create_app())
    assert signed(restarted, 'I am 62', 'message').json()['status'] == 'replied'
    assert signed(restarted, 'I am 62', 'message').json()['status'] == 'duplicate'
    assert signed(restarted, 'STOP', 'stop').json()['status'] == 'stopped'
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM waha_chats').fetchone()['n'] == 0
        assert tx.execute('SELECT COUNT(*) AS n FROM case_sessions').fetchone()['n'] == 0
    assert sender.call_count == 3


@pytest.mark.parametrize('exc,state', [(LLMError('429 fixture quota'), 'rate_limited_or_quota_exhausted'),
                                     (LLMError('connection unavailable'), 'provider_unavailable')])
def test_provider_error_states(monkeypatch, exc, state):
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-api-key')
    assert inference.failure(exc) == state
    assert inference.status()['state'] == state
    monkeypatch.delenv('OPENAI_API_KEY')
    assert inference.status()['state'] == 'not_configured'


def test_health_cache_and_safe_public_status(persistent):
    with TestClient(create_app()) as client:
        for route in ('/healthz', '/readyz', '/api/health/live', '/api/health/ready'):
            assert client.get(route).status_code == 200
        response = client.get('/api/inference/status')
        assert response.headers['cache-control'] == 'no-store'
        assert 'KEY' not in response.text and 'DATABASE_URL' not in response.text


def test_production_fails_closed_without_postgres(monkeypatch):
    monkeypatch.setenv('DEPLOYMENT_MODE', 'production')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError, match='PostgreSQL'):
        with TestClient(create_app()):
            pass


def test_legacy_session_restart_and_sse_cursor_are_authorized(persistent, monkeypatch):
    monkeypatch.setenv('SSE_WINDOW_SECONDS', '1')
    first = TestClient(create_app())
    response = first.post('/api/sessions', json={'profile': {'age': 62}, 'web': False})
    assert response.status_code == 200
    sid = response.json()['id']
    for _ in range(100):
        snapshot = first.get('/api/sessions/' + sid).json()
        if snapshot['state'] not in ('starting', 'investigating'):
            break
        time.sleep(0.01)
    second = TestClient(create_app())
    second.cookies.update(first.cookies)
    assert second.get('/api/sessions/' + sid).json()['profile']['age'] == 62
    stranger = TestClient(create_app())
    assert stranger.get(f'/api/sessions/{sid}/events?once=true').status_code == 403
    events = second.get(f'/api/sessions/{sid}/events?once=true').json()
    assert events
    cursor = events[0]['seq']
    resumed = second.get(f'/api/sessions/{sid}/events?once=true', headers={'Last-Event-ID': str(cursor)}).json()
    assert all(event['seq'] > cursor for event in resumed)
    stream = second.get(f'/api/sessions/{sid}/events')
    assert stream.status_code == 200 and ': connected' in stream.text
    assert stream.headers['x-accel-buffering'] == 'no' and stream.headers['cache-control'] == 'no-store'


def test_rescue_case_stores_flags_not_document_names(persistent, monkeypatch):
    mock_extraction(monkeypatch)
    client = TestClient(create_app())
    sid = create_case(client)
    response = client.post('/api/rescue/preflight', json={'case_id': sid, 'scheme_id': 'pm_kisan',
                           'facts': {'ekyc_done': False}, 'records': {'aadhaar': {'name': 'Synthetic Citizen'}}})
    assert response.status_code == 200
    case = client.get('/api/cases/' + sid).json()
    assert 'ekyc_not_done' in case['rescue']['flags']
    assert 'Synthetic Citizen' not in json.dumps(case)
    assert TestClient(create_app()).post('/api/rescue/preflight', json={'case_id': sid, 'scheme_id': 'pm_kisan'}).status_code == 403


def test_stop_cancels_previously_queued_waha_reply(persistent, monkeypatch):
    configure_waha(monkeypatch)
    sender = Mock(side_effect=[httpx.ConnectError('fixture failure'), None])
    monkeypatch.setattr(waha, '_send', sender)
    client = TestClient(create_app())
    assert signed(client, 'YOJANA', 'queued-start').status_code == 503
    assert signed(client, 'STOP', 'stop').json()['status'] == 'stopped'
    assert signed(client, 'YOJANA', 'queued-start').json()['status'] == 'cancelled'
    assert sender.call_count == 2


def test_closed_case_cannot_mutate_until_reopened(persistent, monkeypatch):
    provider = mock_extraction(monkeypatch)
    client = TestClient(create_app())
    sid = create_case(client)
    client.patch('/api/cases/' + sid, json={'status': 'closed'})
    response = client.post('/api/v2/conversation', json={'session_id': sid, 'text': 'I am a farmer'})
    assert response.status_code == 409 and len(provider.calls) == 1


def test_build_output_uses_https_proxy_and_rejects_secret_urls(tmp_path):
    import shutil
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    isolated = tmp_path / 'frontend'
    shutil.copytree(ROOT / 'frontend', isolated, ignore=shutil.ignore_patterns('.vercel'))
    env = {**os.environ, 'BACKEND_ORIGIN': 'https://backend.invalid'}
    result = subprocess.run([node, str(isolated / 'build.mjs')], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    config = json.loads((isolated / '.vercel/output/config.json').read_text())
    assert config['version'] == 3
    assert config['routes'][0]['dest'] == 'https://backend.invalid/api/$1'
    assert config['routes'][0]['headers']['Cache-Control'] == 'no-store'
    env['BACKEND_ORIGIN'] = 'https://user:secret@backend.invalid'
    assert subprocess.run([node, str(isolated / 'build.mjs')], env=env, capture_output=True).returncode != 0


def test_frontend_sync_and_no_embedded_credentials():
    from scripts.sync_frontend import sync
    sync(check=True)
    for path in (ROOT / 'frontend/public').rglob('*'):
        if path.is_file():
            text = path.read_text(encoding='utf-8')
            assert 'postgresql://' not in text and 'fixture-api-key' not in text


def test_postgres_actual_migrations_and_restart_when_available(monkeypatch):
    """Runs in deployment CI or explicitly against a dedicated disposable database."""
    url = os.getenv('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('TEST_POSTGRES_URL not provided; real PostgreSQL was not exercised')
    pytest.importorskip('psycopg')
    monkeypatch.setenv('DATABASE_URL', url)
    case_store.migrate()
    case_store.migrate()
    sid = 'deployment-smoke-' + str(time.time_ns())
    with case_store.transaction(sid) as tx:
        tx.save(sid, {'profile': {'age': 62}, 'pending': {'field': 'occupation'}}, token='fixture-owner')
        tx.message(sid, 'user', 'I am 62')
    # A new DB connection proves persistence independently of the prior transaction.
    with case_store.transaction(sid) as tx:
        assert tx.session(sid, 'fixture-owner')['state']['profile']['age'] == 62
        assert len(tx.history(sid)) == 1
        tx.execute('DELETE FROM case_sessions WHERE id=?', (sid,))
