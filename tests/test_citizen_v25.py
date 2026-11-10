"""V2.5 relevance, capability handoff, speech and summary authorization regressions."""
import base64
import io
import json
import time
import wave
from unittest.mock import Mock
from urllib.parse import urlsplit, parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from core.questions import question_candidates, next_best_question
from core.eligibility import evaluate_scheme
from core.languages import registry, normalize
from core.relevance import intent_from_text, relevance
from saathi.shortlist import build_shortlist
from web import case_store, voice, speech, waha_bridge as waha, conversation_api as api
from web.app import create_app
from llm.providers.mock_provider import MockProvider
from test_deployment_v24 import persistent, configure_waha, signed, mock_extraction


def scheme(sid, name, rules, state=None):
    return {'id':sid,'name':name,'eligibility':{'all':rules},'rank':{'value_score':3,'effort':2},
            'benefit_summary':'Verify benefit details on the source page.','source_url':'https://example.gov.in/'+sid,
            'last_verified':'2026-10-01','state':state,'origin':'curated','verified':True}


def test_retired_senior_financial_intent_beats_hundred_scholarships():
    income={'field':'annual_family_income_inr','op':'lte','value':100000,'reason':'Income ceiling'}
    pension=scheme('pension','Old Age Pension',[{'field':'age','op':'gte','value':60,'reason':'Age threshold'},income])
    catalog={'pension':pension}
    for i in range(100):
        catalog[f'student{i}']=scheme(f'student{i}','Student Scholarship',[
            {'field':'is_student','op':'eq','value':True,'reason':'Must be a student'}])
    profile={'age':62,'occupation':'retired','state':'maharashtra',
             '_intent':intent_from_text('I am retired and need financial assistance in Maharashtra')}
    assert question_candidates(profile,catalog)[0]['field']=='annual_family_income_inr'
    assert next_best_question(profile,catalog)['fields']==['annual_family_income_inr']
    assert 'is_student' not in profile  # Relevance is not invented eligibility evidence.
    result=build_shortlist(profile,catalog)
    assert result['needs_information'][0]['id']=='pension'
    assert all('student' not in item['id'] for item in result['needs_information'])
    before=evaluate_scheme(pension,profile)
    assert before.status=='needs_info'
    assert evaluate_scheme(pension,{**profile,'annual_family_income_inr':50000}).status=='eligible'


def test_category_override_occupation_and_geographic_rules():
    student=scheme('student','Student Scholarship',[{'field':'is_student','op':'eq','value':True}])
    person={'age':62,'occupation':'retired','state':'maharashtra','_intent':{'categories':['education']}}
    assert question_candidates(person,{'student':student})[0]['field']=='is_student'
    elsewhere=scheme('elsewhere','Old Age Pension',[{'field':'is_bpl','op':'eq','value':True}],state='tamil_nadu')
    assert not question_candidates(person,{'elsewhere':elsewhere})
    assert relevance(student,{'occupation':'student'})>1
    farm=scheme('farm','Farmer assistance',[])
    assert relevance(farm,{'occupation':'retired'})==0
    assert intent_from_text('I am not a student',{'categories':['income']})['categories']==['income']


def test_conversation_uses_senior_intent_context(persistent,monkeypatch):
    mock_extraction(monkeypatch,{'age':62,'occupation':'retired','state':'maharashtra'})
    with TestClient(create_app()) as client:
        response=client.post('/api/v2/conversation',json={'text':'I am retired and need financial assistance'})
        assert response.status_code==200
        assert response.json()['question']['field']!='is_student'
        with case_store.transaction() as tx:
            state=tx.session(response.json()['session_id'],trusted=True)['state']
            assert 'income' in state['intent_context']['categories']


def setup_link(monkeypatch):
    configure_waha(monkeypatch)
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN','https://frontend.invalid')
    sender=Mock();monkeypatch.setattr(waha,'_send',sender)
    client=TestClient(create_app())
    assert signed(client,'YOJANA','start').status_code==200
    assert 'Reply *Chat* or *Call*' in sender.call_args.args[-1]
    assert signed(client,'Call','call').json()['status']=='voice_link'
    url=sender.call_args.args[-1].split('\n')[-1]
    token=parse_qs(urlsplit(url).fragment)['handoff'][0]
    return client,sender,token


