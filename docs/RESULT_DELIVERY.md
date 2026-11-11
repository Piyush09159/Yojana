# Confirmed WhatsApp results, PDF guide and Bolna follow-up call

Runbook for `feat/confirmed-results-pdf-bolna-followup`. Everything here is **off by default** and is
enabled one flag at a time.

## 0. Security first (before any outbound call is enabled)

`feature/call-agent` committed live-looking Bolna values in `deploy/.env.example` (API key, agent id,
webhook secret). They are **not** on this branch, but deleting them from a file does not remove them
from Git history. Treat them as compromised:

1. In the Bolna dashboard: revoke that API key and create a new one; create a new webhook secret.
2. Put the new values only in the EasyPanel environment (never in the repo, never in chat or logs).
3. Optionally ask the repository owner to delete the `feature/call-agent` branch, or rewrite its
   history, once nothing else needs it. Revocation is what actually protects the account.

`BOLNA_FOLLOWUP_ENABLED` must stay `false` until step 1 is done.

## 1. What the citizen sees (WhatsApp)

```
YOJANA -> language list -> questions / voice notes (unchanged)
assessment complete
  -> review: "Before I send scheme matches, please check what I recorded: Age 62, State Maharashtra,
     Work Retired, Family income under ₹1 lakh. Is this correct?"   [Proceed] [Change] [Help]
PROCEED -> shortlist text (official names in English, lead in the chosen language)
        -> one PDF document: yojana-guide-<lang>.pdf
        -> separate offer: "Would you like one automated follow-up phone call ...?" [Call me] [No call]
CALL ME -> one Bolna call about 8 s later (provider delays can make it later)
```

* The review shows only `age, gender, state, district, occupation, is_student, current_study_level,
  income band, farm land, land acres`. Other facts are counted ("+2 more details ... not shown").
  Never Aadhaar, bank details, date of birth or transcripts. Scheme names never appear in a review.
* Every choice works as an interactive list row (`review:proceed:<version>`, `call:yes:<version>`),
  as a number while that menu is open, or as words in en/hi/mr (`PROCEED`, `आगे बढ़ें`, `पुढे जा`,
  `CHANGE`, `बदलें`, `बदला`, `HELP`, `मदद`, `मदत`, `CALL ME`, `NO CALL`, `GUIDE`). Lists fall back to the
  numbered text when `WAHA_INTERACTIVE_LISTS` is off or WAHA refuses the list.
* `HELP` is context-aware (waiting for review / correcting / after delivery) and never approves,
  cancels or sends anything. `MENU`, `LANGUAGE` and `STOP` work at any point; changing language
  re-shows an open review in the new language.
* `CHANGE` lists the shown facts with numbers. A number asks that one question (short answers like
  "64" are read by code); free text works too. The next answers go through the existing
  explicit-correction path (`correct_facts=True`) and `core/fields` validation. A new review version
  follows and needs a new PROCEED.
* `GUIDE` re-queues a failed PDF; otherwise it says the guide was sent / is being prepared.
* `STOP` deletes the case as before and also cancels every pending PDF/call and scrubs stored
  addresses, phone ciphers and result snapshots.

## 2. State machine and guarantees

```
ASSESSING -> AWAITING_CONFIRMATION (result_reviews.status=awaiting, version v, facts_hash)
   CHANGE  -> changing -> ASSESSING -> AWAITING_CONFIRMATION (v+1)
   HELP    -> AWAITING_CONFIRMATION (unchanged)
   PROCEED -> confirmed -> result_deliveries: schemes queued -> sent (WAHA accepted the outbox reply)
           -> pdf queued -> generating -> sent | retrying -> failed (citizen told; GUIDE retries)
           -> call_offer pending -> offered -> accepted | declined
           -> followup_calls: queued -> dialing -> started -> completed | no_answer | failed
STOP -> cancelled, from any state
```

