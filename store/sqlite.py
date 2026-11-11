"""SQLite backend of the scheme database (the default). One file, data/schemes.db, holds every scheme and crawl record.

Why a database and not YAML files: a session needs only the schemes of one state and district,
not all of them. Rows are filtered by indexed columns, so the engine reads ~50 rows instead of
loading thousands of files. Saving a scheme no longer re-reads the whole catalog.

    load_catalog(state="maharashtra", district="beed")   -> {id: scheme_dict} for the engine
    save_scheme(scheme)                                  -> (id, "new" | "updated" | ...)
    mark_url(url, "scheme", sid) / url_record(url)       -> crawl bookkeeping

The full scheme stays a plain dict (same shape as the old YAML) in the `data` column, so the
rules engine, the extractor and the tests are unchanged. The columns beside it exist only to
filter and dedupe quickly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz, process

from core.awareness import norm_name

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "schemes.db"
DUPLICATE_SCORE = 92

SCHEMA = """
CREATE TABLE IF NOT EXISTS schemes (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    name_norm       TEXT NOT NULL,
    state           TEXT,
    district        TEXT,
    agency_type     TEXT,
    origin          TEXT NOT NULL DEFAULT 'discovered',
    verified        INTEGER NOT NULL DEFAULT 0,
    benefit_annual_inr INTEGER DEFAULT 0,
    source_url      TEXT,
    source_site     TEXT,
    content_hash    TEXT,
    rule_count      INTEGER DEFAULT 0,
    updated_at      TEXT,
    data            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS schemes_state ON schemes(state);
CREATE INDEX IF NOT EXISTS schemes_district ON schemes(district);
CREATE INDEX IF NOT EXISTS schemes_name_norm ON schemes(name_norm);
CREATE INDEX IF NOT EXISTS schemes_source ON schemes(source_url);

CREATE TABLE IF NOT EXISTS crawl_urls (
    url         TEXT PRIMARY KEY,
    status      TEXT NOT NULL,          -- scheme | not_scheme | fetch_error | skipped
    scheme_id   TEXT,
    note        TEXT,
    at          TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_local = threading.local()
_version = 0            # bumped on every write so cached catalogs reload


def db_path() -> Path:
    return Path(os.getenv("SCHEME_DB") or DEFAULT_PATH)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """One connection per thread (the web app reads from several)."""
    target = str(Path(path) if path else db_path())
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "target", None) == target:
        return conn
    if conn is not None:
        conn.close()
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")   # two processes may write (mapper + extractor)
    conn.executescript(SCHEMA)
    _local.conn, _local.target = conn, target
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = _local.target = None


def version() -> int:
    return _version


def _bump() -> None:
    global _version
    _version += 1


# ---------- reading ----------

def _row_to_scheme(row: sqlite3.Row) -> dict:
    scheme = json.loads(row["data"])
    scheme["verified"] = bool(row["verified"])
    return scheme


def load_catalog(state: str | None = None, district: str | None = None,
                 include_discovered: bool = True, path=None) -> dict[str, dict]:
    """Schemes the engine should consider: central ones plus the person's state, and within that
    state only their district's schemes (a Beed scheme is of no use to someone in Pune)."""
    conn = connect(path)
    where, args = [], []
    if not include_discovered:
        where.append("origin = 'curated'")
    if state:
        where.append("(state IS NULL OR state = ?)")
        args.append(state)
    if district:
        where.append("(district IS NULL OR district = ?)")
        args.append(district)
    elif state:
        where.append("district IS NULL")          # no district given: skip district-only schemes
    sql = "SELECT * FROM schemes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return {r["id"]: _row_to_scheme(r) for r in conn.execute(sql, args)}


def get_scheme(scheme_id: str, path=None) -> dict | None:
    row = connect(path).execute("SELECT * FROM schemes WHERE id = ?", (scheme_id,)).fetchone()
    return _row_to_scheme(row) if row else None


def count(path=None) -> int:
    return connect(path).execute("SELECT COUNT(*) FROM schemes").fetchone()[0]


def stats(path=None) -> dict:
    conn = connect(path)
    return {
        "schemes": count(path),
        "rules": conn.execute("SELECT COALESCE(SUM(rule_count), 0) FROM schemes").fetchone()[0],
        "by_origin": {r[0]: r[1] for r in conn.execute("SELECT origin, COUNT(*) FROM schemes GROUP BY origin")},
        "by_state": {(r[0] or "central"): r[1] for r in
                     conn.execute("SELECT state, COUNT(*) FROM schemes GROUP BY state ORDER BY COUNT(*) DESC")},
        "districts": conn.execute("SELECT COUNT(DISTINCT district) FROM schemes WHERE district IS NOT NULL").fetchone()[0],
        "verified": conn.execute("SELECT COUNT(*) FROM schemes WHERE verified = 1").fetchone()[0],
        "urls": {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM crawl_urls GROUP BY status")},
    }


# ---------- duplicates ----------

def find_duplicate(scheme: dict, path=None) -> str | None:
    """An existing scheme that is the same one. Compared only within the same state and district,
    so "Lek Ladki Yojana" in Beed and in Pune stay separate rows."""
    conn = connect(path)
    key = norm_name(scheme["name"])
    if not key:
        return None
    rows = conn.execute(
        "SELECT id, name_norm FROM schemes WHERE state IS ? AND district IS ?",
        (scheme.get("state"), scheme.get("district"))).fetchall()
    if not rows:
        return None
    names = [r["name_norm"] for r in rows]
    hit = process.extractOne(key, names, scorer=fuzz.token_set_ratio, score_cutoff=DUPLICATE_SCORE)
    return rows[hit[2]]["id"] if hit else None


# ---------- writing ----------

def save_scheme(scheme: dict, path=None) -> tuple[str, str]:
    """Insert or update one scheme. Returns (id, status) where status is
    "new", "updated", "unchanged" or "curated_exists" (a hand-verified copy wins)."""
    conn = connect(path)
    dup = find_duplicate(scheme, path)
    if dup:
        row = conn.execute("SELECT origin, content_hash FROM schemes WHERE id = ?", (dup,)).fetchone()
        if row["origin"] == "curated" and scheme.get("origin") != "curated":
            return dup, "curated_exists"
        if row["content_hash"] and row["content_hash"] == scheme.get("content_hash"):
            return dup, "unchanged"
    sid = dup or scheme["id"]
    scheme = {**scheme, "id": sid}
    elig = scheme.get("eligibility") or {}
    conn.execute(
        """INSERT INTO schemes (id, name, name_norm, state, district, agency_type, origin, verified,
                                benefit_annual_inr, source_url, source_site, content_hash, rule_count,
                                updated_at, data)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, name_norm=excluded.name_norm, state=excluded.state,
                district=excluded.district, agency_type=excluded.agency_type,
                benefit_annual_inr=excluded.benefit_annual_inr, source_url=excluded.source_url,
                source_site=excluded.source_site, content_hash=excluded.content_hash,
                rule_count=excluded.rule_count, updated_at=excluded.updated_at, data=excluded.data""",
        (sid, scheme["name"], norm_name(scheme["name"]), scheme.get("state"), scheme.get("district"),
         scheme.get("agency_type"), scheme.get("origin", "discovered"),
         int(bool(scheme.get("verified", scheme.get("origin") == "curated"))),
         int(scheme.get("benefit_annual_inr") or 0), scheme.get("source_url"), scheme.get("source_site"),
         scheme.get("content_hash"), len(elig.get("all") or []) + len(elig.get("any") or []),
         datetime.now(timezone.utc).isoformat(timespec="seconds"),
         json.dumps(scheme, ensure_ascii=False, default=str)))
    conn.commit()
    _bump()
    return sid, "updated" if dup else "new"


def set_verified(scheme_id: str, verified: bool = True, path=None) -> bool:
    conn = connect(path)
    scheme = get_scheme(scheme_id, path)
    if scheme is None:
        return False
    scheme["verified"] = verified
    conn.execute("UPDATE schemes SET verified = ?, data = ? WHERE id = ?",
                 (int(verified), json.dumps(scheme, ensure_ascii=False, default=str), scheme_id))
    conn.commit()
    _bump()
    return True


def delete_scheme(scheme_id: str, path=None) -> bool:
    conn = connect(path)
    changed = conn.execute("DELETE FROM schemes WHERE id = ?", (scheme_id,)).rowcount
    conn.commit()
    _bump()
    return bool(changed)


# ---------- crawl bookkeeping (replaces cache/discovery_index.json) ----------

def queue_urls(urls: list[tuple[str, str]], path=None) -> int:
    """Remember scheme pages found by the cheap crawl, to extract later. [(url, source_json)].
    URLs already seen (extracted, failed or queued) are left alone. Returns how many are new."""
    conn = connect(path)
    before = conn.execute("SELECT COUNT(*) FROM crawl_urls").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO crawl_urls (url, status, note, at) VALUES (?,'queued',?,?)",
        [(u, note, datetime.now(timezone.utc).isoformat(timespec="seconds")) for u, note in urls])
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM crawl_urls").fetchone()[0] - before


def queued_urls(limit: int | None = None, path=None) -> list[dict]:
    sql = "SELECT url, note FROM crawl_urls WHERE status = 'queued' ORDER BY rowid"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in connect(path).execute(sql)]


def queued_count(path=None) -> int:
    return connect(path).execute("SELECT COUNT(*) FROM crawl_urls WHERE status = 'queued'").fetchone()[0]


def mark_url(url: str, status: str, scheme_id: str | None = None, note: str = "", path=None) -> None:
    conn = connect(path)
    conn.execute("""INSERT INTO crawl_urls (url, status, scheme_id, note, at) VALUES (?,?,?,?,?)
                    ON CONFLICT(url) DO UPDATE SET status=excluded.status, scheme_id=excluded.scheme_id,
                                                   note=excluded.note, at=excluded.at""",
                 (url, status, scheme_id, note[:300],
                  datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def url_record(url: str, path=None) -> dict | None:
    row = connect(path).execute("SELECT * FROM crawl_urls WHERE url = ?", (url,)).fetchone()
    return dict(row) if row else None


def seen_urls(path=None) -> set[str]:
    return {r[0] for r in connect(path).execute("SELECT url FROM crawl_urls")}
