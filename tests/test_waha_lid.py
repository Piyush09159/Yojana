"""NOWEB 2026.10.1 LID regression: signed inbound events, stubbed WAHA HTTP.

These prove exact outgoing API addressing, not live account delivery.
"""
import hashlib
import hmac
import json
import os
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from web.app import create_app
from web import case_store, voice, waha_bridge as waha, waha_media
from test_deployment_v24 import persistent, configure_waha, mock_extraction

LID = '900000000000001@lid'  # Synthetic opaque ID, never a phone-number alias.


@pytest.fixture
def channel(persistent, monkeypatch):
    configure_waha(monkeypatch)
    # Exercise the actual _send adapter and inspect its HTTP JSON, not just a
    # mocked _send call. No real WAHA network call or WhatsApp message is sent.
    post = Mock(return_value=httpx.Response(201, json={'id':'synthetic-accepted'},
                request=httpx.Request('POST','http://waha:3000/api/sendText')))
    monkeypatch.setattr(waha.httpx,'post',post)
    return TestClient(create_app()), post


def incoming(client, chat=LID, text='yojana', mid='activation', valid_hmac=True, **fields):
    raw=json.dumps({'event':'message','session':'default','payload':{
        'from':chat,'fromMe':False,'body':text,'id':mid,**fields}}).encode()
    mac=hmac.new(os.environ['WAHA_HMAC_KEY'].encode(),raw,hashlib.sha512).hexdigest()
    return client.post('/api/webhooks/waha',content=raw,headers={
        'Content-Type':'application/json','X-Webhook-Hmac-Algorithm':'sha512',
        'X-Webhook-Hmac':mac if valid_hmac else 'invalid'})


@pytest.mark.parametrize('chat',[LID,'1@lid','12345@lid','123456@lid','12345678901234567890@lid',
                                      '900000000000@c.us'])
def test_activation_sends_menu_to_exact_personal_id(channel,chat):
    client,post=channel
    assert incoming(client,chat=chat).json()['status']=='started'
    post.assert_called_once()
    assert post.call_args.args==('http://waha:3000/api/sendText',)
    request=post.call_args.kwargs
    assert request['headers']=={'X-Api-Key':'fixture-api-key'}
    assert request['json']['chatId']==chat
    assert request['json']['session']=='default'
    assert 'Welcome to Yojana Saathi!' in request['json']['text']
    assert 'Reply *Chat* or *Call*' in request['json']['text']
    assert incoming(client,chat=chat).json()['status']=='duplicate'
    assert post.call_count==1


@pytest.mark.parametrize('chat',[
    '900000000000001@g.us','status@broadcast','900000000000001@broadcast',
    '900000000000001@newsletter','900000000000001@s.whatsapp.net',
    '900000000000001@lid.evil','900000000000001@LID','abc@lid','@lid',
    '123456789012345678901@lid','900000000000001:2@lid',
    ' 900000000000001@lid','900000000000001@lid\n','９０００００００００００００１@lid',None])
def test_rejects_non_personal_and_malformed_ids(channel,chat):
    client,post=channel
    assert incoming(client,chat=chat).json()['status']=='ignored'
    post.assert_not_called()


@pytest.mark.parametrize('fields',[{'fromMe':True},{'isGroup':True},
                                  {'broadcast':True},{'participant':'900000000000@c.us'}])
def test_lid_does_not_bypass_context_filters(channel,fields):
    client,post=channel
    assert incoming(client,**fields).json()['status']=='ignored'
    post.assert_not_called()


def test_lid_still_requires_opt_in_and_valid_hmac(channel):
    client,post=channel
    assert incoming(client,text='hello').json()['status']=='not_opted_in'
    assert incoming(client,mid='unsigned',valid_hmac=False).status_code==403
    post.assert_not_called()