| Guarantee | How |
|---|---|
| No scheme before PROCEED | The WAHA bridge swaps the final answer for the review when `assessment_complete` and no question is pending. |
| Stale PROCEED cannot release old results | PROCEED re-hashes the case profile; list rows carry the review version. A mismatch shows the current review instead. |
| One result message, one PDF, one call per version | `delivery_idempotency(chat_hash, version, kind)`, `result_deliveries UNIQUE(chat_hash, version)`, `followup_calls.delivery_id UNIQUE`, WAHA event outbox dedupe. |
| PDF only after the scheme text was accepted | `pdf_status` moves `waiting -> queued` only in the outbox success path (`result_review.event_sent`). |
| Call only after text + PDF accepted, explicit consent, trusted number | Offer is sent only when `pdf_status=sent`; CALL ME writes `call_consents`; the runner re-checks everything at dial time. |
| No duplicate calls across restarts | Row committed as `dialing` before the HTTP request; an expired `dialing` lease becomes `failed (interrupted)`, never redialled. Timeouts are not retried (could be ringing); only connect errors and 429/5xx are retried (max 3 tries). Bolna's own `retry_config` is disabled. |
| No number from @lid | Only `<digits>@c.us` (WhatsApp-verified number, country code in `BOLNA_ALLOWED_COUNTRY_CODES`, default 91) is offered; the last 4 digits are shown before consent. @lid chats get no offer. |
| Calls never change facts | The follow-up webhook updates only `followup_calls` (status, provider status). Transcripts and extracted data are not stored. |
| Exactly-once is **not** claimed | WAHA acceptance is not a handset receipt. A crash between WAHA accepting the file and the commit can resend it once after the 3-minute lease. |

## 3. Code map

| File | Role |
|---|---|
| `web/result_review.py` | review texts (en/hi/mr), facts hash, review/delivery state, cancellation |
| `web/waha_bridge.py` | gate on completion; PROCEED/CHANGE/HELP/CALL/GUIDE handling; STOP cancellation; outbox hook |
| `web/whatsapp_menu.py` | versioned row ids, numbered fallback for `review`/`call`/`change`, list payloads |
| `web/whatsapp_delivery.py` | WAHA `/api/sendFile` (base64, PDF magic + size check), PDF stage with leases/backoff, call offer |
| `web/followup_calls.py` | consent, trusted phone, quiet hours, Bolna call runner, status webhook |
| `web/bolna.py` | Bolna HTTP adapter + ported browser verification calls (`BOLNA_VERIFICATION_ENABLED`) |
| `web/delivery_worker.py` | runner: API-startup loop or `python -m web.delivery_worker [--once]`; `/api/health/delivery` |
| `web/migrations/006_bolna_verification.sql`, `007_result_delivery.sql` | additive tables only |

## 4. Environment (yojana-api on EasyPanel)

Existing (unchanged): `DATABASE_URL`, `SESSION_SIGNING_KEY`, `WAHA_BASE_URL`, `WAHA_API_KEY`,
`WAHA_SESSION`, `WAHA_HMAC_KEY`, `YOJANA_WAHA_ENABLED=true`, `WAHA_INTERACTIVE_LISTS`, `STT_PROVIDER`,
`LOCAL_STT_URL`.

| Variable | Value | Notes |
|---|---|---|
| `PDF_SERVICE_URL` | `https://<render domain>` | copy from the Render dashboard, do not guess |
| `PDF_SERVICE_TOKEN` | one of Render's `PDF_SERVICE_TOKENS` | ≥ 24 random chars |
| `PDF_SERVICE_TIMEOUT` / `PDF_SERVICE_RETRIES` | `60` / `2` | |
| `SCHEME_REVIEW_CONFIRMATION_ENABLED` | `false`, then `true` | step 1 |
| `WHATSAPP_PDF_SEND_ENABLED` | `false`, then `true` | step 2; needs the review gate |
| `PDF_DELIVERY_MAX_ATTEMPTS` | `4` | backoff 30 s, 2 min, 10 min |
| `WHATSAPP_PDF_MAX_BYTES` | `8388608` | |
| `BOLNA_FOLLOWUP_ENABLED` | `false`, then `true` | step 3, only after rotation and compliance sign-off |
| `BOLNA_API_KEY` | **rotated** key | |
| `BOLNA_FOLLOWUP_AGENT_ID` | new notification agent | not the verification agent |
| `BOLNA_WEBHOOK_SECRET` | **rotated**, ≥ 24 random chars | part of the webhook URL path |
| `BOLNA_FROM_PHONE_NUMBER` | optional E.164 | default: account number |
| `BOLNA_FOLLOWUP_DELAY_SECONDS` | `8` | clamped 5–600 |
| `BOLNA_ALLOWED_COUNTRY_CODES` | `91` | |
| `CALL_QUIET_HOURS` | `21:00-09:00` | IST; calls are deferred to the window end, skipped if consent would be > 24 h old |
| `DELIVERY_WORKER_ENABLED` | unset (runs in the API) | `false` if you run the runner as its own service |
| `BOLNA_VERIFICATION_ENABLED`, `BOLNA_AGENT_ID` | `false` / empty | browser verification calls, separate |

