"""Container health check for both services built from the one image.

yojana-api     (default CMD)          GET http://127.0.0.1:8000/readyz must return 200
yojana-worker  (python -m worker)     the worker heartbeat file must be fresh

The worker is detected from PID 1's command line (or YOJANA_PROCESS=worker), so the same image
works for both EasyPanel services without a second Dockerfile. Prints nothing secret.
"""
import os
import sys
import time
import urllib.request
from pathlib import Path


def is_worker() -> bool:
    if os.getenv('YOJANA_PROCESS', '').lower() == 'worker':
        return True
    try:
        cmdline = Path('/proc/1/cmdline').read_bytes().replace(b'\0', b' ')
    except OSError:
        return False
    return b'-m worker' in cmdline


def main() -> int:
    if is_worker():
        path = Path(os.getenv('WORKER_HEARTBEAT', str(Path(os.getenv('HOME') or '.') / 'worker.heartbeat')))
        limit = float(os.getenv('WORKER_HEARTBEAT_MAX_AGE', '900'))   # > the 600 s job lease
        try:
            age = time.time() - float(path.read_text(encoding='ascii'))
        except (OSError, ValueError):
            return 1
        return 0 if age < limit else 1
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=4) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == '__main__':
    sys.exit(main())
