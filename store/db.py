"""The scheme database. Two backends with the same functions:

    SQLite (default)   data/schemes.db, or the file in SCHEME_DB     store/sqlite.py
    PostgreSQL         when DATABASE_URL is set                      store/pg.py

The backend is chosen on every call, from the environment, so scripts and tests can switch
without re-importing anything. Callers keep using `from store import db`.

    load_catalog(state="maharashtra", district="beed")   -> {id: scheme_dict} for the engine
    save_scheme(scheme)                                  -> (id, "new" | "updated" | ...)
    mark_url(url, "scheme", sid) / url_record(url)       -> crawl bookkeeping
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from . import sqlite as _sqlite

ROOT = Path(__file__).resolve().parent.parent
# Read .env without overriding variables already set (tests set DATABASE_URL="" on purpose).
load_dotenv(ROOT / ".env", override=False)

DEFAULT_PATH = _sqlite.DEFAULT_PATH
DUPLICATE_SCORE = _sqlite.DUPLICATE_SCORE


def backend_name() -> str:
    return "postgres" if (os.getenv("DATABASE_URL") or "").strip() else "sqlite"


def _b():
    if backend_name() == "postgres":
        from . import pg
        return pg
    return _sqlite


def db_path():
    """Where the data lives, for display: a file path, or the Postgres URL without its password."""
    return _b().db_path()


def connect(path=None):
    return _b().connect(path)


def close() -> None:
    _sqlite.close()
    if backend_name() == "postgres":
        from . import pg
        pg.close()


def version() -> int:
    return _b().version()


def load_catalog(state: str | None = None, district: str | None = None,
                 include_discovered: bool = True, path=None) -> dict[str, dict]:
    return _b().load_catalog(state, district, include_discovered, path)


def get_scheme(scheme_id: str, path=None) -> dict | None:
    return _b().get_scheme(scheme_id, path)


def count(path=None) -> int:
    return _b().count(path)


def stats(path=None) -> dict:
    return _b().stats(path)


def find_duplicate(scheme: dict, path=None) -> str | None:
    return _b().find_duplicate(scheme, path)


def save_scheme(scheme: dict, path=None) -> tuple[str, str]:
    return _b().save_scheme(scheme, path)


def set_verified(scheme_id: str, verified: bool = True, path=None) -> bool:
    return _b().set_verified(scheme_id, verified, path)


def delete_scheme(scheme_id: str, path=None) -> bool:
    return _b().delete_scheme(scheme_id, path)


def queue_urls(urls: list[tuple[str, str]], path=None) -> int:
    return _b().queue_urls(urls, path)


def queued_urls(limit: int | None = None, path=None) -> list[dict]:
    return _b().queued_urls(limit, path)


def queued_count(path=None) -> int:
    return _b().queued_count(path)


def mark_url(url: str, status: str, scheme_id: str | None = None, note: str = "", path=None) -> None:
    return _b().mark_url(url, status, scheme_id, note, path)


def url_record(url: str, path=None) -> dict | None:
    return _b().url_record(url, path)


def seen_urls(path=None) -> set[str]:
    return _b().seen_urls(path)
