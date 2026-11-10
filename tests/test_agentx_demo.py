"""Synthetic release acceptance; no live provider, WAHA, or PostgreSQL claim."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from llm.providers.mock_provider import MockProvider
from web.app import create_app
from web import case_store, conversation_api as api, voice, waha_bridge as waha
from test_deployment_v24 import persistent, configure_waha, signed
from test_citizen_v25 import linked_call
from test_assessment_completion import catalog


@pytest.mark.parametrize('language', ['en', 'hi', 'mr'])
def test_multilingual_followup_completion_and_dashboard_restore(persistent, monkeypatch, language):
    fixture = yaml.safe_load((Path(__file__).parent/'fixtures/agentx_conversations.yaml').read_text(encoding='utf-8'))[language]
    provider = MockProvider(queue=[
        {'language': language, 'intent': 'give_info', 'requested_category': 'income',
         'facts': {'age': 62, 'state': 'maharashtra', 'occupation': 'retired'}},
        {'language': language, 'intent': 'give_info', 'facts': {'annual_family_income_inr': 40000}}])
    monkeypatch.setattr(api, 'get_llm', lambda _: provider)
    monkeypatch.setattr(api, 'load_catalog', catalog)
    with TestClient(create_app()) as client:
        first = client.post('/api/v2/conversation', json={'text':fixture['intro'], 'lang':language, 'language_override':True}).json()
        assert not first['assessment_complete']
        assert first['question']['field'] == 'annual_family_income_inr'
        assert first['speech_language'] == language
        assert all(card['status'] != 'eligible' for card in first['recommendations'])
        final = client.post('/api/v2/conversation', json={'session_id':first['session_id'], 'text':fixture['income'], 'lang':language, 'language_override':True}).json()
        assert final['assessment_complete'] and final['question'] is None
        assert final['speech_language'] == language and len(final['spoken_text']) <= 360
        assert len(final['recommendations']) == 5
        reloaded = TestClient(create_app());reloaded.cookies.update(client.cookies)
        saved = reloaded.get('/api/cases/'+first['session_id']).json()
        assert saved['recommendations']['schemes'] == final['recommendations']
        assert len(saved['messages']) == 4
        assert TestClient(create_app()).get('/api/cases/'+first['session_id']).status_code == 403


def test_literal_menu_labels_and_duplicate_call_link(persistent, monkeypatch):
    configure_waha(monkeypatch)
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN', 'https://demo.invalid')
    sender=Mock();monkeypatch.setattr(waha,'_send',sender)
    with TestClient(create_app()) as client:
        assert signed(client,'YOJANA','release-start').json()['status']=='started'
        assert 'Reply *Chat* or *Call*' in sender.call_args.args[-1]
        assert signed(client,'Chat','release-chat').json()['status']=='chat_selected'
        signed(client,'YOJANA','release-menu')
        assert signed(client,'Call','release-call').json()['status']=='voice_link'
        invite=sender.call_args.args[-1]
        assert invite.splitlines()[-1].startswith('https://demo.invalid/voice#handoff=')
        count=sender.call_count
        assert signed(client,'Call','release-call').json()['status']=='duplicate'
        assert sender.call_count==count
        token=parse_qs(urlsplit(invite.splitlines()[-1]).fragment)['handoff'][0]
        assert client.post('/api/voice/redeem',json={'token':token}).status_code==200
        assert client.post('/api/voice/redeem',json={'token':token}).status_code==410


def test_summary_never_routes_to_second_whatsapp_citizen(persistent, monkeypatch):
    channel,browser,sender,sid,cid,_=linked_call(monkeypatch)
    original_hash=None
    with case_store.transaction() as tx:
        original_hash=tx.execute('SELECT chat_hash FROM waha_chats WHERE session_id=?',(sid,)).fetchone()['chat_hash']
    # The same public webhook is used, but the unrelated number gets its own case.
    body=waha.WAHAEvent(event='message',session='default',payload={
        'from':'900000000001@c.us','id':'other-citizen-start','body':'YOJANA'})
    assert waha.persistent_event(body)['status']=='started'
    assert browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True}).status_code==200
    sender.reset_mock();voice.deliver_summaries()
    assert sender.call_count==1
    assert sender.call_args.args[3]=='900000000000@c.us'
    assert browser.get('/api/voice/'+cid).json()['recipient_delivery_verified'] is False
    with case_store.transaction() as tx:
        assert tx.execute('SELECT chat_hash FROM summary_outbox WHERE call_id=?',(cid,)).fetchone()['chat_hash']==original_hash


def test_failed_summary_exhaustion_revocation_and_renewal(persistent, monkeypatch):
    _,browser,sender,_,cid,_=linked_call(monkeypatch)
    browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True})
    sender.side_effect=httpx.ConnectError('synthetic transport outage')
    for _ in range(5):
        with case_store.transaction() as tx:
            tx.execute('UPDATE summary_outbox SET next_attempt=0 WHERE call_id=?',(cid,))
        voice.deliver_summaries()
    state=browser.get('/api/voice/'+cid).json()
    assert state['whatsapp']=={'status':'failed','attempts':5}
    assert browser.post(f'/api/voice/{cid}/summary/cancel',json={}).status_code==200
    before=sender.call_count;voice.deliver_summaries();assert sender.call_count==before
    browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True})
    sender.side_effect=None;voice.deliver_summaries()
    assert browser.get('/api/voice/'+cid).json()['whatsapp']['status']=='accepted_by_waha'
