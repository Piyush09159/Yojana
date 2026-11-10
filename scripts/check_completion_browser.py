"""Local integration checks; mocked extraction and simulated audio, no paid calls.

Use the deployment_mock.yaml backend fixture and an authorized case database.
"""
import argparse
import base64
import io
import json
import os
import wave
from playwright.sync_api import sync_playwright


def check(url):
    root=url.rstrip('/')
    with sync_playwright() as playwright:
        executable=os.getenv('BROWSER_EXECUTABLE')
        browser=playwright.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
        page=browser.new_page(viewport={'width':390,'height':950})
        page.add_init_script("Object.defineProperty(window,'speechSynthesis',{value:undefined});")
        page.goto(root+'/voice');page.locator('#voice-start').click()
        page.wait_for_function("!document.querySelector('#voice-send').disabled")
        completed=None
        for index in range(10):
            page.locator('#voice-text').fill('I am 62 in Maharashtra')
            with page.expect_response(lambda r:'/api/voice/' in r.url and r.url.endswith('/turn')) as response:
                page.locator('#voice-send').click()
            completed=response.value.json()
            if completed.get('assessment_complete'):
                break
            page.wait_for_function("!document.querySelector('#voice-send').disabled")
        assert completed['assessment_complete'] and completed['voice_finalized']
        page.wait_for_function("document.querySelector('#voice-state').textContent==='Completed'")
        assert page.locator('#voice-summary .summary-card').count()>0
        saved_id=completed['session_id']
        page.goto(root+'/app')
        page.wait_for_function("document.querySelectorAll('#saved-matches .citizen-result').length>0")
        assert page.get_by_role('heading',name='Your Scheme Matches',exact=True).count()==1
        assert 'Assessment saved:' in page.locator('#assessment-date').inner_text()
        assert 'ended' in page.locator('#citizen-history').inner_text()
        page.reload();page.wait_for_function("document.querySelectorAll('#saved-matches .citizen-result').length>0")
        page.locator('#refresh-case').click();page.wait_for_function("!document.querySelector('#refresh-case').disabled")
        assert page.locator('#saved-matches a').count()>0
        assert all('transcript' not in key.lower() for key in page.evaluate('Object.keys(localStorage)'))
        # A valid ID alone gives another browser no data, including on optional restoration.
        stranger=browser.new_page();stranger.goto(root+'/app');stranger.evaluate('(id)=>sessionStorage.setItem("yojana-case-id",id)',saved_id);stranger.reload()
        stranger.wait_for_function("document.querySelector('#assessment-date').textContent.includes('expired')")
        assert stranger.locator('#saved-matches .citizen-result').count()==0
        stranger.close();page.close()
        print('Automatic backend completion/finalization, persisted voice summary, dashboard reload/return/refresh and unauthorized restoration passed')

        # Isolate playback failure handling while keeping session creation/end on the real backend.
        page=browser.new_page(viewport={'width':390,'height':950})
        page.add_init_script("""window.deviceSpoken=[];window.audioPlayed=0;
Object.defineProperty(window,'speechSynthesis',{value:{speak(u){window.deviceSpoken.push(u.text);setTimeout(()=>u.onend?.(),10)},cancel(){}}});
window.SpeechSynthesisUtterance=class {constructor(text){this.text=text}};
window.Audio=class {play(){window.audioPlayed++;setTimeout(()=>this.onended?.(),10);return Promise.resolve()}pause(){}};""")
        def capabilities(route):
            response=route.fetch();data=response.json();data['tts_configured']=True;route.fulfill(response=response,json=data)
        page.route('**/api/voice/capabilities',capabilities)
        counter={'turn':0,'tts':0}
        def turn(route):
            counter['turn']+=1
            route.fulfill(status=200,json={'session_id':'browser-ui-fixture','message':'A short response is ready.',
                           'spoken_text':'A short response is ready.','assessment_complete':False,'language':'en'})
        page.route('**/api/voice/*/turn',turn)
        audio=io.BytesIO()
        with wave.open(audio,'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*1600)
        def tts(route):
            counter['tts']+=1
            if counter['tts']<3:route.fulfill(status=200,content_type='audio/wav',body=audio.getvalue())
            else:route.fulfill(status=402,json={'detail':{'code':'speech_credits_exhausted'}})
        page.route('**/api/voice/tts',tts)
        page.goto(root+'/voice');page.locator('#voice-start').click()
        page.wait_for_function("!document.querySelector('#voice-send').disabled")
        for index in range(5):
            page.locator('#voice-text').fill('Synthetic UI reply '+str(index));page.locator('#voice-send').click()
            page.wait_for_function('(n)=>document.querySelectorAll("#voice-transcript .turn").length===n',arg=(index+1)*2)
            page.wait_for_function("!document.querySelector('#voice-send').disabled")
            page.wait_for_function('(n)=>window.audioPlayed+window.deviceSpoken.length>=n',arg=index+1)
        assert counter['tts']==3 and page.evaluate('window.deviceSpoken.length')>=3
        assert page.locator('#voice-transcript').inner_text().count('A short response is ready.')==5
        page.locator('#voice-end').click();page.wait_for_function("document.querySelector('#voice-state').textContent==='Completed'")
        print('Two successful audio turns followed by exhausted credits: automatic device fallback, no repeated server TTS and manual end passed (simulated audio)')
        page.close();browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);check(parser.parse_args().url)
