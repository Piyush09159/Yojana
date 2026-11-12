"""V2.5 browser checks against an explicitly mocked development backend.

Backend: DATABASE_URL configured, SESSION_AUTH_REQUIRED=true, LLM_PROVIDER=mock,
MOCK_LLM_FILE=tests/fixtures/deployment_mock.yaml. No hosted speech request is made.
"""
import argparse
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


def check(url):
    root=url.rstrip('/')
    out=Path('artifacts/v25/browser');out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        executable=os.getenv('BROWSER_EXECUTABLE')
        browser=playwright.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
        for width in [1440,1024,768,390,320]:
            page=browser.new_page(viewport={'width':width,'height':1000})
            page.goto(root+'/')
            assert page.get_by_role('heading',level=1).inner_text()=='Find government schemes\nyou may qualify for.'
            for title in ['Find Schemes','Talk to Saathi','Check Eligibility','Fix a Blocked Benefit']:
                assert page.get_by_role('link',name=title,exact=True).count()>=1
            for lang,word in [('hi','सरकारी'),('mr','सरकारी'),('en','government schemes')]:
                page.locator('#site-language').select_option(lang)
                assert word in page.locator('#hero-title').inner_text()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(out/f'landing-{width}.png'),full_page=True)
            page.goto(root+'/app#schemes')
            assert page.locator('#page-schemes').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.goto(root+'/app#rescue')
            page.wait_for_function("document.querySelector('#scheme-id').options[0].value!==''")
            page.locator('#scheme-id').select_option('pm_kisan')
            page.locator('#status-text').fill('eKYC pending')
            page.locator('#rescue-send').click()
            page.wait_for_function("document.querySelector('#rescue-result').textContent.includes('Government status verified: No')")
            page.goto(root+'/voice')
            page.wait_for_function("document.querySelector('#voice-language').options.length===24")
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(out/f'voice-{width}.png'),full_page=True)
            page.close()
            print(f'{width}px: citizen landing/actions/translations, explorer, real Rescue and voice layout passed')
        for lang,text in [('en-IN','I am 62 in Maharashtra'),('hi-IN','मेरी उम्र 62 है'),('mr-IN','माझे वय 62 आहे')]:
            page=browser.new_page(viewport={'width':390,'height':900})
            page.add_init_script("Object.defineProperty(window,'SpeechRecognition',{value:undefined});Object.defineProperty(window,'webkitSpeechRecognition',{value:undefined});Object.defineProperty(window,'speechSynthesis',{value:undefined});")
            page.goto(root+'/voice#handoff=short-invalid-token')
            assert not page.evaluate('location.hash')
            # The token is removed immediately, and navigation alone must not redeem it.
            page.goto(root+'/voice')
            page.wait_for_function("document.querySelector('#voice-language').options.length===24")
            page.locator('#voice-language').select_option(lang)
            page.locator('#voice-start').click()
            page.wait_for_function("!document.querySelector('#voice-send').disabled")
            page.locator('#voice-listen').click()
            assert page.locator('#voice-state').inner_text()=='Type your message'
            page.locator('#voice-text').fill(text)
            page.locator('#voice-send').click()
            page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length===2")
            page.locator('#voice-mute').click()
            assert page.locator('#voice-mute').get_attribute('aria-pressed')=='true'
            page.reload()
            page.locator('#voice-continue').click()  # Fresh start deliberately creates another citizen case.
            page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length===2")
            page.locator('#voice-end').click()
            page.wait_for_function("document.querySelector('#voice-state').textContent==='Completed'")
            assert 'No WhatsApp summary requested' in page.locator('#summary-status').inner_text()
            assert page.request.get(root+'/api/inference/status').json()['state']=='mocked'
            page.screenshot(path=str(out/f'summary-{lang}.png'),full_page=True)
            page.close()
            print(f'{lang}: typed voice fallback, manual language, transcript recovery, mute and summary passed (mocked extraction)')
        # Recognition can be unavailable/denied; simulate its events without opening a real microphone.
        page=browser.new_page(viewport={'width':390,'height':900})
        page.add_init_script("""window.SpeechRecognition=class {start(){this.onstart?.();setTimeout(()=>this.onerror?.({error:'not-allowed'}),10)} stop(){} abort(){}};""")
        page.goto(root+'/voice');page.locator('#voice-start').click();page.wait_for_function("!document.querySelector('#voice-listen').disabled")
        page.locator('#voice-listen').click();page.wait_for_function("document.querySelector('#voice-state').textContent==='Recognition unavailable'")
        assert page.locator('#voice-text').is_enabled()
        print('Denied microphone: typed fallback stays usable (simulated permission event)')
        page.close()
        page=browser.new_page(viewport={'width':390,'height':900})
        page.add_init_script("""Object.defineProperty(window,'speechSynthesis',{value:undefined});
window.SpeechRecognition=class {start(){this.onstart?.();setTimeout(()=>{const r=[{transcript:'I am 62 in Maharashtra'}];r.isFinal=true;this.onresult?.({results:[r]})},100)} stop(){} abort(){}};""")
        page.goto(root+'/voice');page.locator('#voice-start').click();page.wait_for_function("!document.querySelector('#voice-listen').disabled")
        page.locator('#voice-listen').click()
        page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length===2")
        assert 'I am 62 in Maharashtra' in page.locator('#voice-transcript').inner_text()
        page.locator('#voice-end').click();page.wait_for_function("document.querySelector('#voice-state').textContent==='Completed'")
        print('Recognition success event: transcript reaches the authorized real engine endpoint (simulated recognition, mocked model)')
        page.close();browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);check(parser.parse_args().url)
