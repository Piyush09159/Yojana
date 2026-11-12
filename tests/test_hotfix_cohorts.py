"""Actual-catalog regressions: incomplete scraped predicates cannot prove eligibility."""
from copy import deepcopy
import json
import time
import io
import wave
import base64
from pathlib import Path
import shutil
import pytest
from fastapi.testclient import TestClient
from discovery.catalog import load_catalog
from core.cohorts import MEAL_IDS, NAVODAYA_IDS
from core.eligibility import evaluate_scheme
from core.relevance import relevance
from core.assessment import relevant_catalog, decide, recommendations
from saathi.shortlist import build_shortlist
from llm.providers.mock_provider import MockProvider
from web import conversation_api as api, case_store, voice, speech
from fastapi import HTTPException
from web.app import create_app
from test_deployment_v24 import persistent

STUDENT={'age':22,'state':'maharashtra','district':'nashik','occupation':'student',
         'is_student':True,'current_study_level':'undergraduate'}
SENIOR={'age':62,'state':'maharashtra','occupation':'retired','annual_family_income_inr':40000}
SCHOOL_IDS=MEAL_IDS|NAVODAYA_IDS


@pytest.fixture(autouse=True)
def actual_catalog_copy(_isolate_side_effects):
    # Exercise shipped rows without writing to the committed database.
    from store import db
    db.close()
    shutil.copyfile(Path(__file__).resolve().parents[1]/'data/schemes.db',_isolate_side_effects)


def assess(profile):
    catalog=relevant_catalog(profile,load_catalog())
    result=build_shortlist(profile,catalog)
    return decide(profile,catalog,result),result


@pytest.mark.parametrize('profile',[STUDENT,SENIOR,{'age':62,'state':'maharashtra','is_student':True}])
def test_adult_default_excludes_audited_school_programs(profile):
    decision,_=assess(profile)
    assert not SCHOOL_IDS & {c['id'] for c in decision['recommendations']}
    assert not decision['assessment_complete']
    for sid in SCHOOL_IDS:
        assert evaluate_scheme(load_catalog()[sid],profile).status!='eligible'


def test_actual_college_and_senior_shortlists_and_followups_differ():
    student, _=assess({**STUDENT,'_intent':{'categories':['education']}})
    senior, _=assess({**SENIOR,'_intent':{'categories':['income']}})
    assert student['next_candidate']['field']=='course_name'
    assert senior['next_candidate']['field'] in ('is_bpl','annual_family_income_inr','marital_status','disability_percent')
    assert {c['id'] for c in student['recommendations']} != {c['id'] for c in senior['recommendations']}
    assert senior['recommendations'][0]['id']=='ignoaps'
    for c in student['recommendations']:
        assert 'secondary schools' not in c['name'].lower()
        if any(x in c['name'].lower() for x in ('minority','vjnt','sbc','sc student')):
            assert c['status']!='eligible'
    minimal,_=assess({'age':22,'state':'maharashtra'})
    assert not minimal['assessment_complete']
    assert minimal['next_candidate']['field']=='occupation'


def test_child_grade_and_school_enrollment_are_mandatory_and_source_preserved():
    catalog=load_catalog();raw=deepcopy(catalog)
    child={'age':10,'state':'maharashtra','is_student':True,'current_study_level':'class_1_to_8'}
    for sid in SCHOOL_IDS:
        result=evaluate_scheme(catalog[sid],child)
        assert result.status=='needs_info' and 'enrolled_grade' in result.missing_fields
        assert result.source_url==catalog[sid]['source_url']
    nav=catalog['jawahar_navodaya_vidyalaya']
    assert evaluate_scheme(nav,{**child,'enrolled_grade':4}).status=='ineligible'
    assert evaluate_scheme(nav,{**child,'enrolled_grade':5}).status!='eligible'
    meal=catalog['school_nutrition_scheme']
    assert evaluate_scheme(meal,{**child,'enrolled_grade':5}).status=='needs_info'
    assert evaluate_scheme(meal,{**child,'enrolled_grade':5,'school_management':'private_unaided'}).status=='ineligible'
    assert evaluate_scheme(meal,{**child,'enrolled_grade':5,'school_management':'aided'}).status!='eligible'
    assert catalog==raw
    # Adults actually enrolled in school are not generically age-blacklisted.
    assert relevance(meal,{**child,'age':30})>0


def test_meal_aliases_deduplicated_and_manual_matches_do_not_complete():
    profile={'age':10,'state':'maharashtra','occupation':'student','is_student':True,
             'current_study_level':'class_1_to_8','enrolled_grade':5,'school_management':'aided',
             '_intent':{'categories':['education']}}
    catalog={k:v for k,v in load_catalog().items() if k in SCHOOL_IDS}
    result=build_shortlist(profile,catalog)
    cards=recommendations(result)
    assert len(MEAL_IDS & {c['id'] for c in cards})<=1
    decision=decide(profile,catalog,result)
    assert decision['provisional']
    assert all(c['status']!='eligible' for c in cards)


