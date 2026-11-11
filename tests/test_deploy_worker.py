"""Deployment packaging, worker lifecycle, outbound safety and the Postgres catalog cache.
Offline: no Docker, no Postgres, no network."""
import ast
import importlib
import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------- Dockerfile packaging ----------

def _local_packages_imported_by(start: list[str]) -> set[str]:
    """Top-level project packages reachable by imports from the given packages."""
    local = {p.name for p in ROOT.iterdir() if (p / '__init__.py').exists()}
    seen, todo = set(), list(start)
    while todo:
        pkg = todo.pop()
        if pkg in seen:
            continue
        seen.add(pkg)
        for path in (ROOT / pkg).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                        [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else []
                for name in names:
                    top = name.split('.')[0]
                    if top in local and top not in seen:
                        todo.append(top)
    return seen


def test_dockerfile_copies_every_package_the_api_and_worker_import():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    copied = set(re.findall(r'^COPY --chown=\S+ (\w+) \1$', dockerfile, re.M))
    needed = _local_packages_imported_by(['web', 'worker'])
    assert {'agents', 'worker', 'store', 'saathi'} <= needed
    assert needed <= copied, f'missing from Dockerfile: {sorted(needed - copied)}'
    assert 'schemes' in copied                         # fields/questions YAML read at runtime
    assert 'smoke_imports.py' in dockerfile and 'healthcheck.py' in dockerfile
    assert 'USER 10001:10001' in dockerfile


def test_smoke_import_list_imports():
    from deploy_helpers import smoke_modules
    for name in smoke_modules():
        importlib.import_module(name)


# ---------- container health check ----------

def test_healthcheck_worker_heartbeat(tmp_path, monkeypatch):
    from deploy_helpers import healthcheck
    beat = tmp_path / 'hb'
    monkeypatch.setenv('YOJANA_PROCESS', 'worker')
    monkeypatch.setenv('WORKER_HEARTBEAT', str(beat))
    assert healthcheck.main() == 1                     # no heartbeat yet
    beat.write_text(str(time.time()))
    assert healthcheck.main() == 0
    beat.write_text(str(time.time() - 3600))
    assert healthcheck.main() == 1                     # stuck for an hour


# ---------- worker lifecycle ----------

def test_worker_stops_on_signal_and_writes_heartbeat(tmp_path, monkeypatch):
    from worker import loop
    monkeypatch.setenv('WORKER_HEARTBEAT', str(tmp_path / 'hb'))

    def one(kinds=None):
        loop.STOP.set()                                # SIGTERM arrives while idle
        return None
    monkeypatch.setattr(loop, 'run_one', one)
    try:
        assert loop.run(['notify'], once=False, idle=30, out=lambda *_: None) == 0
    finally:
        loop.STOP.clear()
    assert float((tmp_path / 'hb').read_text()) > time.time() - 60


def test_job_with_expired_lease_is_recovered_after_restart():
    from store import runtime
    jid = runtime.job_enqueue('notify', {'segment': 's'}, dedupe_key='notify:restart')
    first = runtime.job_claim(['notify'], lease=-1)    # worker "dies": lease already expired
    assert first['id'] == jid
    again = runtime.job_claim(['notify'], lease=300)   # restarted worker takes it again
    assert again['id'] == jid and again['attempts'] == 2
    assert runtime.job_enqueue('notify', {'segment': 's'}, dedupe_key='notify:restart') is None
    runtime.job_done(jid, {'ok': True})
    assert runtime.job_get(jid)['status'] == 'done'


def test_worker_refuses_container_local_sqlite_in_production(monkeypatch):
    from worker.__main__ import production_problem
    monkeypatch.setenv('DEPLOYMENT_MODE', 'production')
    monkeypatch.setenv('SCHEME_DATABASE_URL', '')
    assert 'SCHEME_DATABASE_URL' in production_problem()
    monkeypatch.setenv('SCHEME_DATABASE_URL', 'postgresql://u@db.invalid/yojana')
    assert production_problem() is None


def test_worker_never_messages_people_unless_explicitly_enabled(monkeypatch):
    from saathi import senders
    import web.waha_bridge as waha
    sent = []
    monkeypatch.setattr(waha, '_send', lambda *a: sent.append(a))
    for key, value in {'YOJANA_WAHA_ENABLED': 'true', 'WAHA_BASE_URL': 'http://waha:3000',
                       'WAHA_API_KEY': 'k', 'WAHA_HMAC_KEY': 'x' * 40}.items():
        monkeypatch.setenv(key, value)
    state = {'reply_to': {'channel': 'waha', 'chat_id': '900000000000@c.us'}}
    monkeypatch.delenv('WORKER_OUTBOUND_ENABLED', raising=False)
    assert senders.deliver(state, 'new schemes') is False and sent == []
    monkeypatch.setenv('WORKER_OUTBOUND_ENABLED', 'true')
    assert senders.deliver(state, 'new schemes') is True and len(sent) == 1


# ---------- Postgres catalog cache (backend faked) ----------

def test_postgres_catalog_is_cached_and_reloaded_after_writes(monkeypatch):
    from discovery import catalog
    from store import db
    loads = []
    version = [1]
    monkeypatch.setattr(db, 'backend_name', lambda: 'postgres')
    monkeypatch.setattr(db, 'version', lambda: version[0])
    monkeypatch.setattr(db, 'load_catalog', lambda *a, **k: loads.append(1) or {'a': {'id': 'a'}})
    monkeypatch.setenv('SCHEME_DATABASE_URL', 'postgresql://u@db.invalid/yojana')
    catalog._cache.clear()
    assert catalog.cached_catalog() == {'a': {'id': 'a'}}
    catalog.cached_catalog()
    assert len(loads) == 1
    version[0] = 2                                     # this process saved a scheme
    catalog.cached_catalog()
    assert len(loads) == 2
    monkeypatch.setenv('SCHEME_CATALOG_TTL_SECONDS', '0')
    catalog.cached_catalog()
    assert len(loads) == 3
    catalog._cache.clear()


def test_sqlite_catalog_is_never_cached(monkeypatch):
    from discovery import catalog
    from store import db
    loads = []
    monkeypatch.setattr(db, 'load_catalog', lambda *a, **k: loads.append(1) or {})
    catalog.cached_catalog(); catalog.cached_catalog()
    assert len(loads) == 2


def test_production_rejects_non_postgres_scheme_database(monkeypatch):
    from web import runtime
    monkeypatch.setenv('DEPLOYMENT_MODE', 'production')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u@db.invalid/cases')
    monkeypatch.setenv('SCHEME_DATABASE_URL', 'sqlite:///tmp/x.db')
    with pytest.raises(RuntimeError, match='SCHEME_DATABASE_URL'):
        runtime.validate_configuration()


# ---------- destructive migration guard ----------

def test_agents_migration_refuses_to_drop_non_empty_tables():
    sql = (ROOT / 'store' / 'sql' / '002_agents.sql').read_text(encoding='utf-8')
    guard, first_drop = sql.index('RAISE EXCEPTION'), sql.index('DROP TABLE')
    assert guard < first_drop
    for table in ('job', 'conversation', 'segment_search'):
        assert f"'{table}'" in sql[:first_drop]
