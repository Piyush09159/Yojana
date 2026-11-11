"""Every message the bot sends, built by code in English, Hindi or Marathi. No model writes these,
so they cost nothing, read the same every time, and cannot mention a scheme, amount or link that is
not in the engine's result. (The Replier agent is used only for free questions.)

    question_text(pending, lang)           the next question, with numbered options when short
    results_text(cards, lang, ...)         the 5-scheme answer
    details_text(details, lang)            "reply 1-5" -> one scheme's documents, steps, source
    followup_text(cards, lang)             "I found more schemes" after the gap lane ran
"""
from __future__ import annotations

from .short_answer import choices_of

LANGS = ("en", "hi", "mr")

# Hand-written questions carry text_hi / text_mr in schemes/questions.yaml. These cover questions
# built from fields.yaml (English descriptions only) that people are asked most.
QUESTION_TEXT = {
    "is_student": {"hi": "क्या आप अभी पढ़ाई कर रहे हैं?", "mr": "तुम्ही सध्या शिक्षण घेत आहात का?"},
    "occupation": {"hi": "आप अभी क्या काम करते हैं?", "mr": "तुम्ही सध्या कोणते काम करता?"},
    "annual_family_income_inr": {"hi": "आपके परिवार की सालाना आय कितनी है?", "mr": "तुमच्या कुटुंबाचे वार्षिक उत्पन्न किती आहे?"},
    "state": {"hi": "आप किस राज्य में रहते हैं?", "mr": "तुम्ही कोणत्या राज्यात राहता?"},
    "district": {"hi": "आप किस जिले में रहते हैं?", "mr": "तुम्ही कोणत्या जिल्ह्यात राहता?"},
    "owns_cultivable_land": {"hi": "क्या आपके परिवार के पास खेती की जमीन है?", "mr": "तुमच्या कुटुंबाकडे शेतीची जमीन आहे का?"},
    "residence": {"en": "Do you live in a village or in a town/city?", "hi": "आप गाँव में रहते हैं या शहर में?",
                  "mr": "तुम्ही गावात राहता की शहरात?"},
    "has_bank_account": {"en": "Do you have a bank account?", "hi": "क्या आपका बैंक खाता है?", "mr": "तुमचे बँक खाते आहे का?"},
}
WORDS = {
    "yes_no": {"en": "(yes / no)", "hi": "(हाँ / नहीं)", "mr": "(हो / नाही)"},
    "reply_number": {"en": "Reply with a number:", "hi": "नंबर लिखकर जवाब दें:", "mr": "क्रमांक लिहून उत्तर द्या:"},
    "header": {"en": "Schemes that may fit you:", "hi": "आपके लिए संभव योजनाएँ:", "mr": "तुमच्यासाठी संभाव्य योजना:"},
    "start": {"en": "Start with this one", "hi": "सबसे पहले यह", "mr": "सर्वात आधी ही"},
    "per_year": {"en": "about Rs {v} a year", "hi": "लगभग ₹{v} प्रति वर्ष", "mr": "सुमारे ₹{v} दरवर्षी"},
    "no_amount": {"en": "amount not stated", "hi": "राशि नहीं बताई गई", "mr": "रक्कम दिलेली नाही"},
    "unverified": {"en": "found on {site}, not yet checked by a person",
                   "hi": "{site} पर मिली, अभी किसी व्यक्ति ने जाँच नहीं की",
                   "mr": "{site} वर आढळली, अजून व्यक्तीने तपासलेली नाही"},
    "weak": {"en": "check carefully: {why}", "hi": "ध्यान से जाँचें: {why}", "mr": "काळजीपूर्वक तपासा: {why}"},
    "details_hint": {"en": "Reply 1-{n} for documents and how to apply.",
                     "hi": "दस्तावेज़ और आवेदन का तरीका जानने के लिए 1-{n} लिखें।",
                     "mr": "कागदपत्रे आणि अर्ज कसा करायचा हे जाणून घेण्यासाठी 1-{n} लिहा."},
    "searching": {"en": "I am also checking official government websites for more schemes for you and will message you.",
                  "hi": "मैं आपके लिए सरकारी वेबसाइटों पर और योजनाएँ भी खोज रहा हूँ, मिलने पर संदेश भेजूँगा।",
                  "mr": "मी तुमच्यासाठी सरकारी संकेतस्थळांवर आणखी योजना शोधत आहे, मिळाल्यावर संदेश पाठवीन."},
    "none": {"en": "I could not confirm a scheme for you yet from what you have told me.",
             "hi": "आपकी दी गई जानकारी से अभी कोई योजना पक्की नहीं हो पाई।",
             "mr": "तुम्ही दिलेल्या माहितीवरून अजून कोणतीही योजना निश्चित झाली नाही."},
    "disclaimer": {"en": "This is guidance, not a government service. Check the official page before applying.",
                   "hi": "यह केवल मार्गदर्शन है, सरकारी सेवा नहीं। आवेदन से पहले आधिकारिक पेज देखें।",
                   "mr": "हे फक्त मार्गदर्शन आहे, सरकारी सेवा नाही. अर्ज करण्यापूर्वी अधिकृत पान पहा."},
    "documents": {"en": "Documents", "hi": "दस्तावेज़", "mr": "कागदपत्रे"},
    "how": {"en": "How to apply", "hi": "आवेदन कैसे करें", "mr": "अर्ज कसा करायचा"},
    "check": {"en": "Check yourself", "hi": "खुद जाँचें", "mr": "स्वतः तपासा"},
    "source": {"en": "Source", "hi": "स्रोत", "mr": "स्रोत"},
    "followup": {"en": "Update: I found {n} more scheme(s) that may fit you on official websites:",
                 "hi": "अपडेट: सरकारी वेबसाइटों पर आपके लिए {n} और योजना(एँ) मिलीं:",
                 "mr": "अपडेट: सरकारी संकेतस्थळांवर तुमच्यासाठी आणखी {n} योजना सापडल्या:"},
    "reask": {"en": "Sorry, I could not read that. Please answer simply:", "hi": "माफ़ कीजिए, समझ नहीं पाया। कृपया सरल शब्दों में बताएँ:",
              "mr": "माफ करा, समजले नाही. कृपया सोप्या शब्दांत सांगा:"},
}
NO_LIST_FIELDS = {"state", "district"}        # too many options to list; free text is read instead
MAX_LISTED = 9
# option labels people read in their own language (the stored value stays the fields.yaml key)
LABELS = {
    "hi": {"rural": "गाँव", "urban": "शहर", "male": "पुरुष", "female": "महिला", "transgender": "ट्रांसजेंडर",
           "sc": "अनुसूचित जाति (SC)", "st": "अनुसूचित जनजाति (ST)", "obc": "ओबीसी (OBC)", "ews": "ईडब्ल्यूएस (EWS)",
           "general": "सामान्य", "widowed": "विधवा", "married": "विवाहित", "never_married": "अविवाहित",
           "divorced": "तलाकशुदा", "separated": "अलग रह रहे", "abandoned": "परित्यक्ता"},
    "mr": {"rural": "गाव", "urban": "शहर", "male": "पुरुष", "female": "महिला", "transgender": "तृतीयपंथी",
           "sc": "अनुसूचित जाती (SC)", "st": "अनुसूचित जमाती (ST)", "obc": "ओबीसी (OBC)", "ews": "ईडब्ल्यूएस (EWS)",
           "general": "खुला", "widowed": "विधवा", "married": "विवाहित", "never_married": "अविवाहित",
           "divorced": "घटस्फोटित", "separated": "विभक्त", "abandoned": "परित्यक्ता"},
}


