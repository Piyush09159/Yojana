"""The catalog the engine decides on. Backed by the database (store/db.py), not by files.

    load_catalog(state="maharashtra", district="beed")  -> {id: scheme} the engine should consider
    save_discovered(scheme)                             -> (id, "new" | "updated" | ...)

Kept as its own module so callers do not need to know where schemes are stored.
"""
from __future__ import annotations

import os
import threading
import time

from core.awareness import norm_name
from store import db

__all__ = ["load_catalog", "save_discovered", "find_duplicate", "norm_name"]


def load_catalog(state: str | None = None, district: str | None = None,
                 include_discovered: bool = True, path=None) -> dict[str, dict]:
    return db.load_catalog(state, district, include_discovered, path)


_cache: dict = {}
_cache_lock = threading.Lock()


def cached_catalog() -> dict[str, dict]:
    """The whole catalog for the web conversation and readiness probe.

    On PostgreSQL (SCHEME_DATABASE_URL) the full catalog is several MB of JSONB, so reading it on
    every WhatsApp/web message adds latency and Neon egress. It is kept in memory for
    SCHEME_CATALOG_TTL_SECONDS (default 60; the worker's new schemes appear within that time) and
    reloaded at once after a write from this process. SQLite reads stay uncached (local and fast).
    A Postgres outage is never hidden behind a stale copy past the TTL."""
    if db.backend_name() != "postgres":
        return db.load_catalog()
    try:
        ttl = max(0.0, float(os.getenv("SCHEME_CATALOG_TTL_SECONDS", "60")))
    except ValueError:
        ttl = 60.0
    key = (os.getenv("SCHEME_DATABASE_URL"), db.version())
    with _cache_lock:
        hit = _cache.get("all")
        if hit and hit[0] == key and time.monotonic() - hit[1] < ttl:
            return dict(hit[2])
    catalog = db.load_catalog()
    with _cache_lock:
        _cache["all"] = (key, time.monotonic(), catalog)
    return dict(catalog)


def catalog_for(profile: dict, path=None) -> dict[str, dict]:
    """Only the schemes that can apply to this person: central + their state + their district."""
    return db.load_catalog(profile.get("state"), profile.get("district"), path=path)


def save_discovered(scheme: dict, path=None) -> tuple[str, str]:
    return db.save_scheme(scheme, path)


def find_duplicate(scheme: dict, path=None) -> str | None:
    return db.find_duplicate(scheme, path)
