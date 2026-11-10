> Updated WhatsApp voice-note setup and release status: [WhatsApp final integration](WHATSAPP_FINAL.md).

# AgentX submission candidate — 2026-10-30

## Release identity and recovery

Keep `feature/v2.5-citizen-experience` for both backend and frontend. Its starting commit is `a4ed680335f5eb20d5a8ec79dbf8cdeace903a74`. The single local release commit containing this runbook is the submission candidate; record its exact SHA with `git rev-parse HEAD` and use that same SHA for both builds. `artifacts/agentx/release.json` records it after committing. Do not submit the old V2.4 or main commit.

The existing uncommitted cohort/eligibility/identity/audio hotfixes were preserved, including `core/cohorts.py`. Before changes, tracked/staged binary patches and copies of untracked files were saved in an external `Yojana-AgentX-recovery-*` temporary folder with a HEAD/branch manifest. No reset, unrelated merge, force-push or deletion of source occurred.

Repository branch intent is confirmed; actual EasyPanel/Vercel deployment branch settings are **UNVERIFIED** without access to those services. No publication or deployment is authorized by this release commit. Review first, then deploy both services from the exact same reviewed candidate SHA.

## Honest acceptance matrix

PASS below describes the explicitly stated local test, not an external service assertion. No external credential is configured in this checkout. No test used a real citizen, live audio recording, real WhatsApp number or paid provider.

| # | Requirement | Local result and evidence | External/live result |
|---|---|---|---|
| 1 | WhatsApp YOJANA/menu | **PASS** signed webhook fixtures offer named `Chat / Call`; numbers remain conversation facts | **UNVERIFIED** QR-paired WAHA |
| 2 | WhatsApp Chat | **PASS** shared conversation/eligibility pipeline; retries do not repeat fact mutation | **UNVERIFIED** Groq + actual WhatsApp |
| 3 | WhatsApp Call link | **PASS** unique HTTPS fragment capability, same authorized case, at most 10-minute lifetime | **UNVERIFIED** actual mobile invitation |
| 4 | One-time redemption | **PASS** expiry/replay/STOP/access grants and encrypted route fixtures | **UNVERIFIED** deployed HTTPS/cookie proxy |
| 5 | Browser voice/interruption | **PASS** tap cancels pending TTS, audio and synthesis; stale capture/STT discarded; next turn once; denial/typed fallback | **UNVERIFIED** physical iOS/Android microphone, audible Sarvam/device voice |
| 6 | Auto assessment/safe schemes | **PASS** shipped catalog college/senior regressions, source-audited cohorts, missing facts and provisional results | **UNVERIFIED** real-model extraction quality |
| 7 | Dashboard history | **PASS** authorized reload restores facts, transcript and shortlist; fresh citizen has separate case | **UNVERIFIED** real PostgreSQL restart |
| 8 | Consent/summary | **PASS** one queue row, five-attempt bound, revocation including in-flight consent, correct recipient only; WAHA acceptance != delivery | **UNVERIFIED** provider send and recipient receipt |
| 9 | STOP/replay/expiry | **PASS** signed duplicate events, stale invitation rejection, STOP deletes grants/case and cancels pending work | **UNVERIFIED** actual WAHA retry/restart |
| 10 | English/Hindi/Marathi | **PASS** synthetic multi-turn fixtures, localized key questions/short conclusions, browser workflows; all 23 registry entries retained | **UNVERIFIED** live Groq/Sarvam language quality; registry membership is not coverage |

**312 Python tests passed, one real-PostgreSQL test skipped, no failures.** Baseline before integration was 306 passed/one skipped. The new six tests cover multilingual completion/history, literal menu choices, correct recipient and retry/revocation. Existing hotfix regressions are intact.

Frontend build, all JavaScript syntax checks, 19-asset frontend synchronization, pip dependency checks and local route/readiness/SSE smoke checks pass. Citizen layouts pass at 1440/1024/768/390/320px. Speech tests cover decode failure, denied autoplay, silent playback, timeout, 402/429/504, no device voice, final replies, replay and stale audio. The new browser acceptance covers tap interruption in pending TTS/server audio/device speech, permission denial, typed fallback, stale STT and in-flight summary revocation. These media/provider events are simulated.

Migrations `001_cases.sql`, `002_voice.sql`, `003_channel_choices.sql` ran twice successfully against an isolated SQLite development adapter. Real PostgreSQL migration/execution is **UNVERIFIED**: no PostgreSQL/Docker service or `TEST_POSTGRES_URL` is available here. The production code still requires PostgreSQL. The public catalog was copied for browser testing and remains unchanged.

