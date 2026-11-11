# WhatsApp menus and multilingual conversation

Code: `web/whatsapp_menu.py` (menus, option ids, list payload), `web/waha_bridge.py` (`_handle`, `_deliver`),
`core/language_detect.py` (turn language, switch requests), `web/conversation_api.py` (shared switch handling),
migration `web/migrations/005_channel_menu.sql` (applied automatically at API startup).
Tests: `tests/test_whatsapp_menus.py`, `tests/test_multilingual.py`.

## The flow a citizen sees

1. **YOJANA** (or योजना) → opt-in plus the trilingual **language menu**. The citizen's existing case is never touched;
   sending YOJANA again only reopens the menu.
2. Choosing a language → **main menu** in that language: Chat · Browser voice call · Scheme updates · Change language.
3. Nobody has to pick from a menu. Any question or voice note is answered straight away, and closes the menu.

### Example messages (exact text sent)

Language menu (after YOJANA):

```
🙏 Welcome to Yojana Saathi · योजना साथी में आपका स्वागत है · योजना साथीमध्ये आपले स्वागत आहे

Choose your language · अपनी भाषा चुनें · तुमची भाषा निवडा:

1. English
2. हिंदी (Hindi)
3. मराठी (Marathi)

Reply 1, 2 or 3 · 1, 2 या 3 लिखें · 1, 2 किंवा 3 पाठवा

STOP: unsubscribe · बंद करें · थांबवा
```

English main menu:

```
Okay, we'll talk in English.

How would you like to continue? You can also just type your question or send a voice note.

1. *Chat* — Type or send voice notes (up to 15 seconds)
2. *Browser voice call* — Talk in a private browser page
3. *Scheme updates* — Optional alerts about reviewed schemes
4. *Change language* — English, हिंदी, मराठी

Reply with a number. MENU, LANGUAGE, HELP and STOP work at any time.
Never send Aadhaar, bank or identity numbers. Independent guidance; not a government service.
```

Hindi main menu:

```
ठीक है, अब हिंदी में बात करेंगे।

आप कैसे आगे बढ़ना चाहेंगे? आप सीधे अपना सवाल लिख सकते हैं या वॉइस नोट भेज सकते हैं।

1. *चैट* — लिखें या वॉइस नोट भेजें (15 सेकंड तक)
2. *ब्राउज़र वॉइस कॉल* — निजी ब्राउज़र पेज पर बात करें
3. *योजना अपडेट* — समीक्षा की गई योजनाओं की वैकल्पिक सूचना
4. *भाषा बदलें* — English, हिंदी, मराठी

नंबर लिखकर जवाब दें। MENU, भाषा, HELP और STOP कभी भी काम करते हैं।
आधार, बैंक या पहचान नंबर कभी न भेजें। यह स्वतंत्र मार्गदर्शन है, सरकारी सेवा नहीं।
```

Marathi main menu:

```
ठीक आहे, आता मराठीत बोलूया.

पुढे कसे जायचे? तुम्ही थेट प्रश्न लिहू शकता किंवा व्हॉइस नोट पाठवू शकता.

1. *चॅट* — लिहा किंवा व्हॉइस नोट पाठवा (15 सेकंदांपर्यंत)
2. *ब्राउझर व्हॉइस कॉल* — खाजगी ब्राउझर पेजवर बोला
3. *योजना अपडेट्स* — तपासलेल्या योजनांच्या ऐच्छिक सूचना
4. *भाषा बदला* — English, हिंदी, मराठी

क्रमांक पाठवा. MENU, भाषा, HELP आणि STOP कधीही वापरता येतात.
आधार, बँक किंवा ओळख क्रमांक कधीही पाठवू नका. हे स्वतंत्र मार्गदर्शन आहे, सरकारी सेवा नाही.
```

When a case already has answers, the first line is instead "Okay, I'll continue in English. Your answers so far are
kept." (or the Hindi/Marathi version). If a question is pending, that question is asked again in the new
language instead of showing the menu.

## How a choice is recognised

Every option has a stable id: `lang:en|hi|mr` and `menu:chat|call|updates|language`. Display labels decide nothing.
They are checked in this order:

| Source | Example | When it applies |
|---|---|---|
| List row id | `_data.message.listResponseMessage.singleSelectReply.selectedRowId = "lang:mr"` | always (unknown ids are ignored) |
| Row title in `body` | `मराठी (Marathi)`, `ब्राउझर व्हॉइस कॉल\nखाजगी…` | always (first line) |
| Number | `3` | only while that menu was the last thing sent, for at most 1 hour |
| Words | `chat`, `कॉल`, `Change language`, `भाषा बदला`, `Hindi`, `मराठीत बोला`, `speak in English` | always |

