"""WhatsApp menus for Yojana Saathi: language choice, main menu, help.

Every option has a stable internal id (`lang:hi`, `menu:call`). A choice is recognised from:
  1. the row id of an interactive list reply (WAHA NOWEB: _data.message.listResponseMessage
     .singleSelectReply.selectedRowId), checked first;
  2. the row title WAHA puts in `body` for a list reply (first line), in any of the three languages;
  3. a number, but only while that menu is the last thing we sent (`menu` state), so "2" in answer
     to "How many acres?" stays an answer;
  4. words in any of the three languages ("chat", "कॉल", "भाषा बदला").
Display labels are never compared to decide anything else.

Interactive lists are sent with WAHA `POST /api/sendList` when WAHA_INTERACTIVE_LISTS=true. WAHA
calls list messages fragile, so any send failure falls back to the numbered text menu, which is
also the default until a real phone has shown the list (see docs/WHATSAPP_MENUS.md).
"""
from __future__ import annotations

import os
import time

from core.language_detect import clean

MENU_TTL = 3600          # a number is read as a menu choice for one hour after the menu was sent

LANGUAGE_IDS = ('lang:en', 'lang:hi', 'lang:mr')
MAIN_IDS = ('menu:chat', 'menu:call', 'menu:updates', 'menu:language')

LANGUAGE_ROWS = {'lang:en': 'English', 'lang:hi': 'हिंदी (Hindi)', 'lang:mr': 'मराठी (Marathi)'}

MAIN_ROWS = {
    'en': {'menu:chat': ('Chat', 'Type or send voice notes (up to 15 seconds)'),
           'menu:call': ('Browser voice call', 'Talk in a private browser page'),
           'menu:updates': ('Scheme updates', 'Optional alerts about reviewed schemes'),
           'menu:language': ('Change language', 'English, हिंदी, मराठी')},
    'hi': {'menu:chat': ('चैट', 'लिखें या वॉइस नोट भेजें (15 सेकंड तक)'),
           'menu:call': ('ब्राउज़र वॉइस कॉल', 'निजी ब्राउज़र पेज पर बात करें'),
           'menu:updates': ('योजना अपडेट', 'समीक्षा की गई योजनाओं की वैकल्पिक सूचना'),
           'menu:language': ('भाषा बदलें', 'English, हिंदी, मराठी')},
    'mr': {'menu:chat': ('चॅट', 'लिहा किंवा व्हॉइस नोट पाठवा (15 सेकंदांपर्यंत)'),
           'menu:call': ('ब्राउझर व्हॉइस कॉल', 'खाजगी ब्राउझर पेजवर बोला'),
           'menu:updates': ('योजना अपडेट्स', 'तपासलेल्या योजनांच्या ऐच्छिक सूचना'),
           'menu:language': ('भाषा बदला', 'English, हिंदी, मराठी')},
}

# Words that choose an option at any time (in addition to numbers while the menu is open).
WORDS = {
    'lang:en': {'english', 'eng', 'इंग्लिश', 'इंग्रजी', 'अंग्रेज़ी', 'अंग्रेजी'},
    'lang:hi': {'hindi', 'हिंदी', 'हिन्दी', 'हिंदी hindi', 'हिंदी (hindi)'},
    'lang:mr': {'marathi', 'मराठी', 'मराठी marathi', 'मराठी (marathi)'},
    'menu:chat': {'chat', 'चैट', 'चॅट', 'गप्पा'},
    'menu:call': {'call', 'कॉल', 'बोलणे', 'browser voice call', 'voice call', 'browser call',
                  'ब्राउज़र वॉइस कॉल', 'ब्राउझर व्हॉइस कॉल', 'वॉइस कॉल', 'व्हॉइस कॉल'},
    'menu:updates': {'scheme updates', 'योजना अपडेट', 'योजना अपडेट्स'},
    'menu:language': {'change language', 'language', 'lang', 'languages', 'भाषा', 'भाषा बदलें', 'भाषा बदला',
                      'भाषा बदलो', 'bhasha', 'bhasha badlo'},
}
_WORD_TO_ID = {word: option for option, words in WORDS.items() for word in words}
for _lang, _rows in MAIN_ROWS.items():
    for _option, (_title, _) in _rows.items():
        _WORD_TO_ID.setdefault(clean(_title), _option)
