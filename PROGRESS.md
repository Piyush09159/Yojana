# PROGRESS.md: Scheme Saathi change log

This file is the project's history, newest entry first. Read it top to bottom to see what is done, what changed and why.

**Rule for Claude (and humans):** every session that changes code, data or docs adds one entry at the top of the log, using the template below. Never edit or delete old entries. If something was wrong, add a new entry that corrects it.

---

## Current state (update this block every session)

| | |
|---|---|
| Version | v2.5 citizen experience (live deployment unverified) |
| Last updated | 2026-10-31 |
| Tests | 390 passed, 1 skipped, 0 failed (real PostgreSQL requires an external test server) |
| Phase | AgentX WhatsApp hotfix: NOWEB personal LID addressing, preserved activation/voice/consent controls |
| Schemes | 267 public scheme records; 1,008 crawl records; catalog preserved unchanged |
| Eval | eligibility 36/36 on 12 personas; start-with 12/12; name matcher P=1.00 R=1.00 on 20 pairs (small self-labelled sets) |
| Next task | Owner review/publication; Docker and PostgreSQL validation; EasyPanel/Vercel setup and genuine hosted inference/WAHA checks |

---

## NOWEB personal LID sender hotfix: 2026-10-31

**Who:** Codex, under repository-owner instructions.

**Root cause / change:** The signed persistent webhook rejected personal `@lid` senders because its allowlist matched only phone-number `@c.us` addresses. Accept bounded ASCII numeric personal LIDs as well as existing phone JIDs. Preserve the full incoming chat ID and suffix for case hashing, reply requests and encrypted summary routes; never guess a phone number from an opaque LID. Groups, status/broadcast/newsletter IDs, malformed/device-qualified IDs, bot echoes and group-context flags remain rejected. No schema, credential, existing database/session, frontend or eligibility changes.

**Regression evidence:** Signed inbound `yojana` activation exercises the real `_send` adapter with stubbed HTTP and asserts exact `/api/sendText` recipient/session/menu. Covers repeated events, pending reply retry, distinct LIDs, the same digits under different suffixes, conflicting event ownership, scoped STOP, HMAC rejection, voice-note same-case deduplication, browser one-time links, cross-user case authorization and consent-based summary routing. Final full suite: **390 passed, 0 failed, 1 skipped** in 113.19 seconds, including 30 LID regressions. Real PostgreSQL remains skipped without `TEST_POSTGRES_URL`. Python compilation and diff checks passed.

