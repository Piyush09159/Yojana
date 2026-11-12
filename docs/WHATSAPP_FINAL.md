# Yojana Saathi: final WhatsApp integration

Branch: `feature/v2.5-citizen-experience`. This release descends from and preserves `95b99fccaf6cee2e14471da7b9d0b289605075ae`. Use `git rev-parse HEAD` for the exact final commit and deploy that same revision for frontend and backend. No push or deployment is performed by this change.

## Behavior

- Before activation: silence, including for `START`, app names, substrings, `Chat`, `Call`, numbers and audio. Accept only normalized whole commands `YOJANA`, `yojna`, `योजना`. Direct personal messages only; groups, broadcasts, statuses and bot echoes are ignored.
- After activation: a plain-text localized bullet list offers **Chat** or **Call**. Hindi: `चैट` / `कॉल`; Marathi: `चॅट` / `गप्पा` / `कॉल`. Numeric messages remain conversation data. Ordinary text and voice notes work immediately after activation without repeating the keyword.
- `English`, `Hindi` / `हिंदी`, `Marathi` / `मराठी` switch language without replacing the case. Devanagari text and Sarvam language detection prioritize Hindi/Marathi; English text can switch back. Groq still extracts validated facts. Other language scripts use the extractor's detection and an explicit demo fallback notice. The 23-language registry is retained, not advertised as fully live-tested.
- `Call` creates a one-use HTTPS browser invitation, valid for 10 minutes, bound to the same authorized case. Existing tap interruption, completion, dashboard history, PostgreSQL schema and explicit-consent summaries are preserved.
- `STOP`, `unsubscribe`, `बंद`, `थांबा` revoke the channel, delete its case/grants/call links through existing cascade rules and cancel pending replies. A note transcribed concurrently cannot restore the revoked case.
- Summary API acceptance is never described as recipient delivery.

## Media contract and limits

The repository's WAHA image is deliberately `${WAHA_IMAGE}`: no deployed immutable tag/digest or engine has been provided. Therefore **actual engine compatibility remains UNVERIFIED**. Inspect `/api/server/version`, `/api/sessions` and the installed private Swagger/OpenAPI before enabling this on the paired service; do not invent a pinned version or change an existing working engine.