for _option, _title in LANGUAGE_ROWS.items():
    _WORD_TO_ID.setdefault(clean(_title), _option)

MENU = {'menu', 'main menu', 'options', 'मेनू', 'मेन्यू', 'मेनु', 'पर्याय', 'विकल्प', 'menu please'}
HELP = {'help', 'मदद', 'सहायता', 'मदत', 'help me', '?'}
YES = {'yes', 'y', 'ok', 'okay', 'correct', 'right', 'haan', 'han', 'ha', 'ho', 'hoy', 'हाँ', 'हां', 'हा', 'जी',
       'हो', 'होय', 'बरोबर', 'सही', 'ठीक', 'ठीक है', 'ठीक आहे'}
NO = {'no', 'n', 'wrong', 'nahi', 'nahin', 'नहीं', 'नही', 'नाही', 'गलत', 'चूक', 'चुकीचे'}


def interactive_enabled():
    return os.getenv('WAHA_INTERACTIVE_LISTS', 'false').strip().lower() == 'true'


def selected_row_id(payload):
    """The list row id from a NOWEB list reply, or None. Tolerates the ephemeral/view-once wrappers."""
    data = payload.get('_data') if isinstance(payload, dict) else None
    message = data.get('message') if isinstance(data, dict) else None
    for _ in range(4):
        if not isinstance(message, dict):
            return None
        reply = message.get('listResponseMessage')
        if isinstance(reply, dict):
            single = reply.get('singleSelectReply') or {}
            row = single.get('selectedRowId') if isinstance(single, dict) else None
            return row if isinstance(row, str) and row in LANGUAGE_IDS + MAIN_IDS else None
        inner = None
        for wrapper in ('ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2', 'documentWithCaptionMessage'):
            if isinstance(message.get(wrapper), dict):
                inner = message[wrapper].get('message')
                break
        message = inner
    return None


def choice(text, open_menu=None, row_id=None):
    """The option id this message selects, or None. `open_menu` is 'language' or 'main' when that
    menu is still awaiting an answer; only then is a bare number a menu choice."""
    if row_id:
        return row_id
    first_line = (text or '').split('\n', 1)[0]
    value = clean(first_line)
    if not value:
        return None
    if value.isdigit() and open_menu in ('language', 'main'):
        options = LANGUAGE_IDS if open_menu == 'language' else MAIN_IDS
        index = int(value)
        return options[index - 1] if 1 <= index <= len(options) else 'invalid'
    if '\n' in (text or '') and value not in _WORD_TO_ID:
        return None          # a multi-line message is a message, unless its first line is a row title
    return _WORD_TO_ID.get(value)


def menu_open(row, now=None):
    """The menu awaiting a numeric answer for this chat ('language'/'main') or None."""
    if not row or not row['menu'] or not row['menu_at']:
        return None
    return row['menu'] if (now or time.time()) - row['menu_at'] <= MENU_TTL else None


# ---------- text ----------

WELCOME = ('🙏 Welcome to Yojana Saathi · योजना साथी में आपका स्वागत है · योजना साथीमध्ये आपले स्वागत आहे\n\n'
           'Choose your language · अपनी भाषा चुनें · तुमची भाषा निवडा:')
