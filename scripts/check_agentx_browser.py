"""Tap interruption acceptance against a local synthetic development backend.

Speech/media/provider events are simulated; this does NOT verify audible device
speech, a physical microphone, Sarvam, Groq, WhatsApp delivery or PostgreSQL.
"""
import argparse
import os
from urllib.parse import urlsplit
from pathlib import Path
from playwright.sync_api import sync_playwright

MEDIA = """
window.recognizers=[];window.deviceSpoken=[];window.audioStarts=0;window.audioPauses=0;window.speechCancels=0;
window.SpeechRecognition=class {
 start(){window.recognizers.push(this);this.onstart?.()}
 abort(){this.onend?.()} stop(){this.onend?.()}
 emit(text){const callback=this.onresult;const row=[{transcript:text}];row.isFinal=true;
  const event={results:[row]};callback?.(event);callback?.(event)}
};
Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{
 speak(u){window.deviceSpoken.push(u)},cancel(){window.speechCancels++}
}});
window.SpeechSynthesisUtterance=class{constructor(text){this.text=text}};
window.Audio=class{
 play(){window.audioStarts++;window.oldAudioEnded=this.onended;this.onplaying?.();return Promise.resolve()}
 pause(){window.audioPauses++}
};
"""


def check(url):
    if urlsplit(url).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise SystemExit('Run this fixture-only check against your local development backend.')
    root=url.rstrip('/')
    out=Path('artifacts/agentx/browser');out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True, **({'executable_path':os.environ['BROWSER_EXECUTABLE']} if os.getenv('BROWSER_EXECUTABLE') else {}))
        for mode in ['pending_tts','server_audio','device_voice','permission_denied','typed_fallback']:
            page=browser.new_page(viewport={'width':390,'height':900})
            page.add_init_script(MEDIA)
            if mode=='permission_denied':
                page.add_init_script("window.SpeechRecognition=class{start(){this.onerror?.({error:'not-allowed'})}abort(){}stop(){}}")
            if mode=='typed_fallback':
                page.add_init_script("Object.defineProperty(window,'SpeechRecognition',{value:undefined});Object.defineProperty(window,'webkitSpeechRecognition',{value:undefined});")
            def capabilities(route):
                data=route.fetch().json();data.update(tts_configured=mode in ('pending_tts','server_audio'),stt_configured=False)
                route.fulfill(json=data)
            page.route('**/api/voice/capabilities',capabilities)
            requests=[]
            def turn(route):
                requests.append(route.request.post_data_json)
                route.fulfill(json={'message':'Reply '+str(len(requests)), 'spoken_text':'Short reply '+str(len(requests)), 'language':'en','assessment_complete':False})
            page.route('**/api/voice/*/turn',turn)
            held=[];tts_calls=[]
            def tts(route):
                tts_calls.append(1)
                if mode=='pending_tts' and len(tts_calls)==1:held.append(route)
                else:route.fulfill(status=200,content_type='audio/wav',body=b'RIFF-synthetic-audio')
            page.route('**/api/voice/tts',tts)
            page.goto(root+'/voice')
            page.locator('#voice-start').click()
            page.wait_for_function("!document.querySelector('#voice-send').disabled")
            page.locator('#voice-text').fill('Synthetic first turn');page.locator('#voice-send').click()
            page.wait_for_function("!document.querySelector('#voice-interrupt').disabled")
            if mode=='pending_tts':page.wait_for_function("document.querySelector('#voice-state').textContent==='Speaking'")
            elif mode=='server_audio':page.wait_for_function('window.audioStarts===1')
            else:page.wait_for_function('window.deviceSpoken.length===1')
            before=page.evaluate('window.audioStarts')
            page.locator('#voice-interrupt').click()
            assert 'old reply will not resume' in page.locator('#voice-interruption').inner_text()
            assert page.evaluate('window.speechCancels')>0
            if mode=='server_audio':assert page.evaluate('window.audioPauses')>=1
            if mode=='permission_denied':
                page.wait_for_function("document.querySelector('#voice-state').textContent==='Recognition unavailable'")
                page.locator('#voice-text').fill('Synthetic interruption');page.locator('#voice-send').click()
            elif mode=='typed_fallback':
                assert page.locator('#voice-state').inner_text()=='Type your message'
                page.locator('#voice-text').fill('Synthetic interruption');page.locator('#voice-send').click()
            else:
                page.wait_for_function("document.querySelector('#voice-state').textContent==='Listening'")
                # A cancelled old synthesis completion must not overwrite Listening.
                page.evaluate('window.oldAudioEnded?.();window.deviceSpoken[0]?.onend?.()')
                assert page.locator('#voice-state').inner_text()=='Listening'
                for pending in held:
                    try:pending.fulfill(status=200,content_type='audio/wav',body=b'RIFF-stale')
                    except Exception:pass  # Aborted network requests cannot always be fulfilled.
                assert page.evaluate('window.audioStarts')==before
                page.evaluate("window.recognizers.at(-1).emit('Synthetic interruption')")
            page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length===4")
            assert len(requests)==2
            assert requests[1]['text']=='Synthetic interruption'
            assert requests[0]['request_id']!=requests[1]['request_id']
            if mode in ('pending_tts','server_audio'):page.wait_for_function(f'window.audioStarts==={before+1}')
            else:page.wait_for_function('window.deviceSpoken.length===2')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(out/f'interrupt-{mode}.png'),full_page=True)
            page.unroute_all(behavior='ignoreErrors');page.close()
            print(mode+': tap cancels old speech; next turn exactly once; stale callback ignored (simulated media)',flush=True)

        # A late server transcription cannot submit into a muted/new capture.
        page=browser.new_page()
        page.add_init_script("""
Object.defineProperty(window,'SpeechRecognition',{value:undefined});Object.defineProperty(window,'webkitSpeechRecognition',{value:undefined});
window.tracksStopped=0;window.recorders=[];
Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[{stop(){window.tracksStopped++}}]})}});
window.MediaRecorder=class{constructor(){window.recorders.push(this);this.state='inactive'}start(){this.state='recording';this.ondataavailable?.({data:new Blob(['synthetic'])})}stop(){this.state='inactive';this.onstop?.()}};
window.AudioContext=class{async decodeAudioData(){return{duration:.1,numberOfChannels:1,length:1600,sampleRate:16000,getChannelData:()=>new Float32Array(1600)}}async close(){}};
""")
        def stt_capabilities(route):
            data=route.fetch().json();data.update(stt_configured=True,tts_configured=False);route.fulfill(json=data)
        page.route('**/api/voice/capabilities',stt_capabilities)
        stt_held=[];unexpected=[]
        page.route('**/api/voice/stt',lambda route:stt_held.append(route))
        page.route('**/api/voice/*/turn',lambda route:(unexpected.append(1),route.fulfill(json={'message':'Unexpected','assessment_complete':False})))
        page.goto(root+'/voice');page.locator('#voice-start').click()
        page.wait_for_function("!document.querySelector('#voice-listen').disabled")
        page.locator('#voice-listen').click();page.locator('#voice-listen').click()
        page.wait_for_function("document.querySelector('#voice-state').textContent==='Transcribing'")
        assert stt_held
        page.locator('#voice-mute').click()
        try:stt_held[0].fulfill(json={'text':'Stale transcription must not submit'})
        except Exception:pass
        assert not page.locator('#voice-send').is_disabled()
        assert page.locator('#voice-text').input_value()=='' and not unexpected
        page.unroute_all(behavior='ignoreErrors');page.close()
        print('Cancelled STT cannot submit a stale turn; typed fallback remains enabled (simulated recording)',flush=True)

        # Revocation while an auto-completion End request is still in flight.
        page=browser.new_page(viewport={'width':390,'height':900})
        page.add_init_script("Object.defineProperty(window,'speechSynthesis',{value:undefined})")
        page.route('**/api/voice/*/turn',lambda route:route.fulfill(json={
            'message':'Saved conclusion','spoken_text':'Conclusion','assessment_complete':True,
            'summary':{'schemes':[],'caveat':'Provisional guidance only.'}}))
        ends=[];cancelled=[]
        page.route('**/api/voice/*/end',lambda route:ends.append(route))
        page.route('**/api/voice/*/summary/cancel',lambda route:(cancelled.append(1),route.fulfill(json={'status':'cancelled'})))
        page.goto(root+'/voice');page.locator('#voice-start').click()
        page.wait_for_function("!document.querySelector('#voice-send').disabled")
        page.locator('#summary-consent').check()
        page.locator('#voice-text').fill('Synthetic completion');page.locator('#voice-send').click()
        page.wait_for_function("document.querySelector('#voice-state').textContent==='Completed'")
        page.locator('#summary-consent').uncheck()
        page.route('**/api/voice/*',lambda route:route.fulfill(json={'whatsapp':{'status':'cancelled'}}) if route.request.method=='GET' else route.fallback())
        assert ends
        ends[0].fulfill(json={'whatsapp':{'status':'pending'}})
        page.wait_for_function("document.querySelector('#summary-status').textContent.includes('Summary cancelled')")
        assert len(cancelled)==1
        page.unroute_all(behavior='ignoreErrors');page.close()
        print('In-flight summary consent revocation cancels queued send; Completed state displayed (simulated queue)',flush=True)
        browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True)
    check(parser.parse_args().url)
