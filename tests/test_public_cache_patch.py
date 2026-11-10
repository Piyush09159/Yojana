"""Tests do not need a running Valkey instance."""
import pytest
from web import public_cache as cache


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}
    def get(self, key):
        return self.store.get(key)
    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl
    def delete(self, key):
        self.store.pop(key, None)


def test_disabled(monkeypatch):
    monkeypatch.delenv('REDIS_URL', raising=False)
    key = cache.public_key('catalog')
    assert cache.get_public(key) is None
    assert cache.put_public(key, {'ok': True}) is False


def test_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setenv('REDIS_URL', 'redis://cache.example:6379/0')
    monkeypatch.setattr(cache, '_client', lambda: fake)
    key = cache.public_key('scheme', scheme_id='test')
    assert cache.put_public(key, {'name': 'योजना'}, ttl=60)
    assert cache.get_public(key) == {'name': 'योजना'}
    assert fake.ttls[key] == 60
    cache.invalidate_public(key)
    assert cache.get_public(key) is None


def test_version_invalidation(monkeypatch):
    monkeypatch.setenv('SCHEME_CACHE_VERSION', 'v1')
    old = cache.public_key('region', state='MH', district='Beed')
    monkeypatch.setenv('SCHEME_CACHE_VERSION', 'v2')
    assert old != cache.public_key('region', state='MH', district='Beed')


def test_fail_open(monkeypatch):
    class Offline:
        def get(self, key): raise ConnectionError('offline')
        def setex(self, key, ttl, value): raise ConnectionError('offline')
    monkeypatch.setenv('REDIS_URL', 'redis://cache.example:6379/0')
    monkeypatch.setattr(cache, '_client', lambda: Offline())
    key = cache.public_key('catalog')
    assert cache.get_public(key) is None
    assert cache.put_public(key, {'ok': True}) is False


def test_large_value_is_skipped(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://cache.example:6379/0')
    monkeypatch.setattr(cache, '_client', lambda: FakeRedis())
    assert cache.put_public(cache.public_key('catalog'), 'a' * 300000) is False


def test_private_namespace_is_rejected():
    with pytest.raises(ValueError):
        cache.public_key('citizen', phone='private')