**Engine contract:** Owner confirms deployed NOWEB CORE 2026.10.1 receives personal LIDs. Checked the matching upstream [POST /api/sendText controller](https://github.com/devlikeapro/waha/blob/2026.10.1/src/api/chatting.controller.ts#L73-L82), [NOWEB sendText method](https://github.com/devlikeapro/waha/blob/2026.10.1/src/core/engines/noweb/session.noweb.core.ts#L1080-L1100), and [LID-preserving JID conversion](https://github.com/devlikeapro/waha/blob/2026.10.1/src/core/utils/jids.ts#L56-L68); official [send-message documentation](https://waha.devlike.pro/docs/how-to/send-messages/#chatid) lists LID chat targets. This establishes source-contract support, not acceptance or delivery on the deployed instance.

**Live acceptance remains UNVERIFIED:** No live `WAHA_BASE_URL` / `WAHA_API_KEY` is configured in this environment. After deploying the reviewed fix to the existing service, send `yojana` from a controlled personal LID account, confirm its exact `chatId` is submitted to `/api/sendText`, inspect the actual response and confirm menu receipt on that same handset. Repeat the event without another reply, try another controlled sender, and verify STOP/voice notes. A successful send API response alone is not delivery evidence. No live message was sent, no session was re-paired, and nothing was pushed or deployed by this change.

---

## AgentX WhatsApp final: 2026-10-30

**Who:** Codex, under repository-owner instructions.

**Changed:** Preserved release `95b99fccaf6cee2e14471da7b9d0b289605075ae` on the existing feature branch. Unified development and signed persistent WhatsApp activation policy: exact normalized YOJANA/yojna/योजना only, localized plain-text Chat/Call list, numeric facts never menu commands, direct citizen messages only. Added trusted-origin authenticated bounded voice-note retrieval, in-memory ffmpeg OGG/Opus conversion and existing Sarvam STT integration. Media I/O releases the case transaction so STOP can revoke; version recheck prevents late audio restoring the case. Durable outbox dedup prevents repeated turns on retry. Added channel-language migration 004 and same-case language continuity into browser Call. Existing eligibility, browser interruption, privacy/security and case-storage code retained unchanged.

**Documentation:** Replaced obsolete README with judge-facing features, workflow/architecture Mermaid diagrams, real local interface screenshots, setup/security details and honest evidence table. Added `docs/WHATSAPP_FINAL.md` with exact variables, engine-schema limitations, acceptance checklist and 90-second script. Public URLs, demo video and redacted live WhatsApp screenshot were not supplied; none fabricated.

**Validation:** 360 passed, 0 failed, 1 real-PostgreSQL test skipped. Local real OGG/Opus decode and malformed/oversized/overlong rejection passed. Provider responses and WhatsApp sends mocked. Full frontend build, 19-asset synchronization, JavaScript syntax and pip checks passed. Real local HTTP/readiness/SSE, desktop/tablet/mobile and EN/HI/MR typed browser flows passed; microphone/audio events simulated. Interruption, stale STT/TTS, completion, restored dashboard and consent revocation browser checks passed. Release-file scan found only reviewed fake test/CI credentials. No databases, QR images, private links or secrets included.

**Limitations:** No deployed origins, pinned running WAHA image/engine or live credentials available. Actual WAHA schema/QR session, Groq/Sarvam account calls, real PostgreSQL, container build and physical phone/recipient delivery remain UNVERIFIED. System TEMP drive was full; tests use ignored D-drive temp. An attempted removal of two disposable C-drive pytest caches was denied by Windows permissions; no project or recovery files removed. No push/deploy/main change.

**Next:** Review the local release commit, privately configure the existing EasyPanel services, inspect the actual WAHA engine/schema and execute the external acceptance checklist. Keep backend/frontend on the same exact reviewed commit.

---

## AgentX candidate: 2026-10-30: reliable tap interruption and verified local demo
**Who:** Codex with repository-owner instructions
**Goal:** Finish the smallest reliable demo while preserving all uncommitted V2.5 hotfix work.

**Changed**
- Preserved source-audited cohort, eligibility, completion, relevance, citizen-isolation and snapshot safety changes. Recovery binary patches plus untracked source copies were saved outside the repository before implementation.
- Added explicit Interrupt / Speak Now: synchronous cancellation of server audio/device synthesis, pending TTS abort, generation invalidation and immediate gesture-based listening. Capture epochs, duplicate-final guards and aborted STT prevent stale/duplicate turns. This is tap interruption, not acoustic hands-free barge-in.
- Added visible Completed state, in-flight WhatsApp summary consent revocation and literal 1 Chat / 2 Call menu support. Recipient-bound queues and API-acceptance-only delivery wording are retained.
- Added English/Hindi/Marathi synthetic multi-turn fixtures, relevant localized questions and brief fixed concluding speech. All 23 language entries remain; live coverage is unverified.
- Synchronized all 19 public frontend assets, bumped frontend metadata to 2.5.1, added focused backend/browser regressions and AgentX acceptance/setup/judge documentation. No new database migration or paid service was introduced.

**Tests:** 312 passed, 1 real-PostgreSQL test skipped, 0 failed; pip check, JavaScript checks, frontend build/sync and diff checks passed. Existing cohort/speech/citizen suites and the new interruption/consent suite passed. Actual local HTTP/readiness/SSE and automatic finalization/dashboard reload passed against mock extraction and an isolated SQLite adapter. All three migrations ran twice on that adapter. Five viewport sizes and English/Hindi/Marathi fixtures passed.

**Decisions / limitations**
- An initial follow-up test run encountered a full Windows system-temp drive; using an ignored project-drive temp directory resolved it. The successful full run uses that location.
- No real provider/database credentials, deployment URLs, Docker/PostgreSQL service or WAHA QR session were available. Real PostgreSQL, Groq/Sarvam, physical mobile audio, deployed proxies and actual recipient receipt remain UNVERIFIED. Mocked tests never establish live success.
- Create one local feature-branch release commit after verification. Do not push, deploy, modify main, force-push, reset or merge unrelated branches. Intended backend/frontend source is the same release SHA on feature/v2.5-citizen-experience; live control-panel branch settings cannot be confirmed here.

**Next:** Owner review of docs/AGENTX_DEMO.md, private EasyPanel configuration/QR pairing, real PostgreSQL and provider/mobile smoke checks, then explicit publication/deployment approval.

## v2.5: 2026-10-30: automatic assessment and resilient voice
**Who:** Codex
**Goal:** Complete useful assessments without catalog exhaustion and restore authorized results reliably.

**Changed**
- Added deterministic intent/geography coverage, uncertainty and question-budget policy shared by existing conversation channels.
- Persisted automatic voice completion atomically, added short speech and stale playback cancellation, and bounded Sarvam quota/error fallback.
- Added authorized dashboard match/history restoration and refresh/refinement controls; synchronized standalone frontend.
- Added completion, authorization, speech failure and browser restoration regression coverage and updated deployment runbook.

**Tests:** 295 passed, 1 skipped; frontend build and JavaScript syntax checks passed. Completion browser suite, citizen responsive workflows at five widths, English/Hindi/Marathi workflows and route/SSE smoke checks passed against local mocked inference and simulated audio. Python syntax/secret review found only existing CI references and synthetic test candidates; catalog matches HEAD, index empty.

**Known issues / not done**
- Real PostgreSQL persistence test skipped without an external test server. Hosted Groq/Sarvam, physical microphone/device audio and production proxy behavior require staging verification. No commit, push or deployment performed; no new migration or required environment variable.

## v2.5: 2026-10-30: critical cohort, identity and audio hotfix
**Who:** Codex
**Goal:** Prevent school-only matches and incomplete rule data from falsely confirming eligibility; prevent mixed citizen facts and silent speech failure.

**Changed**
- Audited actual school nutrition/Navodaya catalog rows and official district pages. Added non-mutating source-backed class/enrollment/management overlays; preserved the public catalog and source URLs.
- Unreviewed extraction cannot produce plain eligible. Manual matches cannot count as confirmed completion. Student course/year and useful income questions, pension relevance and program deduplication are explicit.
- Fresh start creates a separate case; continuing is explicit. Identity corrections invalidate stale life-stage facts/results. Dependents use separate bounded profiles. Old persisted snapshots and retry caches cannot restore unsafe eligibility claims.
- Audio decode errors use direct device fallback. Added Replay, autoplay/stall/timeout/error handling, final speech after ending, stale callback guards, fresh-summary clearing, and silent/empty WAV rejection.
- Added actual-catalog, identity, legacy-snapshot and audio regressions, expanded local browser acceptance, and documented before/after outcomes in docs/HOTFIX_V25_AUDIT.md.

**Tests:** 306 passed, 1 skipped; frontend build, synchronization, JavaScript syntax and diff checks passed. Local mocked browser checks cover actual college/senior cases, explicit continuation/fresh start, responsive English/Hindi/Marathi flows, automatic finalization/restoration, SSE and simulated speech failures/replay. No real inference or paid speech was called.

**Known issues / not done**
- Real PostgreSQL, provider connectivity/credits, physical mobile audio and production proxy require staging verification. Broader human audit of extracted catalog rules remains necessary. No migration/new environment variable, commit, push or deployment.

## Entry template

```
## vX.Y: YYYY-MM-DD: short title
**Who:** Claude (model) / name
**Goal:** what this session was trying to do

**Changed**
- file: what changed and why

**Decisions**
- decision, and the reason

**Tests:** N passed, M failed (`python -m pytest -q`)
**Eval:** numbers if they changed

**Known issues / not done**
- ...

**Next:** the next concrete step
```

---

## v2.5: 2026-10-30: Citizen experience, relevant questions and browser voice
**Who:** Codex with repository owner authorization
**Goal:** Implement V2.5 on feature/v2.5-citizen-experience without touching production, merging, pushing or calling paid services.

**Changed**
- core/relevance.py, core/questions.py, saathi/shortlist.py and web/conversation_api.py: explicit category/life-stage/occupation priority dominates unresolved-scheme count. Retired seniors seeking financial help explore pensions/income support, not scholarships. Geography and official eligibility stay deterministic and unchanged; no student flag or benefit is inferred.
- web/frontend.py and public citizen assets: explicit government-scheme hero, four prominent citizen actions, source/evidence/missing-requirement cards, manual language options and saved voice history. Existing layouts, API contracts and owner/session restoration remain. Standalone frontend sync/build includes /voice.
- web/voice.py, web/speech.py and browser voice assets: explicit microphone controls, recognition/transcription/thinking/playback states, typed fallback, bounded optional server STT, server-only Sarvam TTS and retained minimized transcript/ranked summaries. Real HTTP adapters are implemented but live speech quality is unverified.
- Migrations 002_voice.sql and 003_channel_choices.sql add one-use link hashes, browser grants, encrypted channel routes, voice history/snapshots, summary outbox and persistent Chat/Call menu state. Numeric chat answers no longer accidentally select Call.
- Secure WAHA START -> Chat/Call, same-case HTTPS fragment handoff, explicit atomic redemption, replay/expiry/STOP denial and encrypted retry payloads. Consent-based summary worker supports pending/retrying/failed/cancelled/accepted_by_waha, manual retries and cancellation; recipient delivery remains explicitly unverified.
- 23-language BCP-47 registry with separate text/browser STT/provider speech coverage, automatic/mixed-language extraction, manual override and English fallback. Added ten-language fixtures without claiming real model or translation validation.
- Deployment docs/runtime templates updated; docs/V25_CITIZEN_EXPERIENCE.md contains exact local/mock and staging instructions. Added scripts/check_citizen_browser.py; the old V2.3 browser script has fixed historical hero assertions and was write-protected, so its source was preserved.

**Tests:** Full suite 283 passed, 1 skipped, 0 failed; targeted V2.5 suite 26 passed. Existing baseline preserved apart from updating the migration count for the two additive migrations. Dependency, frontend sync/build, JavaScript syntax and whitespace checks pass. Five-width browser checks passed through the standalone frontend/local proxy, covering citizen actions/translations, Explorer, real Rescue, voice layout, mocked en/hi/mr typed voice/restoration/mute/summary and simulated permission failure. HTTP API/routes/cache and incremental SSE/resumption smoke passed. Mobile screenshots visually inspected.
**Eval:** No new real hosted-inference, recognition, speech synthesis, WhatsApp receipt or eligibility-quality claim. Public scheme catalog unchanged.

**Known issues / not done**
- Docker/PostgreSQL server binaries remain unavailable here; real PostgreSQL test is explicitly skipped. Production migrations/service restart and Vercel edge behavior still need staging checks.
- Sarvam/Groq/Qwen credentials were not supplied or used for live calls. Actual microphone/browser-provider quality, ten-priority-language speech quality and WAHA pairing/delivery remain manual validation requirements.
- Third-party send acknowledgement and database commit are not atomic; ambiguous failures can duplicate a WhatsApp response. The UI never equates WAHA acceptance with verified recipient delivery.
- The existing untracked frontend/.gitignore was present before this work and was left untouched. No staging, commit, push, merge or deployment was performed.

**Next:** Review the branch; follow the V2.5 runbook for Docker/PostgreSQL, genuine hosted inference, mobile microphone, Sarvam and opted-in WhatsApp staging checks before considering publication/deployment.

---

## v2.4: 2026-10-29: Deployment infrastructure and persistent case memory
**Who:** Codex with repository owner authorization
**Goal:** Prepare the existing website for Vercel, Docker/EasyPanel, PostgreSQL and a separate WAHA service on the existing 6 GB VM; do not push or deploy.

**Changed**
- Standalone `frontend/` retains all public/dashboard/legacy assets. Its dependency-free Node build emits Vercel Build Output API HTTPS rewrites; API calls and native SSE stay on the frontend origin. Added asset drift checks and local proxy simulation.
- Added pinned-Python non-root Docker runtime, writable public catalog scratch copy, one worker, bounded concurrency, health/readiness, staging Compose and separate official-image WAHA Compose template with a pairing volume and protected API/dashboard.
- Added PostgreSQL case migrations/repository (SQLite adapter for offline validation), owner authorization via HttpOnly cookie or Bearer capability, validated facts, bounded minimized messages, pending questions, case status, guidance-review approvals, absolute retention, deletion and cleanup. Restores main and legacy sessions; interrupted legacy discovery is reported rather than replayed.
- Added signed SHA-512 WAHA endpoint, hashed opt-in mappings, durable event/outbox deduplication and retry reuse; STOP clears the local case and cancels queued earlier replies. Production disables the old URL-secret webhook. Ambiguous external-send failures remain at-least-once, not exactly-once.
- Hosted OpenAI-compatible inference keeps Groq/Qwen support, bounded SDK timeout, safe provider status/error classes, contextual extraction and metadata-only error logging. Production rejects local/mock providers. No genuine inference request was attempted.
- Added deployment tests, mocked multilingual browser checks, Docker/PostgreSQL restart smoke tooling, exact EasyPanel/Vercel/WAHA/rollback instructions in DEPLOYMENT.md and origin/storage audit in docs/DEPLOYMENT_AUDIT_V24.md.

**Tests:** Full suite 257 passed, 1 skipped, 0 failed; original 237-test baseline preserved. `pip check`, frontend synchronization/build, JavaScript syntax and whitespace checks pass. All five-width responsive browser checks pass through the built frontend/local reverse-proxy simulator, including English/Hindi/Marathi landing, navigation, chat retry/reset, Rescue, channels, legacy/docs. Separate en/hi/mr mocked browser checks pass for conversation, cookie authorization, saved-case reload and deletion. HTTP routes, API cache controls and incremental SSE/resume passed through the local proxy.
**Eval:** No new real-world model-quality claim. Public scheme catalog unchanged.

**Known issues / not done**
- Docker build was attempted but Docker CLI/daemon is unavailable. No PostgreSQL server is installed; its dedicated test is explicitly skipped. Image execution, actual PostgreSQL migrations/service-restart persistence and resource consumption remain owner-run validation steps; SQLite tests are not a substitute.
- Live Vercel rewrites, EasyPanel deployment, hosted-model requests and actual WAHA pairing/delivery are unverified. No URLs or external successes are fabricated.
- Local filesystem permissions prevented writing `.github/workflows/deployment.yml`; the complete optional CI workflow is supplied as `deploy/github-actions-deployment.yml` for owner activation. Existing workflow is untouched.
- Existing SECURITY.md was also write-protected; the V2.4 deployment audit explicitly supersedes its old in-memory-only notes. README links the current deployment guidance.
- No staging, commit, GitHub push, cloud deployment, real outbound message or paid inference request was performed.

**Next:** Review the feature branch and run the supplied Docker/PostgreSQL smoke on a Docker host. Then publish/deploy deliberately following DEPLOYMENT.md, configure exact runtime URLs/secrets and perform live synthetic checks before public traffic.

---

## v2.3: 2026-10-29: Resume cleanup and preserve GitHub history
**Who:** Codex with repository owner authorization
**Goal:** Recheck the stable website and publish a linear descendant of GitHub's existing history without starting V2.4.

**Changed**
- Extended `.gitignore` for alternate environments, tox/nox output and temporary patch files.
- Rechecked the ZIP-derived unborn checkout against a fresh clone of `refactor` at `054a768ee47f2b5461947ac442dbf66b726d2669`: no remote-only files; public catalog is byte-identical.
- Created the integration checkout at the workspace sibling `github-integration`, based on both existing remote commits. No unrelated history replacement or remote branch deletion.
- Removed four disposable cache files. OS permissions prevented removing 64 existing cache files; they remain ignored. Preserved the external recovery backup and usable original virtual environment.

**Decisions**
- Create `main` only while it is absent remotely and `refactor` remains unchanged; an ordinary push preserves GitHub's existing history. Remote default-branch settings remain unchanged.
- Fresh standard Python 3.13 environment with locked requirements validates reproducibility independently of Codex paths.
- Staged source scan found only two fictional WAHA test credentials; 176 historical text versions had no detector findings. The unchanged database has only public scheme/crawl tables and empty metadata.

**Tests:** 237 passed, 0 failed in the existing standard environment; fresh integration environment validation and `pip check` passed. Browser checks at 1440/1024/768/390/320px passed for landing/dashboard, English/Hindi/Marathi, navigation, redirects, Rescue, channels, legacy and docs.
**Eval:** Unchanged. No live-model or live-WhatsApp accuracy claim.

**Known issues / not done**
- Some ignored caches are owned by another Windows account and cannot be physically removed in this session.
- Publication is blocked: Git Credential Manager cannot authenticate in this session; the connected GitHub integration returns HTTP 403 `Resource not accessible by integration` for tree creation despite reporting repository push permission. Remote still has only `refactor`; no remote `main` or pull request was created.
- V2.4 has not started. Live multilingual LLM validation, durable case memory and external WAHA delivery remain future work.

**Next:** Record the final publication SHA and synchronization result in the user handoff; stop after repository integration.

---

## v2.3: 2026-10-28: Consolidated website, repository audit and reproducible publication

**Who:** Codex with repository owner authorization
**Goal:** Preserve the verified website, remove disposable output, restore reproducibility and publish safely without rewriting shared history.

**Changed**
- Consolidated public landing at `/`, citizen dashboard at `/app`, `/chat` redirect, legacy form and APIs. Shared branding and English/Hindi/Marathi landing copy are included.
- External full backup before cleanup. Removed three unreferenced installer snapshots, runtime logs and generated caches; retained source, tests, documents, installer and official catalog.
- Hardened ignore rules, explicit public-catalog exception, text/binary attributes, pinned runtime requirements, separate development dependencies and offline CI.
- Standard Python 3.13 environments replace the broken runtime-dependent setup. Windows launcher selects `.venv` explicitly. Replaced local machine paths in documentation/browser checks.
- Added security/data-handling guidance and repository audit. Reviewed current source, both remote commits and public database for sensitive content.

**Decisions**
- Local history was unborn. Remote default `refactor` had two commits and no `main`; use a temporary clone and create a linear `main` descendant rather than replacing history. No force-push, branch deletion, hard reset or destructive Git clean.
- No remote-only files or unaccounted teammate changes found. Public catalog rows match the existing remote; no citizen tables or sensitive identity-field keys found.
- Two detector hits are fictional WAHA test credentials. No real secret found in reviewed source/history.

**Tests:** 237 passed, 0 failed (`python -m pytest -q` in a fresh standard Python 3.13 environment). `pip check` passed. Browser checks at 1440/1024/768/390/320px passed, including translations, routes, chat states, scheme/Rescue/channel flows and docs. HTTP and asset checks passed.
**Eval:** Existing small fixture evaluation is unchanged; no new real-world accuracy claim.

**Known issues / not done**
- Hosted model extraction and external WhatsApp delivery require private configuration and live validation.
- Durable sessions, deployment access/rate controls and queued webhook processing remain deployment work. Voice and live government status/submission are not implemented.

**Next:** Validate deployment configuration and review top scheme rules against official sources. See `docs/REPOSITORY_AUDIT.md` for cleanup and integration details.

---

## v1.0: 2026-10-23: Benefit valuation and a ranking that explains itself

**Who:** Claude (claude-opus-5) with Kshitij
**Goal:** Make ranking the strong feature: work out what each scheme is really worth to this person,
how squarely it targets them, and show why it came first.

**The problem with the old ranking.** It used `2 * value_score - effort`, where both numbers were the
extractor's 1-5 guess. Measured on 258 scraped schemes: `value_score` was the default 3 for 136 of
them and `effort` for 148, so the ranking was driven mostly by defaults. `benefit_annual_inr` was set
on only 28% and conflated three different things: a one-time grant, an insurance ceiling and yearly
income. A Rs 20,00,000 *village* budget and a Rs 25,000 award to *institutions* were being ranked as
personal benefits.

**Changed**
- `core/benefit.py` (new): turns any benefit into comparable rupees per year for one person.
  Monthly x months; one-time spread over 3 years; "until the girl turns 18" over 18 years; an
  insurance cover or "up to" ceiling valued at 4% (a cap is not income); free services with no amount
  fall back to a band from the usefulness guess. `is_personal()` drops money paid to a village,
  panchayat, school or institution, so it can never outrank a person's own scheme. Works on the
  schemes already stored, by reading the benefit text, so nothing had to be re-extracted.
- `core/ranking.py` rewritten: `score = match + 2*value + confidence - effort`, each part explainable.
  `match_strength()` counts only conditions the person *meets* that say who the scheme is for;
  conditions on identity (caste, disability, widowhood, occupation, age, BPL) count double.
  **Exclusions do not count**: passing "you are not an income-tax payer" says nothing about being
  targeted, and PM-KISAN's 7 exclusions had been inflating its match to 9. Scope rules (state,
  district) do not count either. Every scheme carries `reasons`, shown in the UI.
