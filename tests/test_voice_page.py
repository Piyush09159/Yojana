"""Static checks on the browser voice page (the page itself is driven in real Chrome by
scripts/voice_browser_smoke.py, which is not part of pytest)."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parent.parent / 'web' / 'static'


def test_every_element_the_script_uses_exists():
    js = (STATIC / 'voice.js').read_text(encoding='utf-8')
    html = (STATIC / 'voice.html').read_text(encoding='utf-8')
    used = set(re.findall(r"\$\('([a-z0-9-]+)'\)", js))
    defined = set(re.findall(r'id="([a-z0-9-]+)"', html))
    assert used and used <= defined, used - defined


def test_states_are_the_documented_set():
    js = (STATIC / 'voice.js').read_text(encoding='utf-8')
    names = set(re.findall(r"state\('([a-z]+)'", js))
    assert names <= {'idle', 'listening', 'transcribing', 'thinking', 'speaking', 'reconnecting', 'ended'}, names


def test_page_keeps_capability_out_of_history_and_referrer():
    js = (STATIC / 'voice.js').read_text(encoding='utf-8')
    html = (STATIC / 'voice.html').read_text(encoding='utf-8')
    assert "history.replaceState(null,'',location.pathname)" in js
    assert '<meta name="referrer" content="no-referrer">' in html
    assert 'localStorage' not in js                       # per-tab sessionStorage only, cleared on 403/404/410
    assert 'summary-consent' in html and 'consent_whatsapp:$(\'summary-consent\').checked' in js


def test_voice_page_is_served():
    from web.app import create_app
    r = TestClient(create_app()).get('/voice')
    assert r.status_code == 200 and 'voice.js' in r.text
