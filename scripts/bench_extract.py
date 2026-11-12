"""Benchmark models on the real job: reading an official scheme page into rules. Uses the network
(fetches official pages, respecting robots.txt and rate limits) and the given models. Saves nothing.

    python scripts/bench_extract.py local:qwen2.5:7b-instruct local:nemotron-3-nano
    python scripts/bench_extract.py --pages 5 local:qwen2.5:7b-instruct

Pages are ones the catalog already extracted with a cloud model, so each result is compared with
that reference: did the JSON validate, is the scheme name the same, how many rules survived the
quote check, and how long it took.

Rule accuracy matters most: a rule can pass the quote check (the sentence is on the page) and still
be mapped wrongly ("Residing within Kerala" -> residence = rural), which wrongly rejects eligible
people. So every rule is compared with the reference (same field, op and value = agrees), rules the
reference lacks are printed for a person to judge, and the verdict needs at least 80% agreement.
The reference is itself a cloud model's extraction, not ground truth.

Pass bar: valid JSON on at least 80% of pages, names right, at least 80% of rules agreeing with
the reference, under about 60 s a page.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz  # noqa: E402

from core.awareness import norm_name  # noqa: E402
from ingest.extract import extract_scheme  # noqa: E402
from ingest.fetch import FetchError, Fetcher  # noqa: E402
from llm import LLMError, get_llm  # noqa: E402

SQLITE_FILE = ROOT / "data" / "schemes.db"


def reference_pages(n: int) -> list[tuple[str, dict]]:
    """n extracted pages from different sites, with the stored scheme as the reference answer."""
    conn = sqlite3.connect(f"file:{SQLITE_FILE.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT c.url, s.data, s.source_site FROM crawl_urls c JOIN schemes s ON s.id = c.scheme_id "
        "WHERE c.status = 'scheme' AND s.origin = 'discovered' AND s.rule_count >= 2 ORDER BY c.rowid").fetchall()
    conn.close()
    picked, sites = [], set()
    for url, data, site in rows:
        if site in sites:
            continue
        sites.add(site)
        picked.append((url, json.loads(data)))
        if len(picked) == n:
            break
    return picked


def rule_keys(scheme: dict) -> set[tuple]:
    e = scheme.get("eligibility") or {}
    return {(r["field"], r["op"], json.dumps(r["value"], sort_keys=True))
            for r in (e.get("all") or []) + (e.get("any") or [])}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+", help="provider:model, e.g. local:qwen2.5:7b-instruct")
    ap.add_argument("--pages", type=int, default=5)
    args = ap.parse_args()

    fetcher = Fetcher()
    pages = []
    for url, ref in reference_pages(args.pages * 2):
        if len(pages) == args.pages:
            break
        try:
            pages.append((fetcher.fetch(url), ref))
        except FetchError as e:
            print(f"  skip {url[:70]} ({str(e)[:60]})")
    print(f"{len(pages)} reference pages fetched\n")

    for spec in args.models:
        provider, _, model = spec.partition(":")
        try:
            llm = get_llm("extractor", provider=provider, model=model)
        except LLMError as e:
            print(f"{spec}: cannot start: {e}")
            continue
        print(f"== {spec}")
        ok = names = agree = total_rules = 0
        times = []
        for page, ref in pages:
            t0 = time.perf_counter()
            try:
                res = extract_scheme(page, llm)
                dt = time.perf_counter() - t0
                times.append(dt)
                if res.scheme is None:
                    print(f"  {dt:6.1f}s  NO SCHEME ({res.error})   ref: {ref['name'][:50]}")
                    continue
                ok += 1
                score = fuzz.token_set_ratio(norm_name(res.scheme["name"]), norm_name(ref["name"]))
                names += score >= 80
                mine, theirs = rule_keys(res.scheme), rule_keys(ref)
                agree += len(mine & theirs)
                total_rules += len(mine)
                print(f"  {dt:6.1f}s  ok  name {'same' if score >= 80 else 'DIFF'} ({score:.0f})  "
                      f"rules {len(mine)}: {len(mine & theirs)} agree with ref ({len(theirs)}), "
                      f"dropped {len(res.dropped)}  | {res.scheme['name'][:45]}"
                      + ("" if score >= 80 else f"  vs ref: {ref['name'][:45]}"))
                quotes = {(r["field"], r["op"], json.dumps(r["value"], sort_keys=True)): r.get("source_quote", "")
                          for r in (res.scheme["eligibility"].get("all") or [])
                          + (res.scheme["eligibility"].get("any") or [])}
                for key in sorted(mine - theirs):
                    print(f"            extra: {key[0]} {key[1]} {key[2]}  <- {quotes.get(key, '')[:80]!r}")
                for key in sorted(theirs - mine):
                    print(f"            missed: {key[0]} {key[1]} {key[2]}")
            except LLMError as e:
                dt = time.perf_counter() - t0
                times.append(dt)
                print(f"  {dt:6.1f}s  FAIL {str(e)[:90]}")
        avg = sum(times) / len(times) if times else 0
        accuracy = agree / total_rules if total_rules else 0.0
        verdict = ("PASS" if pages and ok >= 0.8 * len(pages) and names >= 0.8 * ok and accuracy >= 0.8
                   and avg <= 60 else "fail")
        print(f"  -> valid {ok}/{len(pages)}, names right {names}/{ok}, rules agreeing {agree}/{total_rules} "
              f"({100 * accuracy:.0f}%), avg {avg:.0f}s/page: {verdict}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
