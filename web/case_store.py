"""Transactional case repository. PostgreSQL in production, SQLite for offline tests.

Public scheme data stays in the existing independent SQLite catalog. No citizen
case ever goes into that catalog. Connections are short-lived and bounded by the
single-worker deployment's request/concurrency limits.
"""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import HTTPException
from .privacy import minimize, redact


def enabled():
    return bool(os.getenv('DATABASE_URL'))


def auth_required():
    return os.getenv('SESSION_AUTH_REQUIRED', 'true' if enabled() else 'false').lower() == 'true'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def chat_digest(value):
    secret = os.getenv('SESSION_SIGNING_KEY', '')
    if len(secret) < 32:
        raise RuntimeError('SESSION_SIGNING_KEY must have at least 32 characters for persistent WAHA')
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


class Transaction:
    def __init__(self, connection, postgres):
        self.connection, self.postgres = connection, postgres

    def execute(self, sql, params=()):
        return self.connection.execute(sql.replace('?', '%s') if self.postgres else sql, params)

    def lock(self, key):
        if self.postgres and key:
            identifier = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
            self.execute('SELECT pg_advisory_xact_lock(?)', (identifier,))

    def session(self, sid, token=None, trusted=False):
        row = self.execute('SELECT * FROM case_sessions WHERE id=?', (sid,)).fetchone()
        if not row or row['expires_at'] <= time.time():
            raise HTTPException(404, 'Session expired or not found')
        if auth_required() and not trusted:
            authorized = token and hmac.compare_digest(row['owner_hash'], digest(token))
            if token and not authorized:
                authorized = self.execute('SELECT owner_hash FROM case_grants WHERE session_id=? AND owner_hash=? AND expires_at>?',
                                          (sid, digest(token), time.time())).fetchone()
            if not authorized:
                raise HTTPException(403, 'Session access denied')
        return {**dict(row), 'state': json.loads(row['state'])}

    def save(self, sid, state, token=None, kind='conversation'):
        now = time.time()
        ttl = max(1, min(int(os.getenv('CASE_RETENTION_DAYS', '30')), 365)) * 86400
        if not self.execute('SELECT id FROM case_sessions WHERE id=?', (sid,)).fetchone():
            count = self.execute('SELECT COUNT(*) AS n FROM case_sessions').fetchone()['n']
            if count >= int(os.getenv('MAX_ACTIVE_CASES', '2000')):
                raise HTTPException(503, 'Case capacity reached; retry after retention cleanup')
        self.execute('''INSERT INTO case_sessions(id,owner_hash,kind,state,status,created_at,updated_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            state=excluded.state,updated_at=excluded.updated_at,
            expires_at=case_sessions.created_at+(excluded.expires_at-excluded.created_at)''',
            (sid, digest(token or ''), kind, json.dumps(minimize(state), ensure_ascii=False),
             'open', now, now, now + ttl))

    def message(self, sid, role, text):
        self.execute('INSERT INTO case_messages VALUES(?,?,?,?,?)',
                     (uuid.uuid4().hex, sid, role, redact(text), time.time()))
        limit = max(2, min(int(os.getenv('CASE_HISTORY_LIMIT', '40')), 200))
        self.execute('''DELETE FROM case_messages WHERE session_id=? AND id NOT IN
            (SELECT id FROM case_messages WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT ?)''',
            (sid, sid, limit))

    def history(self, sid):
        return [dict(row) for row in self.execute(
            'SELECT role,content,created_at FROM case_messages WHERE session_id=? ORDER BY created_at,id', (sid,))]


@contextmanager
def transaction(key=''):
    url = os.environ['DATABASE_URL']
    postgres = url.startswith(('postgresql://', 'postgres://'))
    if postgres:
        import psycopg
        from psycopg.rows import dict_row
        connection = psycopg.connect(url, row_factory=dict_row, connect_timeout=5,
                                     options='-c statement_timeout=60000 -c idle_in_transaction_session_timeout=90000')
    elif url.startswith('sqlite:///'):
        connection = sqlite3.connect(url[len('sqlite:///'):], timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
    else:
        raise RuntimeError('DATABASE_URL must be postgresql:// (or sqlite:/// for offline validation)')
    tx = Transaction(connection, postgres)
    try:
        tx.lock(key)
        yield tx
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate():
    with transaction('schema-migrations') as tx:
        tx.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at DOUBLE PRECISION NOT NULL)')
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            if tx.execute('SELECT version FROM schema_migrations WHERE version=?', (path.name,)).fetchone():
                continue
            for sql in path.read_text(encoding='utf-8').split(';'):
                if sql.strip():
                    tx.execute(sql)
            tx.execute('INSERT INTO schema_migrations VALUES(?,?)', (path.name, time.time()))


def purge():
    with transaction('retention') as tx:
        now = time.time()
        tx.execute('DELETE FROM case_sessions WHERE expires_at<?', (now,))
        tx.execute('DELETE FROM voice_links WHERE expires_at<?', (now,))
        tx.execute('DELETE FROM case_grants WHERE expires_at<?', (now,))
        tx.execute('DELETE FROM waha_events WHERE updated_at<?', (now - 7 * 86400,))
        tx.execute('DELETE FROM waha_chats WHERE updated_at<?',
                   (now - max(1, min(int(os.getenv('CASE_RETENTION_DAYS', '30')), 365)) * 86400,))


def ready():
    with transaction() as tx:
        present = {row['version'] for row in tx.execute('SELECT version FROM schema_migrations')}
        return {path.name for path in (Path(__file__).parent/'migrations').glob('*.sql')} <= present
