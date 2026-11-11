"""Run one citizen message through the whole system and show every step. Live: uses the
configured extractor LLM and the configured database (Neon when DATABASE_URL is set).

    python scripts/try_flow.py "I am a 34 year old construction worker in Mysuru, Karnataka..."
    python scripts/try_flow.py --update 3 "मी 45 वर्षांची विधवा आहे, बीड..."

Steps: privacy guard -> LLM reads the message -> code validates the facts -> database returns the
schemes in scope -> engine decides and ranks (no LLM) -> top 5 -> enough good matches? If not,
the gap is logged and, with --update N, N queued official pages are read into the database and
the engine runs again, so you can see the database grow.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.fields import validate_profile  # noqa: E402
from core.quality import quality_of  # noqa: E402
from core.questions import question_candidates  # noqa: E402
from discovery.catalog import catalog_for  # noqa: E402
from llm import get_llm  # noqa: E402
from saathi.shortlist import build_shortlist  # noqa: E402
from saathi.understand import understand  # noqa: E402
from store import db  # noqa: E402
from web.conversation_api import SENSITIVE  # noqa: E402

TARGET = 5                 # schemes to show the user
QUALITY_FLOOR = 30         # a match counts as "good" only if its record is at least this complete
GOOD = ("eligible", "eligible_check_manually")


def age_band(age) -> str:
    if not isinstance(age, (int, float)):
        return "?"
    return "0-17" if age < 18 else "18-39" if age < 40 else "40-59" if age < 60 else "60+"


def segment(profile: dict) -> tuple[str, dict]:
    dims = {k: profile.get(k) for k in ("state", "district", "occupation", "gender", "social_category")}
    dims["age_band"] = age_band(profile.get("age"))
    return "|".join(str(v or "?") for v in dims.values()), dims


def top5(sl: dict) -> list[dict]:
    cards = ([sl["start_with"]] if sl.get("start_with") else []) + sl.get("others", [])
    return cards[:TARGET]


def show(cards: list[dict], catalog: dict) -> int:
    good = 0
    for i, c in enumerate(cards, 1):
        q = quality_of(catalog[c["id"]]).score
        is_good = c["status"] in GOOD and q >= QUALITY_FLOOR
        good += is_good
        label = "START WITH THIS" if i == 1 else f"#{i}"
        value = f"Rs {c['annual_value_inr']:,}/yr" if c.get("annual_value_inr") else "amount not stated"
        print(f"  {label:15} {c['name'][:60]}")
        print(f"                  {c['status']}, {value}, quality {q}{'' if is_good else '  (below floor)'}"
              f", {'verified' if c['verified'] else 'not yet checked by a person'}")
        print(f"                  why: {'; '.join(c['why_ranked'][:2])}")
        print(f"                  source: {c['source_url']}")
    return good


def run_engine(profile: dict) -> tuple[dict, dict, float]:
    t0 = time.perf_counter()
    catalog = catalog_for(profile)
    t_db = time.perf_counter() - t0
    return catalog, build_shortlist({**profile, "_asked": []}, catalog, n_others=TARGET - 1), t_db


def log_gap(seg_key: str, dims: dict, good: int, web: bool, new: int) -> None:
    if db.backend_name() != "postgres":
        return
    from psycopg.types.json import Jsonb

    from store import pg
    pg.connect().execute("INSERT INTO coverage_event (segment_key, dims, db_good_count, web_triggered, web_new_count) "
                         "VALUES (%s, %s, %s, %s, %s)", (seg_key, Jsonb(dims), good, web, new))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("message")
    ap.add_argument("--update", type=int, default=0, help="if short of good matches, read N queued pages")
    ap.add_argument("--web", type=int, default=0,
                    help="if short of good matches, run up to N live web searches (official sites only)")
    args = ap.parse_args()

    print(f"MESSAGE: {args.message}\n")
    for pattern, kind in SENSITIVE:                                      # 1. privacy guard, before any model
        if pattern.search(args.message):
            print(f"Blocked: remove the {kind} first.")
            return 1

    llm = get_llm("extractor")                                           # 2. LLM reads (never decides)
    t0 = time.perf_counter()
    u = understand(llm, args.message)
    print(f"[1] LLM read the message ({llm.name}, {time.perf_counter() - t0:.1f}s): language={u.language}, "
          f"intent={u.intent}")
    profile, errors = validate_profile(u.facts)                          # 3. code validates
    print(f"[2] Facts kept by code: {json.dumps(profile, ensure_ascii=False)}")
    if u.dropped or errors:
        print(f"    dropped as invalid: {u.dropped + errors}")

    catalog, sl, t_db = run_engine(profile)                              # 4-5. database + engine
    print(f"\n[3] Database ({db.backend_name()}): {len(catalog)} schemes in scope for "
          f"state={profile.get('state')}, district={profile.get('district')}  ({t_db:.2f}s)")
    print(f"[4] Engine checked {sl['schemes_checked']} schemes: "
          f"{1 + len(sl['others']) + sl['more_eligible_not_shown'] if sl['start_with'] else 0} likely eligible, "
          f"{len(sl['near_misses'])} near misses, {sl['need_more_info']} need more information\n")
    cards = top5(sl)
    print(f"[5] Top {TARGET} for this person:")
    good = show(cards, catalog) if cards else 0
    if not cards:
        print("  (no scheme confirmed yet)")
    cands = question_candidates({**profile, "_asked": []}, catalog, limit=1)
    if cands:
        print(f"\n    Next question the bot would ask: {cands[0]['question']['text_en']!r} "
              f"(settles {len(cands[0]['schemes'])} schemes)")

    seg_key, dims = segment(profile)                                     # 6. enough?
    print(f"\n[6] Good matches: {good}/{TARGET}  (segment {seg_key})")
    if good >= TARGET:
        print("    Enough from the database. No web work needed.")
        log_gap(seg_key, dims, good, False, 0)
        return 0
    print(f"    Short by {TARGET - good}. Gap logged for the nightly crawl.")
    new = 0
    if args.update:
        from discovery.discover import Discoverer, DiscoveryReport
        batch = db.queued_urls(args.update)
        if not batch:
            print("    No queued official pages to read.")
        else:
            d = Discoverer(llm=llm)
            before = db.count()
            print(f"    Reading {len(batch)} queued official pages into the database with {llm.name} ...")
            report = DiscoveryReport()
            for row in batch:
                meta = json.loads(row["note"]) if row["note"] else {}
                t1 = time.perf_counter()
                sid = d.process_url(row["url"], report, extra=meta)
                print(f"      {time.perf_counter() - t1:5.1f}s  {'saved ' + sid if sid else 'no scheme'}  <- {row['url'][:70]}")
            new = db.count() - before
            print(f"    Database: {before} -> {db.count()} schemes ({new} new, {len(report.updated_schemes)} updated); "
                  f"{db.queued_count()} pages still queued")
            catalog, sl, _ = run_engine(profile)
            print(f"\n[7] Engine re-run on the updated database:")
            good = show(top5(sl), catalog)
            print(f"    Good matches now: {good}/{TARGET}")
    if args.web:
        from discovery.discover import Discoverer
        d = Discoverer(llm=llm)
        if d.search.name == "none":
            print("    Live web search needs SEARCH_PROVIDER (serpapi or tavily) in .env.")
        else:
            before = db.count()
            print(f"\n    Live web search ({d.search.name}, up to {args.web} searches, official sites only) ...")
            t1 = time.perf_counter()
            report = d.discover_for_profile(profile, max_searches=args.web, max_pages=2 * args.web)
            added = db.count() - before
            new += added
            print(f"    {report.searches} searches ({d.search.live_calls} paid), {report.pages_fetched} pages read, "
                  f"{added} new schemes saved, {len(report.updated_schemes)} updated "
                  f"({time.perf_counter() - t1:.0f}s)")
            for sid in report.new_schemes:
                s = db.get_scheme(sid)
                print(f"      + {s['name'][:60]}  ({s.get('source_site', '')}, quality {quality_of(s).score})")
            for url, why in report.skipped[:6]:
                print(f"      - skipped {url[:60]}: {why[:50]}")
            catalog, sl, _ = run_engine(profile)
            print(f"\n[8] Engine re-run after the web search:")
            good = show(top5(sl), catalog)
            print(f"    Good matches now: {good}/{TARGET}")
    log_gap(seg_key, dims, good, bool(args.update or args.web), new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
