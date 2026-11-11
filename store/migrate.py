"""Move schemes between YAML files and the database, and dump the database for review.

    python -m store migrate            YAML scheme files -> data/schemes.db (run once)
    python -m store export [dir]       database -> readable YAML (backup, diffs, review)
    python -m store stats              what the database holds
    python -m store verify <id>        mark a scheme as checked by a person
    python -m store pg-init            PostgreSQL: create the schema and seed the geography
    python -m store pg-import [file]   PostgreSQL: copy schemes and crawl records from SQLite
    python -m store quality            PostgreSQL: quality scores, levels, targets, topics

The backend is SQLite unless DATABASE_URL is set (see store/db.py).

Only scheme files move. schemes/fields.yaml, questions.yaml and flagship.yaml are vocabulary
and configuration, not schemes, and stay as files.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

from core.loader import load_scheme_dir

from . import db

ROOT = Path(__file__).resolve().parent.parent
CURATED_DIR = ROOT / "schemes"
DISCOVERED_DIR = ROOT / "schemes" / "discovered"
EXPORT_DIR = ROOT / "data" / "export"


def import_dir(folder: Path, origin: str, path=None, log=print) -> int:
    if not folder.exists():
        return 0
    schemes = load_scheme_dir(folder, skip_invalid=True)
    n = 0
    for scheme in schemes.values():
        scheme.setdefault("origin", origin)
        if origin == "curated":
            scheme["verified"] = True
        _, status = db.save_scheme(scheme, path)
        if status in ("new", "updated"):
            n += 1
    log(f"  {folder.name or folder}: {n} of {len(schemes)} scheme(s) written")
    return n


def migrate(path=None, log=print) -> int:
    log(f"Importing YAML schemes into {db.db_path()}")
    n = import_dir(CURATED_DIR, "curated", path, log)
    n += import_dir(DISCOVERED_DIR, "discovered", path, log)
    log(f"Done: {db.count(path)} schemes in the database")
    return n


def export(folder: Path = EXPORT_DIR, path=None, log=print) -> int:
    """Write every scheme back to YAML so a person can read, diff or back it up."""
    folder.mkdir(parents=True, exist_ok=True)
    catalog = db.load_catalog(path=path)
    for sid, scheme in catalog.items():
        header = (f"# {scheme.get('source_url', '')}\n"
                  f"# verified: {scheme.get('verified')} | exported from the database\n")
        (folder / f"{sid}.yaml").write_text(
            header + yaml.safe_dump(scheme, allow_unicode=True, sort_keys=False, width=110),
            encoding="utf-8")
    log(f"Exported {len(catalog)} schemes to {folder}")
    return len(catalog)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "stats"
    if cmd == "migrate":
        migrate()
    elif cmd == "export":
        export(Path(argv[1]) if len(argv) > 1 else EXPORT_DIR)
    elif cmd == "verify" and len(argv) > 1:
        ok = db.set_verified(argv[1], True)
        print(f"{argv[1]}: {'marked verified' if ok else 'not found'}")
    elif cmd in ("pg-init", "pg-import", "quality"):
        if db.backend_name() != "postgres":
            print("Set DATABASE_URL in .env first (postgresql://user:password@localhost:5432/yojana).")
            return 2
        from . import pg_admin
        print(f"Database: {db.db_path()}")
        if cmd == "pg-init":
            pg_admin.init_schema()
        elif cmd == "pg-import":
            pg_admin.import_from_sqlite(argv[1] if len(argv) > 1 else None)
        else:
            pg_admin.quality_report()
    elif cmd == "stats":
        s = db.stats()
        print(f"Database ({db.backend_name()}): {db.db_path()}")
        print(f"{s['schemes']} schemes ({s['by_origin']}), {s['rules']} rules, "
              f"{s['verified']} checked by a person, {s['districts']} districts")
        print("By state:", ", ".join(f"{k} {v}" for k, v in s["by_state"].items()))
        if s["urls"]:
            print("Pages seen:", ", ".join(f"{k} {v}" for k, v in s["urls"].items()))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
