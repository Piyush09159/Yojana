# Production readiness audit: refactor after refactor2.0 (2026-11-11)

Branch: `fix/neon-worker-voice`, from `refactor` at `9f77874` (Merge PR #4, `refactor2.0`).
Nothing was committed, pushed, merged or deployed. No production database, WAHA account, Valkey
instance or Sarvam account was contacted. All results below come from this workstation unless marked
otherwise.

**Verdict: not production-ready yet.** The code-level blockers below are fixed and covered by tests.
Production readiness still depends on the live checks in the last section: a real Neon connection,
the worker running on EasyPanel, a WhatsApp voice-note round trip, and Vercel talking to the backend.

## 1. Repository state

* `origin/refactor` = `9f77874`. Local `refactor` was 4 commits behind and was fast-forwarded (no local
  changes, nothing rewritten). `refactor2.0` (`ed47494` database, `f9e96a8` orchestration, `23255bf`
  merge) is fully contained in `refactor`.
* The merge added `agents/`, `worker/`, `store/pg.py`, `store/pg_admin.py`, `store/runtime.py`,
  `store/sql/001_init.sql`, `store/sql/002_agents.sql`, `saathi/orchestrator.py` and related files:
  65 files, +5898/−305. There were no conflict markers, and every module imports.
* The merge did not touch `frontend/`, `web/static/`, `web/waha_*.py`, `web/speech.py` or
  `web/public_cache.py`.

## 2. Confirmed blockers (root causes) and fixes

| # | Blocker | Evidence | Fix |
|---|---|---|---|
| B1 | **WhatsApp voice notes were silently dropped** | WAHA NOWEB 2026.10.1 (the deployed engine) builds messages with `body: body \|\| null` ([source](https://github.com/devlikeapro/waha/blob/2026.10.1/src/core/engines/noweb/session.noweb.core.ts#L3542)), and an audio message has no caption, so every voice note arrives with `body: null`. `persistent_event` required `isinstance(text, str)` and returned `{"status": "ignored"}` with HTTP 200, so no reply was sent and WAHA did not retry. Reproduced: `test_noweb_voice_note_with_null_body_gets_a_reply` fails on the old code with `'ignored' == 'replied'`. | `web/waha_bridge.py`: a `null` body on a media message becomes `""`. A text message with a `null` body is still ignored. |
| B2 | **Every WAHA voice-note URL was rejected as untrusted** | WAHA 2026.10.1 local storage key is `{session}/{message.id}.{ext}` ([source](https://github.com/devlikeapro/waha/blob/2026.10.1/src/core/media/local/MediaLocalStorage.ts#L92)), giving `.../api/files/default/<id>.oga`. The allowlist `^/api/files/[A-Za-z0-9_@.%-]+$` forbade the second segment. Once B1 was fixed, every note would have got "couldn't transcribe". | `web/waha_media.py` accepts `/api/files/<file>` and `/api/files/<WAHA_SESSION>/<file>` only. Other sessions, `..`, encoded slashes, query strings and credentials are still rejected. |
| B3 | Fragile media origin | WAHA builds `media.url` from **its own** `WAHA_BASE_URL` (default `http://localhost:3000`). If that differs from the backend's private origin, all notes failed. | Optional `WAHA_MEDIA_ORIGINS` list of advertised origins. Audio is **always fetched from the backend's `WAHA_BASE_URL`**, using only the validated path, so the API key never goes to a host taken from the payload. |
| B4 | Docker image missing `agents/` and `worker/` | `Dockerfile` copied neither. `conversation_api` → `saathi.short_answer`/`sufficiency`, `discovery.discover` → `agents.*`, and `python -m worker` all need them. | Added both `COPY` lines. A build-time `smoke_imports.py` now fails the build on any missing module or an ffmpeg without OGG/Opus. A test checks that every package reachable from `web`/`worker` is copied. |
| B5 | Worker container would be restarted forever | Its `HEALTHCHECK` polled `:8000/readyz`, but the worker serves no HTTP, so it would always be unhealthy and EasyPanel/Swarm would kill it. | `deploy/healthcheck.py`: the api checks `/readyz`, the worker checks a heartbeat file (detected from PID 1's command line or `YOJANA_PROCESS=worker`). |
| B6 | Worker could run against nothing | Without `SCHEME_DATABASE_URL`, the worker used a container-local SQLite file that the api never writes to, so it silently processed nothing. | `python -m worker` exits with code 2 in `DEPLOYMENT_MODE=production` unless `SCHEME_DATABASE_URL` is set. |
| B7 | Destructive migration had no guard | `002_agents.sql` runs `DROP TABLE IF EXISTS job, conversation, segment_search`. It is skipped once recorded in `schema_migration`, but a manual or partial re-run would delete queued jobs and conversations. | A `DO $$` guard raises before any drop if one of those tables holds rows. Added `python -m store pg-status`: a read-only session that lists applied and pending migrations and row counts. |

### Other defects fixed

* **Voice failures now always get a specific text reply.** Before, only some exception types were
  caught, and an unexpected error produced a 500 → WAHA retry → a new download and a new paid Sarvam
  call on each retry → eventually silence. Now every transcription failure gives a localized reply
  (en/hi/mr) that names the reason: too long, voice unavailable, a spoken identity/bank number, or
  generic "please retry".
* **Sarvam is not paid twice for the same note.** When the model returns 429/503, the turn still rolls
  back so WAHA retries, but the transcript is kept in memory (15 min, 64 entries) and reused.
* **Worker shutdown.** SIGTERM finishes the current job, then the worker exits. A job interrupted by
  SIGKILL is reclaimed when its 600 s lease expires (tested).
* **Outbound safety.** `saathi/senders.deliver` (worker follow-ups) now also requires
  `WORKER_OUTBOUND_ENABLED=true`. Before, enabling WAHA for the api was enough for the worker to message people.
* **Neon load.** Every web/WhatsApp message and every 30 s readiness probe loaded the **whole** catalog
  (several MB of JSONB) from Postgres. `discovery.catalog.cached_catalog()` keeps it in memory for
  `SCHEME_CATALOG_TTL_SECONDS` (default 60) and reloads at once after a write in the same process.
  This applies on Postgres only; SQLite behaviour is unchanged.
* **Config validation.** In production, `SCHEME_DATABASE_URL` must be `postgresql://` when set.
  At startup the api logs one `VOICE_CONFIG_OK` / `VOICE_CONFIG_INCOMPLETE` line (setting names
  only). `/api/integrations/waha/status` now reports `voice_notes_ready`.
* **Diagnostics.** `web/diagnostics.py` emits allow-listed JSON lines with the codes
  `WAHA_WEBHOOK_RECEIVED`, `WAHA_WEBHOOK_IGNORED` (with a reason such as `session_mismatch` or
  `not_opted_in`), `WAHA_MEDIA_UNAVAILABLE`, `WAHA_MEDIA_UNSUPPORTED`, `WAHA_MEDIA_UNTRUSTED`,
  `WAHA_MEDIA_DOWNLOAD_FAILED`, `WAHA_AUDIO_DECODE_FAILED`, `VOICE_TOO_LONG`, `SARVAM_STT_DISABLED`,
  `SARVAM_STT_FAILED`, `VOICE_TRANSCRIPT_READY`, `VOICE_CONVERSATION_FAILED`, `WAHA_REPLY_SENT` and
  `WAHA_REPLY_DELIVERY_FAILED`. Event ids are 8-character prefixes of a keyed HMAC. A test asserts that
  no phone number, API key, media path or transcript appears in the logs.

## 3. Architecture findings (not changed)

**Request flow, as it actually runs.** WhatsApp → WAHA → `POST /api/webhooks/waha` (HMAC-SHA512,
128 KB cap, timestamp window) → `persistent_event` (opt-in, dedupe by keyed message id, per-chat
advisory lock) → [voice: download → ffmpeg → Sarvam STT] → `web/conversation_api.conversation()` →
Reader model (`shared_llm("reader")`) or code for short answers → `core` engine (deterministic
eligibility and ranking) on the catalog → reply text → `waha_events` outbox committed →
`/api/sendText`. The web `/app` uses the same `conversation()`.

**The orchestrator is not on the production path.** `saathi/orchestrator.Orchestrator` (Reader and
Replier through `agents.runner.run_agent`) is used only by `python -m saathi` and the worker's
`notify` job. WhatsApp and `/app` use `web/conversation_api.py`, which takes three pieces from the new
design: the shared Reader client with its fallback chain, `saathi.short_answer`, and the gap-fill
queue. Its docstring claimed otherwise and has been corrected.

**Recommendation: do not switch now.** Moving web and WhatsApp to the orchestrator would change the
session format (`case_sessions` JSON vs. the `conversation` table), the response shape that the
dashboard renders (`assessment`, `recommendations`, `spoken_text`, ...), and the consent/STOP and
identity-correction behaviour that the existing 400+ tests pin. Eligibility is already deterministic
on both paths. The smallest safe step later is to have `conversation_api` call
`run_agent(READER, ...)` in place of `understand()` directly, which adds cache and budget accounting
without changing any format.

**Agents.** Scout, Extractor and Verifier run in the worker lanes (`gapfill`, `extract`) and in
`discovery.discover`. Reader and Replier run only in the orchestrator. `run_agent` never raises,
validates output and falls back to code. Each role has its own timeout (`ROLE_TIMEOUTS`). Daily token
caps exist for the `gap`/`batch` lanes; `realtime` is uncapped by default.

**Worker notify reaches nobody on WhatsApp today.** It reads the `conversation` table, which only the
orchestrator writes. WhatsApp scheme alerts are a separate, existing feature (`web/notifications.py`).

**Databases.** `DATABASE_URL` (cases, consent, WAHA outbox; `schema_migrations`) and
`SCHEME_DATABASE_URL` (catalog, jobs, agent cache and budget; `schema_migration`) share **no** table
names, so one Neon database can hold both. The Postgres scheme store reconnects once when Neon drops
an idle connection (`pg._reconnecting`, `runtime._run`). Job claims use `FOR UPDATE SKIP LOCKED` with a
partial unique index on active `dedupe_key`.

**Residual risks.**
* `job_done`/`job_fail` do not check lease ownership. A worker that outlives its lease could finish a
  job that was reclaimed. The handlers are idempotent, so the effect is a duplicate summary row.
* If the model stays down (429/503) through all of WAHA's retries (8, exponential from 2 s, about
  8.5 min), the citizen gets no reply. This is the same for text and voice and was an existing design
  choice.
* `waha_chats` rows are purged after `CASE_RETENTION_DAYS` without activity. After that, a returning
  user's messages get no reply until they send YOJANA again (by design: opt-in).
* The worker uses a single autocommit connection per thread. `pg.CONNECT_TIMEOUT=10` covers Neon cold starts.
* The Redis key for `/api/catalog` is not namespaced by backend. After switching to Postgres, bump
  `SCHEME_CACHE_VERSION` or wait up to `SCHEME_CACHE_TTL_SECONDS`.

## 4. Redis / Valkey

`web/public_cache.py` is unchanged by the merge. It caches public data only (`catalog`, `scheme`), uses
150/200 ms timeouts, returns `None` on any error (so it never blocks), and clamps TTL to 30–3600 s.
Engine decisions never read from Redis. With `SCHEME_DATABASE_URL` set, every catalog read goes to
Postgres (there is no fallback to the bundled SQLite file; the only extra layer is the new 60 s
in-process copy). Valkey authentication comes from `REDIS_URL` (`redis://default:<password>@host:6379/0`)
on every connection, so it survives a Valkey restart. Existing tests in `test_redis_cache*` pass.
**Not verified live:** the production Valkey was not contacted.

## 5. Frontend

`frontend/` is untouched by `refactor2.0`, and `frontend/public/static/*` is byte-identical to
`web/static/*`. `build.mjs` refuses to build without an HTTPS `BACKEND_ORIGIN` and proxies `/api/*` to it
with no-store headers; no secret is read. Node is not installed here, so **the build was not run**.

## 6. Test results (actual)

| Run | Result |
|---|---|
| Baseline on `9f77874` before changes | 479 passed, 13 skipped (35.6 s) |
| After fixes, `python -m pytest -q`, three consecutive runs | **521 passed, 13 skipped** (40–50 s) |
| New `tests/test_voice_pipeline.py` | 31 passed. Signed webhook end to end with WAHA and Sarvam mocked at the httpx layer: body-null notes, nested and alias paths, inline base64, 9 failure modes each giving one reply and no re-download, hi/mr failure text, en/hi/mr transcript language, model-outage retry reusing the transcript, delivery retry without duplicates, private diagnostics, Sarvam request shape |
| New `tests/test_deploy_worker.py` | 11 passed. Dockerfile package coverage, smoke import list, worker heartbeat health check, SIGTERM stop, lease recovery after a "crash", production guard, outbound gate, catalog cache, SCHEME_DATABASE_URL validation, migration guard |
| Image-layout simulation (only the directories the Dockerfile copies) | All smoke imports pass. App starts. `/healthz` 200, `/readyz` 200 (SQLite) |
| Skipped | 9 opt-in Postgres tests (`TEST_DATABASE_URL` not set here), 2 real-ffmpeg tests, 2 others |

One earlier full-suite run exceeded a 10-minute timeout while its output was piped through `tail`. It
did not reproduce in three later runs (40–50 s each). Cause unknown; noted for transparency.

**Not run here (tools or access unavailable):** `docker build` (no Docker), real ffmpeg OGG/Opus decode
(no ffmpeg; the build smoke check now enforces it), Postgres tests (no server or `TEST_DATABASE_URL`),
Neon, Valkey, WAHA, Sarvam, EasyPanel, the Vercel build (no Node).

## 7. Final status

| Component | Working | Tested | Production-ready |
|---|---|---|---|
| FastAPI | Confirmed working (local, image-layout simulation) | Yes, offline | Requires external access: EasyPanel deploy and `/readyz` |
| Neon schemes | Not yet verified here (PROGRESS.md reports 270 schemes on Neon from the merge author) | Postgres tests skipped here | Requires external access: run `pg-status` first |
| Citizen database | Confirmed working on SQLite. Postgres path unchanged by this branch | Yes (SQLite) | Requires external access |
| Agent orchestration | Partly integrated: worker lanes yes, web/WhatsApp no (see §3) | Yes, mocks | Not yet verified live |
| Background worker | Confirmed broken before (not in image, health check), fixed | Yes, offline | Requires external access: deploy `yojana-worker` |
| WhatsApp text | Confirmed working offline | Yes | Requires external access: live round trip |
| WhatsApp voice | Confirmed broken (B1, B2), fixed | Yes, mocked WAHA/Sarvam | Requires external access: live voice round trip |
| Sarvam STT | Request contract verified against mocks | Yes, mocks | Requires external access: one paid call with owner approval |
| Redis/Valkey | Unchanged. Offline tests pass | Yes, offline | Requires external access: `REDIS_URL` ping |
| Vercel frontend | Unchanged by merge | Build not run (no Node) | Requires external access |

## 8. Files changed

`Dockerfile`, `deploy/healthcheck.py` (new), `deploy/smoke_imports.py` (new), `web/waha_bridge.py`,
`web/waha_media.py`, `web/diagnostics.py` (new), `web/runtime.py`, `web/conversation_api.py` (import
only), `discovery/catalog.py`, `store/sql/002_agents.sql`, `store/pg_admin.py`, `store/migrate.py`,
`worker/__main__.py`, `worker/loop.py`, `saathi/senders.py`, `saathi/orchestrator.py` (docstring),
`.env.example`, `tests/conftest.py`, `tests/test_voice_pipeline.py` (new), `tests/test_deploy_worker.py`
(new), `tests/deploy_helpers.py` (new), `docs/*` (these three documents), `PROGRESS.md`, `CLAUDE.md` (commands).
