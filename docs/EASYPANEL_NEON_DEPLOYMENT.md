# EasyPanel + Neon deployment runbook (yojana-api, yojana-worker)

These steps need your EasyPanel, Neon, WAHA and Vercel access. Nothing here was run against
production by the audit. Never paste secret values into chat, tickets or Git.

## 0. Before you start

1. Merge `fix/neon-worker-voice` into `refactor` through a reviewed PR, then deploy the merge commit.
2. Note the image digest currently running `yojana-api`. That digest is your rollback target.
3. Back up Neon: in the Neon console, create a **branch** of the production database named
   `pre-refactor2-<date>`. It is instant and copy-on-write.

## 1. Environment variables

### yojana-api (public, port 8000)

| Variable | Value / note |
|---|---|
| `DEPLOYMENT_MODE` | `production` |
| `DATABASE_URL` | Neon citizen DB, `postgresql://…?sslmode=require` (existing value: **do not change**) |
| `SCHEME_DATABASE_URL` | Neon scheme DB, `postgresql://…?sslmode=require`. It may be the same database as `DATABASE_URL` (no shared table names). |
| `SESSION_AUTH_REQUIRED` | `true` |
| `SESSION_SIGNING_KEY` | ≥32 chars (existing, **keep**: it keys chat hashes; changing it orphans every WhatsApp opt-in) |
| `APP_ORIGINS` | exact HTTPS Vercel origin(s), comma-separated |
| `PUBLIC_FRONTEND_ORIGIN` | the HTTPS Vercel origin (voice handoff links) |
| `LLM_PROVIDER` / `READER_*` / `READER_FALLBACKS` | hosted providers only (Groq/Gemini). Production rejects `local`/`mock`. |
| `GROQ`/`OPENAI_API_KEY`, `OPENAI_BASE_URL` (https), `GEMINI_API_KEY` | as today |
| `REDIS_URL` | `redis://default:<password>@<valkey-service>:6379/0` (see VALKEY_EASYPANEL.md) |
| `SCHEME_CACHE_VERSION` | bump (for example `v2`) when switching the catalog to Neon |
| `YOJANA_WAHA_ENABLED` | `true` |
| `WAHA_BASE_URL` | private WAHA origin, for example `http://yojana_waha:3000` |
| `WAHA_API_KEY`, `WAHA_SESSION=default`, `WAHA_HMAC_KEY` (≥32) | must match WAHA |
| `WAHA_MEDIA_ORIGINS` | only if WAHA's own `WAHA_BASE_URL` differs (for example `http://localhost:3000`) |
| `STT_PROVIDER=local`, `LOCAL_STT_URL`, `LOCAL_STT_TOKEN`, `LOCAL_STT_TIMEOUT_SECONDS=30` | voice notes through the private yojana-stt service ([LOCAL_STT.md](LOCAL_STT.md)) |
| `SARVAM_STT_ENABLED=false`, `SARVAM_API_KEY` | Sarvam only for TTS. Speech-to-text never goes to Sarvam when `STT_PROVIDER=local`. |
| `SCHEME_CATALOG_TTL_SECONDS` | optional, default 60 |

### yojana-worker (no domain, no port)

The worker uses the same image. Set **command** = `python -m worker`.

| Variable | Value |
|---|---|
| `DEPLOYMENT_MODE` | `production` (the worker then refuses to start without `SCHEME_DATABASE_URL`) |
| `SCHEME_DATABASE_URL` | **the same value as yojana-api** |
| `LLM_PROVIDER`, `EXTRACTOR_*`, `SCOUT_*`, `VERIFIER_*`, provider keys | extraction models |
| `SEARCH_PROVIDER`, `SERPAPI_API_KEY` | optional. Without them, gapfill logs "no SEARCH_PROVIDER" and does nothing paid. |
| `BUDGET_SEARCHES_DAY` (default 8), `BUDGET_TOKENS_GAP` (300000), `BUDGET_TOKENS_BATCH` (1000000) | daily cost caps |
| `WORKER_OUTBOUND_ENABLED` | **leave unset/false**. The worker then never messages people. |
| `YOJANA_PROCESS` | `worker` (optional; the health check also detects `python -m worker`) |

The worker does **not** need `DATABASE_URL`, WAHA, Sarvam or Redis settings. Leave them out.

## 2. Neon: a safe migration

Run from the **yojana-api console** after deploying the new image. These commands are read-only
unless stated otherwise.

```sh
python -m store pg-status        # read-only session: applied/pending migrations, row counts
python -m store stats            # catalog size by origin/state (read-only)
```

Decide from the `pg-status` output:

