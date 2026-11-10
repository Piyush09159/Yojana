"""Run a manually approved, source-linked WhatsApp campaign (not SMS).

Requires existing production DATABASE_URL, WAHA_* environment variables.
This CLI deliberately has no bypass of the citizen's UPDATES opt-in.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web import case_store, notifications


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True, help='Unique revision ID, e.g. beed-scholarship-20261111-v1')
    parser.add_argument('--scheme-id', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--district', default='', help='Optional district filter')
    parser.add_argument('--source-url', required=True, help='Reviewed official HTTPS scheme URL')
    parser.add_argument('--messages', required=True, type=Path, help='JSON object with en/hi/mr message texts')
    parser.add_argument('--reviewed', action='store_true', help='Human confirms accuracy and official source')
    parser.add_argument('--send', action='store_true', help='Omit to run queue-only dry run')
    parser.add_argument('--max-send', type=int, default=20, help='One-run sending cap (1-100)')
    args = parser.parse_args()
    if not 1 <= args.max_send <= 100:
        parser.error('--max-send must be between 1 and 100')
    if not case_store.enabled():
        parser.error('DATABASE_URL required')
    text = json.loads(args.messages.read_text(encoding='utf-8'))
    if not isinstance(text, dict):
        parser.error('Messages must be JSON object')
    with case_store.transaction('alerts-prepare:' + args.campaign) as tx:
        size = notifications.prepare(tx, campaign=args.campaign, scheme_id=args.scheme_id,
           state=args.state,district=args.district,source_url=args.source_url,
           approved=args.reviewed,body=text)
    print('Eligible recipients matched:', size, '| Campaign:', args.campaign)
    if not args.send:
        print('Queued only. No WhatsApp messages sent; re-run with --send to dispatch.')
        return
    url = os.environ['WAHA_BASE_URL'].rstrip('/')
    key = os.environ['WAHA_API_KEY']
    session = os.environ['WAHA_SESSION']
    if not url.startswith('https://'):
        parser.error('WAHA_BASE_URL must be HTTPS for this sender')

    def sender(chat, message):
        response = httpx.post(url + '/api/sendText',headers={'X-Api-Key':key},
            json={'session':session,'chatId':chat,'text':message},timeout=12)
        response.raise_for_status()

    sent = 0
    for _ in range(args.max_send):
        with case_store.transaction('alerts-send:' + args.campaign) as tx:
            status = notifications.deliver_one(tx,campaign=args.campaign,sender=sender)
        if status == 'empty':
            break
        if status == 'retry':
            print('Delivery rejected or unavailable. Stop; retry later with same campaign ID.')
            sys.exit(2)
        sent += 1
        time.sleep(1.2)
    print('Sent and recorded:',sent)
    print('Pending sends can be continued with same campaign ID and --send.')


if __name__ == '__main__':
    main()
