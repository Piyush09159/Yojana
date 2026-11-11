# WhatsApp voice notes: troubleshooting

## Event flow and where each step can fail

| # | Step | Code | Diagnostic code(s) | Citizen sees |
|---|---|---|---|---|
| 1 | WAHA receives the note, downloads it to its file store | WAHA (`WAHA_EVENTS_DOWNLOAD_MEDIA`) | none | none |
| 2 | WAHA POSTs a `message` event to `/api/webhooks/waha` | WAHA (`WHATSAPP_HOOK_*`) | missing = not configured, or wrong URL | none |
| 3 | Size ≤128 KB, HMAC-SHA512, timestamp within 24 h | `waha_bridge.signed_webhook` | HTTP 413 / 403 (no log line) | none (WAHA retries) |
| 4 | Event, session, sender, group/self checks | `_ignore_reason` | `WAHA_WEBHOOK_IGNORED detail=session_mismatch\|sender_format\|...` | none (by design) |
| 5 | Opted in? (sent YOJANA, not STOP, not expired) | `persistent_event` | `WAHA_WEBHOOK_IGNORED detail=not_opted_in` | none (by design: privacy) |
| 6 | Media metadata | `waha_media.media_url` | `WAHA_MEDIA_UNAVAILABLE`, `WAHA_MEDIA_UNSUPPORTED`, `WAHA_MEDIA_UNTRUSTED` | "couldn't transcribe" |
| 7 | Download from trusted WAHA origin (8 s, 2 MB, no redirects) | `waha_media.download` | `WAHA_MEDIA_DOWNLOAD_FAILED status=…`, `VOICE_TOO_LONG` | "couldn't transcribe" / "longer than 15 s" |
| 8 | ffmpeg → 16 kHz mono PCM WAV, ≤15 s | `waha_media.decode` | `WAHA_AUDIO_DECODE_FAILED detail=ffmpeg not installed\|…`, `VOICE_TOO_LONG` | as above |
| 9 | STT: `STT_PROVIDER=local` → yojana-stt (see [LOCAL_STT.md](LOCAL_STT.md)); `sarvam` → Sarvam `saaras:v3` | `speech.stt` | `STT_DISABLED`, `LOCAL_STT_FAILED status=503/504 detail=speech_provider_unavailable\|speech_busy\|speech_timeout`, `VOICE_NO_SPEECH`, `SARVAM_STT_FAILED status=402…`, `VOICE_BUSY`, `VOICE_SENSITIVE_NUMBER` | "voice not available, please type" / "couldn't transcribe" / "without the number" |
| 10 | Transcript ready, language normalised (`hi-IN`→`hi`) | | `VOICE_TRANSCRIPT_READY language=… source=sarvam\|cache` | |
| 11 | Conversation (Reader model + deterministic engine) | `conversation_api.conversation` | `VOICE_CONVERSATION_FAILED status=429/503` (rolled back, WAHA retries, transcript reused) | reply after the retry |
| 12 | Reply committed to the `waha_events` outbox | | | |
| 13 | `POST /api/sendText` | `_send` | `WAHA_REPLY_SENT` or `WAHA_REPLY_DELIVERY_FAILED status=…` (HTTP 503 → WAHA retries) | reply |
| 14 | Retries | outbox status `sent` | response `duplicate` | no second message |

Steps 4 and 5 are the only intentional silent outcomes. Every failure in steps 6–9 now sends one
localized reply.

## Fixed in this branch

1. NOWEB sends `body: null` for voice notes. Before this fix they were silently `ignored` (step 4).
2. WAHA's URL path is `/api/files/<session>/<id>.oga`. Before this fix it was rejected as untrusted (step 6).
3. Unexpected errors in steps 6–9 no longer cause a 500 and a retry loop that pays Sarvam on each retry.

## Required configuration

