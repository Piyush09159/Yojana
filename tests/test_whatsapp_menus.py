"""WhatsApp menus: language list, main menu, numbered-text fallback, stable option ids, WAHA NOWEB
list replies (selectedRowId), commands at any point, and an assessment that is never restarted.

Payload shape of a NOWEB list reply, from WAHA's source (src/core/engines/noweb/session.noweb.core.ts,
extractBody + toWAMessage): `body` is the selected row's title (and description, newline-joined) and
`_data.message.listResponseMessage.singleSelectReply.selectedRowId` is the row id we sent."""
import json
import logging
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from web import case_store, waha_bridge as waha, whatsapp_menu as menu
from web.app import create_app
from test_deployment_v24 import persistent, configure_waha, signed, mock_extraction  # noqa: F401
from test_voice_pipeline import post

CHAT = '900000000000@c.us'


@pytest.fixture
def wa(persistent, monkeypatch):  # noqa: F811
    configure_waha(monkeypatch)
    sender, lister = Mock(), Mock()
    monkeypatch.setattr(waha, '_send', sender)
    monkeypatch.setattr(waha, '_send_list', lister)
    return TestClient(create_app()), sender, lister


def list_reply(client, row_id, title, mid, wrap=None):
    message = {'listResponseMessage': {'title': title, 'listType': 1,
                                       'singleSelectReply': {'selectedRowId': row_id}}}
    if wrap:
        message = {wrap: {'message': message}}
    return post(client, {'from': CHAT, 'id': mid, 'body': title, 'fromMe': False, 'hasMedia': False,
                         '_data': {'key': {'id': mid}, 'message': message}})


def last_text(sender):
    return sender.call_args.args[-1]


def case_state():
    with case_store.transaction() as tx:
        sid = tx.execute('SELECT session_id FROM waha_chats').fetchone()['session_id']
        return sid, tx.session(sid, trusted=True)['state']


def menu_row():
    with case_store.transaction() as tx:
        row = tx.execute('SELECT * FROM channel_menus').fetchone()
        return dict(row) if row else None


# ---------- activation and the language menu ----------

def test_activation_sends_trilingual_numbered_language_menu(wa):
    client, sender, lister = wa
    assert signed(client, 'YOJANA', 'a1').json()['status'] == 'started'
    text = last_text(sender)
    for line in ('1. English', '2. हिंदी (Hindi)', '3. मराठी (Marathi)', 'Reply 1, 2 or 3', 'STOP'):
        assert line in text
    lister.assert_not_called()                           # lists are opt-in (WAHA_INTERACTIVE_LISTS)
    assert menu_row()['menu'] == 'language'


def test_interactive_list_uses_stable_row_ids(wa, monkeypatch):
    client, sender, lister = wa
    monkeypatch.setenv('WAHA_INTERACTIVE_LISTS', 'true')
    signed(client, 'YOJANA', 'a1')
    sender.assert_not_called()
    url, key, session, chat, message = lister.call_args.args
    assert url == 'http://waha:3000' and chat == CHAT and session == 'default'
    assert [r['rowId'] for r in message['sections'][0]['rows']] == ['lang:en', 'lang:hi', 'lang:mr']
    assert message['button'] and message['title']
    assert list_reply(client, 'lang:mr', 'मराठी (Marathi)', 'r1').json()['status'] == 'language_selected'
    message = lister.call_args.args[-1]
    assert [r['rowId'] for r in message['sections'][0]['rows']] == list(menu.MAIN_IDS)
    assert message['sections'][0]['rows'][1]['title'] == 'ब्राउझर व्हॉइस कॉल'


def test_list_failure_falls_back_to_numbered_text(wa, monkeypatch, caplog):
    client, sender, lister = wa
    monkeypatch.setenv('WAHA_INTERACTIVE_LISTS', 'true')
    lister.side_effect = httpx.HTTPStatusError('422', request=httpx.Request('POST', 'http://waha:3000/api/sendList'),
                                               response=httpx.Response(422))
    with caplog.at_level(logging.INFO, logger='yojana.diagnostics'):
        assert signed(client, 'YOJANA', 'a1').json()['status'] == 'started'
    assert '1. English' in last_text(sender)
    assert 'WAHA_LIST_FALLBACK' in caplog.text and '⁣' not in last_text(sender)


def test_api_level_list_request_shape(monkeypatch):
    seen = {}
    monkeypatch.setattr(waha.httpx, 'post', lambda url, **kw: seen.update(url=url, **kw) or httpx.Response(
        201, request=httpx.Request('POST', url)))
    waha._send_list('http://waha:3000', 'k', 'default', CHAT, menu.list_message('main', 'hi'))
    assert seen['url'] == 'http://waha:3000/api/sendList' and seen['headers'] == {'X-Api-Key': 'k'}
    body = seen['json']
    assert body['session'] == 'default' and body['chatId'] == CHAT
    assert {'title', 'description', 'footer', 'button', 'sections'} <= set(body['message'])


