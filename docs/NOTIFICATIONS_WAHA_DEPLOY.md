# Yojana Saathi — WhatsApp multilingual + optional alerts patch

Scope: the current `feature/v2.5-citizen-experience` source tree, not a replacement for anyone's MongoDB scheme-agent work or Bolna AI voice-call service. This package is an **overlay**, not the entire repository.

## What changes

- English, Hindi and Marathi WhatsApp messages use the existing conversation API. Once a user selects Hindi/Marathi, an English phrase embedded in their message won't automatically switch their language back to English. Full 23-language live coverage is **not** claimed or tested.
- `YOJANA` offers *UPDATES* as a separate optional consent, with `UPDATES OFF` / `STOP`. No citizen gets notifications merely by asking a question. Updates are only dispatched following explicit opt-in.
- A new PostgreSQL table stores encrypted WhatsApp recipient identity, state/district, selected language, consent time; another table stores queued/sent notifications. It stores **no phone number** from a `@lid`; LID addresses cannot reliably be converted to Indian phone numbers.
- The approved campaign CLI targets one state and optionally one district; it skips contacts whose district is unknown. Use your agent's MongoDB scheme results only after manual source verification. This integration does not read or change MongoDB.
- WAHA signed webhook max payload is increased to 128 KiB in both the request middleware and the webhook handler. This accommodates some larger NOWEB messages but still rejects oversize payloads. No webhook authentication is removed.
- Existing WAHA voice note processing still requires `SARVAM_STT_ENABLED=true` and `SARVAM_API_KEY`; actual media download/STT needs a live test. No claim of end-to-end voice success without it.

## Deploy safely on GCP EasyPanel

Your existing production `DATABASE_URL` on the GCP PostgreSQL service can be reused; **do not create a second citizen database or copy secrets into Git**. New tables are created by the application's existing `case_store.migrate()` startup hook.

1. Create a branch from the latest source and ensure no uncommitted work. Copy this ZIP's contents to a separate directory, not over the repo.
2. Run `python apply_patch.py --repo D:\\Yojana-Pilot --check` from the extracted ZIP directory. On Windows prefer `py -3.13` if `python` is missing.
3. Run `python apply_patch.py --repo D:\\Yojana-Pilot`. This creates `.before-alert-patch` backups for three preexisting files. Review `git diff` carefully; delete only those backups before staging.
4. Run `py -3.13 -m pytest tests/test_scheme_alerts_patch.py -q` and then the repository's entire test suite. Fix any failures *before* deploying. All teammates should review the branch before merge.
5. Push the branch and make **yojana-api** EasyPanel deploy from that branch/merged commit. Keep **existing** `DATABASE_URL`, `SESSION_SIGNING_KEY`, `WAHA_HMAC_KEY`, `WAHA_SESSION`, `WAHA_API_KEY`, and public HTTPS URL. Avoid restarting/recreating the WAHA session.
6. Check EasyPanel logs for migration errors; confirm `GET /readyz` is HTTP 200. Confirm WAHA session `WORKING` and `GET /api/integrations/waha/status` is configured/enabled/persistent. HTTP readiness is **not** proof of WhatsApp delivery.
7. In **WAHA** EasyPanel environment verify `WHATSAPP_HOOK_EVENTS=message`, `WAHA_EVENTS_DOWNLOAD_MEDIA=true`, `WAHA_EVENTS_DOWNLOAD_MEDIA_MIMETYPES=audio`, and ensure public `media.url` matches the `WAHA_BASE_URL` used by `yojana-api`. Set `SARVAM_STT_ENABLED=true` on **yojana-api**, only if you have a working Sarvam API key and have budgeted speech usage. No unsupported provider claims.
8. From a second device send `YOJANA` → `Chat` → `UPDATES` → `I live in Beed, Maharashtra and ...` → `UPDATES STATUS` → short voice note → `UPDATES OFF` → `STOP`. Check that voice transcripts and errors are not logged. Also test Hindi and Marathi, including code-mixed phrases, with a real provider. Do not send sensitive identity/bank information.
9. Review DLT/telecom obligations before **SMS**. This patch delivers WhatsApp text via an existing WAHA session, not carrier SMS. Unofficial WhatsApp automation can be rate-limited/banned; obtain user consent and use official WhatsApp Business providers for scale.

## Manual reviewed campaign example (read-only dry run before dispatch)

Create `messages.json` on the API server **outside Git**:

```json
{
  "en": "A reviewed scheme update may interest residents of Beed, Maharashtra. Check the official eligibility rules before applying.",
  "hi": "बीड, महाराष्ट्र के निवासियों के लिए एक सत्यापित योजना की सूचना है। आवेदन से पहले आधिकारिक पात्रता देखें।",
  "mr": "बीड, महाराष्ट्रातील रहिवाशांसाठी तपासलेल्या योजनेची माहिती आहे. अर्ज करण्यापूर्वी अधिकृत पात्रता तपासा."
}
```

Replace the URL below with the actual official scheme source and replace the ID with the actual reviewed scheme ID. A fake example URL must **never** be sent to citizens.

```bash
python scripts/send_scheme_updates.py \
  --campaign beed-example-20261111-v1 --scheme-id ACTUAL_VERIFIED_ID \
  --state maharashtra --district beed \
  --source-url https://OFFICIAL-GOVERNMENT-SCHEME-URL \
  --messages messages.json --reviewed
```

This queues only (no messages sent). After reviewing recipient count and content, repeat with `--send --max-send 20`. The command is idempotent per campaign-recipient, stops on WAHA errors, and caps each run to 20 sends with a 1.2 second delay. **Delivery is at-least-once**: duplicate sends can occur if WAHA accepts a send but the DB transaction fails afterwards; do not market exactly-once delivery.

A new revision/correction requires a **new campaign ID**. Unsubscribing deletes the user's pending alert jobs. Run this command only in a protected EasyPanel console; do not expose it as a public HTTP API.

## Budget: strict ₹0.50 goal

The normal text-only path can approach this goal with one low-token Groq model call, deterministic eligibility rules, scheme-result reuse, LLM token limits, and precomputed translations. It is **not** proven under ₹0.50 until real model billing, provider quotas, GCP resource cost, and retries are measured.

Sarvam currently lists STT around ₹0.49/minute. A 15-second voice note alone is ~₹0.12 before LLM, bandwidth, storage or GST. An SMS charge, when used, must be budgeted separately. Don't assume unlimited free Groq/GCP or provider credits; free quotas can expire or be exhausted.

For example, a 10-second note at ₹0.49/minute uses ~₹0.082 of speech service. Preserve ₹0.418 for LLM, processing and infrastructure to meet ₹0.50; actual bills and GST are not included in this example.

Cost controls to add during system integration: track per-message estimated model tokens, speech seconds, WAHA success/retry metrics, MongoDB cache hit/miss, total monthly spend, and refusal/fallback when budget is exhausted. Do not cache personalized citizen inputs across users. Cache only approved public scheme metadata keyed by source, location and version.

## Interface for teammate agents

The MongoDB agent should expose immutable *reviewed* scheme records with e.g. `{scheme_id, version, state, districts, official_source_url, verified_at, reviewed_by}`. Only human-approved records with official HTTPS source links should be passed to the campaign CLI. The calling service can remain separate; both should use the existing session and authorized account boundaries rather than sharing bearer keys or personal data through MongoDB.

## Reverting

Before merging: `git restore web/waha_bridge.py web/runtime.py deploy/.env.example`; remove the five new files listed by installer after inspecting `git status`. Do not revert the PostgreSQL migration by deleting its history or dropping data without approval. Never overwrite a teammate's uncommitted changes.
