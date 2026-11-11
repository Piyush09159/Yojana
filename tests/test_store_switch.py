"""Backend switch (store/db.py), Postgres reconnect (store/pg.py) and discovery without a search key.
Offline: no database server, no network."""
import psycopg
import pytest

from discovery.discover import Discoverer
from discovery.search import NoSearch, SearchError
from llm.providers.mock_provider import MockProvider


def test_sqlite_is_the_default_and_postgres_needs_database_url(monkeypatch):
    from store import db
    monkeypatch.setenv("DATABASE_URL", "")
    assert db.backend_name() == "sqlite"
    assert db.count() == 3                                  # the fixture catalog, on SQLite
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x_test")
    assert db.backend_name() == "postgres"


def test_postgres_url_is_printed_without_its_password(monkeypatch):
    from store import pg
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:s3cret@db.example.com:5432/yojana?sslmode=require")
    shown = pg.db_path()
    assert "s3cret" not in shown and shown == "postgresql://owner@db.example.com:5432/yojana"


def test_dropped_connection_is_retried_once(monkeypatch):
    from store import pg
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return "ok"

    monkeypatch.setattr(pg, "close", lambda: calls.append("closed"))
    assert pg._reconnecting(flaky)() == "ok"
    assert calls == [1, "closed", 1]


def test_a_second_failure_is_raised_not_hidden(monkeypatch):
    from store import pg
    monkeypatch.setattr(pg, "close", lambda: None)

    def always_down():
        raise psycopg.OperationalError("down")

    with pytest.raises(psycopg.OperationalError):
        pg._reconnecting(always_down)()


def test_discovery_works_without_a_search_key(monkeypatch):
    """Reading queued pages needs no search; a missing SEARCH_PROVIDER must not block it."""
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    d = Discoverer(llm=MockProvider(responses={}))
    assert isinstance(d.search, NoSearch) and d.search.live_calls == 0
    with pytest.raises(SearchError, match="web search unavailable"):
        d.search.search("anything")
    report = d.ingest_queries(["widow pension maharashtra"])   # search fails cleanly, nothing breaks
    assert report.searches == 0 and report.new_schemes == []