**yojana-api**: `YOJANA_WAHA_ENABLED=true`, `WAHA_BASE_URL=http://<waha-private-host>:3000`,
`WAHA_API_KEY`, `WAHA_SESSION=default`, `WAHA_HMAC_KEY` (≥32 chars, same value as WAHA's
`WHATSAPP_HOOK_HMAC_KEY`), `SESSION_SIGNING_KEY` (≥32), `DATABASE_URL` (Postgres), `SARVAM_API_KEY`,
`SARVAM_STT_ENABLED=true`, optional `SARVAM_STT_MODEL` (default `saaras:v3`), optional
`WAHA_MEDIA_ORIGINS`.

**WAHA** (deployed NOWEB CORE 2026.10.1; variable names checked against the
[configuration docs](https://waha.devlike.pro/docs/how-to/config/)):

```
WAHA_BASE_URL=http://<waha-private-host>:3000     # must equal the backend's WAHA_BASE_URL origin
WAHA_EVENTS_DOWNLOAD_MEDIA=true                   # default true; if false, media.url is null
WAHA_EVENTS_DOWNLOAD_MEDIA_MIMETYPES=audio        # optional filter; must include audio
WHATSAPP_FILES_LIFETIME=180
WHATSAPP_HOOK_URL=https://<api-domain>/api/webhooks/waha
WHATSAPP_HOOK_EVENTS=message
WHATSAPP_HOOK_HMAC_KEY=<same as WAHA_HMAC_KEY>
# Do not use WAHA_MEDIA_STORAGE=S3 with presigned URLs; local (default) storage is expected.
```

The older names `WHATSAPP_DOWNLOAD_MEDIA` / `WHATSAPP_FILES_MIMETYPES` still work only as fallbacks.

## Media URL validation (WAHA_MEDIA_UNTRUSTED)

WAHA 2026.10.1 builds `media.url` as `<WAHA's own WAHA_BASE_URL>/api/files/<session>/<message-id>.<ext>`.
If WAHA's `WAHA_BASE_URL` is unset, that base is `http://localhost:3000`. The backend accepts the
advertised origin only when it is one of these:

* the backend's `WAHA_BASE_URL` (case and default ports normalised)
* an entry in `WAHA_MEDIA_ORIGINS` (an optional path prefix, e.g. `https://host/waha`, is allowed)
* WAHA's built-in default `http://localhost|127.0.0.1:3000` (or the `WAHA_BASE_URL` port)

The path must be `/api/files/[<WAHA_SESSION>/]<file>`. The URL must have no query, fragment, user info,
`..` or encoded separators. The file is then always downloaded from the backend's `WAHA_BASE_URL`
with only the validated path. The payload never chooses the host. Redirects are not followed, and
the download is limited to 8 s and 2 MB. Each rejection logs a `check=` name and categorical facts
(`scheme`, `origin_kind`, `host_match`, `port_match`). The log never contains the URL, host or file name.

## Safe diagnostic commands

Run these in the EasyPanel console of the named service. They print no secrets.

```sh
# yojana-api: is voice configured? (names only)
python -c "from web.waha_bridge import voice_missing; print(voice_missing() or 'voice config complete')"
# yojana-api: ffmpeg can decode OGG/Opus
ffmpeg -hide_banner -demuxers | grep -w ogg; ffmpeg -hide_banner -decoders | grep -w opus
python /app/smoke_imports.py
# yojana-api: can it reach WAHA privately? (status code only)
python -c "import os,httpx;print(httpx.get(os.environ['WAHA_BASE_URL']+'/api/sessions',headers={'X-Api-Key':os.environ['WAHA_API_KEY']},timeout=5).status_code)"
# public: readiness flags
curl -s https://<api-domain>/api/integrations/waha/status
# yojana-api logs: follow one voice note
#   filter the service logs for "yojana {" and look at the codes in order (table above)
```

On WAHA: confirm the session is `WORKING` in the dashboard, and that the webhook is listed for
event `message` with the HMAC key set.

## Expected log sequence for a good note

```
INFO yojana {"code":"WAHA_WEBHOOK_RECEIVED","engine_has_body":false,"event":"3f9a1c2e","mimetype":"audio/ogg","source":"voice"}
INFO yojana {"code":"VOICE_TRANSCRIPT_READY","event":"3f9a1c2e","language":"hi","ms":2400,"source":"sarvam"}
INFO yojana {"code":"WAHA_REPLY_SENT","event":"3f9a1c2e","result":"replied"}
```

`engine_has_body:false` is normal for NOWEB.

## Recovery

| Symptom (log code) | Action |
|---|---|
| No `WAHA_WEBHOOK_RECEIVED` at all | Check the WAHA webhook URL and HMAC key and that the session is WORKING. A 403 means the HMAC keys differ. |
| `WAHA_WEBHOOK_IGNORED not_opted_in` | The user must send `YOJANA` first (also after STOP, or after `CASE_RETENTION_DAYS` of inactivity). |
| `WAHA_WEBHOOK_IGNORED session_mismatch` | Set `WAHA_SESSION` to the WAHA session name. |
| `WAHA_MEDIA_UNAVAILABLE … media.url is null` | Set `WAHA_EVENTS_DOWNLOAD_MEDIA=true` and include `audio` in the mimetype filter. Restart WAHA. |
| `WAHA_MEDIA_UNTRUSTED check=origin` | WAHA advertises an origin the backend does not trust. `origin_kind=public` means WAHA's own `WAHA_BASE_URL` is its public HTTPS domain: add exactly that origin to the backend's `WAHA_MEDIA_ORIGINS`. `host_match`/`port_match` show which part differs. |
| `WAHA_MEDIA_UNTRUSTED check=query` | WAHA uses S3/presigned media storage. Use local media storage. |
| `WAHA_MEDIA_UNTRUSTED check=session` | `WAHA_SESSION` on the backend differs from the WAHA session name. |
| `WAHA_MEDIA_UNTRUSTED check=path_prefix\|filename\|traversal\|encoded_separator\|scheme\|userinfo` | Unexpected URL shape. Report the check name; do not loosen the validator. |
| `WAHA_MEDIA_DOWNLOAD_FAILED status=401` | `WAHA_API_KEY` differs between the services. 404: the file expired (`WHATSAPP_FILES_LIFETIME`) or WAHA restarted. |
| `WAHA_AUDIO_DECODE_FAILED ffmpeg not installed` | Wrong image. Rebuild from this Dockerfile. |
| `STT_DISABLED` | Set `STT_PROVIDER=local` and `LOCAL_STT_URL` (docs/LOCAL_STT.md). |
| `LOCAL_STT_FAILED detail=speech_provider_unavailable` | yojana-stt is down or not ready: check its `/ready` from the yojana-api console and its `model_unavailable` log. |
| `LOCAL_STT_FAILED detail=speech_timeout` / `speech_busy` | The CPU is too slow or there is a queue. Raise `LOCAL_STT_TIMEOUT_SECONDS`, give the service more CPU, or use `LOCAL_STT_MODEL=base`. |
| `VOICE_NO_SPEECH` | The note was silent. The citizen is asked to retry or type. |
| `SARVAM_STT_DISABLED` | (Sarvam only) Set `SARVAM_API_KEY`. |
| `SARVAM_STT_FAILED status=402` | Sarvam credits are exhausted. This is blocked in-process until restart; top up, then restart. 429: rate limited, blocked for 60 s. 401/403: wrong key. |
| `VOICE_CONVERSATION_FAILED status=503` | Model provider down. Check `/api/inference/status` and `READER_FALLBACKS`. |
| `WAHA_REPLY_DELIVERY_FAILED` | Check that the WAHA session is WORKING. WAHA retries the webhook and the outbox prevents duplicates. |

Testing a live round trip costs one Sarvam STT call per note. Do it only with the owner's approval,
from a controlled handset.
