# PDF application guides (`pdf_service`)

A small separate service that turns a citizen's shortlisted schemes into a printable guide in
English, Hindi or Marathi: what the scheme offers, which conditions the engine could check, the
documents to keep ready, how to apply as the official source publishes it, and official links with
QR codes. It runs on Render; the main backend (EasyPanel) is its only caller.

```
Citizen (website, later WhatsApp)
   │  GET /api/v2/conversation/{session_id}/guide.pdf     owner cookie only, no secret
   ▼
yojana-api (EasyPanel)   web/pdf_api.py: checks ownership, rebuilds the shortlist with the engine,
   │                     builds the payload from the catalog (web/pdf_client.guide_from_cards)
   │  POST /api/v1/pdf/generate     Authorization: Bearer <PDF_SERVICE_TOKEN>
   ▼
yojana-pdf (Render)      validates, renders in memory (Jinja2 → WeasyPrint), returns the bytes
   │  application/pdf
   ▼
yojana-api streams the PDF to the browser (Cache-Control: no-store). Nothing is stored anywhere.
```

## Content rules

The service formats; it never decides and never adds facts.

| Rule | How it is enforced |
|---|---|
| No eligibility claims | `status` must be one of the engine's statuses (`eligible`, `eligible_check_manually`, `check_manually`, `near_miss`, `needs_info`); `ineligible` is refused (422). Each is worded as "conditions we could check", and the cover says the office decides. |
| No invented steps or documents | Steps, documents and benefit text are printed exactly as the catalog holds them. When the catalog has none, the guide says so and points to the official page. |
| No invented deadlines | There is no deadline field (an unknown field is a 422). |
| Every scheme cites its source | `source_url` is required; the last-checked date is printed when the catalog has one. |
| Unverified schemes are labelled | `verified: false` prints "Found on an official website; not yet checked by a person". |
| QR codes only for official links | Only https links on `gov.in` / `nic.in` (or `PDF_QR_DOMAINS`) get a QR code; other links are printed as text. |
| No machine translation of scheme content | Labels are translated (`pdf_service/i18n.py`); scheme text is shown as published, with a note saying so in hi/mr. |

Data limit to know: `how_to_apply` exists for about 230 of 267 catalog schemes and `documents` for
107, so many guides honestly say "check the official page" for those parts.

## API

All generation endpoints need `Authorization: Bearer <token>`, where the token is one of
`PDF_SERVICE_TOKENS`. Request bodies are `Content-Type: application/json`.

### `GET /health`
`200 {"status": "ok", "renderer": "weasyprint", "version": "1.0.0", "auth_configured": true}`;
`503` with `"status": "unavailable"` if WeasyPrint cannot load. No token needed; Render's health check uses it.

### `POST /api/v1/pdf/generate` → one guide
```json
{
  "language": "hi",                       // en | hi | mr (default en)
  "recipient_name": "Sunita Devi",        // optional, ≤80 chars; printed on the cover only
  "reference": "CASE-1234",               // optional, [A-Za-z0-9-]{1,40}
  "generated_on": "2026-11-11",           // optional, default today
  "schemes": [                            // 1..PDF_MAX_SCHEMES (10), unique ids
    {
      "id": "ignoaps",                    // [A-Za-z0-9_.-]{1,120}
      "name": "Indira Gandhi National Old Age Pension (IGNOAPS)",
      "name_local": "इंदिरा गांधी राष्ट्रीय वृद्धावस्था पेंशन",   // optional
      "status": "eligible",
      "verified": true,
      "benefit": "Monthly pension ...",   // ≤1500, as published (no computed yearly amount: catalog
                                          // amounts mix one-time grants and yearly payments)
      "conditions_met": ["Applicant must be 60 or older"],      // ≤12 × ≤400
      "check_yourself": [],               // ≤12 × ≤600
      "missing_information": [],          // ≤20 × ≤120
      "documents": [{"name": "Aadhaar card", "issued_by": "UIDAI"}],   // ≤25
      "how_to_apply": "Apply at the Tehsil office ...",          // ≤4000, line breaks kept
      "apply_url": "https://...",         // optional, absolute http(s), no credentials
      "source_url": "https://nsap.nic.in/",                      // required
      "source_site": "nsap.nic.in",
      "agency": "Ministry of Rural Development",
      "last_verified": "2026-10-21"       // optional date
    }
  ]
}
```
Unknown fields anywhere are rejected. Control characters are rejected.
Optional query `?disposition=inline` (default `attachment`).

### `POST /api/v1/pdf/generate-batch` → several guides in one PDF
`{"guides": [<guide>, ...]}`: 1..`PDF_MAX_GUIDES` (5) guides, at most `PDF_MAX_BATCH_SCHEMES` (25)
schemes in total. Each guide starts on a new page and keeps its own language (for example, the same
shortlist in Marathi and English for a family member who helps).

### Responses
`200 application/pdf`, `Content-Disposition: attachment; filename="yojana-guide-hi-20261111.pdf"`
(no name in the filename), `Cache-Control: no-store, private`.

| Status | `code` | Meaning |
|---|---|---|
| 401 | `unauthorized` | missing or wrong token |
| 413 | `too_large` | body over `PDF_MAX_BODY_BYTES` (256 KiB) |
| 415 | `unsupported_media_type` | not `application/json` |
| 422 | `invalid_request` | `errors: [{loc, type, msg}]`; submitted values are never echoed |
| 503 | `not_configured` | no valid token configured on the service (fails closed) |
| 503 | `not_ready` | WeasyPrint not loaded |
| 503 | `busy` | all render slots taken for `PDF_QUEUE_WAIT_SECONDS`; `Retry-After: 5` |
| 500 | `render_failed` | unexpected render error; no details returned |

