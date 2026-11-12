"""Consent-only WhatsApp scheme alerts; no automated SMS or unverified discovery.

All recipient addresses are encrypted with the existing channel_security.seal.
PostgreSQL is recommended in production. The same minimal SQL works with the
case_store SQLite test adapter. No phone numbers are inferred from WAHA LIDs.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from urllib.parse import urlsplit

from . import case_store
from .channel_security import seal, unseal

UPDATES_ON = {'updates', 'notify', 'notifications', 'alerts', 'सूचना', 'अलर्ट', 'अपडेट', 'सूचना सुरू', 'अपडेट्स'}
UPDATES_OFF = {'updates off', 'no updates', 'alerts off', 'stop updates', 'सूचना बंद', 'अपडेट बंद'}
UPDATES_STATUS = {'updates status', 'alerts status', 'सूचना स्थिति', 'सूचना स्थिती'}

MESSAGES = {
    'en': {
        'on': 'Scheme alerts enabled. We will send relevant, source-linked updates only when a scheme is manually reviewed. Reply UPDATES OFF to stop notifications, or STOP to end Yojana Saathi completely.',
        'off': 'Scheme alerts disabled. Your conversation remains active. Reply UPDATES to subscribe again.',
        'status_on': 'Scheme alerts are ON. Reply UPDATES OFF to unsubscribe.',
        'status_off': 'Scheme alerts are OFF. Reply UPDATES to subscribe.',
        'help': 'Optional scheme updates: reply UPDATES to opt in. Reply UPDATES OFF any time to opt out. No promotional messages.',
    },
    'hi': {
        'on': 'योजनाओं की सूचनाएँ चालू हैं। केवल समीक्षा की गई, आधिकारिक लिंक वाली जानकारी भेजेंगे। बंद करने के लिए UPDATES OFF लिखें; सेवा छोड़ने के लिए STOP लिखें।',
        'off': 'योजनाओं की सूचनाएँ बंद कर दी गई हैं। बातचीत जारी रहेगी। फिर शुरू करने के लिए UPDATES लिखें।',
        'status_on': 'योजनाओं की सूचनाएँ चालू हैं। बंद करने के लिए UPDATES OFF लिखें।',
        'status_off': 'योजनाओं की सूचनाएँ बंद हैं। चालू करने के लिए UPDATES लिखें।',
        'help': 'योजना की नई जानकारी पाने के लिए UPDATES लिखें; बंद करने के लिए UPDATES OFF लिखें।',
    },
    'mr': {
        'on': 'योजनांच्या सूचना सुरू झाल्या आहेत. फक्त तपासलेल्या, अधिकृत दुव्यांसह माहिती पाठवू. बंद करण्यासाठी UPDATES OFF, सेवा सोडण्यासाठी STOP पाठवा.',
        'off': 'योजनांच्या सूचना बंद केल्या आहेत. संभाषण सुरू राहील. पुन्हा सुरू करण्यासाठी UPDATES पाठवा.',
        'status_on': 'योजनांच्या सूचना सुरू आहेत. बंद करण्यासाठी UPDATES OFF पाठवा.',
        'status_off': 'योजनांच्या सूचना बंद आहेत. सुरू करण्यासाठी UPDATES पाठवा.',
        'help': 'नवीन योजनांसाठी UPDATES पाठवा; बंद करण्यासाठी UPDATES OFF पाठवा.',
    },
}


def _value(v: object) -> str:
    if not isinstance(v, str):
        return ''
    value = re.sub(r'\s+', ' ', v.strip().casefold())
    return value[:100] if len(value) <= 100 else ''


def _region(profile: dict) -> tuple[str, str]:
    return _value(profile.get('state')), _value(profile.get('district'))


def _language(lang: str) -> str:
    return lang if lang in MESSAGES else 'en'


def option_notice(lang: str) -> str:
    return '\n\n' + MESSAGES[_language(lang)]['help']


def remove(tx, chat_hash: str):
    """Called in the same transaction as the original STOP revocation."""
    tx.execute('DELETE FROM scheme_alert_recipients WHERE chat_hash=?', (chat_hash,))
    tx.execute('DELETE FROM scheme_alert_outbox WHERE chat_hash=?', (chat_hash,))


def update_profile(tx, chat_hash: str, chat: str, profile: dict, lang: str):
    """Record only opted-in channel contacts; store only coarse location.

    Existing case owner data is not duplicated here.
    """
    state, district = _region(profile)
    if not state and not district:
        return
    tx.execute('''UPDATE scheme_alert_recipients
                  SET state=?,district=?,language=?,address_cipher=?,updated_at=?
                  WHERE chat_hash=?''',
               (state, district, _language(lang), seal(chat), time.time(), chat_hash))


def handle_command(tx, chat_hash: str, chat: str, lang: str, command: str):
    """Return (status, reply) for an explicit alert command or None."""
    lang = _language(lang)
    if command not in UPDATES_ON | UPDATES_OFF | UPDATES_STATUS:
        return None
    if command in UPDATES_ON:
        tx.execute('''INSERT INTO scheme_alert_recipients
          (chat_hash,address_cipher,state,district,language,subscribed_at,updated_at)
          VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(chat_hash) DO UPDATE SET address_cipher=excluded.address_cipher,
              language=excluded.language, updated_at=excluded.updated_at''',
          (chat_hash, seal(chat), '', '', lang, time.time(), time.time()))
        return 'alerts_on', MESSAGES[lang]['on']
    if command in UPDATES_OFF:
        remove(tx, chat_hash)
        return 'alerts_off', MESSAGES[lang]['off']
    enabled = bool(tx.execute('SELECT chat_hash FROM scheme_alert_recipients WHERE chat_hash=?',
                               (chat_hash,)).fetchone())
    return 'alerts_status', MESSAGES[lang]['status_on' if enabled else 'status_off']


def _source(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def prepare(tx, *, campaign: str, scheme_id: str, state: str, district: str,
            source_url: str, approved: bool, body: dict[str, str]) -> int:
    """Idempotently queue a human-approved campaign. No sending happens here."""
    if not approved or not _source(source_url):
        raise ValueError('Campaign requires human approval and a valid official HTTPS source')
    if not re.fullmatch(r'[A-Za-z0-9._-]{5,100}', campaign) or not re.fullmatch(r'[A-Za-z0-9._-]{2,100}', scheme_id):
        raise ValueError('Invalid campaign or scheme ID')
    if any(not isinstance(x, str) or len(x) > 900 for x in body.values()):
        raise ValueError('Invalid notification text')
    if not body or not body.get('en') or any(not body.get(k) for k in ('hi', 'mr')):
        raise ValueError('Reviewed campaigns require English, Hindi and Marathi text')
    state, district = _value(state), _value(district)
    if not state:
        raise ValueError('State is required; do not broadcast country-wide by accident')
    rows = tx.execute('''SELECT chat_hash,address_cipher,language,state,district FROM scheme_alert_recipients
                         WHERE state=?''', (state,)).fetchall()
    now = time.time()
    total = 0
    for row in rows:
        if district and row['district'] != district:
            continue
        if not row['district']:
            continue  # require a known region before targeted alerts
        lang = _language(row['language'])
        message = body[lang].strip() + '\n\n' + source_url + '\n\nUPDATES OFF: unsubscribe'
        if len(message) > 1200:
            raise ValueError('Campaign message exceeds 1200 characters')
        tx.execute('''INSERT INTO scheme_alert_outbox
          (campaign_id,chat_hash,scheme_id,text,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(campaign_id,chat_hash) DO NOTHING''',
          (campaign, row['chat_hash'], scheme_id, message, 'pending', now, now))
        total += 1
    return total


def deliver_one(tx, *, campaign: str, sender, min_interval: float = 1.2) -> str:
    """Deliver one pending notification while holding a DB transaction lock.

    Callers must serialize with tx.lock('alerts-send:' + campaign). All sends
    occur after consent re-check. Only one worker should dispatch at once.
    """
    row = tx.execute('''SELECT o.campaign_id,o.chat_hash,o.text,r.address_cipher
        FROM scheme_alert_outbox o JOIN scheme_alert_recipients r USING(chat_hash)
        WHERE o.campaign_id=? AND o.status='pending'
        ORDER BY o.created_at,o.chat_hash LIMIT 1''', (campaign,)).fetchone()
    if not row:
        return 'empty'
    # If WAHA accepts delivery and the DB transaction then fails, a rare duplicate
    # is possible. This is at-least-once delivery, never exactly-once.
    try:
        sender(unseal(row['address_cipher']), row['text'])
    except Exception:
        # Don't include phone/chat details in logs or exception messages.
        tx.execute("UPDATE scheme_alert_outbox SET updated_at=? WHERE campaign_id=? AND chat_hash=?",
                   (time.time(), campaign, row['chat_hash']))
        return 'retry'
    tx.execute('''UPDATE scheme_alert_outbox SET status='sent',updated_at=?
                  WHERE campaign_id=? AND chat_hash=?''',
               (time.time(), campaign, row['chat_hash']))
    return 'sent'
