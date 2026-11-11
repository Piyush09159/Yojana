"""Privacy-safe structured diagnostics for the WhatsApp/voice path.

One JSON line per event on the `yojana.diagnostics` logger, e.g.

    {"code": "SARVAM_STT_FAILED", "event": "3f9a1c2e", "status": 429, "detail": "speech_rate_limited"}

Only allow-listed keys are emitted, and every value is reduced to a short scalar. Never pass
message text, transcripts, phone numbers/chat ids, media URLs, audio or credentials here: the
`event` key must already be a keyed digest (case_store.chat_digest), and is shortened to 8 chars.
"""
from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger('yojana.diagnostics')
if not log.handlers:
    # uvicorn configures only its own loggers; without a handler INFO lines would be dropped.
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter('%(levelname)s yojana %(message)s'))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

SAFE_KEYS = {'event', 'stage', 'status', 'detail', 'mimetype', 'bytes', 'seconds', 'ms', 'language',
             'source', 'result', 'enabled', 'configured', 'missing', 'engine_has_body'}
ERRORS = {'WAHA_MEDIA_UNAVAILABLE', 'WAHA_MEDIA_UNSUPPORTED', 'WAHA_MEDIA_UNTRUSTED',
          'WAHA_MEDIA_DOWNLOAD_FAILED', 'WAHA_AUDIO_DECODE_FAILED', 'VOICE_TOO_LONG',
          'SARVAM_STT_DISABLED', 'SARVAM_STT_FAILED', 'VOICE_BUSY', 'VOICE_CONVERSATION_FAILED',
          'WAHA_REPLY_DELIVERY_FAILED', 'VOICE_CONFIG_INCOMPLETE', 'VOICE_SENSITIVE_NUMBER', 'VOICE_PROCESSING_FAILED'}


def _safe(value):
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return str(value)[:60]


def emit(code: str, **fields) -> dict:
    record = {'code': code}
    for key, value in fields.items():
        if key not in SAFE_KEYS:
            continue
        if key == 'event' and value:
            value = str(value)[:8]
        record[key] = _safe(value)
    try:
        log.log(logging.WARNING if code in ERRORS else logging.INFO,
                json.dumps(record, ensure_ascii=True, sort_keys=True))
    except Exception:
        pass                                    # diagnostics must never break a reply
    return record
