"""PostgreSQL backend of the scheme database. Selected by store/db.py when SCHEME_DATABASE_URL is set.

Same functions, arguments and return values as store/sqlite.py, so the engine, discovery,
ingestion and the web app work on either backend unchanged. The `path` argument the SQLite
functions take is accepted and ignored here: the database is always SCHEME_DATABASE_URL.

Saving a scheme writes the scheme dict (`scheme.data`) and every derived row (rules, the
"who is it for" targets, names, unmapped criteria, documents, sources) in one transaction,
using store/derive.py, so the structured tables never disagree with the dict the engine reads.

Schema: store/sql/*.sql, applied by `python -m store pg-init` (store/pg_admin.py).
"""
from __future__ import annotations

import functools
import json
import os
import threading
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb as _Jsonb
from rapidfuzz import fuzz, process

from core.awareness import norm_name

from .derive import derive

DUPLICATE_SCORE = 92            # same threshold as the SQLite store, so both dedupe alike

CONNECT_TIMEOUT = 10            # seconds; a suspended Neon database wakes in a few seconds

_local = threading.local()
_version = 0                    # bumped on every write so cached catalogs reload


def _dumps(obj) -> str:
    # Same encoding as the SQLite store: dates (from hand-written YAML) become ISO strings.
    return json.dumps(obj, ensure_ascii=False, default=str)


def Jsonb(obj) -> _Jsonb:
    return _Jsonb(obj, dumps=_dumps)