# ---------- choosing: row id, row title, number, words ----------

@pytest.mark.parametrize('wrap', [None, 'ephemeralMessage'])
def test_list_reply_row_id_selects_language_then_main_menu(wa, wrap):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a1')
    assert list_reply(client, 'lang:hi', 'हिंदी (Hindi)', 'r1', wrap).json()['status'] == 'language_selected'
    assert '1. *चैट*' in last_text(sender) and 'ठीक है' in last_text(sender)
    assert menu_row()['preferred_language'] == 'hi' and menu_row()['menu'] == 'main'


def test_row_title_in_body_works_without_data(wa, monkeypatch):
    client, sender, _ = wa
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN', 'https://demo.invalid')
    signed(client, 'YOJANA', 'a1')
    assert signed(client, 'मराठी (Marathi)', 'title-only').json()['status'] == 'language_selected'
    assert menu_row()['preferred_language'] == 'mr'
    assert signed(client, 'ब्राउझर व्हॉइस कॉल\nखाजगी ब्राउझर पेजवर बोला', 'title2').json()['status'] == 'voice_link'


def test_unknown_row_id_is_ignored_and_title_used(wa):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a1')
    assert list_reply(client, 'lang:xx', 'English', 'bad-row').json()['status'] == 'language_selected'
    assert menu_row()['preferred_language'] == 'en'


@pytest.mark.parametrize('number,lang', [('1', 'en'), ('2', 'hi'), ('3', 'mr')])
def test_numbered_language_choice(wa, number, lang):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a1')
    assert signed(client, number, 'n1').json()['status'] == 'language_selected'
    assert menu_row()['preferred_language'] == lang
    assert '4. *' in last_text(sender)                   # main menu follows in that language


@pytest.mark.parametrize('choice,status', [('1', 'chat_selected'), ('3', 'alerts_on'), ('4', 'language_menu')])
def test_numbered_main_menu(wa, choice, status):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a1'); signed(client, '2', 'n1')
    assert signed(client, choice, 'n2').json()['status'] == status


def test_main_menu_call_gives_one_time_link(wa, monkeypatch):
    client, sender, _ = wa
    monkeypatch.setenv('PUBLIC_FRONTEND_ORIGIN', 'https://demo.invalid')
    signed(client, 'YOJANA', 'a1'); signed(client, '3', 'n1')
    assert signed(client, '2', 'n2').json()['status'] == 'voice_link'
    assert '#handoff=' in last_text(sender) and 'लिंक' in last_text(sender)
    with case_store.transaction() as tx:          # the link is sealed at rest
        assert '#handoff=' not in tx.execute("SELECT reply FROM waha_events WHERE result='voice_link'").fetchone()['reply']


@pytest.mark.parametrize('word,status', [
    ('Chat', 'chat_selected'), ('चॅट', 'chat_selected'), ('Change language', 'language_menu'),
    ('भाषा', 'language_menu'), ('LANGUAGE', 'language_menu'), ('Hindi', 'language_selected'),
    ('मराठीत बोला', 'language_selected'), ('speak in English', 'language_selected'),
    ('MENU', 'main_menu'), ('मेनू', 'main_menu'), ('HELP', 'help'), ('मदत', 'help')])
def test_words_work_in_any_language_without_open_menu(wa, monkeypatch, word, status):
    client, sender, _ = wa
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a1'); signed(client, 'I am 62', 'first')   # menu closed by a normal message
    assert signed(client, word, 'w1').json()['status'] == status


def test_invalid_choice_repeats_menu_and_keeps_waiting(wa):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a1')
    assert signed(client, '7', 'bad').json()['status'] == 'invalid_choice'
    assert 'Please reply with one of the numbers' in last_text(sender) and '3. मराठी' in last_text(sender)
    assert menu_row()['menu'] == 'language'
    assert signed(client, '3', 'good').json()['status'] == 'language_selected'
    signed(client, '0', 'bad2')
    assert 'कृपया दाखवलेल्या' in last_text(sender) and '4. *' in last_text(sender)


def test_normal_message_while_menu_open_is_handled_and_closes_menu(wa, monkeypatch):
    client, sender, _ = wa
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a1')
    assert signed(client, 'I am 62 and need a pension', 'm1').json()['status'] == 'replied'
    assert menu_row()['menu'] is None
    mock_extraction(monkeypatch)
    assert signed(client, '2', 'answer').json()['status'] == 'replied'     # now data, not a menu choice


def test_number_after_menu_expired_is_data(wa, monkeypatch):
    client, sender, _ = wa
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a1')
    with case_store.transaction() as tx:
        tx.execute('UPDATE channel_menus SET menu_at=?', (1.0,))
    assert signed(client, '2', 'late').json()['status'] == 'replied'


