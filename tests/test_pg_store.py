"""PostgreSQL backend (store/pg.py): same behaviour as the SQLite store, plus the derived tables.

Opt-in, because it needs a running Postgres. Point TEST_DATABASE_URL at a throwaway database
whose name ends in "_test" (it is emptied before every test), for example:

    createdb -U postgres yojana_test
    $env:TEST_DATABASE_URL = "postgresql://postgres:<password>@localhost:5432/yojana_test"
    python -m pytest -q tests/test_pg_store.py

Without it, these tests are skipped. They never call an LLM or the network.
"""
import json
import os
from pathlib import Path

import pytest
import yaml

PG_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_DATABASE_URL not set (Postgres tests are opt-in)")

FIXTURES = Path(__file__).parent / "fixtures" / "schemes"
PERSONAS = Path(__file__).parent / "personas.yaml"


def normal(obj):
    """Compare dicts the way both stores serialise them."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str, sort_keys=True))


@pytest.fixture
def pgdb(monkeypatch):
    import psycopg
    if not psycopg.conninfo.conninfo_to_dict(PG_URL).get("dbname", "").endswith("_test"):
        pytest.skip("TEST_DATABASE_URL must name a database ending in _test; it is emptied by these tests")
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    from core.loader import load_scheme_dir
    from store import db, pg, pg_admin
    pg.close()
    pg_admin.init_schema(log=lambda m: None)
    conn = pg.connect()
    conn.execute("TRUNCATE scheme, crawl_url, coverage_event, segment_search, job, conversation, review_task "
                 "RESTART IDENTITY CASCADE")
    conn.execute("DELETE FROM geo_district WHERE auto_added")
    for s in load_scheme_dir(FIXTURES).values():
        db.save_scheme({**s, "origin": "curated", "verified": True})
    yield db
    pg.close()


def discovered(name="Test Kanya Yojana", **over):
    s = {"id": name.lower().replace(" ", "_"), "name": name, "origin": "discovered", "verified": False,
         "state": "maharashtra", "district": None, "source_url": "https://zp.example.gov.in/scheme/1",
         "source_site": "zp.example.gov.in", "last_verified": "2026-10-23", "content_hash": "h1",
         "benefit_summary": "Rs 5,000 one-time for a girl child", "benefit_annual_inr": 0,
         "rank": {"value_score": 3, "effort": 3},
         "eligibility": {"all": [{"field": "has_girl_child_under_10", "op": "eq", "value": True,
                                  "source_quote": "families with a girl child"}]},
         "unmapped_criteria": [], "documents": [], "actions": {}, "status_map": {}, "dependencies": [],
         "records_to_match": []}
    s.update(over)
    return s


def test_backend_switches_on_database_url(pgdb):
    assert pgdb.backend_name() == "postgres"
    assert pgdb.count() == 3


def test_catalog_matches_the_sqlite_store(pgdb):
    """The same schemes saved to both stores load back identically."""
    from store import sqlite
    lite = sqlite.load_catalog(path=os.environ["SCHEME_DB"])
    assert normal(pgdb.load_catalog()) == normal(lite)


def test_derived_rows_are_written_with_the_scheme(pgdb):
    from store import pg
    conn = pg.connect()
    s = pgdb.get_scheme("pm_kisan")
    n_rules = len(s["eligibility"].get("all") or []) + len(s["eligibility"].get("any") or [])
    assert conn.execute("SELECT COUNT(*) AS n FROM scheme_rule WHERE scheme_id = 'pm_kisan'").fetchone()["n"] == n_rules
    assert conn.execute("SELECT COUNT(*) AS n FROM scheme_target WHERE scheme_id = 'pm_kisan'").fetchone()["n"] > 0
    row = conn.execute("SELECT level, quality_score, verified FROM scheme WHERE id = 'pm_kisan'").fetchone()
    assert row["level"] == "central" and row["verified"] and 0 < row["quality_score"] <= 100


def test_dedupe_statuses_match_the_sqlite_store(pgdb):
    sid, status = pgdb.save_scheme(discovered())
    assert status == "new"
    assert pgdb.save_scheme(discovered())[1] == "unchanged"                     # same content hash
    assert pgdb.save_scheme(discovered(content_hash="h2"))[1] == "updated"
    assert pgdb.save_scheme(discovered("Test Kanya Yojana Scheme", content_hash="h3"))[0] == sid   # fuzzy dup
    curated_copy = {**pgdb.get_scheme("pm_kisan"), "origin": "discovered", "id": "pm_kisan_copy"}
    assert pgdb.save_scheme(curated_copy) == ("pm_kisan", "curated_exists")


def test_district_scheme_is_scoped_like_sqlite(pgdb):
    pgdb.save_scheme(discovered("Beed ZP Girl Scheme", district="beed"))
    assert "beed_zp_girl_scheme" in pgdb.load_catalog("maharashtra", "beed")
    assert "beed_zp_girl_scheme" not in pgdb.load_catalog("maharashtra", "pune")
    assert "beed_zp_girl_scheme" not in pgdb.load_catalog("maharashtra")
    assert "pm_kisan" in pgdb.load_catalog("maharashtra", "pune")             # central: everywhere


def test_unknown_district_is_registered_not_rejected(pgdb):
    from store import pg
    pgdb.save_scheme(discovered("Wayanad Tribal Scheme", state="kerala", district="wayanad"))
    row = pg.connect().execute("SELECT auto_added FROM geo_district WHERE state_key = 'kerala' AND key = 'wayanad'").fetchone()
    assert row and row["auto_added"]


def test_queue_is_remembered_in_order_and_not_re_queued(pgdb):
    urls = [("https://a.gov.in/en/scheme/two/", "{}"), ("https://a.gov.in/en/scheme/one/", "{}")]
    assert pgdb.queue_urls(urls) == 2
    assert pgdb.queue_urls(urls) == 0
    assert [u["url"] for u in pgdb.queued_urls()] == [u for u, _ in urls]    # first found, first read
    pgdb.mark_url(urls[0][0], "scheme", "one")
    assert pgdb.queued_count() == 1
    assert pgdb.url_record(urls[0][0])["status"] == "scheme"


def test_set_verified_and_delete(pgdb):
    from store import pg
    sid, _ = pgdb.save_scheme(discovered())
    assert pgdb.set_verified(sid) and pgdb.get_scheme(sid)["verified"] is True
    assert pgdb.delete_scheme(sid)
    assert pg.connect().execute("SELECT COUNT(*) AS n FROM scheme_rule WHERE scheme_id = %s", (sid,)).fetchone()["n"] == 0


def test_engine_decisions_are_unchanged_on_postgres(pgdb):
    """The labelled personas from eval.py give the expected results on the Postgres catalog."""
    from core.eligibility import check_eligibility
    from core.loader import load_schemes
    from core.ranking import rank_schemes
    catalog = load_schemes()                       # goes through store.db -> Postgres
    for p in yaml.safe_load(PERSONAS.read_text(encoding="utf-8")):
        results = check_eligibility(p["profile"], catalog)
        statuses = {r.scheme_id: r.status for r in results}
        for sid, want in p["expected"].items():
            assert statuses[sid] == want, (p["id"], sid)
        start = rank_schemes(results, catalog).start_with
        assert (start.scheme_id if start else None) == p.get("start_with"), p["id"]