def dsn() -> str:
    url = (os.getenv("SCHEME_DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("SCHEME_DATABASE_URL is not set; the Postgres store needs it")
    return url


def db_path() -> str:
    """The database URL without its password, safe to print."""
    info = psycopg.conninfo.conninfo_to_dict(dsn())
    user = info.get("user", "")
    return (f"postgresql://{user + '@' if user else ''}{info.get('host', 'localhost')}:"
            f"{info.get('port', '5432')}/{info.get('dbname', '')}")


def connect(path=None) -> psycopg.Connection:
    """One autocommit connection per thread (the web app reads from several); writes that touch
    several tables open an explicit transaction."""
    conn = getattr(_local, "conn", None)
    if conn is not None and not conn.closed and not conn.broken and getattr(_local, "dsn", None) == dsn():
        return conn
    if conn is not None and not conn.closed:
        conn.close()
    conn = psycopg.connect(dsn(), autocommit=True, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT)
    _local.conn, _local.dsn = conn, dsn()
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None and not conn.closed:
        conn.close()
    _local.conn = _local.dsn = None


def version() -> int:
    return _version


def _bump() -> None:
    global _version
    _version += 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- reading ----------

def _row_to_scheme(row: dict) -> dict:
    scheme = dict(row["data"])
    scheme["verified"] = bool(row["verified"])
    return scheme


def load_catalog(state: str | None = None, district: str | None = None,
                 include_discovered: bool = True, path=None) -> dict[str, dict]:
    """Schemes the engine should consider: central ones plus the person's state, and within that
    state only their district's schemes. Same rule as the SQLite store; closed schemes excluded."""
    where, args = ["status <> 'closed'"], []
    if not include_discovered:
        where.append("origin = 'curated'")
    if state:
        where.append("(state_key IS NULL OR state_key = %s)")
        args.append(state)
    if district:
        where.append("(district_key IS NULL OR district_key = %s)")
        args.append(district)
    elif state:
        where.append("district_key IS NULL")      # no district given: skip district-only schemes
    sql = f"SELECT id, verified, data FROM scheme WHERE {' AND '.join(where)} ORDER BY id"
    return {r["id"]: _row_to_scheme(r) for r in connect().execute(sql, args)}


def get_scheme(scheme_id: str, path=None) -> dict | None:
    row = connect().execute("SELECT id, verified, data FROM scheme WHERE id = %s", (scheme_id,)).fetchone()
    return _row_to_scheme(row) if row else None


def count(path=None) -> int:
    return connect().execute("SELECT COUNT(*) AS n FROM scheme").fetchone()["n"]


def stats(path=None) -> dict:
    conn = connect()
    one = lambda sql: list(conn.execute(sql).fetchone().values())[0]  # noqa: E731
    return {
        "schemes": count(),
        "rules": one("SELECT COALESCE(SUM(rule_count), 0) FROM scheme"),
        "by_origin": {r["origin"]: r["n"] for r in
                      conn.execute("SELECT origin, COUNT(*) AS n FROM scheme GROUP BY origin")},
        "by_state": {(r["state_key"] or "central"): r["n"] for r in
                     conn.execute("SELECT state_key, COUNT(*) AS n FROM scheme GROUP BY state_key "
                                  "ORDER BY COUNT(*) DESC, state_key")},
        "districts": one("SELECT COUNT(DISTINCT district_key) FROM scheme WHERE district_key IS NOT NULL"),
        "verified": one("SELECT COUNT(*) FROM scheme WHERE verified"),
        "urls": {r["status"]: r["n"] for r in
                 conn.execute("SELECT status, COUNT(*) AS n FROM crawl_url GROUP BY status")},
    }


# ---------- duplicates ----------

def find_duplicate(scheme: dict, path=None) -> str | None:
    """An existing scheme that is the same one, compared only within the same state and district
    (same rule and threshold as the SQLite store)."""
    key = norm_name(scheme["name"])
    if not key:
        return None
    rows = connect().execute(
        "SELECT id, name_norm FROM scheme WHERE state_key IS NOT DISTINCT FROM %s "
        "AND district_key IS NOT DISTINCT FROM %s ORDER BY id",
        (scheme.get("state"), scheme.get("district"))).fetchall()
    if not rows:
        return None
    hit = process.extractOne(key, [r["name_norm"] for r in rows], scorer=fuzz.token_set_ratio,
                             score_cutoff=DUPLICATE_SCORE)
    return rows[hit[2]]["id"] if hit else None


# ---------- writing ----------

def _register_district(conn, state: str | None, district: str | None) -> None:
    """A scraped scheme may name a district the geography table does not list yet. Register it
    (flagged auto_added) so the database grows with the catalog instead of rejecting the scheme."""
    if not district:
        return
    if not state:
        raise ValueError(f"scheme names district {district!r} but no state")
    conn.execute("INSERT INTO geo_district (state_key, key, name_en, auto_added) VALUES (%s, %s, %s, TRUE) "
                 "ON CONFLICT DO NOTHING", (state, district, district.replace("_", " ").title()))


def upsert(scheme: dict, *, first_seen: datetime | None = None, updated_at: datetime | None = None,
           conn=None) -> None:
    """Write one scheme and all its derived rows, replacing what was there. No dedupe: callers
    that want it use save_scheme. The importer uses this directly to copy rows one to one."""
    d = derive(scheme)
    c = d.columns
    conn = conn or connect()
    with conn.transaction():
        _register_district(conn, c["state_key"], c["district_key"])
        conn.execute(
            """INSERT INTO scheme (id, name, name_norm, level, state_key, district_key, agency_type,
                   agency_name, ministry, categories, benefit_kind, annual_value_inr, benefit_personal,
                   origin, verified, awareness, quality_score, rule_count, unmapped_count, source_url,
                   source_site, content_hash, data, first_seen, updated_at, last_checked)
               VALUES (%(id)s, %(name)s, %(name_norm)s, %(level)s, %(state_key)s, %(district_key)s,
                   %(agency_type)s, %(agency_name)s, %(ministry)s, %(categories)s, %(benefit_kind)s,
                   %(annual_value_inr)s, %(benefit_personal)s, %(origin)s, %(verified)s, %(awareness)s,
                   %(quality_score)s, %(rule_count)s, %(unmapped_count)s, %(source_url)s, %(source_site)s,
                   %(content_hash)s, %(data)s, COALESCE(%(first_seen)s, now()), COALESCE(%(updated_at)s, now()),
                   %(last_checked)s)
               ON CONFLICT (id) DO UPDATE SET
                   name = EXCLUDED.name, name_norm = EXCLUDED.name_norm, level = EXCLUDED.level,
                   state_key = EXCLUDED.state_key, district_key = EXCLUDED.district_key,
                   agency_type = EXCLUDED.agency_type, agency_name = EXCLUDED.agency_name,
                   ministry = EXCLUDED.ministry, categories = EXCLUDED.categories,
                   benefit_kind = EXCLUDED.benefit_kind, annual_value_inr = EXCLUDED.annual_value_inr,
                   benefit_personal = EXCLUDED.benefit_personal, origin = EXCLUDED.origin,
                   verified = EXCLUDED.verified, awareness = EXCLUDED.awareness,
                   quality_score = EXCLUDED.quality_score, rule_count = EXCLUDED.rule_count,
                   unmapped_count = EXCLUDED.unmapped_count, source_url = EXCLUDED.source_url,
                   source_site = EXCLUDED.source_site, content_hash = EXCLUDED.content_hash,
                   data = EXCLUDED.data, updated_at = EXCLUDED.updated_at,
                   last_checked = EXCLUDED.last_checked""",
            {**c, "id": scheme["id"], "data": Jsonb(scheme), "first_seen": first_seen,
             "updated_at": updated_at or _now(), "last_checked": c["last_checked"] or None})
        sid = scheme["id"]
        for table in ("scheme_rule", "scheme_target", "scheme_name", "scheme_unmapped", "scheme_document"):
            conn.execute(f"DELETE FROM {table} WHERE scheme_id = %s", (sid,))
        conn.execute("UPDATE scheme_source SET is_primary = FALSE WHERE scheme_id = %s", (sid,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO scheme_rule (scheme_id, block, position, field, op, value, reason, source_quote, "
                "near_miss, promoted) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(sid, r["block"], r["position"], r["field"], r["op"], Jsonb(r["value"]), r["reason"],
                  r["source_quote"], r["near_miss"], r["promoted"]) for r in d.rules])
            cur.executemany("INSERT INTO scheme_target (scheme_id, dimension, value) VALUES (%s, %s, %s)",
                            [(sid, dim, val) for dim, val in d.targets])
            cur.executemany("INSERT INTO scheme_name (scheme_id, name, lang, name_norm) VALUES (%s, %s, %s, %s) "
                            "ON CONFLICT DO NOTHING", [(sid, n, lang, nn) for n, lang, nn in d.names])
            cur.executemany("INSERT INTO scheme_unmapped (scheme_id, position, text) VALUES (%s, %s, %s)",
                            [(sid, i, t) for i, t in enumerate(d.unmapped)])
            cur.executemany("INSERT INTO scheme_document (scheme_id, position, name, key) VALUES (%s, %s, %s, %s)",
                            [(sid, i, n, k) for i, (n, k) in enumerate(d.documents)])
            # sources accumulate: a page seen before keeps its row, a new page adds one
            cur.executemany(
                """INSERT INTO scheme_source (scheme_id, url, site, fetched_at, content_hash, is_primary)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (scheme_id, url) DO UPDATE SET site = EXCLUDED.site,
                       fetched_at = EXCLUDED.fetched_at, content_hash = EXCLUDED.content_hash,
                       is_primary = EXCLUDED.is_primary""",
                [(sid, s["url"], s["site"], s["fetched_at"] or None, s["content_hash"], s["is_primary"])
                 for s in d.sources])
    _bump()


def save_scheme(scheme: dict, path=None) -> tuple[str, str]:
    """Insert or update one scheme. Returns (id, status) where status is
    "new", "updated", "unchanged" or "curated_exists" (a hand-verified copy wins)."""
    conn = connect()
    dup = find_duplicate(scheme)
    if dup:
        row = conn.execute("SELECT origin, content_hash FROM scheme WHERE id = %s", (dup,)).fetchone()
        if row["origin"] == "curated" and scheme.get("origin") != "curated":
            return dup, "curated_exists"
        if row["content_hash"] and row["content_hash"] == scheme.get("content_hash"):
            return dup, "unchanged"
    sid = dup or scheme["id"]
    upsert({**scheme, "id": sid}, conn=conn)
    return sid, "updated" if dup else "new"


def set_verified(scheme_id: str, verified: bool = True, path=None) -> bool:
    scheme = get_scheme(scheme_id)
    if scheme is None:
        return False
    scheme["verified"] = verified
    upsert(scheme)
    return True


def delete_scheme(scheme_id: str, path=None) -> bool:
    changed = connect().execute("DELETE FROM scheme WHERE id = %s", (scheme_id,)).rowcount
    _bump()
    return bool(changed)


# ---------- crawl bookkeeping ----------

def queue_urls(urls: list[tuple[str, str]], path=None) -> int:
    """Remember scheme pages found by the cheap crawl, to extract later. URLs already seen
    (extracted, failed or queued) are left alone. Returns how many are new."""
    if not urls:
        return 0
    conn = connect()
    before = conn.execute("SELECT COUNT(*) AS n FROM crawl_url").fetchone()["n"]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO crawl_url (url, status, note) VALUES (%s, 'queued', %s) "
                        "ON CONFLICT (url) DO NOTHING", list(urls))
    return conn.execute("SELECT COUNT(*) AS n FROM crawl_url").fetchone()["n"] - before


def queued_urls(limit: int | None = None, path=None) -> list[dict]:
    sql = "SELECT url, note FROM crawl_url WHERE status = 'queued' ORDER BY priority DESC, seq"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in connect().execute(sql)]


