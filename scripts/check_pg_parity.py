"""Prove the Postgres copy of the catalog behaves exactly like the SQLite original.

    python -m store pg-import            # first, copy the data
    python scripts/check_pg_parity.py    # then compare

Checks, on the real catalog: every scheme dict is identical; scoped loads (state, district) return
the same schemes; stats agree; and the engine gives the same statuses, scores and start-with pick
for the labelled personas and for sample profiles across states and districts.
Needs DATABASE_URL. Local only: no LLM, no network. Exits 1 on any difference.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.eligibility import check_eligibility  # noqa: E402
from core.ranking import rank_schemes  # noqa: E402
from store import db, pg, sqlite  # noqa: E402

SQLITE_FILE = ROOT / "data" / "schemes.db"
SCOPES = [(None, None), ("maharashtra", None), ("maharashtra", "beed"), ("maharashtra", "sangli"),
          ("maharashtra", "pune"), ("karnataka", None), ("delhi", None)]
SAMPLE_PROFILES = [
    {"state": "maharashtra", "district": "beed", "age": 45, "gender": "female", "social_category": "sc",
     "marital_status": "widowed", "annual_family_income_inr": 40000},
    {"state": "maharashtra", "district": "sangli", "age": 16, "gender": "female", "is_student": True,
     "social_category": "obc", "annual_family_income_inr": 90000},
    {"state": "maharashtra", "age": 67, "gender": "male", "occupation": "farmer", "is_bpl": True},
    {"state": "karnataka", "age": 30, "gender": "male", "occupation": "construction_worker"},
]


def normal(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str, sort_keys=True))


def engine_view(profile: dict, catalog: dict) -> dict:
    results = check_eligibility(profile, catalog)
    ranking = rank_schemes(results, catalog)
    return {"statuses": {r.scheme_id: r.status for r in results},
            "scores": {sid: s.score for sid, s in ranking.scored.items()},
            "start_with": ranking.start_with.scheme_id if ranking.start_with else None,
            "queue": [r.scheme_id for r in ranking.queue]}


def main() -> int:
    if db.backend_name() != "postgres":
        print("Set DATABASE_URL in .env first.")
        return 2
    failures: list[str] = []

    def check(label: str, a, b) -> None:
        ok = normal(a) == normal(b)
        print(f"  {'OK  ' if ok else 'DIFF'} {label}")
        if not ok:
            failures.append(label)

    lite_all = sqlite.load_catalog(path=SQLITE_FILE)
    pg_all = pg.load_catalog()
    print(f"SQLite {len(lite_all)} schemes  |  Postgres {len(pg_all)} schemes ({pg.db_path()})")
    check("same scheme ids", sorted(lite_all), sorted(pg_all))
    for sid in sorted(set(lite_all) & set(pg_all)):
        if normal(lite_all[sid]) != normal(pg_all[sid]):
            failures.append(f"scheme {sid}")
            print(f"  DIFF scheme {sid}")
    print(f"  {'OK  ' if not any(f.startswith('scheme ') for f in failures) else 'DIFF'} every scheme dict identical")

    for state, district in SCOPES:
        check(f"scope state={state} district={district}",
              sorted(sqlite.load_catalog(state, district, path=SQLITE_FILE)), sorted(pg.load_catalog(state, district)))

    ls, ps = sqlite.stats(path=SQLITE_FILE), pg.stats()
    for key in ("schemes", "rules", "by_origin", "by_state", "districts", "verified", "urls"):
        check(f"stats {key}", ls[key], ps[key])

    personas = yaml.safe_load((ROOT / "tests" / "personas.yaml").read_text(encoding="utf-8"))
    profiles = [(p["id"], p["profile"]) for p in personas] + [(f"sample_{i}", p) for i, p in enumerate(SAMPLE_PROFILES)]
    for label, profile in profiles:
        state, district = profile.get("state"), profile.get("district")
        check(f"engine {label}", engine_view(profile, sqlite.load_catalog(state, district, path=SQLITE_FILE)),
              engine_view(profile, pg.load_catalog(state, district)))

    print(f"\n{'PARITY OK' if not failures else f'{len(failures)} difference(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