def test_distinct_ids_and_suffixes_are_isolated_and_stop_is_scoped(channel,monkeypatch):
    client,post=channel
    chats=[LID,'900000000000002@lid','900000000000001@c.us']
    sessions=[]
    for index,chat in enumerate(chats):
        assert incoming(client,chat=chat,mid=f'activate-{index}').json()['status']=='started'
        mock_extraction(monkeypatch,{'age':62-index})
        assert incoming(client,chat=chat,text=f'I am {62-index}',mid=f'facts-{index}').json()['status']=='replied'
        assert post.call_args.kwargs['json']['chatId']==chat
        with case_store.transaction() as tx:
            row=tx.execute('SELECT session_id FROM waha_chats WHERE chat_hash=?',
                (case_store.chat_digest('default:'+chat),)).fetchone()
            sessions.append(row['session_id'])
            assert tx.session(row['session_id'],trusted=True)['state']['profile']['age']==62-index
    assert len(set(sessions))==3
    count=post.call_count
    assert incoming(client,chat=chats[1],mid='activate-0').status_code==409
    assert post.call_count==count  # Never replay one sender's outbox to another.
    assert incoming(client,text='STOP',mid='stop').json()['status']=='stopped'
    assert post.call_args.kwargs['json']['chatId']==LID
    with case_store.transaction() as tx:
        assert tx.execute('SELECT id FROM case_sessions WHERE id=?',(sessions[0],)).fetchone() is None
        assert tx.session(sessions[1],trusted=True)
        assert tx.session(sessions[2],trusted=True)


def test_lid_pending_reply_retry_preserves_recipient(channel):
    client,post=channel
    response=post.return_value
    post.side_effect=[httpx.ConnectError('synthetic send failure'),response]
    assert incoming(client).status_code==503
    assert incoming(client).json()['status']=='started'
    assert incoming(client).json()['status']=='duplicate'
    assert [call.kwargs['json']['chatId'] for call in post.call_args_list]==[LID,LID]


def test_lid_voice_note_keeps_case_and_deduplicates(channel,monkeypatch):
    client,post=channel
    incoming(client);mock_extraction(monkeypatch,{'age':62})
    incoming(client,text='I am 62',mid='text')
    with case_store.transaction() as tx:
        sid=tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
    stt=Mock(return_value={'text':'I am retired','language':'en'})
    monkeypatch.setattr(waha_media,'transcribe',stt)
    mock_extraction(monkeypatch,{'occupation':'retired'})
    media={'hasMedia':True,'media':{'url':'http://waha:3000/api/files/synthetic.ogg','mimetype':'audio/ogg'}}
    assert incoming(client,text='',mid='note',**media).json()['status']=='replied'
    assert post.call_args.kwargs['json']['chatId']==LID
    assert incoming(client,text='',mid='note',**media).json()['status']=='duplicate'
    stt.assert_called_once()
    with case_store.transaction() as tx:
        assert tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']==sid
        assert len(tx.history(sid))==4


def test_lid_call_and_consented_summary_keep_exact_recipient(channel,monkeypatch):
    client,post=channel
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN','https://demo.invalid')
    incoming(client)
    assert incoming(client,text='Call',mid='call').json()['status']=='voice_link'
    link=post.call_args.kwargs['json']['text'].splitlines()[-1]
    token=parse_qs(urlsplit(link).fragment)['handoff'][0]
    browser=TestClient(create_app())
    redeemed=browser.post('/api/voice/redeem',json={'token':token})
    assert redeemed.status_code==200
    sid=redeemed.json()['session_id']
    assert browser.post('/api/voice/redeem',json={'token':token}).status_code==410
    assert TestClient(create_app()).get('/api/cases/'+sid).status_code==403
    cid=browser.post('/api/voice/start',json={'session_id':sid}).json()['call_id']
    assert browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True}).status_code==200
    # An unrelated active sender must not take over the encrypted LID route.
    incoming(client,chat='900000000000002@lid',mid='other')
    post.reset_mock();voice.deliver_summaries()
    post.assert_called_once()
    assert post.call_args.kwargs['json']['chatId']==LID
    assert browser.get('/api/voice/'+cid).json()['recipient_delivery_verified'] is False