- `saathi/shortlist.py`: cards gained `rank_score`, `match`, `annual_value_inr`, `why_ranked`.
  `web/static/`: the card shows "Worth about Rs N a year" and an expandable "Why it is ranked here",
  in all three languages. `saathi/explain.py`: the agent must use those lines, never invent amounts.
- `eval.py`: the start-with check now runs against the 3 labelled schemes (the personas were labelled
  when only those existed, so ranking over 267 schemes was not a fair comparison), and reports catalog
  reach separately.
- `tests/test_ranking.py` (new, 21 tests).

**Bugs found while building this**
- A Rs 200/month pension stored as Rs 2,400/year was read as Rs 28,800: the code applied the period to
  a figure that was already annual. An annual figure may now only be reduced, never multiplied.
- PMAY's Rs 1,30,000 house grant was counted as yearly income. One-off wording ("construction of
  houses", "marriage assistance", "funeral", "capital subsidy") is now detected even when the page
  never says "one-time".
- PM-JAY's Rs 5,00,000 hospital cover was counted as cash income; "treatment" and "up to" now mark a
  ceiling.

**Example:** for a 45-year-old SC widow in Beed the top result is Indira Gandhi National Widow Pension
(Rs 18,000/yr, meets 4 conditions including who it is meant for), ahead of Swadhar Greh which pays
more (Rs 40,000/yr) but matches her less specifically.

