"""The worker: take ready jobs and run their handler, one at a time, until told to stop.

A job's lease is renewed by being claimed; if this process dies mid-job, the lease runs out and any
worker takes the job again. Failures are retried with growing waits (1, 2, 4 minutes) up to the
job's max_attempts, then the job is kept as "failed" with its error for a person to look at.
A crashing handler never stops the loop.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from store import runtime

from .jobs import HANDLERS, RetryLater

log = logging.getLogger("worker")
LEASE = 600                     # seconds a job may run before another worker may take it over
STOP = threading.Event()        # set by SIGTERM/SIGINT: finish the current job, then exit


def heartbeat_path() -> Path:
    return Path(os.getenv("WORKER_HEARTBEAT", str(Path(os.getenv("HOME") or ".") / "worker.heartbeat")))


def beat() -> None:
    """Touch the heartbeat file the container health check reads (deploy/healthcheck.py)."""
    try:
        path = heartbeat_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="ascii")
    except OSError:
        pass


def run_one(kinds: list[str] | None = None, handlers: dict | None = None) -> dict | None:
    """Take and run one ready job. Returns {"id", "kind", "status", "result"|"error"} or None."""
    handlers = handlers or HANDLERS
    job = runtime.job_claim(kinds or list(handlers), lease=LEASE)
    if job is None:
        return None
    t0 = time.perf_counter()
    try:
        result = handlers[job["kind"]](job["payload"])
        runtime.job_done(job["id"], result)
        return {"id": job["id"], "kind": job["kind"], "status": "done", "result": result,
                "s": round(time.perf_counter() - t0, 1)}
    except RetryLater as e:
        error = str(e)
    except Exception as e:                    # a bug in one job must not stop the others
        log.exception("job %s (%s) crashed", job["id"], job["kind"])
        error = f"{type(e).__name__}: {e}"
    status = runtime.job_fail(job["id"], error, retry_in=60 * 2 ** (job["attempts"] - 1))
    return {"id": job["id"], "kind": job["kind"], "status": status, "error": error[:200],
            "s": round(time.perf_counter() - t0, 1)}


def run(kinds: list[str] | None = None, once: bool = False, idle: float = 5.0, out=print) -> int:
    """Process jobs. once=True: until none is ready, then return how many ran."""
    done = 0
    while not STOP.is_set():
        beat()
        r = run_one(kinds)
        if r is None:
            if once:
                return done
            STOP.wait(idle)
            continue
        done += 1
        detail = r.get("result") if r["status"] == "done" else r.get("error")
        out(f"  job {r['id']:>4} {r['kind']:8} {r['status']:7} {r['s']:5.1f}s  {detail}")
    return done