def linked_call(monkeypatch):
    channel,sender,token=setup_link(monkeypatch)
    browser=TestClient(create_app())
    redeem=browser.post('/api/voice/redeem',json={'token':token})
    assert redeem.status_code==200,redeem.text
    sid=redeem.json()['session_id']
    started=browser.post('/api/voice/start',json={'session_id':sid})
    assert started.status_code==200,started.text
    return channel,browser,sender,sid,started.json()['call_id'],token


def test_one_time_link_same_case_grant_and_no_plaintext_token(persistent,monkeypatch):
    channel,browser,sender,sid,cid,token=linked_call(monkeypatch)
    with case_store.transaction() as tx:
        mapping=tx.execute('SELECT session_id FROM waha_chats').fetchone()
        assert mapping['session_id']==sid
        for table in ('voice_links','waha_events','channel_routes'):
            serialized=json.dumps([dict(r) for r in tx.execute('SELECT * FROM '+table)])
            assert token not in serialized and '900000000000@c.us' not in serialized
    stranger=TestClient(create_app())
    assert stranger.get('/api/cases/'+sid).status_code==403
    assert stranger.get('/api/voice/'+cid).status_code==403
    assert stranger.post('/api/voice/redeem',json={'token':token}).status_code==410
    assert browser.get('/api/cases/'+sid).status_code==200
    assert signed(channel,'Call','call-again').status_code==200
    new_token=parse_qs(urlsplit(sender.call_args.args[-1].split('\n')[-1]).fragment)['handoff'][0]
    with case_store.transaction() as tx:
        tx.execute('UPDATE voice_links SET expires_at=?',(time.time()-1,))
    assert stranger.post('/api/voice/redeem',json={'token':new_token}).status_code==410


def test_link_rejected_after_stop_and_invalid_origin(persistent,monkeypatch):
    channel,sender,token=setup_link(monkeypatch)
    assert signed(channel,'STOP','stop').status_code==200
    assert TestClient(create_app()).post('/api/voice/redeem',json={'token':token}).status_code==410
    assert signed(channel,'YOJANA','restart').status_code==200
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN','http://unsafe.invalid')
    assert signed(channel,'Call','unsafe').status_code==503


def test_chat_to_call_preserves_the_existing_case(persistent,monkeypatch):
    configure_waha(monkeypatch);monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN','https://frontend.invalid')
    sender=Mock();monkeypatch.setattr(waha,'_send',sender);mock_extraction(monkeypatch,{'age':62,'occupation':'retired'})
    channel=TestClient(create_app());signed(channel,'YOJANA','start-existing')
    assert signed(channel,'Chat','chat-mode').json()['status']=='chat_selected'
    assert signed(channel,'I am retired and 62','profile-existing').json()['status']=='replied'
    with case_store.transaction() as tx:
        original=tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
    signed(channel,'call','call-existing')
    token=parse_qs(urlsplit(sender.call_args.args[-1].split('\n')[-1]).fragment)['handoff'][0]
    browser=TestClient(create_app());data=browser.post('/api/voice/redeem',json={'token':token}).json()
    assert data['session_id']==original
    assert browser.get('/api/cases/'+original).json()['profile']['age']==62


def test_numeric_answer_in_chat_is_not_a_call_menu_choice(persistent,monkeypatch):
    configure_waha(monkeypatch);sender=Mock();monkeypatch.setattr(waha,'_send',sender)
    channel=TestClient(create_app());signed(channel,'YOJANA','choice-start');signed(channel,'Chat','choose-chat')
    mock_extraction(monkeypatch,{'num_children':2})
    response=signed(channel,'2','two-children')
    assert response.json()['status']=='replied'
    assert '#handoff=' not in sender.call_args.args[-1]