## Changes needed for this demo

- Explicit **Interrupt / Speak Now** control stops active reply audio synchronously, aborts pending TTS and advances the playback epoch. It then requests microphone access from the user's tap. Permission/codec failure leaves typed input available.
- Capture epochs, detached handlers and STT aborts prevent old microphone/transcription callbacks crossing into a new turn/case. Recognition final events submit once.
- Visible states include Listening, Thinking, Speaking, Interrupted feedback and Completed. Completed assessments are saved; use Continue saved case/dashboard refinement for further details. The interrupt button applies to an active reply; Mute can stop the final announcement.
- This is **tap-to-interrupt only**, not acoustic hands-free/full-duplex speech detection.
- Unchecking consent cancels pending work, including when the queue request was in flight. Accepted WAHA sends cannot be recalled and never prove recipient delivery.
- Key Hindi/Marathi questions and short concluding speech are localized. Catalog names, amounts, predicates and full source evidence are not invented or machine-translated.

## Demo entry points

Use your **actual HTTPS frontend origin** plus `/`, `/app` and `/voice`. No real public origin has been supplied, so a fabricated deployment link is not listed. The local fixture server used during acceptance is `http://127.0.0.1:8011/`; it uses mocked extraction and SQLite, not live services.

### Reproduce the local fallback honestly (PowerShell)

From the existing repository, after setting up the standard project `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-deploy.txt -r requirements-dev.txt
New-Item -ItemType Directory -Force artifacts/agentx/tmp | Out-Null
$env:TEMP = (Resolve-Path artifacts/agentx/tmp).Path
$env:TMP = $env:TEMP
$env:DATABASE_URL = 'sqlite:///artifacts/agentx/demo-cases.db'
Copy-Item data/schemes.db artifacts/agentx/demo-catalog.db
$env:SCHEME_DB = 'artifacts/agentx/demo-catalog.db'
$env:DEPLOYMENT_MODE = 'development'
$env:SESSION_AUTH_REQUIRED = 'true'
$env:SESSION_SIGNING_KEY = (& .\.venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(48))')
$env:LLM_PROVIDER = 'mock'
$env:MOCK_LLM_FILE = 'tests/fixtures/deployment_mock.yaml'
$env:LLM_LOG = '0'
.\.venv\Scripts\python.exe -m web.migrate
.\.venv\Scripts\python.exe -m web --no-browser --port 8011
```

The temp override avoids the full system temp drive encountered during validation. Do not replace a real deployment database with these fixture settings. If port 8011 is occupied, stop only your known fixture server or choose another port. Never call this fallback a live AI/WhatsApp demonstration.

## Exact remaining human actions for the live demo

1. Supply the real frontend/backend HTTPS origins and existing EasyPanel service names. Reuse existing WAHA/PostgreSQL services and volumes. Review the candidate commit and authorize any push/deployment separately.
2. In the backend's private EasyPanel Environment settings, populate the existing `deploy/.env.example`: `DEPLOYMENT_MODE=production`, a real `postgresql://` `DATABASE_URL`, `SESSION_AUTH_REQUIRED=true`, stable random `SESSION_SIGNING_KEY` (32+ characters), exact HTTPS `APP_ORIGINS`, and `PUBLIC_FRONTEND_ORIGIN`. Do not rotate an existing signing key during the demo: it also protects stored channel routes.
3. For Groq use `LLM_PROVIDER=openai`, `OPENAI_BASE_URL=https://api.groq.com/openai/v1`, private `OPENAI_API_KEY` containing the Groq key, and an explicitly available `EXTRACTOR_MODEL`/`LLM_MODEL` from that account. Resolve any per-role overrides from an old deployment; never use `mock` in production. Set `LLM_LOG=0`.
4. Configure backend-only `SARVAM_API_KEY`, `SARVAM_TTS_MODEL=bulbul:v3`, `SARVAM_STT_ENABLED=true`, `SARVAM_STT_MODEL=saaras:v3`. Keep the existing fixed REST adapter. Browser microphone requires HTTPS and a user gesture; iOS/Android permission/autoplay restrictions still apply. Do not put any key in Vercel or frontend code.
5. Vercel: use the reviewed candidate branch/commit, Root Directory `frontend`, Node 22, Build Command `node build.mjs`. Set only the real HTTPS `BACKEND_ORIGIN`. Verify `/api/*` forwarding preserves cookies and no-store headers. Both frontend and backend must run the same SHA.
6. Run `python -m web.migrate` **twice** in the backend service; check readiness. With a separate disposable staging database, set `TEST_POSTGRES_URL` and run the real PostgreSQL test before claiming PostgreSQL acceptance. Never point that test at live citizen data.
7. Execute `python scripts/deployment_smoke.py --url <actual-HTTPS-frontend-origin> --live-model` for synthetic extraction. Speak one synthetic English/Hindi/Marathi turn and hear each answer. Interrupt during the second spoken reply; confirm no old audio resumes and the next turn appears once. Review the authorized dashboard after reload and a backend restart.
8. Pair WAHA yourself as below. Send YOJANA from a controlled account, try Chat, then CALL, open the private invitation and consent to the summary. Confirm the message on the same handset. Test duplicate events, STOP and invitation expiry. API acceptance alone is not recipient delivery evidence.

