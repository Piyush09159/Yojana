"""Set up and fill the PostgreSQL scheme database.

    python -m store pg-status             read-only: applied/pending migrations and row counts
    python -m store pg-init               apply store/sql/*.sql (once each) and seed the geography
    python -m store pg-import [sqlite]    copy every scheme and crawl record from SQLite (default
                                          data/schemes.db), one to one, keeping ids; safe to re-run
    python -m store quality               quality scores, levels, targets and categories

All need SCHEME_DATABASE_URL (in .env). Nothing here calls an LLM or the network.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import yaml

from core.fields import load_fields

from . import pg

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = Path(__file__).resolve().parent / "sql"
SOURCE_SEEDS = ROOT / "ingest"

# Former and informal names that should resolve to a fields.yaml state key.
STATE_ALIASES = {
    "odisha": ["orissa"], "uttarakhand": ["uttaranchal"], "puducherry": ["pondicherry"],
    "delhi": ["nct_of_delhi", "new_delhi"], "jammu_and_kashmir": ["j_and_k", "jammu_kashmir"],
    "dadra_and_nagar_haveli_and_daman_and_diu": ["dadra_and_nagar_haveli", "daman_and_diu"],
    "andaman_and_nicobar_islands": ["andaman_and_nicobar"],
}


def _title(key: str) -> str:
    small = {"and", "of"}
    return " ".join(w if w in small else w.capitalize() for w in key.split("_"))


# ---------- schema ----------

def init_schema(log=print) -> list[str]:
    """Apply every store/sql/NNN_*.sql not applied yet, in order, each in its own transaction."""
    conn = pg.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migration "
                 "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migration")}
    applied = []
    for path in sorted(SQL_DIR.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
        applied.append(path.name)
        log(f"  applied {path.name}")
    if not applied:
        log("  schema up to date")
    seed_geography(log)
    return applied


def migration_status(log=print) -> dict:
    """Read-only: which store/sql files are applied, which are pending, and how many rows the
    tables a pending migration would drop hold. Run this before pg-init on a populated database.
    Never writes (the session is set read-only) and never prints the connection string."""
    conn = pg.connect()
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    try:
        exists = conn.execute("SELECT to_regclass('schema_migration') IS NOT NULL AS ok").fetchone()["ok"]
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migration")} if exists else set()
        files = [p.name for p in sorted(SQL_DIR.glob("*.sql"))]
        pending = [f for f in files if f not in done]
        rows = {}
        for table in ("scheme", "crawl_url", "job", "conversation", "segment_search", "agent_cache",
                      "agent_budget", "coverage_event"):
            if conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (table,)).fetchone()["ok"]:
                rows[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            else:
                rows[table] = None
    finally:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
    log(f"  applied: {', '.join(sorted(done)) or 'none'}")
    log(f"  pending: {', '.join(pending) or 'none'}")
    log("  rows: " + ", ".join(f"{t}={'missing' if n is None else n}" for t, n in rows.items()))
    risky = [t for t in ("job", "conversation", "segment_search") if rows.get(t)]
    if "002_agents.sql" in pending and risky:
        log(f"  STOP: 002_agents.sql would drop non-empty {', '.join(risky)} (it now refuses; back up first)")
    elif pending:
        log("  safe to run: python -m store pg-init (only the pending files above are applied)")
    return {"applied": sorted(done), "pending": pending, "rows": rows}


def seed_geography(log=print) -> tuple[int, int]:
    """All 36 states and UTs from fields.yaml, and every district the source maps and the field
    aliases know. The full LGD district list (about 780, with official codes) is a later import."""
    conn = pg.connect()
    states = load_fields()["state"]["values"]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO geo_state (key, name_en, aliases) VALUES (%s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET aliases = EXCLUDED.aliases",
            [(s, _title(s), STATE_ALIASES.get(s, [])) for s in states])
    districts: dict[tuple[str, str], set[str]] = {}
    for seed_file in sorted(SOURCE_SEEDS.glob("sources_*.yaml")):
        if seed_file.name.endswith(".resolved.yaml"):
            continue
        seed = yaml.safe_load(seed_file.read_text(encoding="utf-8"))
        state = seed.get("state")
        for key, info in (seed.get("districts") or {}).items():
            districts.setdefault((state, key), set()).update((info or {}).get("aliases") or [])
    # fields.yaml district aliases (renamed districts) all point at Maharashtra keys today
    for alias, key in (load_fields()["district"].get("aliases") or {}).items():
        if ("maharashtra", key) in districts:
            districts[("maharashtra", key)].add(alias)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO geo_district (state_key, key, name_en, aliases) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (state_key, key) DO UPDATE SET aliases = EXCLUDED.aliases",
            [(st, key, _title(key), sorted(aliases)) for (st, key), aliases in sorted(districts.items())])
    log(f"  geography: {len(states)} states/UTs, {len(districts)} districts")
    return len(states), len(districts)


# ---------- import ----------

def import_from_sqlite(sqlite_path: Path | str | None = None, log=print) -> dict:
    """Copy schemes and crawl records from the SQLite store, keeping ids, verified flags and
    timestamps. Uses pg.upsert (no dedupe) so the copy is exact; re-running updates in place."""
    src = Path(sqlite_path) if sqlite_path else ROOT / "data" / "schemes.db"
    if not src.exists():
        raise FileNotFoundError(src)
    lite = sqlite3.connect(src)
    lite.row_factory = sqlite3.Row
    conn = pg.connect()
    schemes = 0
    for row in lite.execute("SELECT id, verified, updated_at, data FROM schemes ORDER BY rowid"):
        scheme = json.loads(row["data"])
        scheme["verified"] = bool(row["verified"])
        pg.upsert({**scheme, "id": row["id"]}, first_seen=row["updated_at"], updated_at=row["updated_at"],
                  conn=conn)
        schemes += 1
    urls = [(r["url"], r["status"], r["scheme_id"], r["note"], r["at"])
            for r in lite.execute("SELECT url, status, scheme_id, note, at FROM crawl_urls ORDER BY rowid")]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO crawl_url (url, status, scheme_id, note, at) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (url) DO UPDATE SET status = EXCLUDED.status, scheme_id = EXCLUDED.scheme_id,
                   note = EXCLUDED.note, at = EXCLUDED.at""", urls)
    lite.close()
    log(f"  imported {schemes} schemes and {len(urls)} crawl records from {src}")
    return {"schemes": schemes, "crawl_urls": len(urls)}


