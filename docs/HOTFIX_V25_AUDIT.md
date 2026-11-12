# V2.5 eligibility, case isolation and audible fallback audit — 2026-10-30

Implemented locally on `feature/v2.5-citizen-experience`; no deployment, push or commit is performed by this work. Live logs, citizen sessions and paid provider credentials were not accessed. Shipped catalog records were inspected through `load_catalog()` and compared with the pre-hotfix engine from Git HEAD. The public database is unchanged.

## Exact source-data defect

| Catalog ID | Extracted predicates | Lost or unmapped conditions | Pre-hotfix status with `is_student=true` |
|---|---|---|---|
| `mid_day_meal_scheme_shaleya_poshan_aahar_yojana` | Only `is_student=true` | Empty `unmapped_criteria`, despite school/class and management restrictions in source | `eligible` |
| `school_nutrition_scheme` | Only `is_student=true` | School management restriction left in text; no class predicate | `eligible_check_manually` |
| `jawahar_navodaya_vidyalaya` | Only `is_student=true` | Class 5 was text in `unmapped_criteria` | `eligible_check_manually` |

All three rows have `origin=discovered`, `verified=false`, and null state/district. The old classifier allowed unreviewed extracted rules to produce `eligible`; the old completion policy also counted `eligible_check_manually` as confirmed. Null scope does not prove universal availability.

