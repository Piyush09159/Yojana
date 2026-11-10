"""One worker only. Copy the immutable bundled catalog into writable scratch space."""
import os
from pathlib import Path
import shutil
import sys

target = Path(os.getenv('SCHEME_DB', '/runtime/schemes.db'))
target.parent.mkdir(parents=True, exist_ok=True)
if not target.exists():
    shutil.copyfile('/app/catalog/schemes.db', target)
os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'web.app:app', '--host', '0.0.0.0',
                         '--port', '8000', '--workers', '1', '--limit-concurrency', '24',
                         '--timeout-keep-alive', '5', '--no-access-log'])
