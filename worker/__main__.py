"""Background lanes of the agent orchestration.

    python -m worker                 run jobs until stopped (gapfill, extract, notify)
    python -m worker --once          run every ready job, then exit
    python -m worker --batch 20      batch lane: queue 20 pages from the crawl queue as extract jobs, run them
    python -m worker --status        jobs by kind and status, and today's spend per lane
    python -m worker --retry-failed --once   send failed jobs back to the queue, then run them

The worker uses the same database as the app (SCHEME_DATABASE_URL, else SQLite) and the agents' shared
model clients, so one machine with a local GPU can run the batch lane against the cloud database.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys

from store import db, runtime

from .loop import STOP, run


def _stop(signum, frame) -> None:
    # EasyPanel/Docker stop sends SIGTERM: finish the job in hand, then exit. A job cut off by a
    # SIGKILL is not lost: its lease runs out and the next worker takes it again.
    STOP.set()


def production_problem() -> str | None:
    """In production the worker must share the app's scheme database; a container-local SQLite
    copy would silently process nothing the app queued."""
    if os.getenv("DEPLOYMENT_MODE") != "production":
        return None
    if db.backend_name() != "postgres":
        return "DEPLOYMENT_MODE=production requires SCHEME_DATABASE_URL (the same Neon database as yojana-api)"
    return None


def queue_batch(n: int) -> int:
    queued = 0
    for row in db.queued_urls(n):
        meta = json.loads(row["note"]) if row.get("note") else None
        payload = {"url": row["url"], "lane": "batch", **({"meta": meta} if meta else {})}
        queued += runtime.job_enqueue("extract", payload, dedupe_key=f"extract:{row['url']}") is not None
    return queued


def status() -> None:
    print(f"Database ({db.backend_name()}): {db.db_path()}")
    counts = runtime.job_counts()
    print("Jobs:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none")
    for lane in ("realtime", "gap", "batch"):
        print(f"Today {lane:8}: {runtime.budget_used(lane)} (cloud)  "
              f"{runtime.budget_used(lane, include_local=True)['tokens']} tokens incl. local")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="python -m worker")
    ap.add_argument("--once", action="store_true", help="run every ready job, then exit")
    ap.add_argument("--batch", type=int, default=0, help="queue N crawl-queue pages for extraction first")
    ap.add_argument("--kinds", default="gapfill,extract,notify")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--retry-failed", action="store_true", help="send failed jobs back to the queue first")
    args = ap.parse_args()
    problem = production_problem()
    if problem:
        print(f"Worker not started: {problem}", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, _stop)
    if args.status:
        status()
        return 0
    if args.retry_failed:
        print(f"Re-queued {runtime.job_retry_failed()} failed job(s).")
    if args.batch:
        print(f"Queued {queue_batch(args.batch)} page(s) for the batch lane.")
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    print(f"Worker on {db.backend_name()}; kinds: {', '.join(kinds)}{' (until idle)' if args.once or args.batch else ''}")
    try:
        n = run(kinds, once=args.once or bool(args.batch))
        print(f"Done: {n} job(s).")
    except KeyboardInterrupt:
        print("\nStopped. Unfinished jobs go back to the queue when their lease runs out.")
    status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
