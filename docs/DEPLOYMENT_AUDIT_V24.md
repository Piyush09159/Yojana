# V2.4 deployment audit and origin contract

The V2.3 baseline was 237 offline tests. The public catalog, deterministic eligibility/ranking engine, original static layouts and translation dictionaries are preserved. V2.4 adds deployment boundaries and persistence; it does not establish live model quality or external WhatsApp delivery.

This document and DEPLOYMENT.md describe the V2.4 security/storage behavior. The earlier in-memory-only notes in SECURITY.md describe the old/default development mode and do not describe a configured V2.4 PostgreSQL deployment. Local file permissions prevented updating that existing file during this implementation.

## Existing origin dependencies

| Component | Previous dependency | V2.4 treatment |
| --- | --- | --- |
| Landing HTML/CSS/JS | `/static/*`, `/app`, and public WAHA configuration status use the current browser origin. | Standalone `frontend/public`, same paths, Vercel external API rewrites. |
| Dashboard | Relative fetches to `/api/v2/conversation`, `/api/catalog`, `/api/rescue/*`, `/api/integrations/waha/status`. In-memory session ID only. | Existing calls retained. A progressive enhancement adds idempotency IDs, a tab-scoped opaque case ID, authorized restore, safe provider errors and linked Rescue summaries. |
| Legacy form | `/api/form`, session creation/answer/ask/snapshot/scheme routes and native `EventSource('/api/sessions/{id}/events')`. | All proxied through the frontend origin. Owner cookies work for both fetch and native EventSource. |
| SSE | Unbounded stream, numeric event IDs, in-memory event list and no Last-Event-ID processing. | Immediate initial comment, heartbeat, no buffering/cache, 25-second bounded windows, Last-Event-ID resume and a bounded persisted event tail. Native EventSource reconnects. |
| Swagger | `/docs` loads schema from `/openapi.json` and CDN-hosted Swagger assets. | Both backend paths forwarded; Swagger CDN remains an external availability dependency. |
| WAHA | URL-secret webhook, `_chats` and `_seen` in RAM; event marked seen before send. | Production disables URL-secret route. Separate raw-body SHA-512 HMAC webhook uses PostgreSQL chat mappings and committed response outbox. |
| LLM | OpenAI-compatible base URL, key and model read on backend; SDK could retry/hold requests. | Same provider abstraction, bounded compatible-provider timeout/no automatic SDK retries, context from validated facts/pending question/bounded messages, safe status/error codes. |
| Storage | Public catalog SQLite; main and legacy sessions/process state in RAM. | Public catalog remains separate. PostgreSQL case state, minimized messages, approvals, timestamps, WAHA state; SQLite case adapter is for offline validation only. |

## Routes

Preserved: `/`, `/app`, `/chat` (307 to `/app`), `/legacy`, `/docs`, `/openapi.json`, `/static/*`, `/api/catalog`, `/api/form`, legacy `/api/sessions` and its events/answer/ask/snapshot routes, `/api/schemes/{id}`, `/api/v2/conversation` and deletion, Rescue preflight/diagnose and public WAHA configuration status.

Added: `/healthz`, `/readyz`, `/api/health/live`, `/api/health/ready`, `/api/inference/status`, `/api/cases/{id}` GET/PATCH, `/api/cases/{id}/approvals`, legacy session deletion and `/api/webhooks/waha` (signed inbound). The old `/api/integrations/waha/{secret}` is development compatibility only and returns 404 in production.

## Access and privacy

- Production requires PostgreSQL, session authorization, exact HTTPS frontend origins and a stable signing key. Local/mock inference providers are rejected in production.
- A random owner capability is stored as an HttpOnly, Secure, SameSite=Lax host-only cookie. PostgreSQL stores its SHA-256 hash. A Bearer header is also accepted for authorized API clients. Tokens are never placed in SSE URLs or browser storage.
- Proxied responses must forward Set-Cookie and requests must forward Cookie; the local proxy checks that contract. Confirm it on the actual Vercel deployment. Direct backend cookies and Vercel cookies have different hosts; use the frontend origin consistently.
- Only an opaque case ID and the existing language preference are stored in browser storage. Reload uses the authorized case API. Another browser cannot retrieve or mutate the case using its ID alone.
- Conversation numbers resembling identity/account/phone numbers are rejected before extraction. Stored message text has long digit sequences and emails removed; arbitrary free text can still contain sensitive personal statements. Do not invite identifying details. Retain only what the service needs and review access/backup policies before public use.
- Rescue records/names/DOB and raw portal text are not added to a case. Only selected scheme, dependency facts and blocker flags are stored. Approval records acknowledge guidance and bind the current profile/Rescue summary hash; they never authorize or perform a government submission.
- Session lifetime defaults to an absolute 30 days from creation, even while active. The configurable history limit is 40 messages (2–200), approvals are capped at 20, legacy events at 200 and case admission defaults to 2,000 active cases. Expired sessions are inaccessible immediately and purged at startup/hourly; children cascade-delete. An explicit purge command is available for operations.
- WAHA addresses are keyed HMACs in PostgreSQL. No raw chat address or incoming raw payload is stored. Reply outbox entries expire after seven days; opt-in mappings expire after the configured idle retention. STOP deletes the local case and cancels older pending replies. WAHA/provider logs and PostgreSQL backups need their own retention controls.

## Delivery and restart semantics

Database transactions and per-session/chat advisory locks serialize updates. Migrations are additive, versioned and transactionally locked. Main conversations restore pending questions and validated facts. Legacy sessions restore their saved state; an interrupted discovery is reported as interrupted rather than silently replayed after restart. One backend worker/replica is required for the current legacy background-thread execution model.

WAHA signs the exact request bytes with SHA-512. Production also requires a signed body timestamp within 24 hours. Unsupported sessions/groups/media/self-messages and non-opted-in chats are ignored. The conversation state, event result and response outbox commit together before sending. A delivery retry reuses the response rather than running extraction twice. Outbound send plus database acknowledgement cannot be an atomic transaction with the external WAHA API: an ambiguous timeout or crash after WAHA accepts a send can duplicate a response. This is at-least-once delivery, not exactly-once. Failed sends require WAHA retries or an operator replay of the same original signed event within its validity window. There is no autonomous outbound campaign or unsolicited sender.

## Infrastructure assumptions and validation limits

The standalone Vercel build reads only `BACKEND_ORIGIN` and `frontend/public`, produces Build Output API v3 rewrites, and refuses non-HTTPS or credential-bearing origins. Runtime credentials must never be configured on Vercel. API responses are no-store, HTML revalidates, and unfingerprinted static files revalidate. No wildcard CORS is needed because browser calls stay same-origin. The local HTTP proxy is a test simulator, not proof of Vercel edge behavior.

Docker runs Python 3.13.17 with pinned dependencies as UID/GID 10001, a writable catalog copy under `/runtime`, one Uvicorn worker, bounded concurrency and no access log. The public catalog is baked into the image; runtime ingestion is not durable. No local inference server, GPU or LiveKit is installed.

Docker and PostgreSQL server binaries were unavailable in the implementation environment. Real image build/container boot, PostgreSQL service restart, actual Vercel routing, genuine inference and WAHA pairing/delivery must be validated using the commands in DEPLOYMENT.md. SQLite-backed restart tests do not replace those checks. The GitHub Actions deployment template is provided under `deploy/`; this session could not write `.github/workflows/deployment.yml` due to local filesystem permissions, so the owner must copy it into place if CI activation is desired.
