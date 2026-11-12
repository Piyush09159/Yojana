"""Local acceptance: real authorized engine + mock fixture; simulated device/audio errors.

Never run against production. No paid provider requests are made by this script.
"""
import argparse
import os
from playwright.sync_api import sync_playwright


def check(url):
    root=url.rstrip('/')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['BROWSER_EXECUTABLE']} if os.getenv('BROWSER_EXECUTABLE') else {}))
        page=browser.new_page(viewport={'width':390,'height':900})
        page.add_init_script("Object.defineProperty(window,'speechSynthesis',{value:undefined});")
        page.goto(root+'/voice');page.locator('#voice-start').click()
        def turn(text):
            page.wait_for_function("!document.querySelector('#voice-send').disabled")
            page.locator('#voice-text').fill(text)
            with page.expect_response(lambda r:r.url.endswith('/turn')) as response:
                page.locator('#voice-send').click()
            return response.value.json()
        student=turn('I am 22, a college undergraduate in Trimbakeshwar Nashik Maharashtra seeking scholarships.')
        assert not student['assessment_complete'] and student['question']['field']=='course_name'
        assert not any('navodaya' in c['id'] or 'meal' in c['id'] for c in student['recommendations'])
        page.locator('#voice-end').click();page.wait_for_function("document.querySelector('#voice-state').textContent==='Completed'")
        page.locator('#voice-start').click()
        assert page.locator('#voice-summary .summary-card').count()==0
        senior=turn('I am 62, retired in Maharashtra, income 40000 a year, seeking financial assistance.')
        assert senior['session_id']!=student['session_id']
        assert senior['profile']['age']==62 and 'is_student' not in senior['profile']
        assert not senior['assessment_complete'] and senior['question']['field']=='is_bpl'
        assert senior['recommendations'][0]['id']=='ignoaps'
        assert {c['id'] for c in student['recommendations']}!={c['id'] for c in senior['recommendations']}
        page.goto(root+'/app');page.wait_for_function("document.querySelectorAll('#saved-matches .citizen-result').length>0")
        page.locator('#fresh-assessment').click();page.locator('#message').fill('I am 22, a college undergraduate in Trimbakeshwar Nashik Maharashtra seeking scholarships.')
        with page.expect_response(lambda r:r.url.endswith('/api/v2/conversation')) as response:page.locator('#send').click()
        fresh=response.value.json();assert fresh['session_id']!=senior['session_id'] and fresh['profile']['age']==22
        page.reload();page.wait_for_function("document.querySelector('#case-status').textContent.includes('restored')")
        page.locator('#continue-assessment').click()
        page.locator('#message').fill('I am 22, a college undergraduate in Trimbakeshwar Nashik Maharashtra seeking scholarships.')
        with page.expect_response(lambda r:r.url.endswith('/api/v2/conversation')) as response:page.locator('#send').click()
        assert response.value.json()['session_id']==fresh['session_id']
        page.close()
        print('Actual catalog: college/senior relevance, high-value followups, separate fresh cases, explicit dashboard continuation passed')

        for mode in ['decode_error','denied','silent','fetch_timeout','402','429','504','no_device','final','final_402','final_muted']:
            page=browser.new_page(viewport={'width':390,'height':900})
            page.add_init_script("""window.deviceSpoken=[];window.audioAttempts=0;
Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{speak(u){window.deviceSpoken.push(u.text);setTimeout(()=>u.onend?.(),10)},cancel(){}}});
window.SpeechSynthesisUtterance=class {constructor(text){this.text=text}};
window.Audio=class {play(){window.audioAttempts++;if(window.testMode==='denied')return Promise.reject(new DOMException('denied','NotAllowedError'));if(window.testMode==='decode_error')setTimeout(()=>this.onerror?.(),10);else if(window.testMode!=='silent')setTimeout(()=>this.onended?.(),10);return Promise.resolve()}pause(){}};""")
            page.add_init_script('window.testMode='+repr(mode)+';')
            if mode=='no_device':page.add_init_script("Object.defineProperty(window,'speechSynthesis',{value:undefined});")
            def capabilities(route):
                response=route.fetch();data=response.json();data['tts_configured']=True;route.fulfill(response=response,json=data)
            page.route('**/api/voice/capabilities',capabilities)
            def mocked_turn(route):
                final=mode.startswith('final')
                route.fulfill(json={'message':'Readable reply immediately available.','spoken_text':'Short conclusion.' if final else 'Short reply.',
                                    'assessment_complete':final,'recommendations':[], 'summary':{'schemes':[],'caveat':'Provisional; verify conditions.'},'language':'en'})
            page.route('**/api/voice/*/turn',mocked_turn)
            calls=[];unresolved=[]
            def speech(route):
                calls.append(1)
                if mode=='fetch_timeout':unresolved.append(route);return  # Client abort timer, without delaying assistant text.
                if mode in ('402','429','504','no_device','final_402'):route.fulfill(status=int(mode) if mode.isdigit() else 402 if mode=='final_402' else 503,json={'detail':{'code':'speech_unavailable'}})
                else:route.fulfill(status=200,content_type='audio/wav',body=b'RIFF-simulated-audio')
            page.route('**/api/voice/tts',speech)
            page.goto(root+'/voice');page.locator('#voice-start').click();page.wait_for_function("!document.querySelector('#voice-send').disabled")
            if mode=='final_muted':page.locator('#voice-mute').click()
            page.locator('#voice-text').fill('Synthetic audio fixture');page.locator('#voice-send').click()
            page.wait_for_function("document.querySelector('#voice-transcript').textContent.includes('Readable reply immediately available.')")
            if mode=='no_device':page.wait_for_function("document.querySelector('#voice-help').textContent.includes('No device voice')")
            elif mode in ('final','final_muted'):
                page.wait_for_function("document.querySelector('#voice-state').textContent==='Completed'")
                if mode=='final_muted':
                    assert page.evaluate('window.audioAttempts')==0 and len(calls)==0
                    page.locator('#voice-mute').click();page.locator('#voice-replay').click()
                page.wait_for_function('window.audioAttempts===1')
            else:page.wait_for_function('window.deviceSpoken.length===1',timeout=15000)
            for pending in unresolved:
                try:pending.fulfill(status=504,json={'detail':{'code':'speech_timeout'}})
                except Exception:pass
            assert len(calls)==1  # No recursive synthesis following decode failure.
            page.locator('#voice-replay').click()
            if mode not in ('no_device','final','final_muted'):page.wait_for_function('window.deviceSpoken.length===2')
            if mode in ('final','final_muted'):
                page.wait_for_function('window.audioAttempts===2')
                assert page.locator('#voice-summary').inner_text()
            page.unroute_all(behavior='ignoreErrors');page.close();print(mode+': text, bounded playback/fallback and replay passed (simulated audio)')

        # Text must appear while TTS is still pending; a late aborted reply cannot play.
        page=browser.new_page()
        page.add_init_script("""window.audioAttempts=0;Object.defineProperty(window,'speechSynthesis',{value:undefined});window.Audio=class {play(){window.audioAttempts++;setTimeout(()=>this.onended?.(),10);return Promise.resolve()}pause(){}};""")
        page.route('**/api/voice/capabilities',capabilities)
        def live_reply(route):route.fulfill(json={'message':'Immediate text','spoken_text':'Short reply','assessment_complete':False,'language':'en'})
        page.route('**/api/voice/*/turn',live_reply)
        held=[]
        def delayed_audio(route):
            if not held:held.append(route)
            else:route.fulfill(status=200,content_type='audio/wav',body=b'RIFF-simulated-audio')
        page.route('**/api/voice/tts',delayed_audio)
        page.goto(root+'/voice');page.locator('#voice-start').click();page.wait_for_function("!document.querySelector('#voice-send').disabled")
        page.locator('#voice-text').fill('First');page.locator('#voice-send').click()
        page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length===2")
        assert page.evaluate('window.audioAttempts')==0
        page.locator('#voice-text').fill('Second');page.locator('#voice-send').click()
        page.wait_for_function('window.audioAttempts===1')
        try:held[0].fulfill(status=200,content_type='audio/wav',body=b'RIFF-stale-audio')
        except Exception:pass  # Aborted requests can no longer be fulfilled.
        assert page.evaluate('window.audioAttempts')==1
        page.unroute_all(behavior='ignoreErrors');page.close();print('Immediate text during pending speech and stale-audio cancellation passed')
        browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);check(parser.parse_args().url)
