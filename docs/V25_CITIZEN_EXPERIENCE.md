# V2.5 citizen experience: local release and verification

See [the V2.5 critical hotfix audit](HOTFIX_V25_AUDIT.md) for source-backed school-cohort rules, unreviewed eligibility safeguards, explicit fresh/continue identity handling and direct device-audio fallback with Replay. These safeguards supersede the earlier permissive completion behavior. No new migration or required environment setting is introduced.

## Automatic assessment and restored results

Python now bounds assessment to relevant geographic/intent candidates and stops on useful coverage, low question value, exhausted useful questions or a question budget. An extractor completion suggestion cannot bypass this policy. Unknown conditions remain explicit; completion is not official eligibility approval. Default question budget is seven, optionally configured with `ASSESSMENT_QUESTION_BUDGET` (1–12). Repeated prompts are bounded. Chat, WhatsApp and browser voice share this policy and return `assessment_complete`, structured recommendations and a short `spoken_text`.

Completed voice turns atomically save the final snapshot and ended timestamp before returning. Repeated request IDs and manual End do not finalize twice. The dashboard restores the authorized case through `/api/cases/{session_id}`, including the latest completed voice summary, assessment date and retained history. Refresh and Refine results are explicit actions. Only the opaque case ID is kept in sessionStorage; transcripts remain in the case store under existing retention rules. No new migration or required environment variable is introduced by these fixes.

