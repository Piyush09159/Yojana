"""Deterministic language handling for one message (English, Hindi, Marathi and code-mixed text).

Two separate ideas, kept apart on purpose:
  preferred language  an explicit choice by the citizen (menu, "मराठीत बोला", the browser selector);
                      stored with the chat/case and used for replies until changed
  turn language       what this one message is written or spoken in (`detect`)

`switch_request` recognises an explicit request to change language. It only matches a whole
message such as "Hindi", "मराठी", "speak in English" or "हिंदी में बात करो", so an answer that
merely mentions a language ("Marathi medium school", "मी मराठी आहे") is never taken as a switch.
No model call, no network; scheme names and amounts are never translated here.
"""
from __future__ import annotations

import re
import unicodedata

SUPPORTED = ('en', 'hi', 'mr')
NAMES = {'en': 'English', 'hi': 'हिंदी', 'mr': 'मराठी'}

_DEVANAGARI = re.compile(r'[ऀ-ॿ]')
_TOKEN = re.compile(r'[ऀ-ॿ]+|[a-z]+')

# Function words that separate the languages (content words such as "pension" are shared).
_MR = {'आहे', 'आहेत', 'आहोत', 'आहात', 'मी', 'मला', 'माझा', 'माझी', 'माझे', 'माझ्या', 'आम्ही', 'आमचे', 'आमच्या',
       'तुम्ही', 'तुमचे', 'तुमच्या', 'नाही', 'नाहीत', 'होय', 'हवी', 'हवे', 'हवा', 'पाहिजे', 'करतो', 'करते', 'करतात',
       'राहतो', 'राहते', 'वर्षांचा', 'वर्षांची', 'वर्षे', 'आणि', 'किंवा', 'साठी', 'मध्ये', 'काय', 'कोणती', 'कोणता',
       'शेती', 'घरात', 'कुटुंबात', 'आहेस', 'मिळेल', 'बोला', 'मराठीत', 'आता', 'झाले', 'नको'}
_HI = {'है', 'हैं', 'हूँ', 'हूं', 'मैं', 'मेरा', 'मेरी', 'मेरे', 'मुझे', 'हम', 'हमारा', 'हमारे', 'आप', 'आपका',
       'नहीं', 'हाँ', 'हां', 'चाहिए', 'करता', 'करती', 'करते', 'रहता', 'रहती', 'साल', 'और', 'या', 'लिए', 'में',
       'क्या', 'कौन', 'कौनसी', 'खेती', 'घर', 'परिवार', 'मिलेगा', 'मिलेगी', 'बोलो', 'बात', 'करो', 'था', 'थी', 'गया'}
_EN = {'i', 'am', 'my', 'is', 'are', 'the', 'a', 'need', 'help', 'years', 'year', 'old', 'want', 'have', 'has',
       'and', 'for', 'what', 'how', 'which', 'scheme', 'schemes', 'live', 'in', 'of', 'me', 'we', 'our', 'yes',
       'no', 'please', 'income', 'farmer', 'work', 'job', 'student', 'son', 'daughter', 'widow', 'with', 'per'}
# Romanised (Latin script) Hindi and Marathi, as typed on phones.
_HI_LATIN = {'hai', 'hain', 'hoon', 'hu', 'mera', 'meri', 'mere', 'mujhe', 'kya', 'nahi', 'nahin', 'chahiye',
             'karta', 'karti', 'rehta', 'rehti', 'saal', 'aur', 'kaise', 'kitna', 'kitni', 'hamara', 'aap', 'kuch',
             'kaun', 'milega', 'milegi', 'haan', 'paisa', 'ghar', 'mein', 'mai'}
_MR_LATIN = {'aahe', 'ahe', 'aahet', 'mala', 'majha', 'majhi', 'mazha', 'mazhi', 'maza', 'mazi', 'tumhi',
             'pahije', 'kay', 'karto', 'karte', 'rahto', 'rahte', 'varsh', 'varshancha', 'aamhi', 'hoy', 'milel',
             'shetkari', 'gharat', 'aata', 'nahiye', 'kasa', 'kashi'}


def _tokens(text):
    return _TOKEN.findall(unicodedata.normalize('NFC', text or '').casefold())


def detect(text):
    """(language, confident) for one message. `confident` is False when the evidence is thin or
    split, so the caller keeps the saved preference or the previous language instead of guessing."""
    tokens = _tokens(text)
    deva = [t for t in tokens if _DEVANAGARI.match(t)]
    latin = [t for t in tokens if not _DEVANAGARI.match(t)]
    if deva:
        # Devanagari decides between Hindi and Marathi; English words mixed in ("मला pension पाहिजे")
        # do not make the message English.
        mr = sum(t in _MR or t.endswith(('च्या', 'साठी', 'मध्ये', 'ांचा', 'ांची')) for t in deva)
        hi = sum(t in _HI for t in deva)
        if mr == hi:
            return ('hi', False) if len(deva) >= len(latin) else ('en', False)
        best = 'mr' if mr > hi else 'hi'
        return best, True
    if not latin:
        return 'en', False
    en = sum(t in _EN for t in latin)
    hi = sum(t in _HI_LATIN for t in latin)
    mr = sum(t in _MR_LATIN for t in latin)
    indic = max(hi, mr)
    if indic >= 2 and indic > en:
        return ('mr' if mr > hi else 'hi'), mr != hi
    if en >= 1 and en >= indic:
        return 'en', en > indic or indic == 0
    return 'en', False