**Tests:** 215 passed, 0 failed. Eval: eligibility 36/36, start-with 12/12 on the labelled schemes,
name matcher P=1.00 R=1.00. Catalog: 267 schemes, 495 rules, 528 pages still queued.

**Known issues / not done**
- Reach is thin for sparse profiles: 1.1 schemes offered per labelled persona, because those personas
  carry only 4-5 facts and most schemes then sit in "needs more info". Real sessions ask questions.
- Some matches are still questionable (a fisheries scheme surfaced for a farm labourer); no labelled
  set exists yet to measure false positives.
- The web UI and investigator still have not been run live.

**Next:** run the web UI live, finish extraction, verify the top schemes by hand.

---

## v0.9: 2026-10-23: Scheme database + two-phase scraping

**Who:** Claude (claude-opus-5) with Kshitij
**Goal:** Replace the YAML scheme files with a database, and make scraping reach the district-level
schemes that people do not know about.

**Changed**
- `store/db.py` (new): SQLite at `data/schemes.db`. Tables `schemes` (indexed by state, district,
  normalised name) and `crawl_urls` (every page seen, including a `queued` state). `load_catalog(state,
  district)` returns only the schemes that can apply to one person. Duplicate search is one indexed
  query plus a rapidfuzz pass over same-state names, instead of comparing against the whole catalog.
- `store/migrate.py` (new): `python -m store migrate | export | stats | verify <id>`.
- **All 29 schemes moved into the database; `schemes/*.yaml` and `schemes/discovered/` deleted**
  (still in git history). `schemes/` keeps only fields.yaml, questions.yaml, flagship.yaml, which are
  vocabulary, not data. The 3 hand-verified schemes are kept as `tests/fixtures/schemes/` so tests stay
  offline and deterministic. `data/schemes.db` is committed; its WAL scratch files are not.
- `discovery/catalog.py` is now a thin layer over the database; `catalog_for(profile)` added.
  `core/loader.load_schemes()` reads the database. `cache/discovery_index.json` replaced by `crawl_urls`.
- **Two-phase scraping.** Phase 1 `--map-urls mh`: walk listing pages, queue every scheme URL, no LLM,
  free. Phase 2 `--extract-queued --limit N`: read queued pages with the LLM, stop and resume any time.
  This shows how many schemes exist before any token is spent.
- `ingest/crawl.py` rewritten: goes straight to the platform's standard `/en/schemes/` listing paths
  instead of guessing from the homepage, follows pagination, and classifies pages in code.
  `worth_extracting()` drops pages with no eligibility wording before the LLM is called.
- `ingest/fetch.py`: a link-heavy page with little text is no longer rejected (listing pages are mostly
  links); the LLM is protected by `worth_extracting` instead.
- Tests: 189 (5 new: URL mapping with pagination, pre-filter, queue behaviour, state/district filtering).

**Measured this session**
- Phase 1 is free and fast: 5 sites -> 60 scheme pages; the full Maharashtra map queued 500+ pages.
  Single districts are rich: Nagpur 43, Kolhapur 32, Sangli 25, Nanded 20 scheme pages.
- **Local models are not usable for bulk extraction.** `qwen2.5:7b-instruct` on this machine took
  112 s, 426 s and 525 s for three pages and produced schema errors twice and a benefit sentence as a
  scheme name. 1,500 pages would take over 160 hours. The earlier "~6 hours overnight" estimate was wrong.
- **Gemini 3.5 flash is the right extractor**: 4.5-13 s per page, no retries, correct names, and it
  found real district schemes (ZP Kanya Kalyan Yojana, ZP Cess Fund illness assistance).
  `.env` now has EXTRACTOR_PROVIDER=gemini with Groq as fallback.

**Bugs found and fixed**
- Queued URLs were treated as "already processed", so the first extraction run did nothing.
- Listing pages were rejected by the fetcher for having little text.
- Page 2+ of a listing was classified as a scheme page, so pagination was never followed.

**Phase 1 result (free, no LLM):** 45 sites -> **920 scheme pages found, 877 queued**, only 3 sites
returned nothing. Richest: ZP Palghar 82, Beed 73, Satara 68, Dharashiv 63, Raigad 60, OBC Bahujan
Kalyan 52, Nagpur 43. Against a starting catalog of 29, this is the scale change the project needed.