A separate runner service (optional) needs the same `DATABASE_URL`, `SESSION_SIGNING_KEY`, `WAHA_*`,
`PDF_SERVICE_*`, `BOLNA_*` and the flags; start it with `python -m web.delivery_worker`. Do not add
these to the scheme discovery worker (`python -m worker`).

## 5. Bolna follow-up agent (create a new one)

* Webhook URL: `https://yojana-pilot-yojana-api.gxecwb.easypanel.host/api/bolna/followup/webhook/<BOLNA_WEBHOOK_SECRET>`
  (the browser verification agent uses `/api/bolna/webhook/<secret>`).
* Variables it receives (`user_data`): `service_name`, `language` (en/hi/mr), `scheme_count`, `guide_sent`.
  No name, number, profile or scheme names.
* Disable the agent's own retries and any data-extraction that asks personal questions.
* Script (adapt per language; the agent must not promise approval, collect or change facts):

> Namaste, this is Yojana Saathi, an independent scheme-guidance service. We recently sent your
> shortlisted schemes and application guide on WhatsApp. If any details are wrong, send CHANGE on
> WhatsApp. For help with documents or applying, send HELP. We are not calling from the government,
> and we will never ask for your Aadhaar number, OTP or bank details.

> हिंदी: नमस्ते, मैं योजना साथी से बोल रहा हूँ, यह एक स्वतंत्र योजना-मार्गदर्शन सेवा है। हमने अभी
> WhatsApp पर आपकी चुनी गई योजनाएँ और आवेदन गाइड भेजी है। कोई जानकारी गलत हो तो WhatsApp पर CHANGE
> भेजें। दस्तावेज़ या आवेदन में मदद के लिए HELP भेजें। हम सरकार की ओर से कॉल नहीं कर रहे, और कभी
> आधार नंबर, OTP या बैंक जानकारी नहीं माँगेंगे।

> मराठी: नमस्कार, मी योजना साथीकडून बोलत आहे, ही एक स्वतंत्र योजना-मार्गदर्शन सेवा आहे. आम्ही नुकतीच
> WhatsApp वर तुमची निवडलेली योजना यादी आणि अर्ज मार्गदर्शिका पाठवली आहे. माहिती चुकीची असल्यास
> WhatsApp वर CHANGE पाठवा. कागदपत्रे किंवा अर्जासाठी मदत हवी असल्यास HELP पाठवा. आम्ही सरकारकडून कॉल
> करत नाही, आणि कधीही आधार क्रमांक, OTP किंवा बँक तपशील विचारणार नाही.

Bolna contract used (docs.bolna.ai, checked 2026-11-12): `POST https://api.bolna.ai/call`
`{agent_id, recipient_phone_number, from_phone_number?, user_data, retry_config:{enabled:false}}` →
`{message, status:"queued", execution_id}`; `GET /executions/{id}` → `status` in `queued, initiated,
ringing, in-progress, call-disconnected, completed, busy, no-answer, canceled, failed, stopped, error,
balance-low`. Mapping: `completed`/`call-disconnected` → completed; `no-answer`/`busy` → no_answer;
`failed`/`error`/`canceled`/`stopped`/`balance-low` → failed. **Verify with one test call** before
production: the webhook body field holding the execution id (`id` or `execution_id` both accepted).

## 6. Deployment order

