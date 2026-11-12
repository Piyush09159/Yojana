"""Copy reviewed existing static assets to the standalone frontend; --check verifies drift."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from web.frontend import decorate
SOURCE = ROOT / 'web/static'
TARGET = ROOT / 'frontend/public'


def sync(check=False):
    mapping = {SOURCE / 'landing.html': TARGET / 'index.html', SOURCE / 'chat.html': TARGET / 'app.html',
               SOURCE / 'index.html': TARGET / 'legacy.html', SOURCE / 'voice.html': TARGET / 'voice.html'}
    for path in SOURCE.iterdir():
        if path.suffix in {'.css', '.js', '.svg'}:
            mapping[path] = TARGET / 'static' / path.name
    changed = []
    for source, target in mapping.items():
        content = source.read_bytes()
        if target.name == 'app.html':
            content = content.replace(b'</body>', b'<script src="/static/deployment.js" defer></script></body>')
            content = decorate(content.decode('utf-8'),dashboard=True).encode('utf-8')
        elif target.name == 'index.html':
            content = decorate(content.decode('utf-8')).encode('utf-8')
        if target.exists() and target.read_bytes() == content:
            continue
        changed.append(target.relative_to(ROOT).as_posix())
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    if check and changed:
        raise SystemExit('Frontend assets out of sync: ' + ', '.join(changed))
    print(f'{len(mapping)} frontend assets ' + ('verified' if check else 'synchronized'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    sync(parser.parse_args().check)
