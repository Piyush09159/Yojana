"""Runtime state shared by the agents, the orchestrator and the worker. Same functions on SQLite
and PostgreSQL (store/db.py picks the backend). Times are Unix seconds.

    job_enqueue / job_claim / job_done / job_fail     hand-offs between agents (the job graph)
    conversation_load / conversation_save              one conversation's state, no message text
    cache_get / cache_put                              an agent's output for an input seen before
    budget_add / budget_used                           daily spend per lane and provider
    segment_searched_within / segment_mark             "did we search the web for people like this?"

A job is taken with UPDATE ... RETURNING on the oldest ready row (FOR UPDATE SKIP LOCKED on
Postgres, so two workers never take the same job) and leased; if its worker dies, the lease runs
out and the job is taken again. A job that keeps failing stops after max_attempts and stays in the
table as "failed" for inspection.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from . import db

DAY = 86_400


def now() -> float:
    return time.time()


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def key_of(*parts) -> str:
    """Stable key for an input: a hash, so cached rows never hold the input text itself."""
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


# ---------- backend plumbing ----------

def _pg() -> bool:
    return db.backend_name() == "postgres"


def _conn():
    if _pg():
        from . import pg
        return pg.connect()
    from . import sqlite
    return sqlite.connect()


def _sql(query: str) -> str:
    return query if _pg() else query.replace("%s", "?")


def _json(obj):
    if _pg():
        from .pg import Jsonb
        return Jsonb(obj)
    return json.dumps(obj, ensure_ascii=False, default=str)


def _unjson(value):
    return json.loads(value) if isinstance(value, str) else value


def _execute(query: str, args) -> list[dict]:
    conn = _conn()
    cur = conn.execute(_sql(query), args)
    rows = [dict(r) for r in cur.fetchall()] if cur.description else []
    if not _pg():
        conn.commit()
    return rows


def _run(query: str, args=()) -> list[dict]:
    """Execute one statement and return its rows. Rows are read before committing: SQLite cannot
    commit while a RETURNING result is still unread. On Postgres, a connection the server dropped
    (Neon closes idle ones) is replaced and the statement run once more, as in store/pg.py. Repeating
    is safe for upserts, ON CONFLICT inserts and job claims (an orphaned claim's lease simply runs
    out); the one exception is a budget increment the server applied just before dropping the
    connection, which then counts twice: rare, and it errs toward spending less."""
    if not _pg():
        return _execute(query, args)
    import psycopg

    from . import pg
    try:
        return _execute(query, args)
    except psycopg.OperationalError:
        pg.close()
        return _execute(query, args)


def _one(query: str, args=()) -> dict | None:
    rows = _run(query, args)
    return rows[0] if rows else None


def _all(query: str, args=()) -> list[dict]:
    return _run(query, args)


def _job(row: dict | None) -> dict | None:
    if row is None:
        return None
    row = dict(row)
    row["payload"] = _unjson(row.get("payload")) or {}
    row["result"] = _unjson(row.get("result"))
    return row


# ---------- agent cache ----------

def cache_get(agent: str, key: str, max_age: float | None = None):
    row = _one("SELECT output, created_at FROM agent_cache WHERE agent = %s AND key = %s", (agent, key))
    if row is None or (max_age is not None and now() - row["created_at"] > max_age):
        return None
    return _unjson(row["output"])


def cache_put(agent: str, key: str, output) -> None:
    _run("INSERT INTO agent_cache (agent, key, output, created_at) VALUES (%s, %s, %s, %s) "
         "ON CONFLICT (agent, key) DO UPDATE SET output = EXCLUDED.output, created_at = EXCLUDED.created_at",
         (agent, key, _json(output), now()))


# ---------- budget ----------

def budget_add(lane: str, provider: str, calls: int = 0, tokens: int = 0, searches: int = 0) -> None:
    _run("INSERT INTO agent_budget (day, lane, provider, calls, tokens, searches) VALUES (%s, %s, %s, %s, %s, %s) "
         "ON CONFLICT (day, lane, provider) DO UPDATE SET calls = agent_budget.calls + EXCLUDED.calls, "
         "tokens = agent_budget.tokens + EXCLUDED.tokens, searches = agent_budget.searches + EXCLUDED.searches",
         (today(), lane, provider, calls, tokens, searches))


def budget_used(lane: str | None = None, include_local: bool = False) -> dict:
    """Today's totals, for one lane or all. Local models cost nothing, so by default they don't count."""
    where, args = ["day = %s"], [today()]
    if lane:
        where.append("lane = %s")
        args.append(lane)
    if not include_local:
        where.append("provider NOT LIKE %s")
        args.append("local:%")
    row = _one(f"SELECT COALESCE(SUM(calls), 0) AS calls, COALESCE(SUM(tokens), 0) AS tokens, "
               f"COALESCE(SUM(searches), 0) AS searches FROM agent_budget WHERE {' AND '.join(where)}", args)
    return {k: int(v) for k, v in row.items()}


# ---------- recent web searches per profile group ----------

def segment_searched_within(segment_key: str, seconds: float) -> bool:
    row = _one("SELECT last_searched FROM segment_search WHERE segment_key = %s", (segment_key,))
    return bool(row) and now() - row["last_searched"] < seconds


def segment_mark(segment_key: str, found: int = 0) -> None:
    _run("INSERT INTO segment_search (segment_key, last_searched, found_count) VALUES (%s, %s, %s) "
         "ON CONFLICT (segment_key) DO UPDATE SET last_searched = EXCLUDED.last_searched, "
         "found_count = EXCLUDED.found_count", (segment_key, now(), found))


# ---------- conversations ----------

def conversation_load(chat_ref: str) -> dict | None:
    row = _one("SELECT state, expires_at FROM conversation WHERE chat_ref = %s", (chat_ref,))
    if row is None or row["expires_at"] < now():
        return None
    return _unjson(row["state"])


def conversation_save(chat_ref: str, state: dict, ttl: float = 7 * DAY) -> None:
    t = now()
    _run("INSERT INTO conversation (chat_ref, state, updated_at, expires_at) VALUES (%s, %s, %s, %s) "
         "ON CONFLICT (chat_ref) DO UPDATE SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at, "
         "expires_at = EXCLUDED.expires_at", (chat_ref, _json(state), t, t + ttl))


def conversation_delete(chat_ref: str) -> None:
    _run("DELETE FROM conversation WHERE chat_ref = %s", (chat_ref,))


def conversations_in_segment(segment_key: str, limit: int = 200) -> list[tuple[str, dict]]:
    """Live conversations for one profile group, newest first: who to tell about newly found schemes."""
    expr = "state->>'segment'" if _pg() else "json_extract(state, '$.segment')"
    rows = _all(f"SELECT chat_ref, state FROM conversation WHERE {expr} = %s AND expires_at > %s "
                f"ORDER BY updated_at DESC LIMIT {int(limit)}", (segment_key, now()))
    return [(r["chat_ref"], _unjson(r["state"])) for r in rows]


# ---------- coverage (how well the database serves each profile group) ----------

def coverage_log(segment_key: str, dims: dict, good: int, web_triggered: bool = False, web_new: int = 0) -> None:
    _run("INSERT INTO coverage_event (segment_key, dims, db_good_count, web_triggered, web_new_count) "
         "VALUES (%s, %s, %s, %s, %s)", (segment_key, _json(dims), good, bool(web_triggered), web_new))


# ---------- jobs ----------

def job_enqueue(kind: str, payload: dict, dedupe_key: str | None = None, delay: float = 0,
                max_attempts: int = 3) -> int | None:
    """Queue a job. Returns its id, or None when a job with the same dedupe_key is already
    queued or running (so the same work is never queued twice)."""
    t = now()
    row = _one("INSERT INTO job (kind, dedupe_key, payload, status, attempts, max_attempts, run_after, created_at) "
               "VALUES (%s, %s, %s, 'queued', 0, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
               (kind, dedupe_key, _json(payload), max_attempts, t + delay, t))
    return int(row["id"]) if row else None


def job_claim(kinds: list[str], lease: float = 300) -> dict | None:
    """Take the oldest ready job of these kinds: queued and due, or running with an expired lease
    (its worker died). A reclaimed job that has used all its attempts is marked failed instead."""
    while True:
        t = now()
        marks = ", ".join(["%s"] * len(kinds))
        lock = " FOR UPDATE SKIP LOCKED" if _pg() else ""
        row = _one(f"""UPDATE job SET status = 'running', attempts = attempts + 1, lease_until = %s
                       WHERE id = (SELECT id FROM job WHERE kind IN ({marks})
                                   AND ((status = 'queued' AND run_after <= %s)
                                        OR (status = 'running' AND lease_until < %s))
                                   ORDER BY run_after, id LIMIT 1{lock})
                       RETURNING *""", (t + lease, *kinds, t, t))
        job = _job(row)
        if job is None:
            return None
        if job["attempts"] > job["max_attempts"]:
            _run("UPDATE job SET status = 'failed', error = %s, finished_at = %s WHERE id = %s",
                 ("worker lost the job too many times (lease expired)", now(), job["id"]))
            continue
        return job


def job_done(job_id: int, result: dict | None = None) -> None:
    _run("UPDATE job SET status = 'done', result = %s, lease_until = NULL, finished_at = %s WHERE id = %s",
         (_json(result or {}), now(), job_id))


def job_fail(job_id: int, error: str, retry_in: float = 60) -> str:
    """Record a failure. Retried after retry_in seconds until max_attempts; returns the new status."""
    row = _one("SELECT attempts, max_attempts FROM job WHERE id = %s", (job_id,))
    if row is None:
        return "missing"
    if row["attempts"] >= row["max_attempts"]:
        _run("UPDATE job SET status = 'failed', error = %s, lease_until = NULL, finished_at = %s WHERE id = %s",
             (error[:500], now(), job_id))
        return "failed"
    _run("UPDATE job SET status = 'queued', error = %s, lease_until = NULL, run_after = %s WHERE id = %s",
         (error[:500], now() + retry_in, job_id))
    return "queued"


def job_retry_failed(kind: str | None = None) -> int:
    """Send failed jobs back to the queue with fresh attempts (after fixing what made them fail)."""
    where, args = ["status = 'failed'"], []
    if kind:
        where.append("kind = %s")
        args.append(kind)
    rows = _run(f"UPDATE job SET status = 'queued', attempts = 0, run_after = %s, lease_until = NULL "
                f"WHERE {' AND '.join(where)} RETURNING id", (now(), *args))
    return len(rows)


def job_get(job_id: int) -> dict | None:
    return _job(_one("SELECT * FROM job WHERE id = %s", (job_id,)))


def job_counts() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in _all("SELECT kind, status, COUNT(*) AS n FROM job GROUP BY kind, status"):
        out.setdefault(r["kind"], {})[r["status"]] = int(r["n"])
    return out
