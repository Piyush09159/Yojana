"""Synthetic WAHA contract and real local decoder checks; no live account claims."""
import base64
import io
import subprocess
import shutil
import wave
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from web.app import create_app
from web import case_store, waha_bridge as waha, waha_media as media, speech
from test_deployment_v24 import persistent, configure_waha, signed, mock_extraction


@pytest.fixture
def channel(persistent, monkeypatch):
    configure_waha(monkeypatch)
    monkeypatch.setenv('SARVAM_STT_ENABLED','true')
    sender=Mock();monkeypatch.setattr(waha,'_send',sender)
    return TestClient(create_app()), sender


@pytest.mark.parametrize('word',['YOJANA','yojana','Yojana','  !yojna!  ','योजना','(योजना)','ＹＯＪＡＮＡ'])
def test_activation(channel,word):
    client,sender=channel
    assert signed(client,word,'activate').json()['status']=='started'
    assert '*Chat*' in sender.call_args.args[-1]
    assert '1 Chat' not in sender.call_args.args[-1]


@pytest.mark.parametrize('word',['START','Yojana-Pilot','yojana saathi','tell me about yojana','yojanax','Chat','Call','1','2','सुरू'])
def test_silence_before_keyword(channel,word):
    client,sender=channel
    assert signed(client,word,'unactivated').json()['status']=='not_opted_in'
    sender.assert_not_called()


@pytest.mark.parametrize('fields',[
    {'from':'123456@g.us'}, {'from':'status@broadcast'}, {'from':'123456@newsletter'},
    {'fromMe':True}, {'participant':'123456@c.us'}, {'broadcast':True}])
def test_no_group_bot_or_broadcast_activation(channel,fields):
    _,sender=channel
    payload={'from':'900000000000@c.us','id':'unsafe','body':'YOJANA',**fields}
    assert waha.persistent_event(waha.WAHAEvent(event='message',session='default',payload=payload))['status']=='ignored'
    sender.assert_not_called()


def note(identifier='note'):
    return waha.WAHAEvent(event='message',session='default',payload={
        'from':'900000000000@c.us','id':identifier,'body':'','hasMedia':True,
        'media':{'url':'http://waha:3000/api/files/voice.ogg','mimetype':'audio/ogg; codecs=opus'}})


@pytest.mark.parametrize('number',['1','2'])
def test_number_before_named_choice_is_data_not_menu(channel,monkeypatch,number):
    client,sender=channel
    mock_extraction(monkeypatch)
    signed(client,'YOJANA','activate')
    assert signed(client,number,'numeric').json()['status']=='replied'
    with case_store.transaction() as tx:
        assert tx.execute('SELECT mode FROM channel_preferences').fetchone()['mode']=='chat'
        assert tx.execute('SELECT COUNT(*) AS n FROM voice_links').fetchone()['n']==0


@pytest.mark.parametrize('name,code',[('Hindi','hi'),('Marathi','mr')])
def test_language_choice_survives_new_browser_call(channel,monkeypatch,name,code):
    client,sender=channel
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN','https://demo.invalid')
    signed(client,'YOJANA','activate');signed(client,name,'language')
    assert signed(client,'कॉल','call').json()['status']=='voice_link'
    with case_store.transaction() as tx:
        sid=tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
        assert tx.session(sid,trusted=True)['state']['lang']==code


def test_voice_same_case_dedup_and_language_switch(channel,monkeypatch):
    client,sender=channel;mock_extraction(monkeypatch)
    signed(client,'YOJANA','a');signed(client,'Chat','b');signed(client,'I am 62','c')
    with case_store.transaction() as tx:
        sid=tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
    transcriber=Mock(return_value={'text':'मला मदत हवी आहे','language':'mr'})
    monkeypatch.setattr(media,'transcribe',transcriber)
    mock_extraction(monkeypatch, {'occupation':'retired'})
    assert waha.persistent_event(note())['status']=='replied'
    assert waha.persistent_event(note())['status']=='duplicate'
    assert transcriber.call_count==1
    assert signed(client,'Hindi','lang').json()['status']=='language_selected'
    with case_store.transaction() as tx:
        assert tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']==sid
        assert tx.session(sid,trusted=True)['state']['lang']=='hi'
        assert len(tx.history(sid))==4
    mock_extraction(monkeypatch, {'age':62})
    assert signed(client,'2','number').json()['status']=='replied'
    assert signed(client,'चैट','named').json()['status']=='chat_selected'


def test_stop_during_transcription_cannot_restore_case(channel,monkeypatch):
    client,sender=channel
    signed(client,'YOJANA','activate')
    def stop(_):
        assert signed(client,'STOP','stop').json()['status']=='stopped'
        return {'text':'I am 62','language':'en'}
    monkeypatch.setattr(media,'transcribe',stop)
    assert waha.persistent_event(note())['status']=='cancelled'
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM case_sessions').fetchone()['n']==0
    assert sender.call_count==2


