"""Best-effort Redis/Valkey cache. PUBLIC scheme data only; no citizen records."""
import hashlib
import json
import os
import threading

_lock = threading.Lock()
_connection = None
_connection_url = None
MAX_BYTES = 256000


def public_key(kind, **fields):
    if kind not in {'catalog', 'scheme', 'region'}:
        raise ValueError('Not a public cache namespace')
    payload = json.dumps({'kind': kind, 'version': os.getenv('SCHEME_CACHE_VERSION', 'v1'), **fields}, sort_keys=True, separators=(',', ':'))
    return 'yojana:public:' + kind + ':' + hashlib.sha256(payload.encode()).hexdigest()


def _client():
    global _connection, _connection_url
    url = os.getenv('REDIS_URL', '').strip()
    if not url:
        return None
    with _lock:
        if _connection is None or _connection_url != url:
            import redis
            _connection = redis.Redis.from_url(url, decode_responses=False,
                socket_connect_timeout=0.15, socket_timeout=0.2,
                retry_on_timeout=False, health_check_interval=30, max_connections=8)
            _connection_url = url
    return _connection


def get_public(key):
    if not os.getenv('REDIS_URL'):
        return None
    try:
        data = _client().get(key)
        if data is None or len(data) > MAX_BYTES:
            return None
        return json.loads(data)
    except Exception:
        return None


def put_public(key, value, ttl=None):
    if not os.getenv('REDIS_URL'):
        return False
    try:
        data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        if len(data) > MAX_BYTES:
            return False
        duration = int(ttl if ttl is not None else os.getenv('SCHEME_CACHE_TTL_SECONDS', '300'))
        _client().setex(key, max(30, min(duration, 3600)), data)
        return True
    except Exception:
        return False


def invalidate_public(key):
    if os.getenv('REDIS_URL'):
        try:
            _client().delete(key)
        except Exception:
            pass