SAFETY = {
    'en': 'Never send Aadhaar, bank or identity numbers. Independent guidance; not a government service.',
    'hi': 'आधार, बैंक या पहचान नंबर कभी न भेजें। यह स्वतंत्र मार्गदर्शन है, सरकारी सेवा नहीं।',
    'mr': 'आधार, बँक किंवा ओळख क्रमांक कधीही पाठवू नका. हे स्वतंत्र मार्गदर्शन आहे, सरकारी सेवा नाही.',
}


def language_menu_text(welcome=True):
    lines = [WELCOME if welcome else 'Choose your language · अपनी भाषा चुनें · तुमची भाषा निवडा:', '']
    lines += [f'{i}. {LANGUAGE_ROWS[option]}' for i, option in enumerate(LANGUAGE_IDS, 1)]
    lines += ['', 'Reply 1, 2 or 3 · 1, 2 या 3 लिखें · 1, 2 किंवा 3 पाठवा', '', 'STOP: unsubscribe · बंद करें · थांबवा']
    return '\n'.join(lines)


MAIN_LEAD = {
    'en': 'How would you like to continue? You can also just type your question or send a voice note.',
    'hi': 'आप कैसे आगे बढ़ना चाहेंगे? आप सीधे अपना सवाल लिख सकते हैं या वॉइस नोट भेज सकते हैं।',
    'mr': 'पुढे कसे जायचे? तुम्ही थेट प्रश्न लिहू शकता किंवा व्हॉइस नोट पाठवू शकता.',
}
MAIN_FOOT = {
    'en': 'Reply with a number. MENU, LANGUAGE, HELP and STOP work at any time.',
    'hi': 'नंबर लिखकर जवाब दें। MENU, भाषा, HELP और STOP कभी भी काम करते हैं।',
    'mr': 'क्रमांक पाठवा. MENU, भाषा, HELP आणि STOP कधीही वापरता येतात.',
}


def main_menu_text(lang):
    rows = MAIN_ROWS[lang]
    lines = [MAIN_LEAD[lang], '']
    lines += [f'{i}. *{rows[option][0]}* — {rows[option][1]}' for i, option in enumerate(MAIN_IDS, 1)]
    lines += ['', MAIN_FOOT[lang], SAFETY[lang]]
    return '\n'.join(lines)


HELP_TEXT = {
    'en': ('Yojana Saathi helps you find government schemes and what you still need to apply.\n\n'
           '• Type your situation, or send a voice note (up to 15 seconds), in English, हिंदी or मराठी.\n'
           '• MENU: options (chat, browser voice call, scheme updates, language)\n'
           '• LANGUAGE: change language at any time; your answers are kept\n'
           '• UPDATES / UPDATES OFF: optional scheme alerts\n'
           '• STOP: leave and delete this chat\'s case'),
    'hi': ('योजना साथी सरकारी योजनाएँ खोजने और आवेदन के लिए बाकी ज़रूरी चीज़ें बताने में मदद करता है।\n\n'
           '• अपनी स्थिति लिखें या वॉइस नोट भेजें (15 सेकंड तक) — English, हिंदी या मराठी में।\n'
           '• MENU: विकल्प (चैट, ब्राउज़र वॉइस कॉल, योजना अपडेट, भाषा)\n'
           '• भाषा: कभी भी भाषा बदलें; आपके जवाब सुरक्षित रहेंगे\n'
           '• UPDATES / UPDATES OFF: योजना सूचनाएँ चालू/बंद\n'
           '• STOP: सेवा छोड़ें और यह केस मिटाएँ'),
    'mr': ('योजना साथी सरकारी योजना शोधण्यासाठी आणि अर्जासाठी अजून काय लागेल ते सांगण्यासाठी मदत करतो.\n\n'
           '• तुमची परिस्थिती लिहा किंवा व्हॉइस नोट पाठवा (15 सेकंदांपर्यंत) — English, हिंदी किंवा मराठीत.\n'
           '• MENU: पर्याय (चॅट, ब्राउझर व्हॉइस कॉल, योजना अपडेट्स, भाषा)\n'
           '• भाषा: कधीही भाषा बदला; तुमची उत्तरे जतन राहतील\n'
           '• UPDATES / UPDATES OFF: योजनांच्या सूचना सुरू/बंद\n'
           '• STOP: सेवा सोडा आणि हा केस हटवा'),
}