```bash
curl -sS -X POST "$PDF_SERVICE_URL/api/v1/pdf/generate" \
  -H "Authorization: Bearer $PDF_SERVICE_TOKEN" -H "Content-Type: application/json" \
  --data @pdf_service/examples/guide_hi.json -o guide_hi.pdf
```

## Environment variables

**PDF service (Render):**

| Variable | Default | |
|---|---|---|
| `PDF_SERVICE_TOKENS` | none (required) | comma-separated; each at least 24 characters. Two values allow rotation. |
| `PORT` / `PDF_PORT` | Render sets `PORT`; else 8002 | listen port |
| `PDF_MAX_SCHEMES` | 10 | schemes per guide |
| `PDF_MAX_GUIDES` | 5 | guides per batch |
| `PDF_MAX_BATCH_SCHEMES` | 25 | schemes per batch |
| `PDF_MAX_BODY_BYTES` | 262144 | request body cap |
| `PDF_CONCURRENCY` | 2 | renders at once (CPU bound) |
| `PDF_QUEUE_WAIT_SECONDS` | 15 | wait for a free slot before `503 busy` |
| `PDF_MAX_CONNECTIONS` | 16 | uvicorn connection limit |
| `PDF_QR_DOMAINS` | `gov.in,nic.in` | domains whose https links get a QR code |

**Backend (EasyPanel, `yojana-api`):** `PDF_SERVICE_URL` (https URL of the Render service; empty turns
the download off), `PDF_SERVICE_TOKEN` (one of the service's tokens), `PDF_SERVICE_TIMEOUT` (60),
`PDF_SERVICE_RETRIES` (2).

Make a token with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Put it in Render and
EasyPanel only, never in the repository, a chat, or a log.

## Privacy and security

- The PDF is built in memory and returned. No disk writes, no database, no cache.
- Logs are one line per request: status, code, timing, counts, languages and byte size. Never names,
  scheme content, tokens or request bodies. The uvicorn access log is off.
- The citizen's name is printed on the cover only if the backend sends one (the download route does
  not), and it is never put in the PDF metadata or the filename.
- WeasyPrint may load only `data:` URIs (the inline QR codes). Links in the payload are printed, never
  fetched, so a payload cannot make the service call another server.
- Templates are autoescaped; payload text cannot inject HTML or CSS.
- Constant-time token comparison; the service refuses to generate when no strong token is configured.
- The container runs as a non-root user (uid 10001).

## Backend integration

- **Browser download (built):** `GET /api/v2/conversation/{session_id}/guide.pdf?lang=hi`
  (`web/pdf_api.py`). It uses the same owner cookie and ownership check as the conversation, rebuilds
  the top-5 shortlist from the stored profile with the engine, and maps service failures to
  `503 pdf_unavailable` (with `Retry-After: 30`) or `502 pdf_failed`. It returns `404 pdf_disabled`
  until `PDF_SERVICE_URL` is set. The frontend only needs a link or button pointing at this URL; no
  frontend change is included in this branch.
- **WhatsApp (future):** the same two calls give the bytes to send as a document through WAHA:
  `pdf = PdfClient.from_env().generate(guide_from_cards(shortlist_cards(shortlist), catalog, language=lang))`.
- **Cold starts:** a free Render instance sleeps when idle. Call `PdfClient.warm_up()` when a
  conversation reaches results, so the service is awake by the time the citizen taps download.

## Local development

```bash
# tests on any OS (fake renderer; no WeasyPrint needed)
pip install -r requirements-dev.txt
python -m pytest -q tests/test_pdf_service.py

# the real service (WeasyPrint needs Pango; use Docker on Windows)
docker build -f Dockerfile.pdf -t yojana-pdf .
docker run --rm -p 8002:8002 -e PDF_SERVICE_TOKENS=<token> yojana-pdf
curl http://localhost:8002/health

# example payloads from synthetic people (engine + catalog), then PDFs inside the image
python scripts/make_pdf_examples.py
docker run --rm -u 0 -v "$PWD/pdf_service/examples:/app/pdf_service/examples" yojana-pdf python -m pdf_service.examples

# real-render tests (fonts embedded, Devanagari text, fetcher refuses URLs), inside the image
docker run --rm -u 0 -v "$PWD:/src:ro" -w /src yojana-pdf sh -c \
  "pip install -q pytest==9.0.2 pypdf==6.21.3 && python -m pytest -q -p no:cacheprovider --noconftest tests/test_pdf_render_real.py"
```

## Deploying on Render

1. Push the branch, then in Render: **New → Blueprint**, pick this repository. Render reads
   `render.yaml` (Docker runtime, `Dockerfile.pdf`, health check `/health`, deploys `feat/pdf-guides`).
2. When asked for `PDF_SERVICE_TOKENS`, paste a token made as above.
3. Wait for the deploy to show **Live**, then check:
   `curl https://<service>.onrender.com/health` → `"status": "ok"`, `"auth_configured": true`.
4. Generate one PDF with the curl command above and open it.
5. On EasyPanel set `PDF_SERVICE_URL=https://<service>.onrender.com` and `PDF_SERVICE_TOKEN=<same token>`
   for `yojana-api`, redeploy, finish a conversation and open `/api/v2/conversation/<id>/guide.pdf`.
6. After the PR is merged, change `branch:` in `render.yaml` (or in the Render dashboard) to the merge branch.

Token rotation: set `PDF_SERVICE_TOKENS=<old>,<new>` on Render, switch EasyPanel to `<new>`, then
remove `<old>` from Render.
