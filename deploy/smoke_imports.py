"""Build-time smoke test (run in the Dockerfile): every module the api and the worker need imports,
and ffmpeg can decode WhatsApp voice notes (OGG container + Opus codec). No network, no secrets."""
import importlib
import os
import shutil
import subprocess
import sys

MODULES = [
    'web.app', 'web.waha_bridge', 'web.waha_media', 'web.speech', 'web.public_cache', 'web.diagnostics',
    'web.pdf_client', 'web.pdf_api', 'web.bolna', 'web.followup_calls', 'web.whatsapp_delivery',
    'web.result_review', 'web.delivery_worker',
    'saathi.orchestrator', 'saathi.short_answer', 'saathi.reply',
    'agents.reader', 'agents.replier', 'agents.scout', 'agents.extractor', 'agents.verifier', 'agents.runner',
    'worker.__main__', 'worker.jobs', 'worker.loop',
    'store.db', 'store.pg', 'store.pg_admin', 'store.runtime', 'store.sqlite',
    'discovery.discover', 'ingest.extract', 'llm.config',
    'psycopg', 'redis', 'httpx', 'fastapi', 'uvicorn',
]


def main() -> int:
    sys.path.insert(0, os.getcwd())          # run from the app root (/app in the image)
    failed = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:                     # report all, not just the first
            failed.append(f'{name}: {type(exc).__name__}: {exc}')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        failed.append('ffmpeg: not installed')
    else:
        demuxers = subprocess.run([ffmpeg, '-hide_banner', '-demuxers'], capture_output=True, text=True).stdout
        decoders = subprocess.run([ffmpeg, '-hide_banner', '-decoders'], capture_output=True, text=True).stdout
        if ' ogg ' not in demuxers:
            failed.append('ffmpeg: no ogg demuxer')
        if ' opus ' not in decoders and 'libopus' not in decoders:
            failed.append('ffmpeg: no opus decoder')
    for line in failed:
        print('SMOKE FAIL', line, file=sys.stderr)
    print('smoke imports ok' if not failed else f'{len(failed)} smoke check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
