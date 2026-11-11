"""WhatsApp: review the recorded facts before any scheme result is sent (SCHEME_REVIEW_CONFIRMATION_ENABLED).

State machine (one chat, one review version at a time; versions only grow):

    ASSESSING -> AWAITING_CONFIRMATION (result_reviews.status='awaiting', version v)
        CHANGE  -> 'changing' (next answers use the explicit-correction path) -> ASSESSING -> new version
        HELP    -> stays awaiting (never approves anything)
        PROCEED -> 'confirmed' -> result_deliveries row: SCHEMES_QUEUED -> SCHEMES_SENT (WAHA accepted
                   the outbox reply) -> PDF_QUEUED -> PDF_SENT -> optional call offer
                   (web/followup_calls.py: CALL_QUEUED -> CALL_STARTED -> COMPLETED|FAILED|NO_ANSWER)
    STOP -> every review/delivery/call for the chat is cancelled and its ciphers scrubbed.

Facts are hashed (facts_hash) when the review is shown. PROCEED re-hashes the case's current profile;
a mismatch (facts changed after the summary) never releases the old recommendations. A list reply
carries the version in its row id (review:proceed:3), so tapping an old list cannot approve a newer
summary either. Confirming the facts is NOT consent to a phone call: that is a separate CALL ME choice.

The review shows only a few validated, coarse facts (income as a band, never Aadhaar/bank/DOB or
transcripts). Scheme names are never part of a review message.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

from .channel_security import seal, unseal
from .diagnostics import emit


def _flag(name):
    return os.getenv(name, 'false').strip().lower() == 'true'


def review_enabled():
    return _flag('SCHEME_REVIEW_CONFIRMATION_ENABLED')


def pdf_enabled():
    return _flag('WHATSAPP_PDF_SEND_ENABLED')


# ---------------------------------------------------------------- facts shown in the review

SHOWN = ('age', 'gender', 'state', 'district', 'occupation', 'is_student', 'current_study_level',
         'annual_family_income_inr', 'owns_cultivable_land', 'land_acres')

LABELS = {
    'age': {'en': 'Age', 'hi': 'उम्र', 'mr': 'वय'},
    'gender': {'en': 'Gender', 'hi': 'लिंग', 'mr': 'लिंग'},
    'state': {'en': 'State', 'hi': 'राज्य', 'mr': 'राज्य'},
    'district': {'en': 'District', 'hi': 'ज़िला', 'mr': 'जिल्हा'},
    'occupation': {'en': 'Work', 'hi': 'काम', 'mr': 'काम'},
    'is_student': {'en': 'Studying now', 'hi': 'अभी पढ़ाई', 'mr': 'सध्या शिक्षण'},
    'current_study_level': {'en': 'Study level', 'hi': 'पढ़ाई का स्तर', 'mr': 'शिक्षणाचा स्तर'},
    'annual_family_income_inr': {'en': 'Family income (yearly)', 'hi': 'परिवार की सालाना आय', 'mr': 'कुटुंबाचे वार्षिक उत्पन्न'},
    'owns_cultivable_land': {'en': 'Farm land', 'hi': 'खेती की ज़मीन', 'mr': 'शेतजमीन'},
    'land_acres': {'en': 'Land (acres)', 'hi': 'ज़मीन (एकड़)', 'mr': 'जमीन (एकर)'},
}
YES_NO = {'en': ('Yes', 'No'), 'hi': ('हाँ', 'नहीं'), 'mr': ('हो', 'नाही')}
VALUES = {
    'male': {'en': 'Male', 'hi': 'पुरुष', 'mr': 'पुरुष'},
    'female': {'en': 'Female', 'hi': 'महिला', 'mr': 'महिला'},
    'transgender': {'en': 'Transgender', 'hi': 'ट्रांसजेंडर', 'mr': 'तृतीयपंथी'},
    'farmer': {'en': 'Farmer', 'hi': 'किसान', 'mr': 'शेतकरी'},
    'agricultural_labourer': {'en': 'Farm labourer', 'hi': 'खेत मज़दूर', 'mr': 'शेतमजूर'},
    'construction_worker': {'en': 'Construction worker', 'hi': 'निर्माण मज़दूर', 'mr': 'बांधकाम कामगार'},
    'street_vendor': {'en': 'Street vendor', 'hi': 'फेरीवाला', 'mr': 'फेरीवाला'},
    'artisan': {'en': 'Artisan', 'hi': 'कारीगर', 'mr': 'कारागीर'},
    'fisher': {'en': 'Fisher', 'hi': 'मछुआरा', 'mr': 'मच्छीमार'},
    'weaver': {'en': 'Weaver', 'hi': 'बुनकर', 'mr': 'विणकर'},
    'domestic_worker': {'en': 'Domestic worker', 'hi': 'घरेलू कामगार', 'mr': 'घरकामगार'},
    'other_unorganised_worker': {'en': 'Unorganised worker', 'hi': 'असंगठित कामगार', 'mr': 'असंघटित कामगार'},
    'self_employed': {'en': 'Self-employed', 'hi': 'स्वरोज़गार', 'mr': 'स्वयंरोजगार'},
    'salaried_private': {'en': 'Private job', 'hi': 'निजी नौकरी', 'mr': 'खाजगी नोकरी'},
    'govt_employee': {'en': 'Government job', 'hi': 'सरकारी नौकरी', 'mr': 'सरकारी नोकरी'},
    'student': {'en': 'Student', 'hi': 'विद्यार्थी', 'mr': 'विद्यार्थी'},
    'unemployed': {'en': 'Not working', 'hi': 'बेरोज़गार', 'mr': 'बेरोजगार'},
    'homemaker': {'en': 'Homemaker', 'hi': 'गृहिणी', 'mr': 'गृहिणी'},
    'retired': {'en': 'Retired', 'hi': 'सेवानिवृत्त', 'mr': 'निवृत्त'},
    'class_1_to_8': {'en': 'Class 1–8', 'hi': 'कक्षा 1–8', 'mr': 'इयत्ता 1–8'},
    'class_9_to_10': {'en': 'Class 9–10', 'hi': 'कक्षा 9–10', 'mr': 'इयत्ता 9–10'},
    'class_11_to_12': {'en': 'Class 11–12', 'hi': 'कक्षा 11–12', 'mr': 'इयत्ता 11–12'},
    'iti_or_diploma': {'en': 'ITI / Diploma', 'hi': 'ITI / डिप्लोमा', 'mr': 'ITI / डिप्लोमा'},
    'undergraduate': {'en': 'Graduation', 'hi': 'स्नातक', 'mr': 'पदवी'},
    'postgraduate': {'en': 'Post-graduation', 'hi': 'स्नातकोत्तर', 'mr': 'पदव्युत्तर'},
    'phd': {'en': 'PhD', 'hi': 'पीएचडी', 'mr': 'पीएचडी'},
}
INCOME_BANDS = [  # upper bound (exclusive), label
    (100000, {'en': 'under ₹1 lakh', 'hi': '₹1 लाख से कम', 'mr': '₹1 लाखापेक्षा कमी'}),
    (250000, {'en': '₹1–2.5 lakh', 'hi': '₹1–2.5 लाख', 'mr': '₹1–2.5 लाख'}),
    (500000, {'en': '₹2.5–5 lakh', 'hi': '₹2.5–5 लाख', 'mr': '₹2.5–5 लाख'}),
    (800000, {'en': '₹5–8 lakh', 'hi': '₹5–8 लाख', 'mr': '₹5–8 लाख'}),
    (None, {'en': 'above ₹8 lakh', 'hi': '₹8 लाख से अधिक', 'mr': '₹8 लाखांपेक्षा जास्त'}),
]


def public_profile(profile):
    return {k: v for k, v in (profile or {}).items() if not str(k).startswith('_') and v is not None}


def facts_hash(profile):
    return hashlib.sha256(json.dumps(public_profile(profile), sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def _lang(lang):
    return lang if lang in ('en', 'hi', 'mr') else 'en'


def display_value(field, value, lang):
    lang = _lang(lang)
    if isinstance(value, bool):
        return YES_NO[lang][0 if value else 1]
    if field == 'annual_family_income_inr' and isinstance(value, (int, float)):
        for bound, label in INCOME_BANDS:
            if bound is None or value < bound:
                return label[lang]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        if value in VALUES:
            return VALUES[value][lang]
        return value.replace('_', ' ').title()[:40]
    return str(value)[:40]


def shown_fields(profile):
    facts = public_profile(profile)
    return [f for f in SHOWN if f in facts]


def facts_lines(profile, lang):
    lang = _lang(lang)
    facts = public_profile(profile)
    return [f'• {LABELS[f][lang]}: {display_value(f, facts[f], lang)}' for f in shown_fields(profile)]


# ---------------------------------------------------------------- texts

REVIEW_LEAD = {
    'en': 'Before I send scheme matches, please check what I recorded:',
    'hi': 'योजनाएँ भेजने से पहले, कृपया देख लें कि मैंने क्या लिखा है:',
    'mr': 'योजना पाठवण्यापूर्वी, मी काय नोंदवले ते कृपया तपासा:',
}
HIDDEN = {
    'en': '(+{n} more detail(s) you shared are used but not shown here.)',
    'hi': '(आपकी बताई {n} और जानकारी उपयोग हुई है, पर यहाँ नहीं दिखाई गई।)',
    'mr': '(तुम्ही दिलेली आणखी {n} माहिती वापरली आहे, पण येथे दाखवलेली नाही.)',
}
REVIEW_ASK = {'en': 'Is this correct?', 'hi': 'क्या यह सही है?', 'mr': 'हे बरोबर आहे का?'}
REVIEW_ROWS = {
    'en': {'proceed': ('Proceed', 'The details are correct'), 'change': ('Change', 'Fix a detail'),
           'help': ('Help', 'What happens next')},
    'hi': {'proceed': ('आगे बढ़ें', 'जानकारी सही है'), 'change': ('बदलें', 'कोई जानकारी ठीक करें'),
           'help': ('मदद', 'आगे क्या होगा')},
    'mr': {'proceed': ('पुढे जा', 'माहिती बरोबर आहे'), 'change': ('बदला', 'एखादी माहिती दुरुस्त करा'),
           'help': ('मदत', 'पुढे काय होईल')},
}
REVIEW_FOOT = {
    'en': 'Reply 1, 2 or 3. Please do not send identity or bank numbers.',
    'hi': '1, 2 या 3 लिखें। पहचान या बैंक नंबर न भेजें।',
    'mr': '1, 2 किंवा 3 पाठवा. ओळख किंवा बँक क्रमांक पाठवू नका.',
}
REVIEW_TITLE = {'en': 'Check your details', 'hi': 'अपनी जानकारी जाँचें', 'mr': 'तुमची माहिती तपासा'}
CHOOSE = {'en': 'Choose', 'hi': 'चुनें', 'mr': 'निवडा'}


def review_body(profile, lang):
    lang = _lang(lang)
    lines = [REVIEW_LEAD[lang], ''] + facts_lines(profile, lang)
    hidden = len(public_profile(profile)) - len(shown_fields(profile))
    if hidden > 0:
        lines += ['', HIDDEN[lang].format(n=hidden)]
    return '\n'.join(lines + ['', REVIEW_ASK[lang]])


def review_text(profile, lang):
    lang = _lang(lang)
    rows = REVIEW_ROWS[lang]
    options = [f'{i}. {rows[k][0]} — {rows[k][1]}' for i, k in enumerate(('proceed', 'change', 'help'), 1)]
    return review_body(profile, lang) + '\n\n' + '\n'.join(options) + '\n\n' + REVIEW_FOOT[lang]


RESULTS_LEAD = {
    'en': 'Thank you. Here are the schemes the system shortlisted based on the details you approved. '
          'Actual eligibility is decided by the relevant government office.',
    'hi': 'धन्यवाद। आपकी पुष्टि की गई जानकारी के आधार पर सिस्टम ने ये योजनाएँ चुनी हैं। '
          'असली पात्रता संबंधित सरकारी कार्यालय तय करता है।',
    'mr': 'धन्यवाद. तुम्ही पुष्टी केलेल्या माहितीवरून प्रणालीने या योजना निवडल्या आहेत. '
          'प्रत्यक्ष पात्रता संबंधित सरकारी कार्यालय ठरवते.',
}
ENGLISH_NOTE = {
    'hi': '(योजनाओं के नाम और विवरण आधिकारिक स्रोत की भाषा में हैं।)',
    'mr': '(योजनांची नावे आणि तपशील अधिकृत स्रोताच्या भाषेत आहेत.)',
}
PDF_COMING = {
    'en': "I'm sending your application guide as a PDF next.",
    'hi': 'आपकी आवेदन गाइड PDF के रूप में अगले संदेश में भेज रहे हैं।',
    'mr': 'तुमची अर्ज मार्गदर्शिका PDF स्वरूपात पुढील संदेशात पाठवत आहोत.',
}
PDF_EXCLUDED = {
    'en': '{n} of these have no official source link, so they are not in the guide.',
    'hi': 'इनमें से {n} योजनाओं का आधिकारिक स्रोत लिंक नहीं है, इसलिए वे गाइड में नहीं हैं।',
    'mr': 'यापैकी {n} योजनांचा अधिकृत स्रोत दुवा नाही, म्हणून त्या मार्गदर्शिकेत नाहीत.',
}
PDF_NONE = {
    'en': 'A PDF guide could not be made because none of these schemes has an official source link.',
    'hi': 'इनमें से किसी योजना का आधिकारिक स्रोत लिंक नहीं है, इसलिए PDF गाइड नहीं बन सकी।',
    'mr': 'यापैकी कोणत्याही योजनेचा अधिकृत स्रोत दुवा नाही, म्हणून PDF मार्गदर्शिका तयार करता आली नाही.',
}
RESULTS_FOOT = {
    'en': 'Reply HELP for next steps, or CHANGE to fix a detail.',
    'hi': 'आगे क्या करें जानने के लिए HELP, कोई जानकारी बदलने के लिए CHANGE लिखें।',
    'mr': 'पुढे काय करायचे ते जाणण्यासाठी HELP, माहिती बदलण्यासाठी CHANGE पाठवा.',
}

HELP_AWAITING = {
    'en': ('Your details are waiting for your check. Nothing has been sent yet.\n\n'
           '• 1 / PROCEED: the details are correct. I will send the shortlisted schemes and a PDF guide.\n'
           '• 2 / CHANGE: fix a detail. You will see the summary again before anything is sent.\n'
           '• MENU → Browser voice call: talk to the assistant instead.\n'
           '• STOP: leave and delete this chat\'s case.\n\n'
           'Please do not send identity or bank numbers.'),
    'hi': ('आपकी जानकारी जाँच के लिए रुकी है। अभी कुछ भी नहीं भेजा गया है।\n\n'
           '• 1 / आगे बढ़ें: जानकारी सही है। मैं चुनी गई योजनाएँ और PDF गाइड भेजूँगा।\n'
           '• 2 / बदलें: कोई जानकारी ठीक करें। कुछ भी भेजने से पहले सारांश फिर दिखेगा।\n'
           '• MENU → ब्राउज़र वॉइस कॉल: सहायक से बात करें।\n'
           '• STOP: सेवा छोड़ें और यह केस मिटाएँ।\n\n'
           'पहचान या बैंक नंबर न भेजें।'),
    'mr': ('तुमची माहिती तपासणीसाठी थांबली आहे. अजून काहीही पाठवलेले नाही.\n\n'
           '• 1 / पुढे जा: माहिती बरोबर आहे. मी निवडलेल्या योजना आणि PDF मार्गदर्शिका पाठवेन.\n'
           '• 2 / बदला: एखादी माहिती दुरुस्त करा. काहीही पाठवण्यापूर्वी सारांश पुन्हा दिसेल.\n'
           '• MENU → ब्राउझर व्हॉइस कॉल: सहायकाशी बोला.\n'
           '• STOP: सेवा सोडा आणि हा केस हटवा.\n\n'
           'ओळख किंवा बँक क्रमांक पाठवू नका.'),
}
HELP_CHANGING = {
    'en': ('Send the correct detail, for example “My age is 64” or “District Nashik”. '
           'Then I will show the summary again for you to check. Nothing is sent before that.'),
    'hi': ('सही जानकारी भेजें, जैसे “मेरी उम्र 64 है” या “ज़िला नासिक”। '
           'फिर मैं सारांश दोबारा दिखाऊँगा। उससे पहले कुछ नहीं भेजा जाएगा।'),
    'mr': ('योग्य माहिती पाठवा, उदा. “माझे वय 64 आहे” किंवा “जिल्हा नाशिक”. '
           'मग मी सारांश पुन्हा दाखवेन. त्याआधी काहीही पाठवले जाणार नाही.'),
}
HELP_DELIVERED = {
    'en': ('Your shortlist was sent above.\n\n'
           '• Documents: the PDF guide lists the documents the official source mentions. Get them from the office that issues them.\n'
           '• Applying: use only the official links in the guide, or visit the office named there. '
           'Yojana Saathi cannot apply for you and is not a government office.\n'
           '• CHANGE: fix a detail. You will get a new summary, and a new guide after you PROCEED again.\n'
           '• GUIDE: send the PDF guide again if it did not arrive.\n'
           '• MENU → Browser voice call: talk to the assistant.\n'
           '• STOP: leave and delete this chat\'s case.'),
    'hi': ('आपकी योजनाओं की सूची ऊपर भेजी गई है।\n\n'
           '• दस्तावेज़: PDF गाइड में आधिकारिक स्रोत में बताए दस्तावेज़ हैं। उन्हें जारी करने वाले कार्यालय से लें।\n'
           '• आवेदन: केवल गाइड के आधिकारिक लिंक का उपयोग करें, या वहाँ बताए कार्यालय जाएँ। '
           'योजना साथी आपकी ओर से आवेदन नहीं कर सकता और सरकारी कार्यालय नहीं है।\n'
           '• CHANGE: कोई जानकारी ठीक करें। नया सारांश मिलेगा, और फिर से आगे बढ़ें चुनने पर नई गाइड।\n'
           '• GUIDE: PDF गाइड नहीं मिली तो दोबारा मँगाएँ।\n'
           '• MENU → ब्राउज़र वॉइस कॉल: सहायक से बात करें।\n'
           '• STOP: सेवा छोड़ें और यह केस मिटाएँ।'),
    'mr': ('तुमची योजनांची यादी वर पाठवली आहे.\n\n'
           '• कागदपत्रे: PDF मार्गदर्शिकेत अधिकृत स्रोतात सांगितलेली कागदपत्रे आहेत. ती देणाऱ्या कार्यालयातून मिळवा.\n'
           '• अर्ज: फक्त मार्गदर्शिकेतील अधिकृत दुवे वापरा, किंवा तिथे सांगितलेल्या कार्यालयात जा. '
           'योजना साथी तुमच्या वतीने अर्ज करू शकत नाही आणि सरकारी कार्यालय नाही.\n'
           '• CHANGE: एखादी माहिती दुरुस्त करा. नवीन सारांश मिळेल, आणि पुन्हा पुढे जा निवडल्यावर नवीन मार्गदर्शिका.\n'
           '• GUIDE: PDF मार्गदर्शिका मिळाली नसेल तर पुन्हा मागवा.\n'
           '• MENU → ब्राउझर व्हॉइस कॉल: सहायकाशी बोला.\n'
           '• STOP: सेवा सोडा आणि हा केस हटवा.'),
}

CHANGE_LEAD = {
    'en': 'Which detail is wrong? Reply with its number, or just type the correct detail (for example: “My age is 64”).',
    'hi': 'कौन-सी जानकारी गलत है? उसका नंबर लिखें, या सीधे सही जानकारी लिखें (जैसे: “मेरी उम्र 64 है”)।',
    'mr': 'कोणती माहिती चुकीची आहे? तिचा क्रमांक पाठवा, किंवा थेट योग्य माहिती लिहा (उदा.: “माझे वय 64 आहे”).',
}
CHANGE_FOOT = {
    'en': 'Nothing will be sent until you check the new summary.',
    'hi': 'नया सारांश जाँचने तक कुछ नहीं भेजा जाएगा।',
    'mr': 'नवीन सारांश तपासेपर्यंत काहीही पाठवले जाणार नाही.',
}
CHANGE_FIELD = {
    'en': 'Okay. Please send the correct value.', 'hi': 'ठीक है। कृपया सही जानकारी भेजें।',
    'mr': 'ठीक आहे. कृपया योग्य माहिती पाठवा.',
}
STALE = {
    'en': 'That choice belongs to an older summary. Here is the latest one:',
    'hi': 'वह विकल्प पुराने सारांश का है। यह नया सारांश है:',
    'mr': 'तो पर्याय जुन्या सारांशाचा आहे. हा नवीन सारांश आहे:',
}
FACTS_CHANGED = {
    'en': 'Your details changed after that summary. Please check the updated summary:',
    'hi': 'उस सारांश के बाद आपकी जानकारी बदल गई। कृपया नया सारांश जाँचें:',
    'mr': 'त्या सारांशानंतर तुमची माहिती बदलली. कृपया नवीन सारांश तपासा:',
}
NOTHING_TO_CONFIRM = {
    'en': 'There is nothing to confirm yet. Please answer the questions first.',
    'hi': 'अभी पुष्टि करने के लिए कुछ नहीं है। कृपया पहले सवालों के जवाब दें।',
    'mr': 'अजून पुष्टी करण्यासारखे काही नाही. कृपया आधी प्रश्नांची उत्तरे द्या.',
}
ALREADY_CONFIRMED = {
    'en': 'Your details are already confirmed and the shortlist was sent above. Reply CHANGE to fix a detail or HELP for next steps.',
    'hi': 'आपकी जानकारी की पुष्टि हो चुकी है और सूची ऊपर भेजी जा चुकी है। जानकारी बदलने के लिए CHANGE, आगे के कदमों के लिए HELP लिखें।',
    'mr': 'तुमच्या माहितीची पुष्टी झाली आहे आणि यादी वर पाठवली आहे. माहिती बदलण्यासाठी CHANGE, पुढील पायऱ्यांसाठी HELP पाठवा.',
}
PDF_FAILED = {
    'en': 'Sorry, your PDF guide could not be created right now. The shortlist above is still valid. Reply GUIDE later to try again.',
    'hi': 'माफ़ कीजिए, अभी आपकी PDF गाइड नहीं बन सकी। ऊपर दी गई सूची मान्य है। बाद में फिर कोशिश के लिए GUIDE लिखें।',
    'mr': 'माफ करा, सध्या तुमची PDF मार्गदर्शिका तयार होऊ शकली नाही. वरील यादी वैध आहे. नंतर पुन्हा प्रयत्नासाठी GUIDE पाठवा.',
}
GUIDE_RETRY = {
    'en': 'Okay, I am trying again. Your PDF guide will arrive shortly.',
    'hi': 'ठीक है, फिर से कोशिश कर रहे हैं। आपकी PDF गाइड जल्द आएगी।',
    'mr': 'ठीक आहे, पुन्हा प्रयत्न करत आहोत. तुमची PDF मार्गदर्शिका लवकरच येईल.',
}
GUIDE_SENT = {
    'en': 'Your PDF guide was already sent above. Scroll up to find it.',
    'hi': 'आपकी PDF गाइड ऊपर भेजी जा चुकी है। उसे ऊपर देखें।',
    'mr': 'तुमची PDF मार्गदर्शिका वर पाठवली आहे. ती वर पाहा.',
}
GUIDE_PENDING = {
    'en': 'Your PDF guide is being prepared. It will arrive shortly.',
    'hi': 'आपकी PDF गाइड तैयार हो रही है। जल्द आएगी।',
    'mr': 'तुमची PDF मार्गदर्शिका तयार होत आहे. लवकरच येईल.',
}
GUIDE_NONE = {
    'en': 'A guide is sent after you check and confirm your details (PROCEED).',
    'hi': 'अपनी जानकारी जाँचकर पुष्टि (आगे बढ़ें) करने के बाद गाइड भेजी जाती है।',
    'mr': 'तुमची माहिती तपासून पुष्टी (पुढे जा) केल्यानंतर मार्गदर्शिका पाठवली जाते.',
}
GUIDE_UNAVAILABLE = {
    'en': 'A PDF guide is not available for this shortlist. Use the official links in the message above.',
    'hi': 'इस सूची के लिए PDF गाइड उपलब्ध नहीं है। ऊपर के संदेश के आधिकारिक लिंक देखें।',
    'mr': 'या यादीसाठी PDF मार्गदर्शिका उपलब्ध नाही. वरील संदेशातील अधिकृत दुवे वापरा.',
}
PDF_CAPTION = {
    'en': 'Your Yojana Saathi application guide. Guidance only, not government approval.',
    'hi': 'आपकी योजना साथी आवेदन गाइड। केवल मार्गदर्शन, सरकारी स्वीकृति नहीं।',
    'mr': 'तुमची योजना साथी अर्ज मार्गदर्शिका. फक्त मार्गदर्शन, सरकारी मंजुरी नाही.',
}

# Words. Numbers are read only while that menu is open (channel_menus.menu).
PROCEED_WORDS = {'proceed', 'confirm', 'continue', 'go ahead', 'details are correct', 'all correct',
                 'आगे बढ़ें', 'आगे बढ़ो', 'आगे बढें', 'आगे', 'पुष्टि', 'पुढे जा', 'पुढे', 'पुढे चला', 'पुष्टी'}
CHANGE_WORDS = {'change', 'edit', 'correct it', 'fix', 'change details', 'बदलें', 'बदलो', 'बदलना', 'बदला', 'बदल',
                'बदलायचे', 'दुरुस्त', 'दुरुस्ती', 'सुधार', 'सुधारें'}
GUIDE_WORDS = {'guide', 'pdf', 'send guide', 'send pdf', 'गाइड', 'मार्गदर्शिका', 'पीडीएफ'}


# ---------------------------------------------------------------- persistence

def latest(tx, chat_hash):
    row = tx.execute('SELECT * FROM result_reviews WHERE chat_hash=? ORDER BY version DESC LIMIT 1',
                     (chat_hash,)).fetchone()
    return dict(row) if row else None


def latest_delivery(tx, chat_hash):
    row = tx.execute('SELECT * FROM result_deliveries WHERE chat_hash=? ORDER BY version DESC LIMIT 1',
                     (chat_hash,)).fetchone()
    return dict(row) if row else None


def mark_once(tx, chat_hash, version, kind):
    """Idempotency key (chat_hash, version, kind). True the first time only."""
    cur = tx.execute('INSERT INTO delivery_idempotency VALUES(?,?,?,?) ON CONFLICT DO NOTHING',
                     (chat_hash, version, kind, time.time()))
    return cur.rowcount == 1


def cancel_downstream(tx, chat_hash, reason):
    """Undelivered work of earlier approvals is dropped. Already-sent messages stay sent; a call that
    is already dialing cannot be recalled (Bolna) and is left to finish."""
    now = time.time()
    tx.execute("""UPDATE result_deliveries SET pdf_status='cancelled',cancel_reason=?,updated_at=?,lease_owner=NULL,lease_until=NULL
                  WHERE chat_hash=? AND pdf_status IN ('waiting','queued','retrying','generating')""", (reason, now, chat_hash))
    tx.execute("""UPDATE result_deliveries SET call_offer='expired',updated_at=?
                  WHERE chat_hash=? AND call_offer IN ('pending','offered')""", (now, chat_hash))
    tx.execute("""UPDATE result_deliveries SET schemes_status='cancelled',updated_at=?
                  WHERE chat_hash=? AND schemes_status='queued'""", (now, chat_hash))
    cancelled = tx.execute("""SELECT consent_id FROM followup_calls WHERE chat_hash=? AND status='queued'""",
                           (chat_hash,)).fetchall()
    tx.execute("""UPDATE followup_calls SET status='cancelled',phone_cipher='',detail=?,updated_at=?
                  WHERE chat_hash=? AND status='queued'""", (reason, now, chat_hash))
    for row in cancelled:
        tx.execute('UPDATE call_consents SET revoked_at=?,revoke_reason=? WHERE id=? AND revoked_at IS NULL',
                   (now, reason, row['consent_id']))
    if cancelled:
        emit('CALL_FOLLOWUP_SKIPPED', detail=reason)


def supersede(tx, chat_hash, reason):
    now = time.time()
    tx.execute("""UPDATE result_reviews SET status='superseded',closed_at=?,close_reason=?
                  WHERE chat_hash=? AND status IN ('awaiting','changing','confirmed')""", (now, reason, chat_hash))
    cancel_downstream(tx, chat_hash, reason)


def cancel_all(tx, chat_hash, reason='stop'):
    """STOP: cancel everything pending and scrub ciphers/snapshots. Rows keep only digests, statuses
    and timestamps (consent audit) until retention purges them."""
    now = time.time()
    cancel_downstream(tx, chat_hash, reason)
    tx.execute("""UPDATE result_reviews SET status='cancelled',closed_at=?,close_reason=?
                  WHERE chat_hash=? AND status IN ('awaiting','changing','confirmed')""", (now, reason, chat_hash))
    tx.execute("""UPDATE result_deliveries SET address_cipher=NULL,snapshot=NULL,cancelled_at=COALESCE(cancelled_at,?),
                  cancel_reason=COALESCE(cancel_reason,?),updated_at=? WHERE chat_hash=?""", (now, reason, now, chat_hash))
    tx.execute("UPDATE followup_calls SET phone_cipher='',updated_at=? WHERE chat_hash=?", (now, chat_hash))
    tx.execute('UPDATE call_consents SET revoked_at=?,revoke_reason=? WHERE chat_hash=? AND revoked_at IS NULL',
               (now, reason, chat_hash))


def request_review(tx, chat_hash, session_id, profile, lang, event_id, lead=None):
    """Show (or re-show) the review for these facts. Returns (status, text, menu_name) or None when
    the same facts were already approved (the normal reply may then be sent)."""
    lang = _lang(lang)
    h = facts_hash(profile)
    row = latest(tx, chat_hash)
    if row and row['status'] == 'confirmed' and row['facts_hash'] == h and row['session_id'] == session_id:
        return None
    if row and row['status'] == 'awaiting' and row['facts_hash'] == h and row['session_id'] == session_id:
        version = row['version']
        tx.execute('UPDATE result_reviews SET language=? WHERE id=?', (lang, row['id']))
    else:
        supersede(tx, chat_hash, 'facts_changed' if row and row['status'] == 'confirmed' else 'new_review')
        version = (row['version'] if row else 0) + 1
        tx.execute('INSERT INTO result_reviews(id,chat_hash,session_id,version,facts_hash,fields,language,status,'
                   'requested_event,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, chat_hash, session_id, version, h, json.dumps(shown_fields(profile)),
                    lang, 'awaiting', event_id, time.time()))
        emit('WAHA_REVIEW_REQUESTED', event=event_id, version=version)
    text = review_text(profile, lang)
    return 'review_requested', ((lead + '\n\n') if lead else '') + text, f'review.{version}'


def start_change(tx, chat_hash, profile, lang):
    """CHANGE: the current review (awaiting or confirmed) stops being valid; undelivered PDF/call
    work is cancelled; the next answers go through the explicit-correction path."""
    lang = _lang(lang)
    row = latest(tx, chat_hash)
    supersede(tx, chat_hash, 'change')
    if row:
        tx.execute("UPDATE result_reviews SET status='changing',close_reason=NULL,closed_at=NULL WHERE id=?",
                   (row['id'],))
    fields = shown_fields(profile)
    lines = [CHANGE_LEAD[lang], '']
    facts = public_profile(profile)
    lines += [f'{i}. {LABELS[f][lang]}: {display_value(f, facts[f], lang)}' for i, f in enumerate(fields, 1)]
    return '\n'.join(lines + ['', CHANGE_FOOT[lang]]), fields


def confirm(tx, review, event_id):
    tx.execute("UPDATE result_reviews SET status='confirmed',confirmed_at=?,confirm_event=? WHERE id=? AND status='awaiting'",
               (time.time(), event_id, review['id']))
    emit('WAHA_PROFILE_CONFIRMED', event=event_id, version=review['version'])


SNAPSHOT_KEYS = ('id', 'name', 'status', 'status_label', 'verified', 'benefit', 'rules_met', 'check_yourself',
                 'missing_requirements', 'documents', 'how_to_apply', 'apply_url', 'source_url', 'source_site',
                 'agency')


def create_delivery(tx, review, chat, cards, guide_count, lang, event_id):
    now = time.time()
    snapshot = seal(json.dumps({'cards': [{k: c.get(k) for k in SNAPSHOT_KEYS} for c in cards]},
                               ensure_ascii=False, default=str))
    pdf_status = 'waiting' if pdf_enabled() and guide_count else 'skipped'
    pdf_error = None if pdf_status == 'waiting' else ('disabled' if not pdf_enabled() else 'no_official_source')
    tx.execute('INSERT INTO result_deliveries(id,review_id,chat_hash,version,language,address_cipher,snapshot,'
               'scheme_count,guide_count,schemes_event,schemes_status,pdf_status,pdf_error,created_at,updated_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (uuid.uuid4().hex, review['id'], review['chat_hash'], review['version'], _lang(lang), seal(chat),
                snapshot, len(cards), guide_count, event_id, 'queued', pdf_status, pdf_error, now, now))
    return pdf_status


def results_text(decision_text, scheme_count, guide_count, pdf_status, lang):
    lang = _lang(lang)
    head = [RESULTS_LEAD[lang]]
    if pdf_status == 'waiting':
        head.append(PDF_COMING[lang])
        if scheme_count > guide_count:
            head.append(PDF_EXCLUDED[lang].format(n=scheme_count - guide_count))
    elif pdf_enabled() and scheme_count and not guide_count:
        head.append(PDF_NONE[lang])
    if lang in ENGLISH_NOTE:
        head.append(ENGLISH_NOTE[lang])
    lead, foot = ' '.join(head), RESULTS_FOOT[lang]
    room = 3450 - len(lead) - len(foot)
    body = decision_text if len(decision_text) <= room else decision_text[:room - 1].rstrip() + '…'
    return lead + '\n\n' + body + '\n\n' + foot


def event_sent(tx, event_id):
    """The outbox reply for this event was accepted by WAHA: if it carried confirmed results,
    the PDF stage may start now (never earlier)."""
    now = time.time()
    row = tx.execute("SELECT id,pdf_status FROM result_deliveries WHERE schemes_event=? AND schemes_status='queued'",
                     (event_id,)).fetchone()
    if not row:
        return
    tx.execute("UPDATE result_deliveries SET schemes_status='sent',schemes_sent_at=?,updated_at=? WHERE id=?",
               (now, now, row['id']))
    if row['pdf_status'] == 'waiting':
        tx.execute("UPDATE result_deliveries SET pdf_status='queued',pdf_next_attempt=? WHERE id=?", (now, row['id']))
        emit('PDF_DELIVERY_QUEUED', event=event_id)


def guide_command(tx, chat_hash, lang):
    lang = _lang(lang)
    d = latest_delivery(tx, chat_hash)
    review = latest(tx, chat_hash)
    if not d or not review or review['status'] != 'confirmed' or review['version'] != d['version']:
        return 'guide_none', GUIDE_NONE[lang]
    if d['pdf_status'] == 'failed':
        tx.execute("UPDATE result_deliveries SET pdf_status='queued',pdf_attempts=0,pdf_next_attempt=?,pdf_error=NULL,"
                   "updated_at=? WHERE id=?", (time.time(), time.time(), d['id']))
        emit('PDF_DELIVERY_QUEUED', detail='citizen_retry')
        return 'guide_retry', GUIDE_RETRY[lang]
    if d['pdf_status'] == 'sent':
        return 'guide_sent', GUIDE_SENT[lang]
    if d['pdf_status'] in ('waiting', 'queued', 'retrying', 'generating'):
        return 'guide_pending', GUIDE_PENDING[lang]
    return 'guide_unavailable', GUIDE_UNAVAILABLE[lang]


def help_text(tx, chat_hash, lang):
    """Context-aware HELP. Never approves, cancels or sends anything. None = no review context."""
    lang = _lang(lang)
    row = latest(tx, chat_hash)
    if not row or row['status'] == 'cancelled':
        return None
    if row['status'] == 'awaiting':
        return HELP_AWAITING[lang]
    if row['status'] == 'changing':
        return HELP_CHANGING[lang]
    if row['status'] == 'confirmed':
        return HELP_DELIVERED[lang]
    return None


def unseal_snapshot(value):
    return json.loads(unseal(value)) if value else None