_LANG_WORDS = {
    'en': ('english', 'eng', 'angrezi', 'इंग्लिश', 'इंग्रजी', 'अंग्रेज़ी', 'अंग्रेजी'),
    'hi': ('hindi', 'हिंदी', 'हिन्दी'),
    'mr': ('marathi', 'मराठी', 'मराठीत'),
}
_LANG = {word: code for code, words in _LANG_WORDS.items() for word in words}
_LANG_RE = '|'.join(sorted((re.escape(w) for w in _LANG), key=len, reverse=True))
_SWITCH = [
    # "Hindi", "Hindi please", "हिंदी में", "मराठीत", "मराठी मध्ये", "in English"
    re.compile(rf'^(?:in\s+)?(?P<l>{_LANG_RE})(?:\s+(?:please|pls|me|mein|madhe|मध्ये|में|मे|language|bhasha|भाषा))*$'),
    # "speak/talk/reply/continue/answer (to me) in X", "switch/change (language) to X"
    re.compile(rf'^(?:please\s+|pls\s+)?(?:speak|talk|reply|answer|continue|write|chat)(?:\s+to\s+me|\s+with\s+me)?\s+in\s+(?P<l>{_LANG_RE})(?:\s+please)?$'),
    re.compile(rf'^(?:please\s+)?(?:switch|change)(?:\s+(?:the\s+)?language)?\s+to\s+(?P<l>{_LANG_RE})(?:\s+please)?$'),
    # "X me baat karo", "X mein bolo", "हिंदी में बात करो", "हिंदी में बोलिए", "मराठीत बोला", "मराठी मध्ये बोला"
    re.compile(rf'^(?P<l>{_LANG_RE})(?:\s+(?:me|mein|madhe|मध्ये|में|मे))?\s+(?:baat\s+karo|baat\s+kijiye|bolo|boliye|bola|bol|बात\s+करो|बात\s+करें|बात\s+कीजिए|बोलो|बोलिए|बोला|बोल|लिहा|लिखो|लिखिए|उत्तर\s+द्या)(?:\s+(?:please|na|ना))?$'),
    # "भाषा मराठी", "language hindi", "भाषा बदला मराठी"
    re.compile(rf'^(?:language|bhasha|भाषा)(?:\s+(?:badlo|badla|बदलो|बदलें|बदला))?\s+(?P<l>{_LANG_RE})$'),
]


def clean(text):
    """Lower-case, NFKC, punctuation/emoji trimmed at both ends, single spaces."""
    value = unicodedata.normalize('NFKC', text or '').strip().casefold()
    while value and (value[0].isspace() or unicodedata.category(value[0]).startswith(('P', 'S'))):
        value = value[1:]
    while value and (value[-1].isspace() or unicodedata.category(value[-1]).startswith(('P', 'S'))):
        value = value[:-1]
    return ' '.join(value.split())


def switch_request(text):
    """The language code an explicit switch request asks for, else None."""
    value = clean(text)
    if not value or len(value) > 60:
        return None
    for pattern in _SWITCH:
        match = pattern.match(value)
        if match:
            return _LANG[match.group('l')]
    return None


_NUMBER_WORDS = {
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'twenty', 'thirty', 'forty',
    'fifty', 'sixty', 'seventy', 'eighty', 'ninety', 'hundred', 'thousand', 'lakh', 'lakhs', 'crore',
    'एक', 'दो', 'तीन', 'चार', 'पाँच', 'पांच', 'छह', 'सात', 'आठ', 'नौ', 'दस', 'बीस', 'तीस', 'चालीस', 'पचास', 'साठ',
    'सत्तर', 'अस्सी', 'नब्बे', 'सौ', 'हज़ार', 'हजार', 'लाख', 'करोड़',
    'दोन', 'पाच', 'सहा', 'नऊ', 'दहा', 'वीस', 'चाळीस', 'पन्नास', 'ऐंशी', 'नव्वद', 'शंभर', 'कोटी',
}


def mentions_number(text):
    """True when a message states a quantity (age, income, land, family size): digits in any
    script or a number word in English, Hindi or Marathi."""
    if any(ch.isdigit() for ch in text or ''):
        return True
    return any(token in _NUMBER_WORDS for token in _tokens(text))


CONFIRM = {
    'en': "Okay, I'll continue in English. Your answers so far are kept.",
    'hi': 'ठीक है, अब मैं हिंदी में बात करूँगा। आपके अब तक के जवाब सुरक्षित हैं।',
    'mr': 'ठीक आहे, आता मी मराठीत बोलेन. तुमची आतापर्यंतची उत्तरे जतन आहेत.',
}
NEXT = {
    'en': 'Tell me what help you need, or send a voice note (up to 15 seconds).',
    'hi': 'बताइए आपको किस मदद की ज़रूरत है, या वॉइस नोट भेजें (15 सेकंड तक)।',
    'mr': 'तुम्हाला कोणती मदत हवी आहे ते सांगा, किंवा व्हॉइस नोट पाठवा (15 सेकंदांपर्यंत).',
}
SWITCH_HINT = {
    'en': 'To continue in English, reply ENGLISH.',
    'hi': 'हिंदी में जारी रखने के लिए हिंदी लिखें।',
    'mr': 'मराठीत पुढे जाण्यासाठी मराठी लिहा.',
}