def test_voice_turn_idempotency_summary_consent_and_confirmed_status(persistent,monkeypatch):
    channel,browser,sender,sid,cid,_=linked_call(monkeypatch)
    provider=mock_extraction(monkeypatch,{'age':62,'occupation':'retired','state':'maharashtra'})
    body={'text':'I am retired and need financial assistance','lang':'en-IN','request_id':'fixture-turn-123'}
    response=browser.post(f'/api/voice/{cid}/turn',json=body)
    assert response.status_code==200,response.text
    assert browser.post(f'/api/voice/{cid}/turn',json=body).status_code==200
    assert len(provider.calls)==1
    ended=browser.post(f'/api/voice/{cid}/end',json={})
    assert ended.status_code==200 and ended.json()['whatsapp']['status']=='not_requested'
    assert ended.json()['recipient_delivery_verified'] is False
    assert browser.post(f'/api/voice/{cid}/turn',json=body).status_code==409
    consent=browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True})
    assert consent.json()['whatsapp']['status']=='pending'
    browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True})
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM summary_outbox').fetchone()['n']==1
    count=sender.call_count
    voice.deliver_summaries()
    assert sender.call_count==count+1
    status=browser.get('/api/voice/'+cid).json()
    assert status['whatsapp']['status']=='accepted_by_waha' and not status['recipient_delivery_verified']
    voice.deliver_summaries();assert sender.call_count==count+1
    assert browser.get('/api/cases/'+sid).json()['voice_sessions'][0]['id']==cid


def test_summary_failure_retry_and_stop_safety(persistent,monkeypatch):
    channel,browser,sender,sid,cid,_=linked_call(monkeypatch)
    assert browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True}).status_code==200
    sender.side_effect=httpx.ConnectError('fixture failure')
    voice.deliver_summaries()
    assert browser.get('/api/voice/'+cid).json()['whatsapp']['status']=='retrying'
    count=sender.call_count
    assert signed(channel,'STOP','stop-final').status_code==503  # STOP commits before acknowledgement delivery.
    voice.deliver_summaries()
    assert sender.call_count==count+1  # Only the explicit STOP acknowledgement was attempted.
    assert browser.get('/api/voice/'+cid).status_code==404
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM summary_outbox').fetchone()['n']==0


def test_summary_consent_can_be_cancelled_and_renewed(persistent,monkeypatch):
    channel,browser,sender,sid,cid,_=linked_call(monkeypatch)
    empty=browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True})
    assert empty.json()['summary']['schemes']==[]
    assert browser.post(f'/api/voice/{cid}/summary/cancel',json={}).status_code==200
    count=sender.call_count;voice.deliver_summaries();assert sender.call_count==count
    assert browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':True}).json()['whatsapp']['status']=='pending'
    voice.deliver_summaries()
    assert browser.post(f'/api/voice/{cid}/summary/cancel',json={}).status_code==409


def pcm():
    stream=io.BytesIO()
    with wave.open(stream,'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\x10\0\xf0'*800)  # non-silent synthetic tone
    return stream.getvalue()


def test_voice_endpoints_authorization_and_audio_limits(persistent,monkeypatch):
    channel,browser,sender,sid,cid,_=linked_call(monkeypatch)
    stranger=TestClient(create_app())
    assert stranger.post('/api/voice/stt',json={'call_id':cid,'audio':base64.b64encode(pcm()).decode()}).status_code==403
    assert stranger.post(f'/api/voice/{cid}/end',json={}).status_code==403
    monkeypatch.setenv('SARVAM_STT_ENABLED','true')
    assert browser.post('/api/voice/stt',json={'call_id':cid,'audio':'invalid'}).status_code==422
    monkeypatch.setattr(speech,'request',lambda *a,**k:{'transcript':'मेरी उम्र 62 है','language_code':'hi-IN'})
    response=browser.post('/api/voice/stt',json={'call_id':cid,'audio':base64.b64encode(pcm()).decode(),'lang':'auto'})
    assert response.status_code==200 and response.json()['language']=='hi'
    assert browser.post('/api/voice/tts',json={'call_id':cid,'text':'arbitrary unauthenticated synthesis'}).status_code==403
    assert browser.post(f'/api/voice/{cid}/end',json={'consent_whatsapp':'yes'}).status_code==422
    assert browser.post(f'/api/voice/{cid}/turn',json={'text':'hello','lang':'unsupported','request_id':'invalid-language-1'}).status_code==422
    assert browser.post('/api/voice/stt',json={'call_id':cid,'audio':'a'*760000}).status_code==413
    with case_store.transaction() as tx:
        assert 'मेरी उम्र' not in tx.session(sid,trusted=True)['state'].get('history',[])