**Extraction quality (sampled by hand):** rules carry real quotes from the page
(PMSBY age 18-70 + bank account; Jal Jeevan rural; Ullas literacy age 15+; Lek Ladki female +
Maharashtra + income <= 1 lakh, with the child-age criteria correctly left as "check manually").
About 1 in 5 pages yields no machine-checkable rule and shows as "check the official page".

**District tagging bug found and fixed.** A Zilla Parishad site publishes its own schemes *and*
state/central ones. The code tagged everything from a ZP site with that district, which hid national
schemes (Jal Jeevan Mission, PMAY-Gramin, Jawahar Navodaya Vidyalaya) from everyone outside that
district. The extractor now returns `is_district_specific`, and only then is the district applied.
21 already-stored schemes were repaired.

**Mistake made and corrected:** while repairing those rows I deleted records whose name was over 90
characters, intending to remove one piece of garbage from the local-model test. That also deleted two
legitimate schemes with long names (State Govt. Merit Scholarship; Dr. Babasaheb Ambedkar Agricultural
Self-Reliance / Birsa Munda). Their pages were re-queued, so they will be read again. A crude length
rule should not have been used to decide what is real.

**Busy vs capped providers.** `llm/fallback.py` now separates a transient 503/timeout ("busy": wait
2s, 6s, 15s and ask the same provider again) from a hard 429/quota ("capped": switch and bench it for
10 minutes). Gemini returned 503 on 5 of 11 calls during the first batch, and the old code benched it
for 10 minutes each time.

