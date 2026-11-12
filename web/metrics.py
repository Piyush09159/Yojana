"""Bounded numeric operational metrics: no session IDs, text, audio or secrets."""
from collections import deque
from threading import Lock
import logging

_values=deque(maxlen=200)
_lock=Lock()
_logger=logging.getLogger('yojana.latency')
if not _logger.handlers:
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter('latency %(message)s'))
    _logger.addHandler(handler)
_logger.setLevel(logging.INFO)
_logger.propagate=False


def record(stage, elapsed, outcome='ok'):
    if stage not in ('stt','inference','tts'):
        return
    value={'stage':stage,'latency_ms':round(max(0,elapsed)*1000,1),
           'outcome':outcome if outcome in ('ok','error','fallback') else 'error'}
    with _lock:
        _values.append(value)
    _logger.info('stage=%s latency_ms=%s outcome=%s',value['stage'],value['latency_ms'],value['outcome'])
