"""Drive /voice in real Chrome: fake microphone fed with a WAV file, real local STT, no paid APIs.

    1. STT service:  STT_MODEL_DIR=<models> LOCAL_STT_TOKEN=<32 x "b"> STT_PORT=8011 STT_HOST=127.0.0.1 python -m stt_service
    2. API:          python scripts/voice_smoke_server.py          (SQLite cases, fake reader model, port 8017)
    3. This script:  python scripts/voice_browser_smoke.py <mic.wav>   (needs playwright and Google Chrome)
mic.wav: a 16 kHz speech clip padded with ~4 s of silence (e.g. tests/fixtures/audio/hi_3.wav plus silence).
Checks: mic denied -> typed fallback, the states of a spoken turn, language change, offline retry,
refresh recovery and phone-width layout. Prints a JSON report; not part of pytest (needs a browser).
"""
import json, sys, time, httpx
from playwright.sync_api import sync_playwright
BASE = 'http://localhost:8017'
MIC = sys.argv[1]
for _ in range(120):
    try:
        if httpx.get('http://127.0.0.1:8011/ready', timeout=2).status_code == 200 and httpx.get(BASE + '/api/voice/capabilities', timeout=2).status_code == 200:
            break
    except Exception:
        pass
    time.sleep(1)
else:
    sys.exit('servers not ready')
print('capabilities', httpx.get(BASE + '/api/voice/capabilities').json() | {'languages': '...'})
TRACK = """window.__states=[];new MutationObserver(()=>{const s=document.body.dataset.state+':'+document.getElementById('voice-state').textContent;if(window.__states.at(-1)!==s)window.__states.push(s)}).observe(document.body,{attributes:true,subtree:true,childList:true,characterData:true});"""
results = {}
def turns(page):
    return page.eval_on_selector_all('#voice-transcript .turn', 'els=>els.map(e=>e.className.split(" ")[1]+": "+e.textContent.slice(0,90))')
with sync_playwright() as p:
    # 1. microphone denied
    b = p.chromium.launch(channel='chrome', args=['--deny-permission-prompts'])
    page = b.new_page(); errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(BASE + '/voice'); page.wait_for_function("document.querySelector('#voice-language optgroup')")
    page.click('#voice-start'); page.wait_for_function("document.getElementById('voice-state').textContent==='Ready'")
    page.click('#voice-listen'); page.wait_for_function("/denied|blocked/i.test(document.getElementById('voice-help').textContent)", timeout=15000)
    results['mic_denied'] = {'state': page.text_content('#voice-state'), 'help': page.text_content('#voice-help'), 'page_errors': errors}
    page.fill('#voice-text', 'मेरी उम्र 62 साल है'); page.click('#voice-send')
    page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length>=2", timeout=30000)
    results['typed_after_denied'] = turns(page)
    b.close()
    # 2. real voice turn through the fake microphone
    b = p.chromium.launch(channel='chrome', args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
                                                  f'--use-file-for-fake-audio-capture={MIC}%noloop', '--autoplay-policy=no-user-gesture-required'])
    ctx = b.new_context(); page = ctx.new_page(); errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(BASE + '/voice'); page.wait_for_function("document.querySelector('#voice-language optgroup')")
    page.evaluate(TRACK)
    page.click('#voice-start'); page.wait_for_function("document.getElementById('voice-state').textContent==='Ready'")
    t0 = time.time(); page.click('#voice-listen')
    page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length>=2 || /Check what I heard|unavailable|Not heard/.test(document.getElementById('voice-state').textContent)", timeout=90000)
    confirm = page.text_content('#voice-state') == 'Check what I heard'
    heard = page.input_value('#voice-text') if confirm else None
    if confirm:
        page.click('#voice-send'); page.wait_for_function("document.querySelectorAll('#voice-transcript .turn').length>=2", timeout=60000)
    results['voice_turn'] = {'seconds': round(time.time() - t0, 1), 'needs_confirmation': confirm, 'heard': heard,
                             'states': page.evaluate('window.__states'), 'turns': turns(page), 'page_errors': errors}
    # 3. language change mid-session
    page.select_option('#voice-language', 'mr-IN')
    page.wait_for_function("[...document.querySelectorAll('#voice-transcript .turn.assistant')].some(e=>e.textContent.startsWith('ठीक आहे'))", timeout=30000)
    results['language_change'] = turns(page)[-1]
    # 4. offline: the typed turn waits, then is retried with the same request id
    ctx.set_offline(True)
    page.fill('#voice-text', 'मी शेतकरी आहे'); page.click('#voice-send')
    page.wait_for_function("document.body.dataset.state==='reconnecting'", timeout=10000)
    offline_state = page.text_content('#voice-state'); time.sleep(1); ctx.set_offline(False)
    page.wait_for_function("[...document.querySelectorAll('#voice-transcript .turn.user')].some(e=>e.textContent==='मी शेतकरी आहे')", timeout=30000)
    results['offline_retry'] = {'state_while_offline': offline_state, 'after': turns(page)[-2:]}
    n = len(turns(page))
    # 5. refresh recovery
    page.reload(); page.wait_for_function("document.querySelector('#voice-language optgroup')")
    visible = page.is_visible('#voice-continue'); page.click('#voice-continue')
    page.wait_for_function(f"document.querySelectorAll('#voice-transcript .turn').length>={n}", timeout=20000)
    results['refresh'] = {'continue_visible': visible, 'turns_restored': len(turns(page)), 'expected': n,
                          'language': page.input_value('#voice-language'), 'page_errors': errors}
    # 6. mobile viewport renders controls
    m = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True).new_page()
    m.goto(BASE + '/voice'); m.wait_for_function("document.querySelector('#voice-language optgroup')")
    results['mobile'] = {'scroll_width': m.evaluate('document.documentElement.scrollWidth'), 'listen_visible': m.is_visible('#voice-listen')}
    b.close()
print(json.dumps(results, ensure_ascii=False, indent=1))