def extract(monkeypatch,facts,category=None):
    client=MockProvider(queue=[{'language':'en','intent':'give_info','facts':facts,'requested_category':category}])
    monkeypatch.setattr(api,'get_llm',lambda _:client)


def test_identity_correction_invalidates_and_fresh_cases_remain_separate(persistent,monkeypatch):
    client=TestClient(create_app())
    extract(monkeypatch,STUDENT,'education')
    first=client.post('/api/v2/conversation',json={'text':'College student in Nashik'}).json()
    sid=first['session_id']
    extract(monkeypatch,{'age':62})
    blocked=client.post('/api/v2/conversation',json={'session_id':sid,'text':'I am 62'})
    assert blocked.status_code==409
    assert client.get('/api/cases/'+sid).json()['profile']['age']==22
    extract(monkeypatch,{'age':62,'occupation':'retired'},'income')
    changed=client.post('/api/v2/conversation',json={'session_id':sid,'text':'Correction: I am actually 62 and retired','correct_facts':True}).json()
    assert changed['profile']['age']==62 and 'current_study_level' not in changed['profile']
    assert not SCHOOL_IDS & {c['id'] for c in changed['recommendations']}
    saved=client.get('/api/cases/'+sid).json()
    assert saved['recommendations']['schemes']==changed['recommendations']
    extract(monkeypatch,STUDENT,'education')
    fresh=client.post('/api/v2/conversation',json={'text':'Another college citizen'}).json()
    assert fresh['session_id']!=sid and fresh['profile']['age']==22
    assert client.get('/api/cases/'+sid).json()['profile']['age']==62
    voice1=client.post('/api/voice/start',json={}).json()
    voice2=client.post('/api/voice/start',json={}).json()
    assert voice1['session_id']!=voice2['session_id']
    assert client.post('/api/voice/start',json={'session_id':voice1['session_id']}).json()['resumed']


def test_dependent_profile_never_inherits_parent_facts(persistent,monkeypatch):
    client=TestClient(create_app());extract(monkeypatch,SENIOR,'income')
    parent=client.post('/api/v2/conversation',json={'text':'I am retired'}).json()
    extract(monkeypatch,{'enrolled_grade':5,'is_student':True},'education')
    child=client.post('/api/v2/conversation',json={'session_id':parent['session_id'],'text':'For my child, studying in class 5'}).json()
    assert 'age' not in child['profile'] and 'occupation' not in child['profile']
    assert 'annual_family_income_inr' not in child['profile']
    assert not child['assessment_complete']


def test_pre_hotfix_dashboard_and_voice_snapshots_cannot_restore_eligible_school_meals(persistent,monkeypatch):
    client=TestClient(create_app());extract(monkeypatch,STUDENT,'education')
    current=client.post('/api/v2/conversation',json={'text':'I am a college student'}).json()
    sid=current['session_id']
    unsafe={'id':'mid_day_meal_scheme_shaleya_poshan_aahar_yojana','name':'Old school meal snapshot',
            'status':'eligible','why':'Eligible for school meal','check_yourself':[]}
    with case_store.transaction() as tx:
        state=tx.session(sid,trusted=True)['state'];state['assessment']={'assessment_complete':True}
        state['last_response']={'results':{'start_with':unsafe},'recommendations':[unsafe]}
        tx.save(sid,state)
        tx.execute('INSERT INTO voice_calls VALUES(?,?,?,?,?,?,?,?)',
                   ('legacy-call',sid,'ended','[]',json.dumps({'schemes':[unsafe],'caveat':'Old'}),0,time.time()-5,time.time()))
    restored=client.get('/api/cases/'+sid).json()
    assert not SCHOOL_IDS & {c['id'] for c in restored['recommendations']['schemes']}
    assert restored['latest_voice_summary']['schemes'][0]['status']=='check_manually'
    history=client.get('/api/voice/legacy-call').json()
    assert history['summary']['schemes'][0]['status_label']=='Needs verification'
    assert 'predates' in history['summary']['caveat']
    ended=client.post('/api/voice/legacy-call/end',json={}).json()
    assert ended['summary']['schemes'][0]['status']=='check_manually'


@pytest.mark.parametrize('frames',[b'',b'\0\0'*1600])
def test_empty_or_silent_provider_audio_falls_back_safely(monkeypatch,frames):
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(frames)
    monkeypatch.setattr(speech,'request',lambda *a,**k:{'audios':[base64.b64encode(out.getvalue()).decode()]})
    with pytest.raises(HTTPException) as error:
        speech.tts('Short response','en')
    assert error.value.status_code==502
    assert error.value.detail['code'] in ('silent_speech_response','invalid_speech_response')