def test_voice_note_is_never_blocked_by_an_open_menu(wa, monkeypatch):
    from web import waha_media as media
    from test_whatsapp_final import note
    client, sender, _ = wa
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a1')
    monkeypatch.setattr(media, 'transcribe', Mock(return_value={'text': 'I need a pension', 'language': 'en'}))
    assert waha.persistent_event(note('v1'))['status'] == 'replied'


# ---------- the assessment survives menus ----------

def test_menu_and_language_during_assessment_keep_case_and_question(wa, monkeypatch):
    client, sender, _ = wa
    mock_extraction(monkeypatch, {'age': 62, 'state': 'maharashtra'})
    signed(client, 'YOJANA', 'a1'); signed(client, '1', 'en')
    signed(client, 'I am 62 in Maharashtra', 'facts')
    sid, before = case_state()
    assert before['pending'], 'fixture catalog should leave a question pending'
    assert signed(client, 'MENU', 'menu').json()['status'] == 'main_menu'
    assert signed(client, 'YOJANA', 'again').json()['status'] == 'started'
    assert signed(client, '3', 'marathi').json()['status'] == 'language_selected'
    text = last_text(sender)
    assert 'ठीक आहे' in text and 'Main' not in text and '4. *' not in text   # re-asks the question, not the menu
    sid_after, after = case_state()
    assert sid_after == sid and after['profile'] == before['profile'] and after['pending'] == before['pending']
    assert after['asked'] == before['asked'] and after['lang'] == 'mr' and after['preferred_lang'] == 'mr'
    assert signed(client, 'Chat', 'chat').json()['status'] == 'chat_selected'
    assert 'मागील प्रश्न' in last_text(sender)


def test_updates_option_is_separate_consent(wa):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a1'); signed(client, '1', 'en')
    assert signed(client, '3', 'updates').json()['status'] == 'alerts_on'
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM scheme_alert_recipients').fetchone()['n'] == 1
        assert tx.execute('SELECT mode FROM channel_preferences').fetchone()['mode'] == 'awaiting'  # chat/call untouched
    assert signed(client, 'UPDATES OFF', 'off').json()['status'] == 'alerts_off'
    with case_store.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM waha_chats WHERE opted_in=1').fetchone()['n'] == 1


def test_stop_during_menu_clears_everything(wa, monkeypatch):
    client, sender, _ = wa
    mock_extraction(monkeypatch)
    signed(client, 'YOJANA', 'a1'); signed(client, '2', 'hi'); signed(client, 'मेरी उम्र 62 है', 'facts')
    assert signed(client, 'STOP', 'stop').json()['status'] == 'stopped'
    with case_store.transaction() as tx:
        for table in ('channel_menus', 'waha_chats', 'case_sessions', 'channel_languages'):
            assert tx.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'] == 0
    assert signed(client, '1', 'after-stop').json()['status'] == 'not_opted_in'


def test_duplicate_list_reply_event_sends_once(wa):
    client, sender, _ = wa
    signed(client, 'YOJANA', 'a1')
    sender.reset_mock()
    assert list_reply(client, 'lang:en', 'English', 'dup').json()['status'] == 'language_selected'
    assert list_reply(client, 'lang:en', 'English', 'dup').json()['status'] == 'duplicate'
    assert sender.call_count == 1


def test_list_replies_and_menus_before_opt_in_are_silent(wa):
    client, sender, _ = wa
    assert list_reply(client, 'lang:hi', 'हिंदी (Hindi)', 'x').json()['status'] == 'not_opted_in'
    assert signed(client, 'MENU', 'y').json()['status'] == 'not_opted_in'
    sender.assert_not_called()


def test_choice_parser_unit():
    assert menu.choice('2', 'language') == 'lang:hi'
    assert menu.choice('2', 'main') == 'menu:call'
    assert menu.choice('2', None) is None
    assert menu.choice('9', 'main') == 'invalid'
    assert menu.choice(' *Chat* ', None) == 'menu:chat'
    assert menu.choice('I want to chat about pension', None) is None
    assert menu.choice('anything', None, row_id='menu:updates') == 'menu:updates'
    assert menu.selected_row_id({'_data': {'message': {'listResponseMessage': {'singleSelectReply': {'selectedRowId': 'evil'}}}}}) is None
    assert menu.menu_open({'menu': 'main', 'menu_at': 0.0}) is None


def test_menu_logs_carry_no_phone_or_text(wa, monkeypatch, caplog):
    client, sender, lister = wa
    monkeypatch.setenv('WAHA_INTERACTIVE_LISTS', 'true')
    with caplog.at_level(logging.DEBUG):
        signed(client, 'YOJANA', 'a1'); list_reply(client, 'lang:mr', 'मराठी (Marathi)', 'r1')
        signed(client, 'HELP', 'h')
    assert '900000000000' not in caplog.text and 'मराठी' not in caplog.text and 'WAHA_LIST_SENT' in caplog.text
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == 'yojana.diagnostics']
    assert all(set(r) <= {'code', 'event', 'source', 'mimetype', 'engine_has_body', 'result', 'menu', 'detail', 'status'}
               for r in records)
