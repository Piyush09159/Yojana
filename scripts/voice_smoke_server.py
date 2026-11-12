"""Local API for scripts/voice_browser_smoke.py (start the STT service first; see docs/BROWSER_VOICE.md): SQLite case store, real local STT service, a fake
reader model that echoes a fixed extraction (no network, no paid APIs)."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
db = os.path.join(tempfile.mkdtemp(), 'cases.db')
os.environ.update({'DATABASE_URL': 'sqlite:///' + Path(db).as_posix(), 'SESSION_AUTH_REQUIRED': 'true',
                   'SESSION_SIGNING_KEY': 'browser-smoke-signing-key-0123456789abcdef',
                   'STT_PROVIDER': 'local', 'LOCAL_STT_URL': 'http://127.0.0.1:8011', 'LOCAL_STT_TOKEN': 'b' * 32,
                   'LOCAL_STT_TIMEOUT_SECONDS': '60', 'TTS_PROVIDER': 'browser', 'SCHEME_DATABASE_URL': '',
                   'LLM_PROVIDER': 'mock', 'VERIFY_RULES': '0'})
os.environ.pop('SARVAM_API_KEY', None)
from llm.providers.mock_provider import MockProvider
from web import conversation_api as api, case_store
class Reader(MockProvider):
    def _call(self, messages, system, json_mode, temperature, max_tokens):
        self.queue = [{'language': 'hi', 'facts': {'age': 62, 'state': 'maharashtra'}, 'intent': 'give_info'}]
        return super()._call(messages, system, json_mode, temperature, max_tokens)
api.get_llm = lambda _: Reader()
case_store.migrate()
import uvicorn
from web.app import create_app
uvicorn.run(create_app(), host='127.0.0.1', port=8017, log_level='warning')
