"""Delivery runner for confirmed WhatsApp results: PDF guide, call offer, follow-up calls.

Durable state lives in the citizen database (DATABASE_URL): result_deliveries and followup_calls
rows with leases, so a restart or a second runner never duplicates work it can detect. Two ways to run:

  * inside yojana-api (default): web/runtime.py starts a loop every DELIVERY_POLL_SECONDS (5) when
    DELIVERY_WORKER_ENABLED is not 'false' and at least one of the delivery flags is on;
  * separately: `python -m web.delivery_worker [--once]` with the same DATABASE_URL, SESSION_SIGNING_KEY,
    WAHA_*, PDF_SERVICE_* and BOLNA_* settings (then set DELIVERY_WORKER_ENABLED=false on the API).
The scheme discovery worker (`python -m worker`) is unrelated and does not need these settings.
"""
from __future__ import annotations

import argparse
import logging
import os
import time

from . import case_store

log = logging.getLogger('yojana.delivery')
_last = {'pass_at': None, 'ok': None}


def active():
    from . import followup_calls, result_review
    return case_store.enabled() and os.getenv('DELIVERY_WORKER_ENABLED', 'true').lower() != 'false' and (
        result_review.pdf_enabled() or followup_calls.enabled() or result_review.review_enabled())


def poll_seconds():
    try:
        return max(2, min(int(os.getenv('DELIVERY_POLL_SECONDS', '5')), 60))
    except ValueError:
        return 5


def run_once(now=None):
    """One bounded pass of every stage. Each stage failing is logged by code only."""
    from . import followup_calls, whatsapp_delivery
    done = {}
    ok = True
    for name, stage in (('pdf', whatsapp_delivery.run_pdf_pass), ('offer', whatsapp_delivery.run_offer_pass),
                        ('call', followup_calls.run_due)):
        try:
            done[name] = stage(now)
        except Exception as exc:
            ok = False
            log.error('delivery stage %s failed: %s', name, type(exc).__name__)
    _last.update(pass_at=time.time(), ok=ok)
    return done


def health():
    """No secrets, no counts of citizens: flags plus how long ago the runner last finished a pass."""
    from . import followup_calls, result_review
    age = None if _last['pass_at'] is None else round(time.time() - _last['pass_at'], 1)
    return {'review_gate': result_review.review_enabled(), 'pdf_send': result_review.pdf_enabled(),
            'followup_calls': followup_calls.enabled(), 'followup_configured': followup_calls.configured(),
            'runner_in_api': active(), 'last_pass_seconds_ago': age, 'last_pass_ok': _last['ok']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Deliver confirmed WhatsApp results (PDF, call offer, calls).')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    if not case_store.enabled():
        raise SystemExit('DATABASE_URL (citizen database) is required')
    case_store.migrate()
    while True:
        print(run_once())
        if args.once:
            return
        time.sleep(poll_seconds())


if __name__ == '__main__':
    main()
