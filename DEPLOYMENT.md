# Yojana-Pilot V2.4 deployment

Latest WhatsApp release: [YOJANA activation, voice-note configuration, fourth migration and external acceptance](docs/WHATSAPP_FINAL.md). Follow this additive configuration before enabling voice notes; the historical V2.4/V2.5 notes below do not establish a live deployment.

AgentX candidate: [local acceptance results, live prerequisites and judge script](docs/AGENTX_DEMO.md). Do not infer a successful live deployment from mocked/local checks.

Release-blocking V2.5 school-cohort, identity and audio fixes: read [the exact catalog audit and staging checklist](docs/HOTFIX_V25_AUDIT.md). Fresh Start creates a separate citizen case; Continue saved case is explicit. Provisional/manual results are never confirmed eligibility. This hotfix adds no migration or required environment variable. Do not publish before reviewing these changes and completing real PostgreSQL/provider/mobile checks.

V2.5 automatic assessment, durable voice finalization, authorized dashboard restoration and speech fallback are documented in [the V2.5 runbook](docs/V25_CITIZEN_EXPERIENCE.md#automatic-assessment-and-restored-results). These fixes need no new migration or required secret. The optional `ASSESSMENT_QUESTION_BUDGET` defaults to seven. Verify real PostgreSQL and hosted speech/inference in staging before deployment; local mocked tests do not establish external connectivity.

V2.5 citizen experience, browser voice, one-use WhatsApp handoff and consent-based summaries are implemented on `feature/v2.5-citizen-experience`. Read [the V2.5 runbook](docs/V25_CITIZEN_EXPERIENCE.md) for migrations, server-only speech settings, language coverage, local commands and required live checks. The V2.4 infrastructure guidance below remains applicable; V2.5 adds a bounded summary worker and never deploys LiveKit or native phone calling.

Target: Vercel static frontend → HTTPS EasyPanel FastAPI Docker app → private PostgreSQL, with a separate private WAHA service on the existing 6 GB GCP VM. Hosted Groq or Qwen handles inference. No GPU, local model service or LiveKit is included. Nothing has been deployed by this change and no deployment URL has been assigned.

See [the application/origin audit](docs/DEPLOYMENT_AUDIT_V24.md) for preserved routes, access control, privacy and delivery semantics. `deploy/.env.example` is the complete runtime template. Never copy runtime secrets to `frontend/`, GitHub, browser code or the Vercel environment.

## 1. Prepare the release and VM

1. Review the uncommitted changes on `feature/v2.4-live-ai-waha`. Commit/publish only after owner review; this implementation does neither. GitHub/EasyPanel cannot build local unpushed files.
2. Record the exact release commit and Docker image digest after a successful build. Keep the preceding known-good release available.
3. In GCP firewall/network rules, retain HTTPS/HTTP ingress to the existing EasyPanel proxy and restrict administrative access. Do not expose PostgreSQL 5432, WAHA 3000 or backend 8000 directly to the public internet. Preserve unrelated running services.
4. Start with one backend worker and one replica, about 1.5 GB backend memory, 768 MB PostgreSQL and 2 GB WAHA. Leave at least 1–1.5 GB for the OS/EasyPanel/proxy and measure real usage. WAHA engine behavior and build-time memory must be measured on this VM; these are starting budgets, not capacity guarantees.
5. Generate independent URL-safe secrets on a trusted machine with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Generate separate database password, signing key, WAHA API key, WAHA dashboard password and HMAC key. Store them in the appropriate EasyPanel runtime-secret fields. Keep `SESSION_SIGNING_KEY` stable across backend releases; changing it changes WAHA chat pseudonyms.

## 2. Create PostgreSQL in EasyPanel

In the existing project, choose **New Service → Postgres**, name the service, choose the database/user/password, and create it. Use a supported reviewed PostgreSQL 17 image/tag consistent with your operations policy; the staging Compose file pins `postgres:17.6-bookworm` for reproducibility. Copy the **internal connection URL** from **Credentials** to the backend's `DATABASE_URL`. Do not publish a database port. Configure scheduled backups and test restoration to a separate disposable database. EasyPanel's PostgreSQL service uses persistent storage; do not delete that service during application rollback. [EasyPanel Postgres documentation](https://easypanel.io/docs/services/postgres).

PostgreSQL holds citizen cases; the bundled `data/schemes.db` remains a separate public scheme catalog. Runtime catalog scratch files are under `/runtime` and are recreated from the image. Do not use the citizen database for bulk scraping or point `SCHEME_DB` at PostgreSQL.

## 3. Create the EasyPanel backend app

1. Choose **New Service → App**, then **Source → GitHub/Git** and select this repository and the reviewed release branch/commit.
2. Set the build method to **Dockerfile**, path `Dockerfile`, context repository root. Avoid the inline-Dockerfile source option, which does not automatically include repository files.
3. In **Environment**, enter values from `deploy/.env.example` using runtime secrets. The minimum production set is:

```dotenv
DEPLOYMENT_MODE=production
DATABASE_URL=<internal PostgreSQL URL from EasyPanel>
SESSION_AUTH_REQUIRED=true
SESSION_SIGNING_KEY=<generated random signing key, at least 32 characters>
APP_ORIGINS=<exact HTTPS Vercel production/custom origin, no trailing slash>
CASE_RETENTION_DAYS=30
CASE_HISTORY_LIMIT=40
MAX_ACTIVE_CASES=2000
SSE_WINDOW_SECONDS=25
REQUESTS_PER_MINUTE=120
LLM_PROVIDER=openai
LLM_MODEL=<model enabled in your hosted provider account>
OPENAI_BASE_URL=<provider's HTTPS OpenAI-compatible v1 URL>
OPENAI_API_KEY=<runtime secret>
LLM_TIMEOUT_SECONDS=30
LLM_LOG=0
YOJANA_WAHA_ENABLED=false
```

4. In **Advanced/Deploy**, set **one replica**, keep the image CMD, and apply memory/CPU limits. The image binds `0.0.0.0:8000` with one Uvicorn worker and a concurrency limit of 24. Do not override it with multiple Gunicorn/Uvicorn workers.
5. In **Domains**, create/select the real backend hostname, enable HTTPS and point its internal target to **HTTP port 8000**. Do not invent a hostname in configuration before EasyPanel/DNS provides one. Use **Domains**, not publicly published TCP ports.
6. Keep the image's writable `/runtime` available. The sample Compose deployment uses a UID/GID-10001 tmpfs; if adding an EasyPanel mount, ensure that UID can write to it. PostgreSQL provides case durability, so a backend volume is not required. Do not mount over `/app` or the bundled catalog.
7. Set the health check to `/readyz` on port 8000 (image health check is included). Startup validates configuration and runs additive migrations under a PostgreSQL advisory lock before serving. `/healthz` is liveness; `/readyz` also checks schema/catalog. AI/WAHA readiness is separate.
8. Deploy only after reviewing the release. Inspect logs for successful startup and a ready response. No runtime secrets should appear in logs.

These UI choices follow the [EasyPanel App service documentation](https://easypanel.io/docs/services/app). Build the backend before routing Vercel traffic to it. An unconfigured model does not make static pages/Rescue unready, but conversation requests report `not_configured`.

Manual migration/retention commands in the backend service shell:

```sh
python -m web.migrate
python -m web.migrate --purge
```

The versioned SQL files are under `web/migrations/`. Migration 001 creates case sessions/messages/approvals and WAHA mappings/events, independently of the public catalog. They are idempotent and additive; there is no automatic destructive downgrade. Expired cases reject access immediately and are purged at startup and hourly. Monitor cleanup failures; deletion from backups and external providers requires separate policy.

## 4. Configure hosted Groq or Qwen inference

Both use the existing `LLM_PROVIDER=openai` adapter; vendor SDK imports remain inside `llm/providers/`. Set `OPENAI_API_KEY`, `OPENAI_BASE_URL` and an explicit `LLM_MODEL` in EasyPanel only. Leave fallback chains empty initially so requests remain within proxy/client deadlines.

- Groq's compatible API base is `https://api.groq.com/openai/v1`. Select an available extraction-capable model in your own Groq account. [Groq documentation](https://console.groq.com/docs/overview).
- Qwen Model Studio's international compatible base is `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`; use the endpoint for the region/account that issued the key, and a model that account enables. [Qwen's official example](https://qwen.ai/blog?id=qwen3.7&locale=en).

Per-role `EXTRACTOR_*`, `PLANNER_*`, `AGENT_*` settings still work. Do not configure a local/mock provider in production. No local GPU is required. Compatible SDK calls have a configurable 30-second timeout and no SDK automatic retry; JSON/schema validation may perform one correction request. Keep fallback chains disabled until their latency/cost is explicitly tested.

`GET /api/inference/status` returns one of:

| State | Meaning |
| --- | --- |
| `not_configured` | Missing supported provider/key/model configuration. |
| `configured_unverified` | Configuration exists, but no successful request has verified it in this process. |
| `provider_unavailable` | Network/auth/model/response failure; no raw provider exception is exposed. |
| `rate_limited_or_quota_exhausted` | Rate limit or quota failure; conversation returns 429. |
| `working` | A real configured provider call and extraction validation succeeded in this process. |
| `mocked` | Development/test fixture only; never evidence of hosted inference. |

The status resets on process restart. It is not an automatic paid probe. Model quality in Hindi/Marathi still needs genuine account-backed evaluation after connectivity succeeds.

## 5. Deploy the separate WAHA service

Use the supported official `devlikeapro/waha` image. Review the current supported engine/tag for your VM architecture, pull it, and record the immutable image digest (`docker image inspect --format '{{index .RepoDigests 0}}' devlikeapro/waha:<reviewed-tag>`). Put that exact official reference in `WAHA_IMAGE`; the Compose template deliberately has no floating `latest` default. [WAHA quick start](https://waha.devlike.pro/docs/overview/quick-start/).

Choose either an EasyPanel App from that image or a separate Compose service using `deploy/waha.compose.yaml`. For an App:

1. Create the WAHA App, set its reviewed official image reference, one replica, initially 2 GB RAM/one CPU, and a persistent volume mounted at `/app/.sessions`.
2. Set a strong `WAHA_API_KEY`, `WAHA_DASHBOARD_USERNAME`, `WAHA_DASHBOARD_PASSWORD`, and matching `WHATSAPP_SWAGGER_USERNAME`/`WHATSAPP_SWAGGER_PASSWORD`. Disable QR printing with `WAHA_PRINT_QR=false`. The dashboard and Swagger credentials protect those interfaces; the API key separately protects API calls. [WAHA security](https://waha.devlike.pro/docs/how-to/security/).
3. Keep API port 3000 private. Restrict any HTTPS administration domain to trusted IPs/VPN, or use an SSH tunnel. Do not make the pairing QR available to the public. Pair the `default` session interactively in the protected dashboard; do not copy existing session credentials into Git.
4. Configure message webhooks with:

```dotenv
WHATSAPP_HOOK_URL=<real HTTPS backend origin>/api/webhooks/waha
WHATSAPP_HOOK_EVENTS=message
WHATSAPP_HOOK_HMAC_KEY=<independent random HMAC key, at least 32 characters>
WHATSAPP_HOOK_RETRIES_POLICY=exponential
WHATSAPP_HOOK_RETRIES_DELAY_SECONDS=2
WHATSAPP_HOOK_RETRIES_ATTEMPTS=8
```

5. On the backend, set `WAHA_BASE_URL` to the real private WAHA service URL (port 3000), `WAHA_API_KEY` to the same plain API key, `WAHA_SESSION=default`, `WAHA_HMAC_KEY` to the same webhook key, and then `YOJANA_WAHA_ENABLED=true`. Leave the legacy URL-secret setting blank. Redeploy the backend after changing runtime settings.
6. In the test account you control, send **START** yourself to opt in. Then send a synthetic welfare question; later send **STOP**. Do not import contact lists or send to anyone who has not opted in.

WAHA signs exact request bytes with the documented SHA-512 HMAC headers. The backend checks them and, in production, the signed event body timestamp. The public URL contains no secret. [WAHA event authentication](https://github.com/devlikeapro/waha-docs/blob/main/content/docs/how-to/events/index.md), [global webhook settings](https://waha.devlike.pro/docs/how-to/config/).

PostgreSQL retains hashed chat mappings and deduplicated event/outbox state across restarts. A failed outbound send returns 503 so WAHA retries the same event; extraction/state changes are not repeated. STOP clears the local case and cancels older queued replies. An ambiguous send timeout/crash may still duplicate an outbound reply because the external send and DB acknowledgement are not atomic. There is no autonomous outbox scheduler; after configured retries are exhausted, inspect operations and replay the original valid signed event if appropriate. Never change IDs to bypass deduplication.

For plain Compose, keep backend and WAHA on a shared private network: attach backend to the `yojana-waha-private` network created by the WAHA Compose project, or combine the configurations under a common network. `http://waha:3000` resolves only when services actually share that network. EasyPanel Apps instead use the private hostname supplied by EasyPanel; do not guess it. PostgreSQL stays on its own private service connection.

## 6. Connect Vercel to GitHub and configure frontend routing

1. After owner-reviewed publication, create/import the repository in Vercel. Set **Root Directory = `frontend`**, **Framework Preset = Other**, **Node.js = 22.x**, **Build Command = `node build.mjs`**. Keep Output Directory unset so Vercel consumes the generated Build Output API directory. No package dependencies are required.
2. Set only `BACKEND_ORIGIN=<actual HTTPS backend origin>` in Vercel Environment Variables for the intended environment. It must contain no path, credentials, query or fragment. Do not set model, PostgreSQL, WAHA or signing secrets there. Build fails closed if this variable is absent/invalid.
3. `frontend/vercel.json` selects the build. `frontend/build.mjs` creates `.vercel/output/config.json` and `.vercel/output/static` using Vercel Build Output API v3. `/api/*`, `/docs` and `/openapi.json` forward to the backend; `/`, `/app`, `/legacy` serve the copied existing interfaces; `/chat` redirects to `/app`.
4. Deploy the reviewed frontend. Copy its actual HTTPS production/custom origin to backend `APP_ORIGINS` and redeploy the backend. For previews, add only exact reviewed origins; no wildcard CORS/origin allowlist. Set preview BACKEND_ORIGIN to an appropriate staging backend rather than production citizen data.
5. Use the frontend origin for all browser/API interactions. Relative API calls, credentials and SSE stay same-origin. Vercel must forward Cookie, Set-Cookie and Last-Event-ID; verify these using the smoke/browser checks after deployment. Never place owner tokens in a URL.

The rewrite behavior and output format follow [Vercel rewrites](https://vercel.com/docs/routing/rewrites) and [Build Output configuration](https://vercel.com/docs/build-output-api/configuration). API responses disable CDN caching; HTML and unfingerprinted assets revalidate. Vercel documents a 120-second external-proxy timeout; this backend starts SSE immediately and closes/reconnects streams every 25 seconds. Preserve `X-Accel-Buffering: no` through the EasyPanel proxy and do not enable response buffering/caching on API routes. [Vercel limits](https://vercel.com/docs/limits).

Static frontend source is standalone. To update it from preserved backend-served assets:

```sh
python scripts/sync_frontend.py
python scripts/sync_frontend.py --check
```

This copies only an explicit public asset set and injects the deployment enhancement; it does not copy `.env`, Python, databases or keys.

## 7. Test before public traffic

### Offline checks and local proxy simulation

```sh
python -m pip install -r requirements-deploy.txt
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pip check
python scripts/sync_frontend.py --check
```

Build frontend with a reserved test hostname only for configuration validation (not a deployment URL):

```powershell
$env:BACKEND_ORIGIN='https://backend.invalid'
node frontend/build.mjs
```

For local persistence tests set a dedicated SQLite `DATABASE_URL=sqlite:///...` and a **copied** `SCHEME_DB`, `SESSION_AUTH_REQUIRED=true`, `LLM_PROVIDER=mock`, and `MOCK_LLM_FILE=tests/fixtures/deployment_mock.yaml` in development mode. Start `python -m web --no-browser --port 8000`, then separately `python scripts/preview_frontend.py --backend http://127.0.0.1:8000 --port 8080`. This is a local proxy simulation, not Vercel itself.

```sh
python scripts/deployment_smoke.py --url http://127.0.0.1:8080 --sse
python scripts/check_multilingual_browser.py --url http://127.0.0.1:8080
```

Optional responsive/browser coverage uses `scripts/check_dashboard_browser.py`; set `DASHBOARD_URL` to the tested frontend, and install Chromium with `python -m playwright install chromium` or set `BROWSER_EXECUTABLE` to an installed Chromium-family browser. All artifacts are ignored under `artifacts/`.

### Docker and real PostgreSQL checks — require a Docker host

For an automated isolated build/boot/migration/backend-restart/PostgreSQL-restart check, fill the ignored staging environment file first, then run:

```sh
python scripts/docker_smoke.py --env-file deploy/.env --project-name yojana-v24-smoke-review
```

This uses a loopback-only free port, writes a synthetic case directly through the repository, verifies authorized API recovery after each service restart, and deletes only that synthetic case. It does not make model or WhatsApp requests. It leaves its isolated Compose containers/volumes for review; stop that named project manually when finished. Do not point staging tests at a production database.

```sh
docker build -t yojana-pilot:v2.4-review .
```

Copy `deploy/.env.example` to ignored `deploy/.env`, fill real **staging** secrets/origins, choose URL-safe password characters for the Compose URL interpolation, then:

```sh
docker compose --env-file deploy/.env config --quiet
docker compose --env-file deploy/.env up -d --build
docker compose --env-file deploy/.env ps
docker compose --env-file deploy/.env exec backend python -m web.migrate
docker compose --env-file deploy/.env exec backend python -m web.migrate
```

To run the dedicated real-PostgreSQL test inside the backend container against its disposable staging database:

```sh
docker compose --env-file deploy/.env cp tests backend:/tmp/tests
docker compose --env-file deploy/.env exec backend sh -c 'cd /app && TEST_POSTGRES_URL="$DATABASE_URL" PYTHONPATH=/app python -m pytest /tmp/tests/test_deployment_v24.py -k postgres_actual -q'
```

Alternatively install deployment requirements in the test runner and set `TEST_POSTGRES_URL` to a dedicated reachable disposable PostgreSQL URL before running the whole suite. Never use production citizen data for tests.

After a genuine synthetic conversation is saved, refresh the browser to prove recovery. Restart the backend service and refresh again; then restart PostgreSQL and verify readiness/case recovery. The current implementation environment could not execute these Docker/PostgreSQL service-restart checks.

The optional workflow template `deploy/github-actions-deployment.yml` runs real PostgreSQL migrations/tests, frontend build and Docker build/boot on GitHub's Linux runner. To activate it after review, copy it to `.github/workflows/deployment.yml`; this session could not write that protected local directory. It has not run and does not publish/deploy images.

### Test actual deployed origins

Use the **real** frontend origin supplied by Vercel:

```sh
python scripts/deployment_smoke.py --url <actual-HTTPS-frontend-origin> --sse
python scripts/deployment_smoke.py --url <actual-HTTPS-frontend-origin> --live-model
```

The first checks routes/cache/streaming and creates a synthetic legacy session (which may use the configured planner). The second explicitly requests one real hosted extraction and requires provider state `working`; it does not claim success merely because a key is present. Check a contextual follow-up in English/Hindi/Marathi and confirm reload/owner authorization. Test an unrelated browser cannot read/delete the case. Verify no model/database/WAHA secret appears in DevTools responses or static assets.

For WAHA, use a controlled opted-in test account and verify START → question → reply → duplicate webhook → STOP. Reboot/restart services and verify mappings/session pairing survive. Confirm no reply to a never-opted-in chat. Live connectivity and delivery remain unverified until these checks succeed.

## 8. Roll back a broken release

1. Stop sending new frontend traffic to the broken release. In Vercel, promote the prior verified deployment; verify its backend target matches the intended environment.
2. In the EasyPanel backend App, redeploy the recorded previous image digest/commit with its matching runtime configuration. Keep a single replica. Do not delete PostgreSQL or its volume.
3. Do not auto-run destructive down migrations. The V2.4 schema is additive. The V2.3 application cannot display PostgreSQL-backed cases, but leaving tables intact allows roll-forward recovery. Disable WAHA in a rollback to the old in-memory adapter until its behavior is reviewed.
4. If data restoration is required, restore a verified backup into a separate database first, inspect it, and switch the runtime connection deliberately. Do not overwrite the live database blindly. Reassess any expired citizen data before restoration.
5. Run readiness/routes/SSE checks, inspect provider status, then deliberately test one synthetic case. Record the reverted release/digest and the reason. Keep WAHA pairing volume and all existing VM services intact.

## Implementation validation status

Actual local results are recorded in PROGRESS.md. Docker image execution, real PostgreSQL service restart, Vercel deployment, live inference and WAHA delivery are not implied by offline tests or a successful static build. No Git push, cloud deployment, external message or paid model request was performed during implementation.