INVALID = {
    'en': 'Please reply with one of the numbers shown, or just type your question.',
    'hi': 'कृपया दिखाए गए नंबरों में से एक लिखें, या सीधे अपना सवाल लिखें।',
    'mr': 'कृपया दाखवलेल्या क्रमांकांपैकी एक पाठवा, किंवा थेट तुमचा प्रश्न लिहा.',
}

LANGUAGE_SET = {
    'en': "Okay, we'll talk in English.",
    'hi': 'ठीक है, अब हिंदी में बात करेंगे।',
    'mr': 'ठीक आहे, आता मराठीत बोलूया.',
}

CHAT_SELECTED = {
    'en': 'Chat selected. Describe your situation or send a voice note (up to 15 seconds). Reply MENU for options; STOP to leave.',
    'hi': 'चैट चुना। अपनी स्थिति लिखें या वॉइस नोट भेजें (15 सेकंड तक)। विकल्पों के लिए MENU; छोड़ने के लिए STOP।',
    'mr': 'चॅट निवडले. तुमची परिस्थिती लिहा किंवा व्हॉइस नोट पाठवा (15 सेकंदांपर्यंत). पर्यायांसाठी MENU; बंद करण्यासाठी STOP.',
}

CONTINUE_CASE = {
    'en': 'Your saved answers are kept. Last question:',
    'hi': 'आपके सहेजे गए जवाब सुरक्षित हैं। पिछला सवाल:',
    'mr': 'तुमची जतन केलेली उत्तरे सुरक्षित आहेत. मागील प्रश्न:',
}

VOICE_CONFIRM = {
    'en': 'I heard: “{text}”\n\nIs that right? Reply YES to continue, or type the correct message.',
    'hi': 'मैंने सुना: “{text}”\n\nक्या यह सही है? आगे बढ़ने के लिए हाँ लिखें, या सही संदेश लिखें।',
    'mr': 'मी ऐकले: “{text}”\n\nहे बरोबर आहे का? पुढे जाण्यासाठी हो पाठवा, किंवा योग्य संदेश लिहा.',
}
VOICE_REJECTED = {
    'en': 'Okay, I did not use that voice note. Please type your message or send the note again.',
    'hi': 'ठीक है, वह वॉइस नोट उपयोग नहीं किया। कृपया संदेश लिखें या नोट दोबारा भेजें।',
    'mr': 'ठीक आहे, तो व्हॉइस नोट वापरला नाही. कृपया संदेश लिहा किंवा नोट पुन्हा पाठवा.',
}


def list_message(menu, lang):
    """The WAHA /api/sendList `message` object for a menu."""
    if menu == 'language':
        return {'title': 'Yojana Saathi · योजना साथी',
                'description': language_menu_text()[:1000],
                'footer': 'STOP: unsubscribe', 'button': 'Language · भाषा',
                'sections': [{'title': 'Language · भाषा',
                              'rows': [{'title': LANGUAGE_ROWS[o], 'rowId': o, 'description': None}
                                       for o in LANGUAGE_IDS]}]}
    rows = MAIN_ROWS[lang]
    return {'title': 'Yojana Saathi', 'description': MAIN_LEAD[lang], 'footer': MAIN_FOOT[lang][:60],
            'button': {'en': 'Options', 'hi': 'विकल्प', 'mr': 'पर्याय'}[lang],
            'sections': [{'title': {'en': 'Options', 'hi': 'विकल्प', 'mr': 'पर्याय'}[lang],
                          'rows': [{'title': rows[o][0], 'rowId': o, 'description': rows[o][1]} for o in MAIN_IDS]}]}
