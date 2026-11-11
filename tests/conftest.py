import os
import shutil
import sys
from pathlib import Path

import pytest

# Tests never touch a real database server. An empty DATABASE_URL keeps every test on its own
# SQLite copy, and stops .env (loaded with override=False) from filling it in. The Postgres
# backend has its own opt-in tests, enabled by TEST_DATABASE_URL (see test_pg_store.py).
os.environ["DATABASE_URL"] = ""

# make `import core` work when running pytest from the project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE_SCHEMES = Path(__file__).parent / "fixtures" / "schemes"


@pytest.fixture(scope="session")
def seed_db(tmp_path_factory):
    """Build one database from the fixture schemes, then copy it per test (fast and isolated)."""
    from core.loader import load_scheme_dir
    from store import db

    path = tmp_path_factory.mktemp("seed") / "seed.db"
    db.connect(path)
    for scheme in load_scheme_dir(FIXTURE_SCHEMES).values():
        db.save_scheme({**scheme, "origin": "curated", "verified": True}, path)
    db.close()
    return path


@pytest.fixture(autouse=True)
def _isolate_side_effects(monkeypatch, tmp_path, seed_db):
    """Every test gets its own scheme database, its own LLM log, and no .env fallbacks."""
    from store import db

    monkeypatch.setenv("LLM_LOG_PATH", str(tmp_path / "llm_calls.jsonl"))
    for var in ("LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL", "LLM_FALLBACKS", "PLANNER_FALLBACKS",
                "EXTRACTOR_FALLBACKS", "AGENT_FALLBACKS", "PLANNER_PROVIDER", "PLANNER_MODEL"):
        monkeypatch.delenv(var, raising=False)
    test_db = tmp_path / "schemes.db"
    shutil.copy(seed_db, test_db)
    monkeypatch.setenv("SCHEME_DB", str(test_db))
    db.close()
    yield test_db
    db.close()