Implemented contract: signed `event=message`, configured session, `payload.id`, direct `payload.from`, `hasMedia=true`, and `payload.media.{url,mimetype,error}`. No engine-private `_data` fields are relied on. The official [WAHA receive-media documentation](https://waha.devlike.pro/docs/how-to/receive-messages/) specifies authenticated `/api/files/...` retrieval; [security documentation](https://waha.devlike.pro/docs/how-to/security/) describes `X-Api-Key` authentication.

- Backend fetches only the exact scheme/authority of configured `WAHA_BASE_URL`, `/api/files/<single filename>`, with API-key header. No credentials/query/fragment, traversal, redirects, proxy environment or external/S3 origins. `media.url=null` fails safely and asks the citizen to type or retry; this version does not guess an undocumented download endpoint.
- Only documented audio MIME types are accepted. Input capped at 2,000,000 bytes, streamed in 64 KB chunks; bounded connect/read and elapsed download times. Two decoder slots per worker. No audio persisted by the backend.
- ffmpeg is installed in the Docker image. Decoder: one thread, pipe protocol only, restricted demuxers, no shell, 8-second subprocess timeout, at most 16 seconds of mono PCM output. Anything exceeding the shared 15-second STT limit is rejected rather than silently truncated into facts.
- Shared `web/speech.py` validates 16 kHz mono PCM WAV and calls server-only Sarvam transcription with automatic language detection. Existing configured model remains `saaras:v3`; account-backed support must be tested. See [Sarvam REST contract](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe). No optional spoken WhatsApp reply is added; text replies are mandatory.
- Audio/network/STT failures create one localized retry-or-type reply. Successful extraction and reply are committed before send; duplicate webhooks reuse the outbox without repeating case turns. STOP can proceed while media I/O is running; a mapping-version check prevents late transcription from applying after revocation or a newer channel turn.

## Required configuration

Use [deploy/.env.example](../deploy/.env.example). Keep secrets in EasyPanel, never in Vercel public variables or README.

| Service | Variables / settings |
| --- | --- |
| Backend | `DATABASE_URL=postgresql://...`, stable `SESSION_SIGNING_KEY` (32+ characters), `SESSION_AUTH_REQUIRED=true`, exact HTTPS `APP_ORIGINS`, `PUBLIC_FRONTEND_ORIGIN` |
| Backend WhatsApp | `YOJANA_WAHA_ENABLED=true`, private `WAHA_BASE_URL`, `WAHA_API_KEY`, `WAHA_SESSION=default`, `WAHA_HMAC_KEY` (32+ characters) |
| Backend speech | `SARVAM_API_KEY`, `SARVAM_STT_ENABLED=true`, account-supported `SARVAM_STT_MODEL` (existing default `saaras:v3`), `SARVAM_TTS_MODEL=bulbul:v3`, `FFMPEG_BINARY=ffmpeg` |
| Backend Groq | `LLM_PROVIDER=openai`, `OPENAI_BASE_URL=https://api.groq.com/openai/v1`, `OPENAI_API_KEY`, account-available `LLM_MODEL`, `LLM_LOG=0` |
| WAHA service | Reviewed immutable `WAHA_IMAGE`, existing engine/session, same API key; authenticated dashboard/Swagger; persistent `/app/.sessions` volume |
| WAHA webhook | HTTPS backend `/api/webhooks/waha`, `WHATSAPP_HOOK_EVENTS=message`, `WHATSAPP_HOOK_HMAC_KEY` matching backend, exponential retries (2 seconds, 8 attempts) |
| WAHA media | Its `WAHA_BASE_URL` must produce media URLs on the exact same private origin configured on the backend; `WAHA_EVENTS_DOWNLOAD_MEDIA=true`, `WAHA_EVENTS_DOWNLOAD_MEDIA_MIMETYPES=audio` |
| WAHA retention | `WHATSAPP_FILES_FOLDER=/tmp/whatsapp-files`, `WHATSAPP_FILES_LIFETIME=180`; ephemeral bounded tmpfs, never a durable audio volume; media API authentication remains enabled |
| Frontend | Only `BACKEND_ORIGIN` with real backend HTTPS origin; no provider, WAHA or database secrets |

The Compose template sets current documented download/retention options. Confirm these exist on the actual pinned image using its configuration docs; never disable media authentication to make a test pass. See [official WAHA configuration](https://waha.devlike.pro/docs/how-to/config/). Its transient media cache and provider retention are separate from the backend's no-audio-storage guarantee.

Run migrations using the documented entrypoint or `python -c "from web.case_store import migrate; migrate()"` after loading the backend environment. `004_channel_language.sql` is additive and idempotent; the official scheme catalog is unchanged. Preserve existing database volumes and signing keys. Local SQLite is a development adapter, not production persistence evidence.

## Acceptance matrix

| Check | Local evidence | External evidence |
| --- | --- | --- |
| Keyword-only activation / silence | PASS: normalized variants, unrelated commands, groups/bot/broadcasts | UNVERIFIED paired account |
| Named Chat/Call / numeric facts | PASS: signed webhook fixtures | UNVERIFIED paired account |
| Voice note to same case | PASS: synthetic media/STT; real local OGG/Opus decoder | UNVERIFIED WAHA + Sarvam |
| Corrupt/oversized/overlong notes | PASS: actual decoder and bounded HTTP tests | UNVERIFIED installed container |
| Dedup / retry / STOP during media | PASS: one case turn, retry outbox, late note cancelled | UNVERIFIED provider retries |
| Language switch EN/HI/MR | PASS: localized text/fixtures; provider contract mocked | UNVERIFIED real speech accuracy |
| WhatsApp → browser → dashboard | PASS: authorized same-case browser and API tests | UNVERIFIED live HTTPS origins |
| Consent summary / cross-user security | PASS: original recipient, revoke, replay and expiry regressions | UNVERIFIED actual recipient receipt |
| Browser tap interruption / mobile | PASS: simulated media browser checks; 320–1440 px layouts | UNVERIFIED physical phones |
| PostgreSQL | SQLite migrations/persistence PASS | SKIPPED: no `TEST_POSTGRES_URL` |

Full-suite counts are recorded after final validation below. Frontend builds use `https://backend.invalid` only for configuration validation, not as a deployment claim. HTTP smoke uses the local running mock backend. There is no available deployment origin to run an external smoke against.

## External acceptance

1. Supply public frontend/backend URLs and privately inspect the deployed WAHA immutable image, engine, Swagger schema and media configuration. A local schema test is not an inspection of your running service.
2. Configure real PostgreSQL, run all four migrations twice and run the PostgreSQL test against a disposable real database. Restart the backend and verify authorized case restoration.
3. Confirm Groq extraction and Sarvam STT/TTS succeed with the configured account/model. Check one real synthetic English, Hindi and Marathi note. Verify `language_code`, localized follow-up, and unchanged official names/links. Never record an identity number.
4. Reuse the existing WAHA service/session. Pair its QR privately if needed; do not commit a QR. Set the signed webhook directly to the backend. Capture only a redacted sample of the media *schema*, not its private URL, audio or phone number.
5. From a controlled handset send `hello`, `START`, `Chat`, `2`: expect silence. Then `!! YOJANA !!`: expect the list. Send `Chat`, a synthetic situation, and a short OGG/Opus note. Verify only one follow-up per event, plus clear failure for a note over 15 seconds.
6. Switch `Hindi`, `Marathi`, `English` and verify the same case facts. Send `Call`, open the private link once; second redemption and a 10-minute-expired link must fail. Tap Interrupt during speech on both iOS/Android, then complete and inspect `/app` history.
7. Decline summary: no send. Explicitly consent: verify arrival on the original handset and no other number. Revoke before queued send; verify cancellation. WAHA API success alone does not pass receipt verification.
8. Send STOP while a note is processing and while an unredeemed link exists; verify no late facts/reply or valid link remains. Re-activate deliberately with YOJANA for a fresh case.
9. Run `python scripts/deployment_smoke.py --url <real-frontend-HTTPS-origin> --live-model`. Check health/readiness directly on backend too. Add public links, redacted WhatsApp screenshot and demo video to README only after these checks.

## 90-second judge script

- **0–10s:** Show landing page and privacy guidance. “Saathi helps discover schemes; the model explains, deterministic rules decide.”
- **10–25s:** Show pre-activation silence, send YOJANA, choose Chat, give a synthetic senior or college-student scenario. Point out the relevant follow-up and absent school-only recommendations for an adult.
- **25–40s:** Send a short Hindi or Marathi voice note. Show the text follow-up and same case. “Audio is transcribed server-side; no identity numbers are needed.”
- **40–60s:** Send Call, open the one-use browser link, interrupt the spoken reply and refine the facts. “Tap interruption, not a native WhatsApp call.”
- **60–75s:** Complete the assessment and open saved dashboard recommendations with official source links. Show Rescue Mode's evidence-based blocker guidance.
- **75–90s:** Explicitly consent to a summary and show actual receipt only if verified. Send STOP and show revocation. If external services are unavailable, label the fallback fixture demo clearly and explain what remains unverified.

## Final local verification

- Full Python suite: **360 passed, 0 failed, 1 skipped** in 39.93 seconds. The skipped test requires real PostgreSQL (`TEST_POSTGRES_URL`).
- JavaScript syntax checks, 19-file frontend synchronization and standalone frontend build: PASS.
- Standard project virtual environment dependency check: PASS.
- Local HTTP smoke and incremental SSE/resume cursor: PASS against port 8012, explicitly mocked extraction.
- Browser: desktop/tablet/mobile 1440, 1024, 768, 390 and 320 px; English/Hindi/Marathi typed fallback; tap interruption and stale audio/STT; assessment completion, reload, history and consent revocation: PASS with simulated media.
- Actual PostgreSQL, Docker image build, hosted endpoints, real Groq/Sarvam calls, QR pairing and recipient receipt: UNVERIFIED, not silently treated as passing.
- Interface screenshots reviewed: empty local landing/dashboard/voice pages only. No WhatsApp capture, QR, citizen data or private handoff link committed.
- Release-file secret scan: no real credentials found. Existing fake CI PostgreSQL credentials and local test keys were reviewed; virtual environments, runtime artifacts and databases are excluded from release files.