A number answering an assessment question ("How many acres?" → `2`) is therefore always data. An out-of-range
number while a menu is open gets "Please reply with one of the numbers shown…" and the menu again.

**Commands at any point:** `MENU`/मेनू/पर्याय, `LANGUAGE`/भाषा, `HELP`/मदद/मदत, `STOP`, `UPDATES`/`UPDATES OFF`.
None of them restarts the assessment. STOP deletes the case and the menu/language rows (cascade); it is accepted
only typed, never from a voice note.

### WAHA NOWEB list payload (verified from source, not yet from a live phone)

Checked in WAHA's source (`devlikeapro/waha`, branch `core`, `src/core/engines/noweb/session.noweb.core.ts`):

- `POST /api/sendList` exists for NOWEB, for 1:1 chats only. Body:
  `{session, chatId, message: {title, description, footer, button, sections: [{title, rows: [{title, rowId, description}]}]}}`.
- The incoming list reply is a normal `message` event. `extractBody` sets `body` to the row title and description
  joined by a newline, `hasMedia` is false, and the raw Baileys message is in `_data`, so the row id is at
  `_data.message.listResponseMessage.singleSelectReply.selectedRowId`. The ephemeral and view-once wrappers are also
  handled.
- WAHA's docs warn: "List Messages are fragile creatures and may stop working at any time" and recommend a text
  fallback. Deprecated buttons are not used.

**Behaviour:**
- With `WAHA_INTERACTIVE_LISTS=true`, a menu is sent as a list. If WAHA returns any HTTP error or a network error, the
  same menu goes out as numbered text and `WAHA_LIST_FALLBACK` is logged.
- **Default is `false` (numbered text).** WAHA can accept a list that the phone then fails to render, and we cannot
  detect that. Turn it on only after the checklist below shows the list on real Android and iOS phones.
- Either way, the numbered text, numbers and words keep working.

## Two language concepts

| | Stored in | Set by | Used for |
|---|---|---|---|
| **preferred language** | `channel_menus.preferred_language` (WhatsApp); `state.preferred_lang` (case, all channels) | menu choice, "मराठीत बोला", voice-page selector | replies, until changed |
| **turn language** | not stored; `turn_language` in the API result | `core/language_detect.detect`, or the STT language for voice | replies when there is no preference |

Rules (`waha_bridge._handle`, `conversation_api._conversation`):

- **No preference:** reply in this message's language. If detection is unsure ("ok", "62", an evenly split
  Hinglish sentence), keep the previous language.
- **With a preference:** reply in it, even when the message is in another language. A one-time hint, such as "To continue
  in English, reply ENGLISH.", tells the citizen how to switch.
- **Explicit switch at any time:** a whole message such as `Hindi`, `मराठी`, `speak in English`,
  `हिंदी में बात करो`, `मराठीत बोला`, `change language to marathi`, `भाषा मराठी`.
  - Answers that only *mention* a language ("Marathi medium school", "मी मराठी आहे") are not switches.
  - A switch makes no model call. The profile, case id, `asked`, `question_count` and pending question are kept, and
    the pending question is asked again in the new language.
- **Code-mixed text:**
  - Devanagari decides between Hindi and Marathi (function words such as है/हूँ vs आहे/मला/पाहिजे). English words
    mixed in do not make the message English.
  - Romanised Hindi or Marathi ("mujhe pension chahiye", "mala pension pahije") is recognised.
- **Scheme names, amounts and eligibility wording are never machine-translated.**
  - Questions come from the fixed en/hi/mr question bank.
  - Final shortlists keep the catalog's official names.
  - When a question has no translation, it is asked in English.

## Voice notes: uncertain numbers are read back

Local STT (Whisper small, CPU) is weak in Marathi: 55% CER on the benchmark clips; see `LOCAL_STT.md`. A misheard
age or income would silently change eligibility. So when a voice note **states a number** (digits, or number words in
en/hi/mr) and either of these is true:

- the language is in `VOICE_CONFIRM_LANGUAGES` (default `mr`), or
- the engine's confidence is below `VOICE_CONFIRM_BELOW` (default 0.75),

the bridge does not use the note yet. It replies:

> मी ऐकले: “मी 62 वर्षांचा आहे” — हे बरोबर आहे का? पुढे जाण्यासाठी हो पाठवा, किंवा योग्य संदेश लिहा.

What happens next:

- **YES** (yes, ok, हाँ, हो, होय, बरोबर…) within 15 minutes: the stored transcript is used as the message.
- **NO** (no, नहीं, नाही, चूक…): it is discarded.
- **Anything else:** it is discarded and the new message is handled normally, so a typed correction simply replaces it.