# ---------- reporting ----------

def quality_report(log=print, worst: int = 10) -> dict:
    conn = pg.connect()
    rows = conn.execute("SELECT id, name, level, quality_score, rule_count, unmapped_count, categories "
                        "FROM scheme ORDER BY quality_score, id").fetchall()
    if not rows:
        log("  no schemes yet")
        return {}
    scores = [r["quality_score"] for r in rows]
    buckets = Counter(min(s // 20, 4) for s in scores)
    labels = ["0-19", "20-39", "40-59", "60-79", "80-100"]
    targets = conn.execute("SELECT dimension, value, COUNT(*) AS n FROM scheme_target "
                           "GROUP BY dimension, value ORDER BY n DESC, dimension, value LIMIT 15").fetchall()
    cats = Counter(c for r in rows for c in r["categories"])
    levels = Counter(r["level"] for r in rows)
    log(f"Schemes: {len(rows)} | by level: {dict(levels)}")
    log(f"Quality (0-100): median {sorted(scores)[len(scores) // 2]}, "
        + ", ".join(f"{labels[b]}: {buckets.get(b, 0)}" for b in range(5)))
    log("Who they are for (top): " + ", ".join(f"{t['dimension']}={t['value']} {t['n']}" for t in targets))
    log("Topics: " + ", ".join(f"{c} {n}" for c, n in cats.most_common()))
    log(f"Weakest {worst} (first to re-extract or review):")
    for r in rows[:worst]:
        log(f"  {r['quality_score']:>3}  {r['id']}  ({r['rule_count']} rules, {r['unmapped_count']} unmapped)")
    return {"schemes": len(rows), "median": sorted(scores)[len(scores) // 2],
            "buckets": {labels[b]: buckets.get(b, 0) for b in range(5)}, "levels": dict(levels)}