1. Review the PR into `refactor`; tests pass (`python -m pytest -q`).
2. Back up production Neon (branch or `pg_dump`). Create a disposable Neon branch, point a local run
   at it and apply the migrations: `DATABASE_URL=<branch url> python -c "from web import case_store; case_store.migrate(); print(case_store.ready())"`.
   Migrations 006 and 007 only `CREATE TABLE/INDEX IF NOT EXISTS`.
3. Merge; deploy `yojana-api` on EasyPanel with all new flags `false`. Leave WAHA, STT, Valkey and
   Neon services untouched; do not re-pair WAHA.
4. Render `yojana-pdf`: confirm `GET /health` → `status: ok`, `auth_configured: true`; switch its
   branch to `refactor` only after the merged deploy is healthy.
5. Set `PDF_SERVICE_URL/TOKEN`; enable `SCHEME_REVIEW_CONFIRMATION_ENABLED=true`; test on a team phone.
6. Enable `WHATSAPP_PDF_SEND_ENABLED=true`; test.
7. After key rotation and compliance review (consent wording, DND/NCPR, quiet hours): set the Bolna
   variables and `BOLNA_FOLLOWUP_ENABLED=true`; test with one consenting team number.

## 7. Checks

From the EasyPanel yojana-api terminal:

```
python -c "import importlib.util as i; print('pdf_client',bool(i.find_spec('web.pdf_client')),'bolna',bool(i.find_spec('web.bolna')),'delivery',bool(i.find_spec('web.whatsapp_delivery')))"
python -c "import os; print('WAHA_SESSION',os.getenv('WAHA_SESSION')); print('PDF configured',bool(os.getenv('PDF_SERVICE_URL') and os.getenv('PDF_SERVICE_TOKEN'))); print('CALL enabled',os.getenv('BOLNA_FOLLOWUP_ENABLED','false'))"
python -m web.delivery_worker --once        # one pass; prints counts only
```

Public:

```
curl -i https://yojana-pilot-yojana-api.gxecwb.easypanel.host/readyz
curl -s https://yojana-pilot-yojana-api.gxecwb.easypanel.host/api/health/delivery   # flags + last runner pass
curl -i https://<render domain>/health
```

Diagnostic codes in the API log (digests and codes only): `WAHA_REVIEW_REQUESTED`,
`WAHA_PROFILE_CONFIRMED`, `PDF_DELIVERY_QUEUED`, `PDF_DELIVERY_SENT`, `PDF_DELIVERY_RETRY`,
`PDF_DELIVERY_FAILED`, `CALL_FOLLOWUP_QUEUED`, `CALL_FOLLOWUP_STARTED`, `CALL_FOLLOWUP_SKIPPED`
(`detail`: disabled, opted_out, no_consent, stale, expired, deferred_quiet_hours, no_trusted_phone,
citizen_cancelled, ...), `CALL_FOLLOWUP_FAILED`, `CALL_FOLLOWUP_FINISHED`.

Manual run: `YOJANA` → language → answers → review → PROCEED → scheme text → PDF → (optional) CALL ME
→ one Bolna execution. Then try CHANGE, HELP, GUIDE, NO CALL and STOP.

## 8. Rollback

1. `BOLNA_FOLLOWUP_ENABLED=false` (queued calls are skipped with `disabled`; NO CALL keeps working).
2. `WHATSAPP_PDF_SEND_ENABLED=false` (queued PDFs become `skipped`).
3. `SCHEME_REVIEW_CONFIRMATION_ENABLED=false` (back to sending results directly).
Keep the tables; never drop them or volumes.

## 9. Not done / to verify live

* Owner-bound OTP form for @lid chats: not built. @lid chats get no call offer.
* India telemarketing/service-call rules, DND (NCPR) scrubbing and consent wording: need legal review.
  The code enforces opt-in, one call, quiet hours and revocation only.
* NOWEB list rendering on real Android/iOS, live WAHA `sendFile` with a real PDF, Render cold start,
  and one live Bolna call: not run from this branch (offline tests only).
* Migrations were tested on SQLite only; run them on a disposable Neon branch first (section 6, step 2).
