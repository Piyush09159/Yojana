"""Shared stopping, finalized voice recovery and safe speech circuit breaking."""
import base64
import hashlib
import json
import time
from unittest.mock import Mock
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from core.assessment import relevant_catalog, decide
from saathi.shortlist import build_shortlist
from web import conversation_api as api, voice, case_store, speech, metrics
from web.app import create_app
from llm.providers.mock_provider import MockProvider
from test_deployment_v24 import persistent
from test_citizen_v25 import scheme, linked_call, pcm


def catalog():
    rules=[{'field':'age','op':'gte','value':60,'reason':'Age threshold'},
           {'field':'annual_family_income_inr','op':'lte','value':100000,'reason':'Income ceiling'}]
    result={f'pension{i}':scheme(f'pension{i}',f'Old Age Pension {i}',rules,state='maharashtra') for i in range(5)}
    for i in range(150):
        result[f'student{i}']=scheme(f'student{i}','Student scholarship',[{'field':'is_student','op':'eq','value':True}])
    return result


def provider(monkeypatch,facts,complete=False):
    client=MockProvider(queue=[{'language':'en','facts':facts,'intent':'give_info','requested_category':'income',
                                 'assessment_complete':complete}])
    monkeypatch.setattr(api,'get_llm',lambda _:client)
    monkeypatch.setattr(api,'load_catalog',catalog)
    return client


FULL={'age':62,'occupation':'retired','state':'maharashtra','annual_family_income_inr':40000}


def test_retired_senior_completes_with_relevant_top_five(persistent,monkeypatch):
    provider(monkeypatch,FULL)
    client=TestClient(create_app())
    response=client.post('/api/v2/conversation',json={'text':'I am retired and need financial assistance.'})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['assessment_complete'] and result['question'] is None
    assert len(result['recommendations'])==5
    assert all(c['id'].startswith('pension') for c in result['recommendations'])
    assert result['results']['schemes_checked']==5
    assert 'not government approval' in result['message']
    assert len(result['spoken_text'])<360
    assert all(c['source_url'] in result['message'] for c in result['recommendations'])


def test_model_cannot_end_ambiguous_situation(persistent,monkeypatch):
    provider(monkeypatch,{'age':62},complete=True)
    result=TestClient(create_app()).post('/api/v2/conversation',json={'text':'I need financial assistance'}).json()
    assert not result['assessment_complete']
    assert not result['assessment']['llm_completion_validated']
    assert result['question']['field']=='state'
    assert all(c['status']=='needs_info' for c in result['recommendations'])


def test_repetition_budget_stops_with_explicit_uncertainty(persistent,monkeypatch):
    monkeypatch.setenv('ASSESSMENT_QUESTION_BUDGET','3')
    monkeypatch.setattr(api,'load_catalog',catalog)
    client=TestClient(create_app());sid=None;questions=[]
    for _ in range(4):
        mock=MockProvider(queue=[{'language':'en','facts':{},'intent':'greeting'}])
        monkeypatch.setattr(api,'get_llm',lambda _,p=mock:p)
        result=client.post('/api/v2/conversation',json={'text':'Hello','session_id':sid}).json()
        sid=result['session_id']
        if result['question']:
            questions.append(result['question']['field'])
    assert result['assessment_complete'] and result['assessment']['reason']=='question_budget'
    assert len(questions)==3 and questions.count('state')<=2
    assert all(c['status']!='eligible' for c in result['recommendations'])
    assert 'provisional' in result['message']


def test_relevant_catalog_is_bounded_and_geographic():
    data=catalog()
    for i in range(100):
        data['other'+str(i)]=scheme('other'+str(i),'Old Age Pension',[],state='tamil_nadu')
    profile={**FULL,'_intent':{'categories':['income']}}
    selected=relevant_catalog(profile,data)
    assert len(selected)<=60 and all(c['state']=='maharashtra' for c in selected.values())


def test_low_marginal_value_completes_without_settling_catalog():
    profile={**FULL,'_intent':{'categories':['income']}}
    data=catalog()
    for index,field in enumerate(['is_bpl','is_ex_serviceman']):
        data['pension'+str(index)]['eligibility']['all']=list(data['pension'+str(index)]['eligibility']['all'])
        data['pension'+str(index)]['eligibility']['all'].append({'field':field,'op':'eq','value':True})
    selected=relevant_catalog(profile,data);results=build_shortlist(profile,selected)
    decision=decide(profile,selected,results,count=3)
    assert decision['assessment_complete'] and decision['remaining_uncertainty']