The [official Beed School Nutrition page](https://www.zpbeed.gov.in/en/scheme/school-nutrition-scheme/) describes classes 1–8 and enumerated school/centre management types. The [official Amravati Navodaya entry](https://zpamravati.gov.in/en/scheme/jawahar-navodaya-vidyalaya/) describes Class 5 examination candidates for Class 6 admission, and displays a 2025 date interval. Both pages were reviewed for this hotfix. They are not complete current-year prospectuses. No admission age threshold, minority, caste, income threshold or national geographic scope was invented.

`core/cohorts.py` adds source-audited, non-mutating rule overlays for these exact IDs. Existing mapped predicates are retained. Nutrition needs school enrollment, current level, enrolled class 1–8 and a listed school management type. Navodaya needs school enrollment and current Class 5. Remaining geographic, current-year, selection and application requirements remain manual verification. School Nutrition and its Marathi Mid Day Meal listing share one program identity in result deduplication. Other duplicates merge only when normalized title, predicates and scope agree; distinct predicates are retained.

Explicit school-only titles such as secondary-school and pre-matric programs are filtered from default college/senior results. This is relevance filtering, not an official age disqualification. Adults who explicitly describe school enrollment remain eligible for exploration; no generic age blacklist is applied to adult education.

## Reproduced before/after

| Profile | Mid Day Meal before → after | School Nutrition before → after | Navodaya before → after | Default results / next question |
|---|---|---|---|---|
| Age 22, undergraduate, student, Nashik/Maharashtra | eligible → ineligible | eligible_check_manually → ineligible | eligible_check_manually → ineligible | School programs excluded; college aid candidates need verification or information; course name asked |
| Age 62, retired, Maharashtra, annual income ₹40,000; student status unknown | needs_info → needs_info | needs_info → needs_info | needs_info → needs_info | School programs excluded; IGNOAPS needs BPL status; assessment stays open |
| Age 62, retired, stale `is_student=true`, no school level | eligible → needs_info | eligible_check_manually → needs_info | eligible_check_manually → needs_info | School programs excluded; no confirmed school eligibility |
| School pupil, Class 5, school type unknown | permissive student predicate | now needs_info for school management | manual verification remains after Class 5 check | Unknown school/source conditions never auto-pass |

The stale-student scenario demonstrates a failure mode; it does not establish what the live system extracted. Without its authorized payload, we cannot attribute that incident to a specific live case. Local code did silently reuse the saved case on voice Start; that behavior is now removed.

Unreviewed discovered records never receive plain `eligible`. Existing API status `eligible_check_manually` is retained for compatibility, with `status_label=Needs verification`, explicit caveats and exclusion from confirmed completion evidence. Confirmed completion needs useful context and multiple qualifying decision fields, not location or a generic student predicate alone. Incomplete category/minority/course conditions remain unknown/manual: no community or caste is inferred. The catalog still needs a broader human eligibility audit; this hotfix does not certify every extracted predicate or correct every source's category mapping.

## Case identity and completion

Fresh voice Start creates a new authorized case. Continue saved case is a separate explicit action; a secure WhatsApp invitation still redeems to the same consented case. Dashboard restoration is read-only until Continue or Refine is chosen; a default new message after restoration starts a fresh case. Fresh start does not delete the old retained case. Age/student/occupation/study-level contradictions require explicit correction (`correct_facts`, Refine, or correction wording); rejected contradictions do not persist. Accepted corrections clear related stale education facts, question state and recommendations before recomputation. A dependent starts a separate bounded beneficiary profile within the authorized case, preserving the previous beneficiary separately rather than combining their facts. Existing retention/purge controls cover this data.

Student assessments ask enrollment/study level, then course/year and income when useful. Senior income assessments prioritize pension-specific uncertainty such as BPL status. Only complete mapped, reviewed evidence counts as confirmed; manual results cannot trigger confirmed completion. Budget/exhaustion can finalize a *provisional* snapshot without asserting eligibility. New profile facts recompute results; idempotent retries retain their original request semantics. Previous completed voice summaries remain historical; newer refinements supersede their dashboard recommendations.

## Audio operation and staging checks

Audio decode failure switches directly to device speech, with no recursive `speak()` call or second synthesis request. Rejected autoplay, stalled playback, provider errors, fetch timeout and absent device speech lead to device fallback or an explicit readable-text/replay status. Epoch checks, callback cleanup and watchdogs prevent stale playback. Replay is a user gesture and works after automatic ending, including on mobile browsers that reject asynchronous audio. Mute cancels audio. Short final announcements remain authorized after backend finalization, while full recommendation lists are never synthesized. Browser logs include only fixed error codes and numeric durations; server latency metrics exclude citizen text, audio, keys and identifiers.

Server TTS also rejects empty, malformed, truncated or all-zero PCM WAV responses instead of accepting a RIFF prefix as proof of usable audio. This detects silent provider payloads, not muted speakers or incorrectly selected device outputs. Old saved dashboard results are re-evaluated under the current policy without changing historical messages; old voice snapshots display reassessment warnings, and old idempotent caches cannot replay a pre-hotfix eligibility decision.

Required staging checks: genuine authorized Groq/Sarvam interaction, speech after at least five turns, user-gesture replay on iOS Safari and Android Chrome, device volume/output selection, supported/unsupported language fallback, and persisted PostgreSQL snapshots after a backend restart. Automated audio events cannot prove actual sound from speakers. Test outage/quota responses with a staging proxy; do not exhaust paid credits for testing. WAHA HMAC, duplicate suppression, consent and STOP controls are unchanged and covered by the Python suite. No migration or new environment variable is required.

## Local verification commands

Changed application files: `core/cohorts.py`, `core/eligibility.py`, `core/relevance.py`, `core/assessment.py`, `saathi/shortlist.py`, `saathi/understand.py`, `schemes/fields.yaml`, `web/conversation_api.py`, `web/cases.py`, `web/voice.py`, `web/speech.py`, `web/static/voice.js`, `web/static/dashboard-state.js` and their synchronized frontend copies. Added `tests/test_hotfix_cohorts.py` and `scripts/check_hotfix_browser.py`; updated existing discovery/web eligibility expectations, non-silent speech test fixtures, deployment mock fixtures and the explicit continuation browser check. Deployment docs and progress log were updated. `core/loader.py`, `core/questions.py` and the frontend sync tool were inspected; their existing schema/question generation/sync behavior supports the new vocabulary without changes.

Python verification: **306 passed, 1 skipped** (real PostgreSQL test requires an external server). Standalone frontend build and JavaScript syntax checks passed. Browser tests use Chromium/Edge with mocked extraction and simulated speech; they do not certify live service behavior.

```sh
python -m pytest -q
python scripts/sync_frontend.py
# Set BACKEND_ORIGIN to an HTTPS backend before building:
node frontend/build.mjs
node --check web/static/voice.js
node --check web/static/dashboard-state.js
python scripts/check_hotfix_browser.py --url http://127.0.0.1:8080
python scripts/check_completion_browser.py --url http://127.0.0.1:8080
python scripts/check_citizen_browser.py --url http://127.0.0.1:8080
python scripts/check_multilingual_browser.py --url http://127.0.0.1:8080
python scripts/deployment_smoke.py --url http://127.0.0.1:8080 --sse
```

Browser checks require a disposable development case database, authorization enabled, `LLM_PROVIDER=mock`, `MOCK_LLM_FILE=tests/fixtures/deployment_mock.yaml`, server speech credentials unset, and the frontend proxy described in the deployment runbook. Optional `BROWSER_EXECUTABLE` selects an installed Chromium/Edge binary. Do not use these fixtures against production.