def queued_count(path=None) -> int:
    return connect().execute("SELECT COUNT(*) AS n FROM crawl_url WHERE status = 'queued'").fetchone()["n"]


def mark_url(url: str, status: str, scheme_id: str | None = None, note: str = "", path=None) -> None:
    connect().execute(
        """INSERT INTO crawl_url (url, status, scheme_id, note, at) VALUES (%s, %s, %s, %s, now())
           ON CONFLICT (url) DO UPDATE SET status = EXCLUDED.status, scheme_id = EXCLUDED.scheme_id,
               note = EXCLUDED.note, at = EXCLUDED.at""",
        (url, status, scheme_id, (note or "")[:300]))


def url_record(url: str, path=None) -> dict | None:
    row = connect().execute("SELECT url, status, scheme_id, note, at FROM crawl_url WHERE url = %s",
                            (url,)).fetchone()
    return dict(row) if row else None


def seen_urls(path=None) -> set[str]:
    return {r["url"] for r in connect().execute("SELECT url FROM crawl_url")}


# ---------- surviving dropped connections ----------

def _reconnecting(fn):
    """Run fn; if the server dropped the connection (Neon suspends idle databases and closes their
    connections), reconnect and run it once more. Safe because every function here can be repeated:
    reads, upserts, ON CONFLICT inserts and deletes give the same result the second time."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except psycopg.OperationalError:
            close()
            return fn(*args, **kwargs)
    return wrapper


for _name in ("load_catalog", "get_scheme", "count", "stats", "find_duplicate", "upsert", "save_scheme",
              "set_verified", "delete_scheme", "queue_urls", "queued_urls", "queued_count", "mark_url",
              "url_record", "seen_urls"):
    globals()[_name] = _reconnecting(globals()[_name])
del _name
