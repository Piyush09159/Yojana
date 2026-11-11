"""Bolna verification calls: offline, Bolna HTTP calls stubbed."""
import httpx
import pytest
from fastapi.testclient import TestClient

from web import bolna, case_store
from web.app import create_app
from test_deployment_v24 import persistent, mock_extraction, create_case

SECRET = 'fixture-bolna-webhook-secret-24-chars'
PHONE = '+919876543210'


@pytest.fixture
def setup(monkeypatch, persistent):
    monkeypatch.setenv('BOLNA_API_KEY', 'fixture-bolna-key')
    monkeypatch.setenv('BOLNA_AGENT_ID', 'fixture-agent-id')
    monkeypatch.setenv('BOLNA_WEBHOOK_SECRET', SECRET)
    mock_extraction(monkeypatch, {'age': 62, 'state': 'maharashtra'})
    client = TestClient(create_app())
    case_id = create_case(client)

    def place(execution_id='exec-1'):
        return httpx.Response(200, json={'execution_id': execution_id},
                              request=httpx.Request('POST', bolna.API + '/call'))
    monkeypatch.setattr(bolna.httpx, 'post', lambda *a, **k: place())
    return client, case_id


def execution(monkeypatch, status='completed', data=None):
    body = {'id': 'exec-1', 'status': status, 'extracted_data': data or {}}
    monkeypatch.setattr(bolna.httpx, 'get', lambda *a, **k: httpx.Response(
        200, json=body, request=httpx.Request('GET', bolna.API + '/executions/exec-1')))


def start(client, case_id, consent=True):
    return client.post('/api/bolna/verify', json={
        'case_id': case_id, 'scheme_id': 'ignoaps', 'phone': PHONE, 'consent': consent})


def hook(client, secret=SECRET):
    return client.post(f'/api/bolna/webhook/{secret}', json={'id': 'exec-1'})


def test_consent_is_required(setup):
    client, case_id = setup
    assert start(client, case_id, consent=False).status_code == 422
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM verification_calls').fetchone()['n'] == 0


def test_stranger_cannot_start_a_call(setup):
    _, case_id = setup
    assert start(TestClient(create_app()), case_id).status_code == 403


def test_one_active_call_per_case(setup):
    client, case_id = setup
    assert start(client, case_id).json()['status'] == 'calling'
    assert start(client, case_id).status_code == 409


def test_webhook_secret_is_checked(setup):
    client, _ = setup
    assert hook(client, secret='wrong').status_code == 403


def test_verified_call_is_validated_and_phone_is_dropped(setup, monkeypatch):
    client, case_id = setup
    start(client, case_id)
    # An invalid income value must be dropped by validate_profile, never stored.
    execution(monkeypatch, data={'age': 62, 'is_bpl': True, 'annual_family_income_inr': 'a lot', 'aadhaar': '1'})
    assert hook(client).json()['status'] == 'verified'
    result = client.get('/api/bolna/' + case_id).json()[0]['result']
    assert result['call_facts'] == {'age': 62, 'is_bpl': True}
    assert result['rejected_values'] and not result['mismatches']
    assert result['eligibility']['status'] == 'eligible'
    assert result['government_submission'] is False
    with case_store.transaction() as tx:
        assert tx.execute('SELECT phone_cipher FROM verification_calls').fetchone()['phone_cipher'] == ''
    assert hook(client).json()['status'] == 'duplicate'      # idempotent


def test_mismatch_is_flagged_not_overwritten(setup, monkeypatch):
    client, case_id = setup
    start(client, case_id)
    execution(monkeypatch, data={'age': 50, 'is_bpl': True})
    assert hook(client).json()['status'] == 'needs_review'
    result = client.get('/api/bolna/' + case_id).json()[0]['result']
    assert result['mismatches']['age'] == {'declared': 62, 'on_call': 50}
    assert client.get('/api/cases/' + case_id).json()['profile']['age'] == 62


def test_unanswered_call_marks_failure_and_drops_phone(setup, monkeypatch):
    client, case_id = setup
    start(client, case_id)
    execution(monkeypatch, status='no-answer')
    assert hook(client).json()['status'] == 'failed'
    with case_store.transaction() as tx:
        assert tx.execute('SELECT phone_cipher FROM verification_calls').fetchone()['phone_cipher'] == ''


def test_call_placement_failure_cleans_up(setup, monkeypatch):
    client, case_id = setup
    def boom(*a, **k):
        raise httpx.ConnectError('synthetic outage')
    monkeypatch.setattr(bolna.httpx, 'post', boom)
    assert start(client, case_id).status_code == 502
    with case_store.transaction() as tx:
        row = tx.execute('SELECT status,phone_cipher FROM verification_calls').fetchone()
        assert row['status'] == 'failed' and row['phone_cipher'] == ''