def _l(lang: str) -> str:
    return lang if lang in LANGS else "en"


def w(key: str, lang: str, **kw) -> str:
    return WORDS[key][_l(lang)].format(**kw)


def _label(value: str) -> str:
    return str(value).replace("_", " ")


def question_text(pending: dict, lang: str) -> str:
    lang = _l(lang)
    field = (pending.get("fields") or [pending.get("field")])[0]
    text = (QUESTION_TEXT.get(field, {}).get(lang) or pending.get(f"text_{lang}")
            or QUESTION_TEXT.get(field, {}).get("en") or pending.get("text_en") or pending.get("text") or field)
    qtype = pending.get("type")
    if qtype in ("yes_no", "bool"):
        return f"{text} {w('yes_no', lang)}"
    choices = choices_of(pending) if qtype in ("choice", "enum") else []
    if choices and field not in NO_LIST_FIELDS and len(choices) <= MAX_LISTED:
        names = LABELS.get(lang, {})
        options = "\n".join(f"{i}. {names.get(c, _label(c))}" for i, c in enumerate(choices, 1))
        return f"{text}\n{w('reply_number', lang)}\n{options}"
    return text


def _card_lines(i: int, card: dict, lang: str) -> list[str]:
    head = f"{i}. {card['name']}" + (f"  ({w('start', lang)})" if i == 1 else "")
    value = w("per_year", lang, v=f"{card['annual_value_inr']:,}") if card.get("annual_value_inr") else w("no_amount", lang)
    lines = [head, f"   {value}"]
    why = (card.get("why_ranked") or [])[:1]
    if why:
        lines.append(f"   {why[0]}")
    if not card.get("verified"):
        lines.append(f"   {w('unverified', lang, site=card.get('source_site') or 'an official site')}")
    if not card.get("good", True) and card.get("weak_reason"):
        lines.append(f"   {w('weak', lang, why=card['weak_reason'])}")
    lines.append(f"   {card.get('source_url', '')}")
    return lines


def results_text(cards: list[dict], lang: str, searching: bool = False) -> str:
    if not cards:
        parts = [w("none", lang)]
    else:
        parts = [w("header", lang)]
        for i, card in enumerate(cards, 1):
            parts.append("\n".join(_card_lines(i, card, lang)))
        parts.append(w("details_hint", lang, n=len(cards)).replace("1-1 ", "1 "))
    if searching:
        parts.append(w("searching", lang))
    parts.append(w("disclaimer", lang))
    return "\n\n".join(parts)


def details_text(details: dict, lang: str) -> str:
    lines = [details["name"]]
    if details.get("benefit"):
        lines.append(details["benefit"])
    if details.get("documents"):
        lines.append(f"{w('documents', lang)}: " + ", ".join(details["documents"]))
    if details.get("how_to_apply"):
        lines.append(f"{w('how', lang)}: {details['how_to_apply']}")
    if details.get("check_yourself"):
        lines.append(f"{w('check', lang)}: " + "; ".join(details["check_yourself"][:3]))
    lines.append(f"{w('source', lang)}: {details.get('source_url', '')}")
    return "\n".join(lines)


def followup_text(cards: list[dict], lang: str) -> str:
    parts = [w("followup", lang, n=len(cards))]
    for i, card in enumerate(cards, 1):
        parts.append("\n".join(_card_lines(i, card, lang)))
    return "\n\n".join(parts)
