"""Offline consent and targeted-notification regression checks."""
import sqlite3
import time
from pathlib import Path

from web import notifications


class Tx:
    def __init__(self, conn):
        self.conn = conn
    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)


def setup(monkeypatch):
    monkeypatch.setattr(notifications, 'seal', lambda text: 'enc:' + text)
    monkeypatch.setattr(notifications, 'unseal', lambda text: text[4:])
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    sql = (Path(__file__).resolve().parents[1] / 'web' / 'migrations' / '9998_scheme_alerts.sql').read_text()
    c.executescript(sql)
    return Tx(c)


def test_opt_in_opt_out_no_unsolicited(monkeypatch):
    tx = setup(monkeypatch)
    address = '12345678@lid'
    hash_ = 'test-hash'
    assert notifications.handle_command(tx, hash_, address, 'mr', 'hello') is None
    assert tx.execute('SELECT COUNT(*) AS n FROM scheme_alert_recipients').fetchone()['n'] == 0
    state, reply = notifications.handle_command(tx, hash_, address, 'mr', 'updates')
    assert state == 'alerts_on'
    assert 'सूचना' in reply
    notifications.update_profile(tx, hash_, address, {'state':'Maharashtra','district':'Beed'}, 'mr')
    row = tx.execute('SELECT * FROM scheme_alert_recipients').fetchone()
    assert row['state'] == 'maharashtra' and row['district'] == 'beed'
    status, _ = notifications.handle_command(tx, hash_, address, 'mr', 'updates off')
    assert status == 'alerts_off'
    assert tx.execute('SELECT COUNT(*) AS n FROM scheme_alert_recipients').fetchone()['n'] == 0


def test_region_filter_idempotency_and_send(monkeypatch):
    tx = setup(monkeypatch)
    for index, district in enumerate(['Beed','Pune']):
        chat = f'{index}0000000@lid'
        h = str(index)
        notifications.handle_command(tx,h,chat,'hi','updates')
        notifications.update_profile(tx,h,chat,{'state':'Maharashtra','district':district},'hi')
    kwargs = {'campaign':'mh-beed-20261111-v1','scheme_id':'verified_scheme',
              'state':'Maharashtra','district':'Beed','source_url':'https://example.gov.in/scheme',
              'approved':True,'body':{'en':'New scheme verified','hi':'नई योजना सत्यापित', 'mr':'नवीन योजना तपासली'}}
    assert notifications.prepare(tx,**kwargs) == 1
    assert notifications.prepare(tx,**kwargs) == 1
    assert tx.execute('SELECT COUNT(*) AS n FROM scheme_alert_outbox').fetchone()['n'] == 1
    delivered = []
    assert notifications.deliver_one(tx,campaign=kwargs['campaign'],sender=lambda c,t:delivered.append((c,t))) == 'sent'
    assert delivered[0][0] == '00000000@lid'
    assert notifications.deliver_one(tx,campaign=kwargs['campaign'],sender=lambda c,t:delivered.append((c,t))) == 'empty'


def test_requires_review_and_geography(monkeypatch):
    import pytest
    tx = setup(monkeypatch)
    kwargs = {'campaign':'test-campaign','scheme_id':'scheme','state':'Maharashtra','district':'Beed',
              'source_url':'https://example.gov.in','approved':False,
              'body':{'en':'English','hi':'Hindi','mr':'Marathi'}}
    with pytest.raises(ValueError): notifications.prepare(tx,**kwargs)
    with pytest.raises(ValueError): notifications.prepare(tx,**{**kwargs,'approved':True,'state':''})
    with pytest.raises(ValueError): notifications.prepare(tx,**{**kwargs,'approved':True,'source_url':'http://bad.test'})


def test_remove_after_stop_deletes_queued(monkeypatch):
    tx = setup(monkeypatch)
    notifications.handle_command(tx,'h','90000000@lid','en','updates')
    notifications.update_profile(tx,'h','90000000@lid',{'state':'Maharashtra','district':'Beed'},'en')
    notifications.prepare(tx,campaign='test-campaign',scheme_id='scheme',state='Maharashtra',district='Beed',
         source_url='https://example.gov.in',approved=True,body={'en':'A','hi':'B','mr':'C'})
    notifications.remove(tx,'h')
    assert tx.execute('SELECT COUNT(*) AS n FROM scheme_alert_outbox').fetchone()['n'] == 0