def test_failed_note_localized_and_not_retried(channel,monkeypatch):
    client,sender=channel
    signed(client,'योजना','activate');signed(client,'Marathi','language')
    transcriber=Mock(side_effect=ValueError('corrupt synthetic audio'))
    monkeypatch.setattr(media,'transcribe',transcriber)
    assert waha.persistent_event(note())['status']=='transcription_failed'
    assert 'पुन्हा' in sender.call_args.args[-1]
    assert waha.persistent_event(note())['status']=='duplicate'
    assert transcriber.call_count==1


def test_unactivated_note_never_downloaded(channel,monkeypatch):
    transcriber=Mock();monkeypatch.setattr(media,'transcribe',transcriber)
    assert waha.persistent_event(note())['status']=='not_opted_in'
    transcriber.assert_not_called()


@pytest.mark.parametrize('url',[
    'https://evil.invalid/api/files/voice.ogg','http://waha:3000/api/sessions',
    'http://waha:3000/api/files/../sessions','http://waha:3000/api/files/%2e%2e',
    'http://waha:3000/api/files/a?token=secret','http://user@waha:3000/api/files/a'])
def test_untrusted_media_rejected(monkeypatch,url):
    monkeypatch.setenv('WAHA_BASE_URL','http://waha:3000')
    with pytest.raises(ValueError):media.media_url({'url':url,'mimetype':'audio/ogg'})


def test_authenticated_bounded_download(monkeypatch,caplog):
    configure_waha(monkeypatch)
    def handle(request):
        assert request.headers['X-Api-Key']=='fixture-api-key'
        return httpx.Response(200,content=b'OggS-synthetic')
    original=httpx.Client
    monkeypatch.setattr(media.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    with caplog.at_level('INFO',logger='httpx'):
        assert media.download(note().payload['media'])==b'OggS-synthetic'
    assert '/api/files/' not in caplog.text


@pytest.mark.parametrize('response',[
    httpx.Response(200,headers={'content-length':str(media.MAX_DOWNLOAD+1)}),
    httpx.Response(200,content=b'x'*(media.MAX_DOWNLOAD+1)),
    httpx.Response(302,headers={'location':'https://evil.invalid/voice.ogg'}),
    httpx.Response(200,content=b'')])
def test_download_rejects_oversize_redirect_and_empty(monkeypatch,response):
    configure_waha(monkeypatch)
    original=httpx.Client
    monkeypatch.setattr(media.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(lambda _:response),**kwargs))
    with pytest.raises((ValueError,httpx.HTTPError)):
        media.download(note().payload['media'])


def test_voice_outbox_retry_does_not_repeat_transcription(channel,monkeypatch):
    client,sender=channel;mock_extraction(monkeypatch)
    signed(client,'YOJANA','activate')
    transcriber=Mock(return_value={'text':'I am 62','language':'en'})
    monkeypatch.setattr(media,'transcribe',transcriber)
    sender.side_effect=httpx.ConnectError('synthetic send failure')
    from fastapi import HTTPException
    with pytest.raises(HTTPException):waha.persistent_event(note())
    sender.side_effect=None
    assert waha.persistent_event(note())['status']=='replied'
    assert transcriber.call_count==1
    with case_store.transaction() as tx:
        sid=tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
        assert len(tx.history(sid))==2


def wav(seconds=1):
    out=io.BytesIO()
    with wave.open(out,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\x01\x00'*16000*seconds)
    return out.getvalue()


def test_real_ogg_opus_decode_and_sarvam_contract(monkeypatch):
    if not shutil.which('ffmpeg'):pytest.skip('Install ffmpeg for decoder validation')
    encoded=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i','pipe:0','-c:a','libopus','-f','ogg','pipe:1'],input=wav(),capture_output=True,check=True).stdout
    monkeypatch.setattr(media,'download',lambda _:encoded)
    monkeypatch.setenv('SARVAM_STT_ENABLED','true')
    provider=Mock(return_value={'transcript':'मी महाराष्ट्रात राहतो','language_code':'mr-IN'})
    monkeypatch.setattr(speech,'request',provider)
    result=media.transcribe({})
    assert result['language']=='mr'
    assert provider.call_args.args==('speech-to-text',)
    assert provider.call_args.kwargs['data']['language_code']=='unknown'
    assert provider.call_args.kwargs['files']['file'][1].startswith(b'RIFF')


@pytest.mark.parametrize('audio',[b'',b'corrupt',b'x'*(media.MAX_DOWNLOAD+1)])
def test_bad_audio_rejected(audio):
    with pytest.raises(ValueError):media.decode(audio)


def test_overlong_audio_rejected():
    if not shutil.which('ffmpeg'):pytest.skip('Install ffmpeg')
    with pytest.raises(ValueError):media.decode(wav(16))
