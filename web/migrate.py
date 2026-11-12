"""python -m web.migrate [--purge]: run additive migrations or retention cleanup."""
import argparse
from .case_store import migrate, purge

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--purge', action='store_true')
    args = parser.parse_args()
    purge() if args.purge else migrate()
    print('Retention cleanup complete' if args.purge else 'Case schema ready')