| pg-status says | Action |
|---|---|
| `pending: none` | Schema is current. Skip to step 3. |
| `pending: 002_agents.sql` and job/conversation/segment_search are 0 or missing | `python -m store pg-init` (applies only the pending files, each in its own transaction). This **writes**. |
| `STOP: 002_agents.sql would drop non-empty …` | Do **not** run pg-init. Export those tables, then plan a hand migration. (The file itself now refuses to drop non-empty tables.) |
| `pending: 001_init.sql` (empty database) | `python -m store pg-init`, then `python -m store pg-import` (copies the bundled SQLite catalog; keeps ids; re-runnable). |

**Never** run `pg-import` into a populated catalog without comparing counts first. It upserts by id and
can overwrite rows that newer extractions changed. Do migration tests only against a disposable Neon
branch or a database whose name ends in `_test` (`TEST_DATABASE_URL`; `tests/test_pg_store.py` empties it).

Parity check (read-only, from a workstation checkout, because `scripts/` is not in the image):
`python scripts/check_pg_parity.py` compares Postgres answers with SQLite answers.

## 3. Deploy yojana-api

1. In the EasyPanel App, set the source to the GitHub repo, branch `refactor`, build type Dockerfile.
2. The build runs `smoke_imports.py`. **A failed build means a missing module or missing ffmpeg OGG/Opus support.**
3. Set the env vars from §1. Domain → port 8000, HTTPS.
4. Deploy. Wait for the container health check (`/readyz`; 60 s start period).
5. Verify:
   ```sh
   curl -s https://<api>/healthz                       # {"status":"alive"}
   curl -s https://<api>/readyz                        # {"status":"ready"}  (Neon catalog + case DB)
   curl -s https://<api>/api/integrations/waha/status  # voice_notes_ready: true
   ```
   In the logs, look for `VOICE_CONFIG_OK`. If you see `VOICE_CONFIG_INCOMPLETE missing=…`, fix the settings it names.

## 4. Deploy yojana-worker

1. Create a new EasyPanel App, `yojana-worker`, with the same repo, branch and Dockerfile (or the same image tag).
2. Override the **command** with `python -m worker`. Add **no domain** and expose no port.
3. Set the env vars from §1. Set the stop grace period ≥30 s if EasyPanel offers it (SIGTERM finishes the current job).
4. Verify from its console:
   ```sh
   python -m worker --status       # backend: postgres, job counts, today's spend
   python /app/healthcheck.py; echo $?   # 0 = heartbeat fresh
   ```
5. Restart test: restart the service. Running jobs are reclaimed after their 600 s lease. Check `--status` again.

## 5. WAHA

See `docs/WHATSAPP_VOICE_DEBUG.md` → Required configuration. Essentials: WAHA's `WAHA_BASE_URL` = the
backend `WAHA_BASE_URL`, `WAHA_EVENTS_DOWNLOAD_MEDIA=true`, webhook
`https://<api>/api/webhooks/waha` for event `message`, with an HMAC key equal to `WAHA_HMAC_KEY`. Keep
WAHA's port private. Pin the WAHA image tag (the deployed engine is NOWEB CORE 2026.10.1).

## 6. Redis / Valkey

From yojana-api: `python -c "import os,redis;print(redis.Redis.from_url(os.environ['REDIS_URL']).ping())"`
should print `True`. The app keeps working if Valkey is down, only slower. Do not restart or flush
production Valkey without agreement. It holds public cache data only.

## 7. Smoke tests after deploy (owner-approved, controlled handset)

1. Web: open the Vercel `/app`, send "I am 62 in Maharashtra", and get a question or results.
2. WhatsApp text: send `YOJANA` → menu; `Chat` → prompt; "I am 62, farmer in Beed" → a reply.
3. WhatsApp voice: send one Hindi note of 5–10 s. Expect a reply, and in the logs
   `WAHA_WEBHOOK_RECEIVED → VOICE_TRANSCRIPT_READY → WAHA_REPLY_SENT` (one paid Sarvam call).
4. Send a 20 s note. Expect the "longer than 15 seconds" reply.
5. `STOP` → confirmation. A later voice note gets no reply (not opted in).
6. Vercel: `BACKEND_ORIGIN=https://<api>` in Production and Preview, deployment branch `refactor`,
   check the deployed commit SHA, and turn off Preview protection for API checks or use a bypass token.

## 8. Rollback

* **api/worker**: redeploy the previous image digest (§0.2). Stop `yojana-worker` if needed. Queued
  jobs stay in Neon and resume later.
* **Catalog**: unset `SCHEME_DATABASE_URL` on yojana-api to return to the bundled SQLite catalog (read-only
  data), then redeploy.
* **Database**: restore from the Neon branch made in §0.3, or point the URLs at it. Citizen data
  (`DATABASE_URL`) is never touched by these migrations.
* **WAHA**: no change in this release. Its webhook URL stays the same.