def test_voice_auto_finalization_is_durable_idempotent_and_refinable(persistent,monkeypatch):
    channel,browser,sender,sid,cid,_=linked_call(monkeypatch)
    provider(monkeypatch,FULL)
    body={'text':'I need financial assistance','request_id':'complete-voice-fixture'}
    response=browser.post(f'/api/voice/{cid}/turn',json=body)
    assert response.status_code==200,response.text
    result=response.json();assert result['assessment_complete'] and result['voice_finalized']
    again=browser.post(f'/api/voice/{cid}/turn',json=body).json()
    assert again['voice_finalized']
    with case_store.transaction() as tx:
        row=tx.execute('SELECT * FROM voice_calls WHERE id=?',(cid,)).fetchone()
        saved_end=row['ended_at'];assert row['status']=='ended' and row['summary']
        assert tx.execute('SELECT COUNT(*) AS n FROM summary_outbox').fetchone()['n']==0
        state=tx.session(sid,trusted=True)['state'];state.pop('last_response',None)
        tx.save(sid,state)
    restored=browser.get('/api/cases/'+sid).json()
    assert len(restored['recommendations']['schemes'])==5
    assert restored['assessment_date']==saved_end
    manual=browser.post(f'/api/voice/{cid}/end',json={})
    assert manual.status_code==200
    with case_store.transaction() as tx:
        assert tx.execute('SELECT ended_at FROM voice_calls WHERE id=?',(cid,)).fetchone()['ended_at']==saved_end
    speech_data=browser.post('/api/voice/tts',json={'call_id':cid,'text':result['spoken_text'],'request_id':body['request_id']})
    assert speech_data.status_code in (200,503) # Authorized conclusion; no key supplied.
    assert TestClient(create_app()).get('/api/cases/'+sid).status_code==403
    fresh=browser.post('/api/voice/start',json={'session_id':sid}).json()
    assert fresh['call_id']!=cid
    provider(monkeypatch,{'annual_family_income_inr':200000})
    changed=browser.post('/api/v2/conversation',json={'text':'My income changed','session_id':sid,'refine':True})
    assert changed.status_code==200 and changed.json()['profile']['annual_family_income_inr']==200000


def test_manual_voice_end_does_not_declare_assessment_complete(persistent,monkeypatch):
    _,browser,_,sid,cid,_=linked_call(monkeypatch)
    provider(monkeypatch,{'age':62})
    response=browser.post(f'/api/voice/{cid}/turn',json={'text':'I need financial assistance','request_id':'manual-end-fixture'})
    assert not response.json()['assessment_complete']
    result=browser.post(f'/api/voice/{cid}/end',json={})
    assert result.status_code==200 and not result.json()['summary']['assessment']['assessment_complete']


def test_tts_after_successful_turns_stops_on_exhausted_credit(monkeypatch):
    monkeypatch.setenv('SARVAM_API_KEY','fixture-credit-key');monkeypatch.setattr(speech,'_blocked',{})
    real=httpx.Client;calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(200,json={'audios':[base64.b64encode(pcm()).decode()]}) if len(calls)<3 else httpx.Response(402,json={'error':'private provider body'})
    monkeypatch.setattr(speech.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(transport),**kw))
    assert speech.tts('First reply','en').startswith(b'RIFF')
    assert speech.tts('Second reply','en').startswith(b'RIFF')
    for _ in range(3):
        with pytest.raises(HTTPException) as error:speech.tts('Next reply','en')
        assert error.value.status_code==402 and error.value.detail['code']=='speech_credits_exhausted'
    assert len(calls)==3


@pytest.mark.parametrize('status,expected',[(429,'speech_rate_limited'),(422,'speech_unsupported_request'),(503,'speech_provider_unavailable')])
def test_safe_speech_errors_and_latency(monkeypatch,status,expected):
    monkeypatch.setenv('SARVAM_API_KEY','fixture-errors-key');monkeypatch.setattr(speech,'_blocked',{})
    real=httpx.Client
    monkeypatch.setattr(speech.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(lambda _:httpx.Response(status,json={'secret':'citizen private data'})),**kw))
    with pytest.raises(HTTPException) as error:speech.tts('Citizen private text','en')
    assert error.value.detail['code']==expected
    value=metrics._values[-1]
    assert set(value)=={'stage','latency_ms','outcome'} and value['stage']=='tts'
    assert 'Citizen' not in json.dumps(value)


def test_speech_timeout_is_explicit(monkeypatch):
    monkeypatch.setenv('SARVAM_API_KEY','fixture-timeout-key');monkeypatch.setattr(speech,'_blocked',{})
    real=httpx.Client
    def timeout(_):raise httpx.ReadTimeout('private transport text')
    monkeypatch.setattr(speech.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(timeout),**kw))
    with pytest.raises(HTTPException) as error:speech.tts('safe short reply','en')
    assert error.value.status_code==504 and error.value.detail['code']=='speech_timeout'
