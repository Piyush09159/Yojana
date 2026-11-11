"""Build-time (or volume-init) download of pinned model weights. Never called at request time.

    python -m stt_service.download_model small [base] --dir /models
"""
import argparse
from pathlib import Path

# Pinned Hugging Face revisions (checked 2026-11-11; MIT licence, not gated).
WHISPER = {
    'small': ('Systran/faster-whisper-small', '536b0662742c02347bc0e980a01041f333bce120'),
    'base': ('Systran/faster-whisper-base', 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'),
}
FILES = ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt']


def download(name, root):
    from huggingface_hub import snapshot_download
    repo, revision = WHISPER[name]
    target = Path(root) / f'faster-whisper-{name}'
    snapshot_download(repo, revision=revision, local_dir=str(target), allow_patterns=FILES)
    missing = [f for f in FILES if not (target / f).exists()]
    if missing:
        raise SystemExit(f'download incomplete: {missing}')
    print(f'{repo}@{revision[:12]} -> {target}')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('models', nargs='+', choices=sorted(WHISPER))
    parser.add_argument('--dir', default='/models')
    args = parser.parse_args(argv)
    for name in args.models:
        download(name, args.dir)


if __name__ == '__main__':
    main()