### WAHA EasyPanel settings (reuse the existing service first)

If no service exists, create one App using the official `devlikeapro/waha` image and pin its reviewed digest. To obtain that digest on the Docker host: `docker pull devlikeapro/waha:latest`, then `docker image inspect --format '{{index .RepoDigests 0}}' devlikeapro/waha:latest`. Use the returned immutable reference in the service. No paid Twilio/native WhatsApp call integration is required.

Use one replica, private container port **3000**, an initial **2 GB RAM / 1 CPU**, and a persistent volume at **`/app/.sessions`**. Keep administration behind trusted access. Set:

```dotenv
WAHA_API_KEY=<private strong API key>
WAHA_DASHBOARD_ENABLED=true
WAHA_DASHBOARD_USERNAME=<private admin name>
WAHA_DASHBOARD_PASSWORD=<private admin password>
WHATSAPP_SWAGGER_USERNAME=<same admin name>
WHATSAPP_SWAGGER_PASSWORD=<same admin password>
WAHA_PRINT_QR=false
WHATSAPP_HOOK_URL=https://<actual-backend-host>/api/webhooks/waha
WHATSAPP_HOOK_EVENTS=message
WHATSAPP_HOOK_HMAC_KEY=<independent private random key, 32+ characters>
WHATSAPP_HOOK_RETRIES_POLICY=exponential
WHATSAPP_HOOK_RETRIES_DELAY_SECONDS=2
WHATSAPP_HOOK_RETRIES_ATTEMPTS=8
```

In the backend set `WAHA_BASE_URL` to EasyPanel's **actual private service hostname**, `WAHA_API_KEY` to the same API key, `WAHA_HMAC_KEY` to the same hook key, `WAHA_SESSION=default`, and `YOJANA_WAHA_ENABLED=true`; leave the legacy URL-secret variable empty. In the protected WAHA dashboard start the `default` session and scan its QR using your controlled WhatsApp account. Do not expose the QR publicly or delete/re-pair an already working session. Confirm signed SHA-512 webhook headers/body and event timestamps reach the backend.

Provider contracts were checked against primary references: [Groq OpenAI compatibility](https://console.groq.com/docs/openai), [Sarvam TTS](https://docs.sarvam.ai/api-reference/text-to-speech/convert), [Sarvam STT](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe), [WAHA events](https://waha.devlike.pro/docs/how-to/events/). Contract review is not a successful account-backed call.

## 90-second judge demonstration

- **0–10s:** Open the public page. “Yojana-Pilot helps citizens understand possible government schemes. Code decides eligibility; AI extracts what the citizen states.” Select Hindi or Marathi.
- **10–25s:** On a paired test WhatsApp account, send YOJANA, choose `Chat`, give a synthetic situation, and show a relevant follow-up. “Missing facts stay unknown.”
- **25–40s:** Send CALL and open its private HTTPS link. “One-time, ten-minute invitation; this continues the same authorized case.” Tap Start and Listen, allowing the microphone.
- **40–55s:** While the assistant speaks, tap **Interrupt / Speak Now**, give a short correction and show the next turn once. “This is reliable tap interruption, not hands-free acoustic barge-in.”
- **55–70s:** Show the completed/provisional scheme cards, missing conditions, documents and official sources. For the college persona, show that school meals/Class 5 admission are excluded. Contrast with the retired senior's pension-oriented guidance.
- **70–85s:** Check explicit consent and request the summary. Show `queued` then `accepted_by_waha`; confirm actual receipt on the same handset separately. Reload the dashboard to show saved history.
- **85–90s:** Send STOP. “No government submission or approval is claimed. Unsupported/live-unverified languages are labelled.”

If live prerequisites are incomplete, demonstrate the clearly labelled synthetic local fallback and state which external checks remain UNVERIFIED. Do not stage fake WhatsApp receipt, provider success or PostgreSQL persistence.