Storage: the pending transcript and the read-back reply are encrypted at rest (`seal`). The log line
`VOICE_CONFIRMATION_REQUESTED` carries only the event id, language and a rounded confidence.

**Why 0.75?** `confidence` is exp(mean token log-probability) from the STT service. On the 20 FLEURS clips:

- English: 0.53–0.98. The one bad clip (CER 0.31) scored 0.53.
- Hindi: 0.64–0.74, with CER 0.21–0.37.
- Marathi: 0.61–0.76. One clip scored **0.76 with CER 1.0**.

So confidence alone cannot protect Marathi, which is why Marathi numbers are always read back. Hindi numbers are
mostly read back at 0.75; clear English is not.

Voice notes can also switch language: a spoken "हिंदी में बात करो" switches like the typed phrase. Local STT stays
the only engine; nothing falls back to Sarvam.

## EasyPanel changes (yojana-api)

No new service is needed. Migration 005 is applied automatically when the API starts (`case_store.migrate()` in
`web/runtime.py`).

| Variable | Value | Note |
|---|---|---|
| `WAHA_INTERACTIVE_LISTS` | `false` | Set `true` only after checklist step 3 passes on real phones |
| `VOICE_CONFIRM_BELOW` | `0.75` | optional; default shown |
| `VOICE_CONFIRM_LANGUAGES` | `mr` | optional; `mr,hi` reads back every Hindi number too; empty = confidence only |

Leave every existing variable unchanged (`STT_PROVIDER=local`, `LOCAL_STT_URL`, WAHA, HMAC and so on).

**Deploy steps:**

1. Merge the branch. EasyPanel redeploys yojana-api and yojana-stt (the STT image only gained the `confidence` field).
2. Expect these log lines: `VOICE_CONFIG_OK`, then `WAHA_WEBHOOK_RECEIVED` and `WAHA_REPLY_SENT result=started` on
   your first YOJANA.

**Rollback:** redeploy the previous image. Migration 005 only adds a table, and the old code ignores it.

## Manual WhatsApp acceptance checklist (real phone, real WAHA)

Use a test number that is opted in for this purpose.

1. Send `YOJANA`.
   - The trilingual numbered menu arrives.
   - Logs show `result=started` and no phone number.
2. Reply `3`.
   - The Marathi main menu arrives.
   - Reply `9`: "कृपया दाखवलेल्या क्रमांकांपैकी…" plus the menu.
   - Reply `1`: chat is selected.
3. **Lists:** set `WAHA_INTERACTIVE_LISTS=true` and redeploy.
   - Send `YOJANA`. A list with a "Language · भाषा" button appears on **Android** and on **iOS**.
   - Pick हिंदी. A Hindi main *list* arrives, and logs show `WAHA_LIST_SENT`.
   - If either phone shows nothing or "unsupported message", set the flag back to `false`.
4. Type `मी 62 वर्षांचा आहे, मला पेन्शन हवी आहे` and answer one question.
   - Then send `MENU`, then `1`: the reply says "मागील प्रश्न" and repeats the same question.
5. Send `speak in English`, then `हिंदी में बात करो`. Each time the same pending question comes back in that language.
6. Send `My son studies in a Marathi medium school`. This is a normal answer, not a language switch.
7. Send a Marathi voice note with your age.
   - You get "मी ऐकले: …".
   - Reply `होय`: the assessment continues.
   - Repeat and reply `नाही`: the note is discarded.
8. Send a Hindi voice note without numbers. It is answered directly, with no read-back.
9. Send an English text after the Marathi voice note. The reply stays Marathi (the preference is set) and adds the
   one-time hint "To continue in English, reply ENGLISH."
10. Pick `2` (Browser voice call). The one-use link opens the voice page already set to your language.
11. Pick `3` (Scheme updates): `alerts_on`. Then send `UPDATES OFF`: alerts stop, and chat still works.
12. Send `STOP`. Everything is cleared, and further messages get no reply until the next `YOJANA`.

## Known limitations

- **Marathi STT quality** is the main limit. Reading numbers back prevents silent wrong facts, but free text can still be
  misheard; see `LOCAL_STT.md` for the IndicConformer next step.
- **Interactive lists** were verified only against WAHA's source and mocked HTTP. Rendering on real phones is unverified,
  hence default off.
- **Language detection** is a word-list heuristic for en/hi/mr. Other scheduled languages reach the model as before,
  with an English-fallback notice.
- **Final shortlists** (scheme cards) are rendered in English with official scheme names. Only questions and fixed
  guidance are localised.