def test_authorized_tts_uses_only_current_assistant_reply(persistent,monkeypatch):
    channel,browser,sender,sid,cid,_=linked_call(monkeypatch)
    mock_extraction(monkeypatch)
    reply=browser.post(f'/api/voice/{cid}/turn',json={'text':'I am 62 in Maharashtra','request_id':'tts-turn-fixture'}).json()['message']
    monkeypatch.setattr(speech,'request',lambda *a,**k:{'audios':[base64.b64encode(pcm()).decode()]})
    response=browser.post('/api/voice/tts',json={'call_id':cid,'text':reply[:1200],'lang':'en-IN'})
    assert response.status_code==200 and response.content.startswith(b'RIFF')
    assert response.headers['content-type']=='audio/wav'
    assert response.headers['cache-control']=='no-store'


def test_server_sarvam_contract_and_missing_key(monkeypatch):
    monkeypatch.delenv('SARVAM_API_KEY',raising=False)
    with pytest.raises(Exception) as error:
        speech.tts('Read the official source.','hi-IN')
    assert error.value.status_code==503
    monkeypatch.setenv('SARVAM_API_KEY','fixture-server-only-speech-key')
    real_client=httpx.Client; requests=[]
    def transport(req):
        requests.append(req)
        assert req.headers['api-subscription-key']=='fixture-server-only-speech-key'
        body=json.loads(req.content)
        assert body['language_code']=='hi-IN' and body['model']=='bulbul:v3'
        return httpx.Response(200,json={'audios':[base64.b64encode(pcm()).decode()]})
    monkeypatch.setattr(speech.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(transport),**kwargs))
    assert speech.tts('Read the source.','hi-IN').startswith(b'RIFF')
    assert len(requests)==1


def test_multilingual_fixture_detection_and_safe_values(persistent,monkeypatch):
    from pathlib import Path
    import yaml
    fixtures=yaml.safe_load((Path(__file__).parent/'fixtures/citizen_languages.yaml').read_text(encoding='utf-8'))
    client=TestClient(create_app())
    for code,item in fixtures.items():
        provider=MockProvider(queue=[{'language':code,'facts':{'age':62,'occupation':'retired','annual_family_income_inr':120000},
                                     'intent':'give_info','requested_category':item['category']}])
        monkeypatch.setattr(api,'get_llm',lambda _,p=provider:p)
        response=client.post('/api/v2/conversation',json={'text':item['text'],'lang':'auto'})
        assert response.status_code==200,response.text
        assert response.json()['language']==code
        assert response.json()['profile']['annual_family_income_inr']==120000
        assert not response.json()['question'] or response.json()['question']['field']!='is_student'


def test_private_invitation_cannot_enter_transcript(persistent):
    client=TestClient(create_app())
    response=client.post('/api/v2/conversation',json={'text':'https://frontend.invalid/voice#handoff='+'a'*43})
    assert response.status_code==422


@pytest.mark.parametrize('code', ['hi-IN','mr-IN','en-IN','bn-IN','ta-IN','te-IN','kn-IN','ml-IN','gu-IN','pa-IN'])
def test_language_registry_and_manual_override(persistent,monkeypatch,code):
    short=normalize(code)
    provider=MockProvider(queue=[{'language':'hi','facts':{'age':62,'annual_family_income_inr':120000},'intent':'give_info'}])
    monkeypatch.setattr(api,'get_llm',lambda _:provider)
    result=TestClient(create_app()).post('/api/v2/conversation',json={'text':'Mixed भाषा, income 120000','lang':code,'language_override':True})
    assert result.status_code==200
    assert result.json()['language']==short and result.json()['profile']['annual_family_income_inr']==120000
    languages=registry();assert len(languages)==23 and len({v['code'] for v in languages})==23
    assert all(not v['live_verified'] for v in languages)


def test_citizen_frontend_voice_and_security_headers():
    client=TestClient(create_app())
    landing=client.get('/')
    assert 'Find government schemes' in landing.text
    assert 'dashboard-state.js' in client.get('/app').text
    page=client.get('/voice')
    assert page.status_code==200 and page.headers['referrer-policy']=='no-referrer'
    for key in ('voice-start','voice-mute','voice-end','voice-transcript','summary-consent'):
        assert 'id="'+key+'"' in page.text
    assert 'SARVAM_API_KEY' not in page.text