Assistant text appears before speech finishes. Speech uses short utterances, cancellation epochs and a bounded timeout. Server TTS failure falls back to device speech or readable text. Sarvam 402 disables further requests for that server key until process restart/key replacement; 429 has a server cooldown and disables server speech for the current browser call. Unsupported languages fall back without repeated requests in that call. See [Sarvam errors](https://docs.sarvam.ai/api/getting-started/errors-troubleshooting). Logs contain only stage, duration and outcome for STT/inference/TTS; they must not include citizen text or identifiers.

Run the additional browser check against the built frontend proxy with the mock extraction fixture:

```sh
python scripts/check_completion_browser.py --url http://127.0.0.1:8080
```

This checks automatic ending, saved results after navigation/reload, unauthorized restoration, two successful simulated audio turns followed by credit exhaustion, device fallback and manual End. It does not verify real microphone hardware, provider credits or production proxy behavior. Before release, verify a genuine authorized Groq conversation and Sarvam speech on a test case, interrupt playback, navigate away after completion, reload the dashboard, confirm one saved summary, and repeat in English/Hindi/Marathi on mobile. Test provider failure using a staging mock/proxy rather than exhausting paid credits. Run migrations and restart-persistence checks against disposable PostgreSQL separately; SQLite development checks do not establish PostgreSQL connectivity.

This release is implemented on `feature/v2.5-citizen-experience`. No merge, push, production change or deployment was performed. The existing V2.4 Docker/EasyPanel/PostgreSQL/Vercel architecture remains. `/voice` is a browser session; there is no native phone call, native WhatsApp call, LiveKit installation or local GPU service.

## Relevance and safe recommendations

`core/relevance.py` adds explicit requested category, occupation and life-stage priority to `core/questions.py` and `saathi/shortlist.py`. A retired senior requesting financial help prioritizes pension/income exploration instead of a large count of undecided scholarships. Geographic constraints still come from the unchanged deterministic eligibility engine. Relevance does not manufacture a student flag, income, occupation or official threshold. An explicit education request can change the exploration category; unknown facts remain unknown. Category detection uses conservative text cues and an optional validated category label from the extractor, including other Indian languages.

Cards retain catalog benefit text, rule evidence, missing information, caveats, next steps and source URLs. Relevant `needs_info` cards can be shown without implying eligibility. Neither category matching nor ranking establishes official approval. The public scheme catalog was not modified.

The landing and dashboard retain their existing styling/assets and receive shared progressive enhancements through `web/frontend.py`, `citizen.js` and `citizen.css`. The hero explicitly identifies government schemes; Find Schemes, Talk to Saathi, Check Eligibility and Fix a Blocked Benefit are prominent. Saved voice-session history and recommendation snapshots are accessible through the authorized dashboard. `scripts/sync_frontend.py` applies the same enhancement to standalone Vercel files; do not hand-edit the copied files.

## Migrations and runtime settings

Additive migrations:

- `002_voice.sql`: case access grants, encrypted channel routes, one-use link hashes, bounded voice transcripts/snapshots and summary outbox.
- `003_channel_choices.sql`: persistent Chat/Call menu state, so a numeric answer such as “2 children” in chat is not mistaken for selecting Call.

Startup applies these under the existing migration lock. For an explicit operator check:

```sh
python -m web.migrate
python -m web.migrate --purge
```

Use the existing PostgreSQL `DATABASE_URL`, `SESSION_AUTH_REQUIRED=true`, stable random `SESSION_SIGNING_KEY`, exact HTTPS `APP_ORIGINS` and retention configuration. SQLite case storage is a development/test adapter, not the production target. Additional variables are documented in root and deployment `.env.example` templates:

```dotenv
PUBLIC_FRONTEND_ORIGIN=<actual HTTPS frontend origin, no credentials/path/query/fragment>
SARVAM_API_KEY=<runtime secret, backend only>
SARVAM_TTS_MODEL=bulbul:v3
SARVAM_STT_ENABLED=false
SARVAM_STT_MODEL=saaras:v3
```

Leave speech keys absent to use typed input and, where available, device speech. Server STT is opt-in; enable it deliberately after validating the service/account. Never put the Sarvam key in Vercel settings, HTML, JavaScript or a browser request header. The frontend only needs the existing non-secret backend origin at build time.

The Vercel build adds `/voice` with no-store/no-referrer handling. `/api/voice/*` uses the existing same-origin API rewrite. Keep HTTPS and forward cookies; do not enable API caching. Voice requires case storage and authorization even in development. PostgreSQL and Docker execution remain separate verification requirements when unavailable locally.

## Browser voice behavior

`/voice` has explicit Start, Listen, Mute/Unmute, Retry and End controls, typed input, live transcript and listening/transcribing/thinking/speaking feedback. Microphone access is requested only when Listen is chosen. Browser SpeechRecognition is used if available; actual recognition coverage and whether audio leaves the device depend on the browser provider. Automatic text detection does not imply the browser can auto-detect every spoken language: automatic listening uses the device language, with manual override offered.

If browser recognition is unavailable, typed input remains usable. With configured server STT, an explicit Listen action records at most 15 seconds, decodes/resamples locally and uploads mono 16 kHz PCM WAV. The server independently enforces base64/body/byte/format/frame limits (512 KB raw, maximum 15 seconds) and accepts no user-provided file path or remote URL. Audio is not persisted by this application. Permission denial, unsupported decoding, provider failure and timeouts leave a typed fallback and retry controls. Ending or muting prevents late audio from playing or a late transcription from automatically submitting another turn.

Sarvam REST integrations use a fixed HTTPS API origin, server-only `api-subscription-key`, bounded timeouts, bounded streamed responses and safe errors. TTS can synthesize only the latest reply belonging to the caller's authorized active voice session, not arbitrary text from an unauthenticated caller. STT text is checked for sensitive identifiers before it can enter conversation history. When server TTS is absent, device speech can be used if available; it is labelled as device-dependent. Provider requests were tested with mocked transports, not live credentials. [Sarvam TTS REST reference](https://docs.sarvam.ai/api-reference/text-to-speech/convert), [Sarvam STT REST reference](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe).

Every voice turn uses the existing extractor/validated-fact engine. Request IDs prevent repeating a completed turn on retry. A case has at most 10 voice sessions and a session at most 40 turns; only the latest turn retains a full idempotent response cache. Transcript text is minimized using the existing privacy boundary and is cascade-deleted with the case under its absolute retention period. Free text can still contain personal statements; do not ask citizens to speak identity, bank or account numbers.

## Secure WhatsApp-to-browser handoff

1. Preserve the V2.4 signed SHA-512 webhook, timestamp checks and private WAHA configuration.
2. An incoming opted-in START offers **1 Chat** or **2 Call**. Free-text chat also works. After selecting Chat, numbers are treated as answers; the explicit `CALL` command switches to browser voice later, or START opens the menu again.
3. Call creates/reuses the **same** case already associated with that WhatsApp chat. A new link invalidates older outstanding links for the case.
4. The HTTPS link contains a random one-use capability in a URL fragment (`/voice#handoff=…`), which is not sent to HTTP servers/referrers. The page removes the fragment immediately. Merely visiting the page does not redeem it: Start triggers an explicit same-origin POST.
5. Only the SHA-256 token hash is stored in the link table. The token expires after 10 minutes or earlier case expiry. Channel addresses and link-bearing webhook retry responses are encrypted using Fernet with a domain-separated key derived from `SESSION_SIGNING_KEY`; raw tokens/phone addresses do not appear in those database rows.
6. Redemption is transactionally serialized, checks the active opted-in mapping and exact case, marks the token used, and grants the browser's HttpOnly owner capability access to that case. It does not replace another owner's credentials or create an unrelated browser copy. Replay, expired links and STOP-revoked links fail.
7. STOP deletes the local case and its grants, links, voice history and queued summaries, and cancels older webhook responses. A forwarded/stolen unredeemed link remains a bearer capability: do not forward it. HTTPS, short lifetime, one use and no logging/storage of raw links reduce leakage; they do not identify the human holding a deliberately shared link.

Keep `SESSION_SIGNING_KEY` stable. Rotation changes chat pseudonyms and encryption keys, so old pending channel payloads cannot be decrypted without an explicit migration/key-rotation procedure. Do not log webhook bodies, invitation links or owner cookies. The production entrypoint already disables access logging. A user pasting an invitation into ordinary chat is rejected before forwarding it to the LLM or storing it.

## Summary, consent and delivery states

End saves a deterministic ranked recommendation snapshot with source links, missing requirements, caveats and next actions. It does not ask a model to invent a benefit, threshold or official decision. Ending is idempotent and waits for any in-flight turn to finish before creating the snapshot. Transcript/snapshot history remains available through the case's existing authorization and retention policy.

WhatsApp sending requires explicit JSON boolean consent from the authorized citizen and an active linked opted-in route. The citizen may consent before ending or review the summary first and use **Send summary with consent**. One outbox row per call makes repeated requests idempotent. An application worker checks due entries every 10 seconds, using the same per-chat transaction lock as STOP, with exponential retries up to five attempts. Failed sends can be explicitly retried. Pending sends can be cancelled; renewed explicit consent can requeue a cancelled send. Once WAHA has accepted a send, this application cannot recall it.

The UI distinguishes `not_requested`, `pending`, `retrying`, `failed`, `cancelled` and `accepted_by_waha`. A successful WAHA HTTP response establishes API acceptance only; **recipient delivery is never presented as verified**. An ambiguous external-send timeout/crash may duplicate a reply because WAHA acceptance and the database acknowledgement cannot be atomic. Processing is retry-safe for case mutation and queue identity, not a claim of exactly-once recipient delivery. WAHA/provider logs and database backups need their own retention rules.

## Indian-language registry and coverage

`core/languages.py` provides 22 scheduled languages plus English with BCP-47 codes. Odia uses `or-IN` in the application and the provider-specific `od-IN` alias where required. Text detection, manual override, mixed-language context and speech capabilities are separate fields. Registry membership is **not** a claim that every language works.

- English/Hindi/Marathi: existing question text and mocked/browser workflows checked. Synthetic/unavailable question translations fall back to English.
- Bengali/Tamil/Telugu/Kannada/Malayalam/Gujarati/Punjabi: primary validation targets, with multilingual mocked extraction/value-preservation fixtures. Full translated question coverage and real inference/speech quality remain unverified; the UI identifies English question fallback.
- Other registered languages: selectable, detection schema supported, graceful text fallback; no live coverage claim.
- Browser STT: device/provider dependent. Sarvam STT/TTS: provider-documented coverage, live unverified here. The configured Bulbul v3 TTS set is narrower than the 23-language registry; unsupported synthesis returns an explicit fallback response rather than claiming success.

Monetary values, catalog names, source URLs and deterministic eligibility rules are not machine-translated by this release. The model may extract stated facts and requested category in the citizen's language; Python validates facts and decides eligibility.

## Run locally without external credentials (PowerShell)

From the repository root, using a standard Python environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-deploy.txt
New-Item -ItemType Directory -Force artifacts/v25 | Out-Null
Copy-Item data/schemes.db artifacts/v25/catalog.db
$env:DEPLOYMENT_MODE='development'
$env:DATABASE_URL='sqlite:///D:/Scheme_sathi/yojana_full_project_builder/Yojana-Pilot/artifacts/v25/local-cases.db'
$env:SCHEME_DB=(Resolve-Path artifacts/v25/catalog.db).Path
$env:SESSION_AUTH_REQUIRED='true'
$env:SESSION_SIGNING_KEY='local-fixture-key-at-least-32-characters-do-not-use-in-production'
$env:LLM_PROVIDER='mock'
$env:MOCK_LLM_FILE=(Resolve-Path tests/fixtures/deployment_mock.yaml).Path
$env:LLM_LOG='0'
.\.venv\Scripts\python.exe -m web --no-browser --port 8000
```

Open `http://127.0.0.1:8000/`, `/app` or `/voice`. Fixture examples: “I am 62 in Maharashtra”, “मेरी उम्र 62 है”, “माझे वय 62 आहे”. Other fixture-free inputs may report an unavailable mock response; do not mistake fixtures for a real language service. For real local model validation, replace mock settings with your hosted provider configuration, never commit keys, and use synthetic citizen data.

For the standalone frontend/proxy simulation, in separate terminals:

```powershell
.\.venv\Scripts\python.exe scripts/sync_frontend.py
$env:BACKEND_ORIGIN='https://backend.invalid' # Reserved test value; not a deployment URL.
node frontend/build.mjs
.\.venv\Scripts\python.exe scripts/preview_frontend.py --backend http://127.0.0.1:8000 --port 8080
```

The `.invalid` origin verifies build configuration; the local preview explicitly overrides the upstream to loopback. This is not proof of Vercel edge behavior. Use the actual HTTPS origin only when deliberately configuring a real release.

## Tests and manual external checks

```sh
python -m pytest -q
python -m pip check
python scripts/sync_frontend.py --check
node --check web/static/voice.js
node --check web/static/citizen.js
python scripts/check_citizen_browser.py --url http://127.0.0.1:8080
python scripts/deployment_smoke.py --url http://127.0.0.1:8080 --sse
```

The browser script needs development Playwright dependencies and Chromium or `BROWSER_EXECUTABLE`. It tests 1440/1024/768/390/320 px layouts, translated landing headings/actions, Explorer/Rescue, voice page, mocked en/hi/mr typed voice, transcript restoration, mute/end summaries and simulated microphone failure. Actual microphone/STT/TTS quality is not established by simulated events. The older V2.3 `check_dashboard_browser.py` has fixed old hero text expectations and was write-protected in this workspace; use the new V2.5 script for the citizen interface. `check_multilingual_browser.py` remains useful for the existing dashboard flow.

Before release, on a staging system you control:

1. Run the existing Docker/PostgreSQL build/restart smoke, or set a dedicated disposable `TEST_POSTGRES_URL` to run its real database test. Docker/PostgreSQL server binaries were absent locally; skipped tests do not prove those services work.
2. Verify real HTTPS `/voice`, cookie forwarding, microphone permission and typed fallback on Android Chrome and iOS Safari/in-app browser. Compare recognition and playback for the ten priority languages with consenting test speakers. Do not claim coverage from model documentation alone.
3. With runtime Sarvam credentials, enable STT deliberately, check accepted small WAV audio, unsupported formats, permission denial, provider quota/timeout and audio limit errors. Verify no key/audio appears in public assets or application storage. No paid speech call was made during implementation.
4. In a controlled opted-in WhatsApp account: START → 1 Chat → synthetic facts → CALL. Redeem the link once, confirm facts and case ID match, then test replay/expiry, a different browser without a link, and STOP before/after redemption.
5. End without consent: no summary send. Then consent: pending/retrying must not be labelled delivered. Confirm WAHA accepts it, simulate a send failure, retry/cancel safely, and verify STOP prevents new queued sends. Recipient delivery remains unverified unless a future receipt integration establishes it.
6. Review actual hosted Groq/Qwen extraction and contextual follow-ups with synthetic retired-senior examples and each priority language. Mock fixtures are not a real inference claim.

Rollback: retain the V2.4 image/frontend and PostgreSQL backup. Migrations are additive; do not delete tables/volumes or force a destructive downgrade. Disable WAHA/summary sending before rolling back to code that lacks the voice protocol. Old code cannot display V2.5 voice history but leaving it intact permits a reviewed roll-forward. No production rollback/deployment was executed here.