**Scope rules are not qualifying evidence (important correctness fix).** "ZP Kanya Kalyan Yojana"
(a girl-child scheme) was the top recommendation for a 28-year-old man in Sangli. Its only extracted
rules were `state = maharashtra` and `district = sangli`; the real condition ("families having one or
two girl children") had gone to unmapped_criteria. Those two rules say *where* a scheme applies, not
*who* qualifies, so counting them as evidence made an un-understood scheme look eligible to everyone in
the district. `core/eligibility.py` now counts only non-scope fields (`SCOPE_FIELDS = {state, district}`)
when deciding between "eligible_check_manually" and "check_manually". After the fix the same man gets
Jal Jeevan Mission (rural, eligible), Har Ghar Nal Se Jal and the Ullas literacy programme.

**Free-tier quota is the real limit on bulk extraction.** A 400-page batch produced only 27 schemes
because every provider ran out: gemini-3.5-flash 429 quota, groq 120b and 20b 429, gemini-3.8-flash 503.
Each Gemini model has its own quota, so EXTRACTOR_FALLBACKS now chains
`gemini-3.5-flash -> gemini-3.1-flash-lite -> gemini-3.8-flash -> groq 120b -> groq 20b`. Pages whose
extraction fails are left `queued`, so nothing is lost and the next run picks them up.

**Tests:** 194 passed, 0 failed (all offline).

**Known issues / not done**
- The investigator and the web flow still have not been run against a live model.
- About half of fetched pages still fail (bot blocks, JavaScript-only sites).
- Awareness is inferred from the agency type; no measurement of what people actually know.

**Next:** finish the Maharashtra extraction, then measure how many lesser-known schemes a real persona gets.

---

## v0.8: 2026-10-23: Web UI + reasoning investigator for lesser-known schemes

**Who:** Claude (claude-opus-5-5) with Kshitij
**Goal:** Web page with a form for basic details, a reasoning "investigator" that deep-dives official sites
for lesser-known schemes, reason-based questions, and results that separate lesser-known schemes.

**Changed**
- `llm/`: provider `local` (Ollama/LM Studio via `LOCAL_BASE_URL`, default http://localhost:11434/v1);
  role `planner`; fallback chains `<ROLE>_FALLBACKS=provider:model,...` (FallbackClient now takes any number
  of clients; connection errors and timeouts also switch). `scripts/bench_llm.py` compares models on
  3 jobs (stopped on request to save tokens; no numbers yet).
- `ingest/sources_mh.yaml` + `ingest/source_map.py`: Maharashtra source map (22 agencies, 36 districts x
  4 domain patterns = 183 candidates). `python -m ingest --probe-sources mh` (no LLM) found 45 usable sites:
  16 agencies + 29 Zilla Parishads; all collector portals disallow bots. Written to sources_mh.resolved.yaml.
- `ingest/crawl.py`: site crawler (same-site scheme-looking links, depth 2, page cap; listing pages expanded,
  others extracted). Pages now keep their links (`Page.links`). `python -m ingest --source-map mh --district X`.
- Scheme metadata `agency_type`, `agency_name`, `district`; implicit district rule in `core/eligibility.py`;
  `district` normalised to a key with aliases (Dharashiv -> osmanabad).
- `core/awareness.py` + `schemes/flagship.yaml`: high / medium / low; `core/ranking.lesser_known`;
  shortlist adds `lesser_known` and awareness/agency on cards.
- `core/questions.question_candidates`: value of information = summed weight (money, usefulness, obscurity)
  of undecided schemes needing the field. `saathi/questioner.py`: planner picks one candidate and writes the
  question + reason; any field outside the candidates is replaced by the top one.
- `saathi/investigator.py`: analyse (planner: needs, tags, search terms) -> plan (code: district sites,
  agencies matching need tags, searches) -> execute (crawler / discoverer) -> reflect (planner: uncovered
  needs, leads, stop). Budget 3 rounds / 12 searches / 25 pages / 180 s. Emits events.
- `web/`: FastAPI (`app.py`, `session.py`, `__main__.py`) with SSE events, structured answers validated by
  code (no LLM), free-text answers via the extractor, free questions via the agent; static page
  (`index.html`, `app.js`, `style.css`) with form, live investigation timeline, question box with
  choice buttons and "why I ask", result cards (start with / lesser-known / also / almost / check page),
  rules-and-quotes dialog, en/hi/mr labels, dark mode.
- `.env`: planner on Gemini `gemini-3.5-flash` (saves Groq tokens), fallbacks to Groq 20b then local
  Ollama models. `start.ps1`: web page is now the default; `-Cli`, `-Crawl [-District X]`.
- Tests: `tests/test_web.py` (12) + 3 in `test_llm.py`; district test updated.

**Decisions**
- Search by needs and agencies, not by famous scheme names: that is where lesser-known schemes are.
- The planner may only choose questions from code-scored candidates and never decides eligibility.
- Result cards are rendered from engine JSON directly (no LLM summary), so results cost no tokens;
  LLM calls per session: ~1 analysis, <= 2 reflections, 1 per question, extraction per new page.
- No live LLM testing this session, at the user's request (Groq token budget).

**Tests:** 184 passed, 0 failed (offline). Server smoke check: page, /api/form (15 fields, 36 districts), app.js served.

**Known issues / not done**
- Not run live yet: prompts for analysis/reflection/question choice may need tuning after first real sessions.
- Step 7 (eval of lesser-known recall with Maharashtra personas) not built yet.
- The terminal chat (`python -m saathi`) still uses the v0.7 flow, not the investigator.
- Value labels in the form/buttons are English words in all languages (field labels are translated).

**Next:** user tries the web page; then tune prompts, crawl more districts, build the lesser-known eval.

---

## v0.7.1: 2026-10-22: One-command Windows launcher

**Who:** Claude (claude-opus-5-5)
**Goal:** Start the whole project with one command.

**Changed**
- `start.ps1` (new): checks Python, creates `.env` from the example if missing, installs packages only if an import fails, sets the console to UTF-8, then runs the chat. Switches: `-NoWeb`, `-Update [-State X -Searches N]` (scrape, then chat), `-Stats`, `-Test`, `-Debug`. ASCII only, so Windows PowerShell 5.1 reads it correctly.
- `start.bat` (new): double-click wrapper that runs start.ps1 with `-ExecutionPolicy Bypass`.
- `saathi/__main__.py`: strip a BOM from input (PowerShell pipes add one, so "quit" was not recognised).
- README, CLAUDE.md commands.

**Tests:** 167 passed, 0 failed. Launcher checked: `-Stats` and `-NoWeb` with "quit" both exit 0.

**Next:** unchanged (grow catalog, verification tool, FastAPI).

---

## v0.7: 2026-10-22: Interactive assistant with web scraping (Phases C, D, E)

**Who:** Claude (claude-opus-5-5) with Kshitij
**Goal:** `python -m saathi`: the system asks questions, scrapes official sites for schemes, decides, and talks back; no longer limited to 3 schemes.

**Changed**
- `ingest/sources.yaml` (new): scrape policy. **myscheme.gov.in blocked**: robots.txt allows all, but its Terms of Use forbid "bots, scraper tools ... unless expressly authorized by the myScheme in writing". india.gov.in returns Access Denied to bots. Only `*.gov.in` / `*.nic.in` pages are used as rule sources.
- `ingest/fetch.py` (new): robots.txt (unreadable robots = skip site), 2 s per domain, 30-day disk cache (cache/web/), HTML via BeautifulSoup, PDF via pypdf, rejects JavaScript-only pages.
- `ingest/extract.py` (new): LLM page -> scheme dict in the curated schema; quote check (exact or fuzzy >= 92); unmappable rules -> unmapped_criteria; any lost rule adds a "check manually" note; a lost `any` alternative drops the block; default near_miss (age 2, income 10%); `promote_unmapped` turns clear applicant-age wording into age rules (not wording about children/students); retry with shorter text on HTTP 413.
- `ingest/__main__.py` (new): `python -m ingest --state X --topics ... --names ... --stats`; 19 topics; follows scheme names found on listing pages; budget counts only paid searches.
- `discovery/search.py` (new): SerpAPI, Tavily, Fake; permanent search cache (cache/search/); "no results" = empty list.
- `discovery/catalog.py` (new): curated + discovered, dedupe by normalised name per state, curated wins; `save_discovered` returns new/updated/unchanged/curated_exists.
- `discovery/discover.py` (new): per-profile discovery (LLM brainstorms scheme NAMES only -> search -> official results -> fetch -> extract -> save); processed URLs remembered in cache/discovery_index.json.
- `core/eligibility.py`: implicit state-residency rule for state schemes; `origin`, `verified`, `unmapped_criteria` on results; statuses `eligible_check_manually` and `check_manually` (no rules, or more unmapped conditions than mapped rules).
- `core/ranking.py`: fully eligible always before check-manually; then score, verified, fewer open conditions, benefit.
- `core/questions.py`: synthetic questions from fields.yaml for fields no curated question covers.
- `core/loader.py`: `load_scheme_dir` (uncached; skip_invalid for web data); discovered schemes may have no rules; `state` validated.
- `schemes/questions.yaml`: 7 basic questions in en/hi/mr (state, gender, occupation, income, category, marital, disability).
- `saathi/` (new): `understand.py` (LLM -> facts + intent, validated), `chat.py` (profiling -> discovery -> up to 4 refining questions -> shortlist -> Q&A; new facts re-run the engine; unknown scheme names are looked up live), `shortlist.py` (deterministic JSON incl. rules_met and check_on_official_page), `explain.py` (agent writes replies only from JSON; markdown stripped), `__main__.py`.
- `llm/fallback.py` (new) + `llm/config.py`: fallback provider on 429/quota/413/503 with a 10-minute cooldown. `openai_provider.py`: retry without JSON mode on Groq `json_validate_failed`.
- `core/fields.py`: `profile_json_schema(brief=True)`.
- `tests/conftest.py`: tests write LLM logs to tmp and ignore the .env fallback (mock calls had been landing in logs/).
- `tests/test_discovery.py` (new, 29 tests) and 4 new tests in `test_llm.py`.
- `.env`: `LLM_FALLBACK_PROVIDER=openai`, `LLM_FALLBACK_MODEL=openai/gpt-oss-20b`. `.gitignore`: cache/. README and CLAUDE.md updated.

**Decisions**
- Scrape only where both robots.txt and the terms allow; myScheme needs written permission (could be requested from Digital India Corporation).
- The LLM may suggest scheme NAMES to search for, never rules or eligibility; rules must be quoted from an official page.
- Weak evidence (more unmapped conditions than rules) is never "likely eligible"; it is shown as "check the official page".

**Bugs found live and fixed**
- PG single-girl-child scholarship ranked first for a 30-year-old mother (1 mapped rule, 2 unmapped) and the agent invented "you are a single girl". Fixed by the evidence rule, ranking order, `rules_met` and a no-assumptions prompt rule.
- Rashtriya Vayoshri (senior citizens) shown to a 30-year-old because "Senior Citizen" was left unmapped. Fixed by `promote_unmapped` and the prompt; the saved file was repaired. A first repair wrongly turned Lek Ladki's "girl up to 18" into an applicant age rule; reverted, and child/student wording is now excluded.
- SerpAPI "no results" stopped the whole run; Groq 413 on long pages; Groq daily token cap (200k) hit; Gemini 3.8 flash 503 overload; Groq 20b `json_validate_failed`; Hindi/Marathi language flip on short answers; markdown in replies; a regex word boundary written as a backspace character.

**Tests:** 167 passed, 0 failed (all offline).
**Live:** 2 bulk runs (27 of 250 SerpAPI credits used) -> 19 discovered schemes. Hindi and Marathi conversations ran end to end. Beed mother profile -> Lek Ladki Yojana first, PM-SYM second, 3 weak matches under "check the official page".

**Known issues / not done**
- See CLAUDE.md section 9 (v0.7 items): ration-card colour mapping, Lek/Lake Ladki duplicate, Groq daily cap, preflight/status not in chat.
- Discovered schemes are unverified; none has been checked by a person yet.

**Next:** grow the catalog state by state; a small review tool to mark discovered schemes verified; Phase F FastAPI.

---

## v0.6.1: 2026-10-22: Try-it script

**Who:** Claude (claude-opus-5-5)
**Goal:** Let the user run text -> LLM extraction -> validation -> engine decision in one command before Phase E exists.

**Changed**
- `scripts/try_query.py` (new): `python scripts/try_query.py "<query>"`. Uses the network for the LLM step only.
- `CLAUDE.md` section 10: command added.

**Tests:** 135 passed, 0 failed (no test changes)
**Live check:** "मेरी उम्र 62 है, 2 एकड़ ज़मीन है, BPL कार्ड है" -> 5 fields -> IGNOAPS eligible (start with), PM-KISAN and PM-JAY need info, next question = land before Feb 2019.

**Next:** Phase C1 source survey.

---

## v0.6: 2026-10-22: Phase B, canonical field vocabulary

**Who:** Claude (claude-opus-5-5)
**Goal:** One fixed list of profile fields that every scheme rule (curated or scraped) and every extracted profile must use, so the scraper cannot invent fields.

**Changed**
- `schemes/fields.yaml` (new): 46 fields with type (bool/int/number/enum/str), min/max or allowed values, group, description and optional question id. Covers age, gender, marital status, social category, minority, disability %, ex-serviceman, pregnancy; state (all 28 states + 8 UTs), district, rural/urban; family income, BPL, ration card type, bank account, pension; occupation (16 values), BOCW registration, e-Shram; student, study level (pre/post-matric), highest education; family size, children, girl child under 10; land, land acres; pucca house; the PM-KISAN exclusions; PM-JAY list; the 5 dependency flags.
- `core/fields.py` (new): `load_fields`, `check_value` (strict types; whole floats accepted for ints; ranges and enums checked), `validate_profile` (drops unknown/bad fields, keeps `_internal` keys and None), `profile_json_schema` (JSON schema for the extractor LLM).
- `core/loader.py`: rules must use a known field with a value of the right type (lists for in/not_in, numeric fields for gt/gte/lt/lte, numeric `near_miss` only on order ops); dependency ids must be fields; `unmapped_criteria` must be a list of strings; questions may only set known fields and `on_no` values must fit; `fields.yaml` is not loaded as a scheme.
- `tests/test_fields.py` (new, 28 tests).
- `CLAUDE.md`: code map, field section and task list updated.

**Decisions**
- Unknown or invalid profile values are dropped, not coerced ("yes" is not turned into true). Coercion belongs in the extractor prompt and answer parser, where the user's language is known.
- `state` is an enum of snake_case names so web-extracted rules and user profiles always match exactly.
- Fields that exist for one scheme only (e.g. `land_owned_before_2019_02_01`) stay, but new single-scheme criteria go into `unmapped_criteria`.

**Tests:** 135 passed, 0 failed. Eval unchanged: eligibility 36/36, start-with 12/12, name matcher P=1.00 R=1.00.
**Live check:** Groq gpt-oss-120b + `profile_json_schema()` on a Hindi sentence extracted 11 correct fields (age, widowed, SC, Maharashtra, Pune, rural, income 80000, BPL, ration card, land, 2 acres) with 0 validation errors. ~2k input / ~1.6k output tokens.

**Known issues / not done**
- The extractor did not infer gender from Hindi verb forms ("रहती हूँ" = female). Decide in Phase E whether grammatical gender counts as "stated".
- Most new fields have no question in `questions.yaml` yet (Phase E).

**Next:** Phase C1: source survey (`ingest/sources.yaml`).

---

## v0.5.1: 2026-10-22: Live keys configured and verified

**Who:** Claude (claude-opus-5-5) with Kshitij
**Goal:** Set up `.env` with the user's keys and run the first live calls.

**Changed**
- `.env` (new, git-ignored): extractor + agent on Groq (`LLM_PROVIDER=openai`, `OPENAI_BASE_URL=https://api.groq.com/openai/v1`, `LLM_MODEL=openai/gpt-oss-120b`); judge on Gemini (`gemini-3.8-flash`); `SEARCH_PROVIDER=serpapi`.
- `.env.example`: the keys had been typed into the template; moved them to `.env` and blanked the template again. Checked git history: the keys were never committed.
- `llm/providers/openai_provider.py`: +4000 tokens of headroom for reasoning models (o-series, gpt-5, gpt-oss, qwen3). gpt-oss on Groq returned empty text at max_tokens=20 because reasoning used the whole budget.
- `llm/providers/gemini_provider.py`: +4000 tokens of thinking headroom, same reason.
- `llm/config.py`: Gemini default model `gemini-2.5-flash` -> `gemini-3.8-flash` (the old one returns 404 "no longer available to new users").
- `tests/test_llm.py`: updated default-model assertion; added reasoning-headroom assertion.

**Decisions**
- `openai/gpt-oss-120b` chosen because it is the strongest general model on this Groq key (others: gpt-oss-20b, qwen3.8-27b, allam-2-7b).
- Judge on a different vendor (Gemini) than extractor (Groq) so it never grades its own output.

**Tests:** 107 passed, 0 failed. Live: `python scripts/llm_smoke.py` OK on all 3 roles; Hindi query -> `{age: 62, owns_cultivable_land: true}` on both providers. Latency: Groq ~0.5 s, Gemini 7-10 s.

**Known issues / not done**
- Gemini latency is high for the judge; acceptable for offline eval.

**Next:** Phase B: `schemes/fields.yaml`.

---

## v0.5: 2026-10-22: Phase A, swappable LLM layer

**Who:** Claude (claude-opus-5-5)
**Goal:** Build `llm/` so every later module calls one interface, with Anthropic, OpenAI(-compatible), Gemini and mock providers selectable per role.

**Changed**
- `llm/base.py` (new): `LLMClient` protocol, `LLMResponse`, `LLMError`, `ProviderBase`. `complete()` adds the JSON schema to the system prompt, parses (tolerates ```json fences), validates with `jsonschema`, retries once with the validation error, then raises `LLMError`. Vendor exceptions are wrapped in `LLMError`.
- `llm/log.py` (new): one JSON line per call to `logs/llm_calls.jsonl` (provider, model, role, attempts, tokens, latency, error). No prompt or reply text, so no PII. `LLM_LOG=0` turns it off; `LLM_LOG_PATH` moves it.
- `llm/config.py` (new): `get_llm(role, provider=, model=)` and `resolve(role)`. Roles: extractor, agent, judge. `<ROLE>_PROVIDER/_MODEL` -> `LLM_PROVIDER/LLM_MODEL` -> defaults. Loads `.env` once at import. Warns when judge == extractor.
- `llm/providers/anthropic_provider.py`: official SDK; text blocks only (skips thinking blocks); no `temperature` (rejected by current Claude models); +8000 tokens thinking headroom on `max_tokens`; `fallbacks="default"` (beta `server-side-fallback-2026-07-01`) on Fable 5.1 / Opus 5.5 / Opus 5 / Sonnet 5.5; refusal raises `LLMError`; optional `ANTHROPIC_EFFORT`.
- `llm/providers/openai_provider.py`: chat completions; `OPENAI_BASE_URL` for Groq/Together/Ollama/vLLM; `response_format=json_object` in JSON mode; `max_completion_tokens` on the official API, `max_tokens` on compatible servers; no temperature for o-series/gpt-5.
- `llm/providers/gemini_provider.py`: google-genai; assistant -> `model` role; `response_mime_type=application/json` in JSON mode.
- `llm/providers/mock_provider.py`: canned replies by prompt substring or a queue; records calls; `MOCK_LLM_FILE` YAML for offline runs.
- `tests/test_llm.py` (new, 24 tests): mock, fence parsing, retry on bad JSON and on schema errors, clear failure after the retry, usage, log contents (no names leak), config resolution, judge warning, adapters with fake SDK clients, and a scan that fails if any module outside `llm/providers/` imports a vendor SDK.
- `scripts/llm_smoke.py` (new): live check per role (text + Hindi JSON extraction).
- `requirements.txt`: python-dotenv, jsonschema, anthropic, openai, google-genai. `.env.example`: ANTHROPIC_EFFORT, LLM_LOG, LLM_LOG_PATH, MOCK_LLM_FILE, default model note. `.gitignore`: logs/.
- `CLAUDE.md`: sections 3, 4, 5, 6, 8 and 10 updated for the ingestion/live-search split, the API phase and the new code.

**Decisions**
- JSON mode is prompt + code validation for every provider rather than each vendor's native structured output, so behaviour and retry are identical across providers and the schema needs no vendor-specific restrictions.
- Default Anthropic model is `claude-sonnet-5-5` because blueprint section 6 names Sonnet 5.5 as primary. Defaults for OpenAI (`gpt-4.1-mini`) and Gemini (`gemini-2.5-flash`) are placeholders; set `<ROLE>_MODEL` to the model you want.
- `LLM_MODEL` applies only to roles on the default provider, so `JUDGE_PROVIDER=gemini` never receives a Claude model name.
- `.env` is loaded once at import so tests can clear variables safely.

**Tests:** 107 passed, 0 failed (`python -m pytest -q`)

**Known issues / not done**
- No live call was made this session (no keys in this environment). Run `python scripts/llm_smoke.py` once keys are in `.env`.
- The OpenAI and Gemini default model names may be out of date; check them against current model lists.
- The judge==extractor warning compares env-resolved settings; CLI overrides on only one role are not compared.

**Next:** Phase B: `schemes/fields.yaml` and loader validation of fields.

---

## v0.4: 2026-10-22: Backend build plan

**Who:** Claude (claude-opus-5-5) with Kshitij
**Goal:** Agree a structured plan for the backend (no WhatsApp/WAHA/UI).

**Changed**
- `docs/BUILD_PLAN.md` (new): phases A to G with milestones, files, tests and verification.

**Decisions**
- Backend only: conversational Q&A engine, CLI, then a FastAPI service with SQLite (Postgres-ready). No WhatsApp, voice or website.
- Goal is "as many schemes as possible" from the web. Discovery is split into **bulk ingestion** (offline crawl of official directories such as myscheme.gov.in, india.gov.in and state portals, only where robots.txt/ToS allow) and **live per-user search** to fill gaps.
- Order: LLM layer -> fields.yaml (~40 fields) -> source survey -> first scrape (50 schemes) -> engine + conversation + CLI -> live search -> FastAPI -> full scrape -> evaluator.
- Keys available for Anthropic, OpenAI-compatible and Gemini; all three providers will be built.

**Tests:** 83 passed, 0 failed (no code changed)

**Known issues / not done**
- CLAUDE.md sections 3, 6 and 8 still describe search-only discovery; update them in the Phase A session.

**Next:** Phase A: `llm/` layer with anthropic, openai, gemini and mock providers.

---

## v0.3: 2026-10-22: Project docs and new end goal

**Who:** Claude (claude-opus-5-5)
**Goal:** Write a handover file for Claude Code, start this change log, and record the new end goal.

**Changed**
- `CLAUDE.md` (new): full project guide for Claude Code. Covers rules, blueprint section map, the new end goal (query -> understand -> discover schemes from the web -> deterministic decision -> explain -> evaluate), current code map, YAML schema, swappable LLM layer design, web discovery design, evaluator design and task order.
- `PROGRESS.md` (new): this log, back-filled with v0.1 and v0.2.
- `docs/blueprint.pdf` (new): copy of the project blueprint, kept as reference.

**Decisions**
- **New end goal:** build the core only (no WhatsApp/WAHA, voice, database or UI). The core must find schemes from the web, not only the 3 hand-written ones.
- **Web schemes use the same YAML schema** as curated ones, so the deterministic engine evaluates both. The LLM extracts rules from the web but never decides eligibility.
- **Every web-extracted rule needs a `source_quote`** that code checks against the fetched page. Rules without a matching quote are dropped. Discovered schemes stay `verified: false` until a human checks them.
- **Criteria with no known field go into `unmapped_criteria`**, and the result becomes "check manually" instead of a guess.
- **LLM is swappable per role** (`extractor`, `agent`, `judge`) through env vars. Only `llm/providers/*` may import vendor SDKs. The judge can use a different model from the extractor so it does not grade its own work.
- Official domains (`*.gov.in`, `*.nic.in`, myscheme.gov.in) are preferred as rule sources. Each site's terms and robots.txt are checked before fetching.

**Tests:** 83 passed, 0 failed (no code changed)

**Known issues / not done**
- Discovery, LLM layer and evaluator are designed but not built.

**Next:** Task 1 in CLAUDE.md section 8: the `llm/` layer with anthropic, openai(-compatible), gemini and mock providers.

---

## v0.2: 2026-10-21: Phase 1 core logic built

**Who:** Claude (claude-opus-5-5)
**Goal:** Build the deterministic core (blueprint layers 1 to 3) in pure Python with tests. No WhatsApp, LLM or UI.

**Changed**
- `schemes/pm_kisan.yaml`: PM-KISAN rules: owns cultivable land, land owned before 01-02-2019, and exclusions (institutional landholder, income-tax payer, constitutional/elected posts, govt employee above Group D, pension of Rs 10,000+ a month, registered professionals, another family member already receiving). Also dependencies (eKYC, land seeding, NPCI seeding, active account), documents, actions and a status map with 7 statuses.
- `schemes/ignoaps.yaml`: old-age pension: age 60+ (near-miss window of 2 years) and BPL. Central share only (Rs 200 for ages 60-79, Rs 500 for 80+).
- `schemes/pmjay.yaml`: Ayushman Bharat: `any` of age 70+ (near-miss 2 years) OR in the beneficiary list.
- `schemes/questions.yaml`: 7 questions in en/hi/mr. All PM-KISAN exclusions are merged into one question so a full run stays at 7 questions or fewer.
- `core/loader.py`: loads and validates YAML (valid ops, actions referenced by status_map and dependencies exist, duplicate ids).
- `core/eligibility.py`: per-rule trace; statuses eligible / near_miss / needs_info / ineligible; a hard fail beats unknowns; for `any` blocks only the closest failed alternative counts; bad input types give `unknown` instead of a crash.
- `core/ranking.py`: score = 2 x value_score - effort; one `start_with` plus a queue.
- `core/questions.py`: `next_best_question` picks the question that touches the most undecided schemes, respects `only_if` and never repeats a question. `apply_answer` handles `on_no` for combined questions.
- `core/name_match.py`: normalisation (honorifics, S/O-W/O-D/O, punctuation, initials, word order), Devanagari to Latin transliteration, phonetic folding (ph/f, w/v, ee/i, x/ks, double letters, final schwa), verdicts match / probable_mismatch / mismatch with differing tokens and notes. Same name in two scripts counts as a match. DOB comparison handles year-only and swapped day/month.
- `core/preflight.py`: cross-document name and DOB checks plus the dependency chain, giving ordered fixes and flags.
- `core/status_decoder.py`: fuzzy match of the reported status (English, Hindi and Marathi aliases), weights boosted by +0.35 when a pre-flight flag matches, top 3 causes.
- `core/checklist.py`, `core/pipeline.py` (`assist()` returns one JSON with everything).
- `tests/`: personas.yaml (12), name_pairs.yaml (20), 4 test files, 83 tests.
- `eval.py`, `demo.py`, `README.md`, `requirements.txt`.

**Decisions**
- Started with 3 schemes (PM-KISAN, IGNOAPS, PM-JAY) because they cover farmer, elderly and health personas from the blueprint.
- No web scraping at runtime in Phase 1. Rules are hand-written YAML with a source URL and date. (Changed in v0.3: web discovery is now the goal.)
- PM-JAY list membership is asked from the user, who checks beneficiary.nha.gov.in, because only that portal knows it.

**Bugs found and fixed during the session**
- Status aliases replaced substrings ("failed" became "faileded"). Changed to whole-word replacement.
- Devanagari vowel signs were stripped by the punctuation regex, so Hindi status text did not match. Devanagari is now kept.
- A 69-year-old not in the PM-JAY list was marked ineligible instead of near-miss, because the hard-failing list rule outvoted the near age rule. Only the closest `any` alternative counts now.
- The question loop needed 8 questions. Merged the institutional-landholder question into the exclusions question.

**Tests:** 83 passed, 0 failed
**Eval:** eligibility 36/36 (12 personas); start-with 12/12; name matcher precision 1.00, recall 1.00 (20 pairs)

**Known issues / not done**
- Rules were written from search results (PM-KISAN FAQ snippets, Vikaspedia, Drishti IAS, myScheme). The official PDFs came back garbled and were not read line by line.
- IGNOAPS Maharashtra top-up is missing. Helpline numbers are unverified. Status strings are not checked against the live portal.
- A "yes" to the combined exclusions question sets `_needs_followup`, but no follow-up question exists.
- The PM-KISAN inheritance exception to the 2019 cut-off is not modelled.
- Eval sets are small and labelled by the same author as the rules, so the 100% figures are weak evidence.

**Next:** Phase 2 agent, or Maharashtra schemes and bigger test sets.

---

## v0.1: 2026-10-21: Planning

**Who:** Claude (claude-opus-5-5) with Kshitij
**Goal:** Understand the blueprint and agree on a build approach.

**Decisions**
- Build the "brain" first (rules engine, matcher, decoder) and the "pipes" (WhatsApp, voice, deploy) later.
- Core principle from the blueprint: the LLM only talks; code decides eligibility.
- Scope priority: steps 1 to 10 plus voice is the real product; dashboard, TTS and self-hosted STT are extras.

**Next:** Build Phase 1 core logic (done in v0.2).
