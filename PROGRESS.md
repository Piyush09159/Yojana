# PROGRESS.md: Scheme Saathi change log

This file is the project's history, newest entry first. Read it top to bottom to see what is done, what changed and why.

**Rule for Claude (and humans):** every session that changes code, data or docs adds one entry at the top of the log, using the template below. Never edit or delete old entries. If something was wrong, add a new entry that corrects it.

---

## Current state (update this block every session)

| | |
|---|---|
| Version | v2.4 PostgreSQL store, live on Neon |
| Last updated | 2026-11-11 |
| Tests | 260 passed, 9 skipped (opt-in Postgres tests: 9/9 pass on Neon `yojana_test`), 0 failed |
| Phase | Public landing page, citizen dashboard, conversational API, Rescue and WAHA adapter; audited repository consolidation |
| Schemes | 270 in Neon (267 imported + 3 from the queue); 523 pages queued |
| Eval | eligibility 36/36 on 12 personas; start-with 12/12; name matcher P=1.00 R=1.00 on 20 pairs (small self-labelled sets) |
| Next task | Run `python -m store pg-init` + `pg-import` + `scripts/check_pg_parity.py` once DATABASE_URL is set; benchmark local Ollama models for extraction |

---

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

## v2.4: 2026-11-11: PostgreSQL store with derived "who is it for" tables
**Who:** Claude (claude-opus-5-5) with Kshitij
**Goal:** Phase 0 of the all-India database plan: a PostgreSQL store with the same functions as the SQLite one, structured tables derived from each scheme, a quality score, and a one-to-one import of the existing catalog. Team decisions: all of India, WhatsApp (WAHA), 5 schemes per user, free LLM services or a local GPU.

**Changed**
- `store/sqlite.py` (new): the SQLite store, moved unchanged from `store/db.py`.
- `store/db.py`: now a switch. Every function delegates to SQLite, or to PostgreSQL when `DATABASE_URL` is set; chosen per call, so callers (`from store import db`) are unchanged. Loads `.env` itself so `python -m store` sees `DATABASE_URL`.
- `store/pg.py` (new): PostgreSQL store, same functions and return values as SQLite (`load_catalog`, `save_scheme`, `find_duplicate`, `queue_urls`, `mark_url`, ...). Saving writes the scheme dict (`scheme.data`, JSONB) and all derived rows in one transaction. Unknown districts in scraped schemes are registered (`auto_added`) instead of rejected. Dates are encoded like SQLite (`default=str`).
- `store/sql/001_init.sql` (new): schema. Geography (`geo_state`, `geo_district`); `scheme` + `scheme_rule`, `scheme_target`, `scheme_name`, `scheme_unmapped`, `scheme_document`, `scheme_source`; `source_site`, `crawl_url` (with priority and insertion order); orchestration tables for Phase 3 (`coverage_event`, `segment_search`, `job`, `conversation`, `review_task`); `pg_trgm` indexes on names.
- `store/derive.py` (new, pure): scheme dict -> rule, target, name, unmapped, document and source rows. Targets come from the rules that let people in (gender=female, occupation=farmer, social_category=sc, age_band, income_limit); exclusions and state/district scope are skipped, as in `core/ranking.py`. Topic categories come from a fixed keyword list.
- `core/quality.py` (new, pure): 0-100 record quality (qualifying rules, checkable share, benefit amount, documents, how to apply, verified, freshness).
- `store/pg_admin.py` (new) + `store/migrate.py`: `python -m store pg-init` (apply SQL files once each, seed 36 states/UTs and the Maharashtra districts), `pg-import` (copy schemes and crawl records from SQLite one to one), `quality` (report).
- `scripts/check_pg_parity.py` (new): compares the full catalog on both backends (every scheme dict, scoped loads, stats, engine statuses/scores/start-with for the 12 personas and 4 sample profiles).
- `tests/test_derive_quality.py` (new, 18 tests). `tests/test_pg_store.py` (new, 9 tests, opt-in via `TEST_DATABASE_URL`, database name must end in `_test`).
- `tests/conftest.py`: sets `DATABASE_URL=""` so no test can write to a real Postgres when `.env` has one.
- `requirements.txt`, `requirements.lock.txt`: `psycopg[binary]` 3.3.8 (+ `tzdata` on Windows).

**Decisions**
- PostgreSQL over MongoDB: the data is relational (geography, rules, targets, sources, dedupe merges, coverage analytics); JSONB keeps the flexible scheme document the engine reads, so `core/` is unchanged.
- The structured tables are derived in code from the dict, never edited separately, so they cannot drift.
- The "who is it for" index is derived from quote-checked rules, not tagged by an LLM (the LLM must not decide eligibility).
- SQLite stays the default; tests keep using it. Postgres is opt-in until parity is proven on the real catalog.

**Measured on the current catalog (derive over all 267 schemes)**
- Levels: 67 central, 180 state, 20 district. Quality median 48/100; 54 schemes score under 30.
- Most common targets: social_category=sc 46, residence=rural 38, is_student 37, st 31, is_bpl 29; gender=female only 13.
- 63 schemes yield no target (no qualifying rule) and 56 no topic category.

**Tests:** 255 passed, 9 skipped (Postgres, opt-in), 0 failed (`python -m pytest -q`)
**Eval:** unchanged

**Known issues / not done**
- Not yet run against a live Postgres: waiting for `DATABASE_URL` in `.env` (the password stays with the user).
- Local extraction benchmark (`qwen2.5:7b-instruct`, `nemotron-3-nano` on an RTX 5060 8 GB) waits for `OLLAMA_CONTEXT_LENGTH=8192` and an Ollama restart. Earlier note (v0.8, other machine): qwen2.5:7b took 112-525 s per page with schema errors.
- Dedupe still compares only within the same state and district, and token_set_ratio over-matches subsets ("pension scheme" vs "widow pension scheme"); fix planned for Phase 2.
- Full LGD district list (about 780, official codes) not imported yet.

**Next:** `python -m store pg-init`, `python -m store pg-import`, `python scripts/check_pg_parity.py`; then the opt-in Postgres tests; then the local model benchmark.

**Update, same day: live on Neon, local model, end-to-end test**
- Live database: Neon PostgreSQL (Singapore). `pg-init` applied; `pg-import` copied 267 schemes + 1,008 crawl records in 6 m 55 s (about a dozen round trips per scheme). `scripts/check_pg_parity.py`: PARITY OK on every check (all scheme dicts, 7 scopes, stats, engine results for 12 personas + 4 sample profiles).
- Fixed: index `scheme_source` clashed with the table of the same name (`store/sql/001_init.sql`, renamed `scheme_by_url`).
- Fixed: `Discoverer` crashed at start-up without `SEARCH_PROVIDER`, which blocked `--extract-queued` (no search needed). `discovery/search.py` `NoSearch` stand-in; searches now fail with a clear SearchError instead.
- Local model: Ollama ran with a 4,096-token context despite `OLLAMA_CONTEXT_LENGTH`, cutting the ~5.7k-token extraction prompt. Created `qwen2.5-7b-8k` and `nemotron-3-nano-8k` (Modelfile `PARAMETER num_ctx 8192`). qwen2.5 7B on RTX 5060: 4k context 58 s/page, 4/5 valid; **8k context 13 s/page, 5/5 valid, 5/5 names right**. Nemotron benchmark stopped (not measured).
- **Rule accuracy is the weak point:** on the Kerala old-age pension page qwen kept the 2 right rules plus 3 wrong ones (`is_bpl=true` from an "other pension" exclusion, `residence=rural` from "residing within Kerala", `family_size>=1` from "must be destitute"). They pass the quote check because the sentence exists. `scripts/bench_extract.py` (new) now scores rule agreement with the reference and lists extra rules.
- `scripts/try_flow.py` (new): one message through every step (privacy guard, LLM read, validation, database scope, engine, top 5, sufficiency, gap log in `coverage_event`, optional `--update N` from the queue).
  - Karnataka construction worker (English): 8 facts right; 68 schemes in scope; 1 good match; gap logged. `--update 3` read 3 queued pages: Neon 267 -> 270, queue 526 -> 523.
  - Maharashtra SC widow (Marathi): language mr; 243 schemes in scope; 5/5 "good".
- **Problems found by the live test (fix before scaling extraction):**
  1. District "बीड" became `bhid`, not `beed`, so Beed's district schemes were not loaded. Districts need matching against the geography table and its aliases.
  2. qwen read "शेतमजूर" (farm labourer) as `occupation=farmer` and added `owns_cultivable_land=true`.
  3. Top 5 contained wrong fits: an atrocity-victim scheme (only the SC rule could be checked) and a Rs 25 lakh fodder-business project counted as Rs 8.3 lakh a year. The "good match" test (status + quality >= 30) is too loose; it also needs match strength and no unchecked condition that is clearly about someone else.
  4. The 3 pages read by `--update` were village-panchayat fund programmes, not citizen schemes (quality 5, 35, 5; one with a wrong `occupation=farmer` rule). Extraction needs a "benefits a person" check before saving.
  5. Scheme ids from Devanagari names become digits or hashes (`5__maharashtra`); slugs need transliteration.
  6. The bot would ask a 34-year-old worker whether he is a student; age should suppress that question.
  7. Each catalog load from Neon takes about 2-2.6 s; cache by `db.version()` before WhatsApp traffic.

**Tests:** 255 passed, 9 skipped (Postgres, opt-in), 0 failed.
**Next:** fixes 1-4 above (district matching, field-quote consistency check in `ingest/extract.py`, stricter "good match", personal-benefit filter), then bulk extraction of the 523 queued pages with `qwen2.5-7b-8k`.

**Update: cloud keys, live web search, cost per query**
- `.env`: the Groq key was in `GROQ_API_KEY`, which the code does not read; copied into `OPENAI_API_KEY` with `OPENAI_BASE_URL=https://api.groq.com/openai/v1`. Extractor on Gemini, fallbacks Groq then local qwen; judge on Groq (different vendor from the extractor).
- `scripts/try_flow.py --web N`: live gap-fill through `Discoverer.discover_for_profile` (SerpAPI, official sites only, saved to Neon). `scripts/query_cost.py` (new): tokens per model from `logs/llm_calls.jsonl`, cost only with prices given on the command line.
- Latency: Gemini `gemini-3.8-flash` 78.5 s to read one message (6.2 s for a tiny call); Groq `openai/gpt-oss-120b` 2.6 s. **Message reading must be on Groq**; Gemini only for background page extraction.
- Karnataka worker with `--web 2`: 2 SerpAPI searches, 2 KLWB pages read, both rejected as "not a single-scheme page", 0 new schemes. 4 Gemini calls, 12,792 in / 917 out tokens, 284 s.
- Maharashtra widow on Groq: district `beed` and occupation `agricultural_labourer` right (qwen gave `bhid` and `farmer` + land). 1 call, 2,196 in / 960 out tokens. 249 schemes in scope, 5/5 "good", but the top 5 still holds wrong fits: PM Matsya Sampada (fisheries), the atrocity-victim scheme, and a PMAY-Gramin record whose source is a ZP Amravati biogas page (name and source mismatch). Engine and data fixes, not LLM.
- Cost: free tiers, so $0 per query. One database-answered query is about 3.2k Groq tokens; a query that triggers web search is about 14k Gemini tokens and 2 SerpAPI searches (of 250 a month).

**Update: review and clean-up before push**
- `store/pg.py`: a cached connection was reused forever; Neon suspends idle databases and drops their connections, so the first call after a quiet spell would fail. Now a broken connection is replaced, every store function reconnects and retries once on `OperationalError` (all are safe to repeat), and connecting times out after 10 s.
- `tests/test_store_switch.py` (new, 5 tests): backend switch, password-free `db_path`, retry once then raise, discovery without a search key.
- Opt-in Postgres tests run for the first time: 9/9 pass against a separate Neon database `yojana_test` (the real data is in `neondb`).
- Whole suite run with all outbound sockets blocked: no network calls, even with real keys in `.env`.
- `.env.example`: `DATABASE_URL`, `TEST_DATABASE_URL`; note that Groq goes in `OPENAI_API_KEY` + `OPENAI_BASE_URL` (`GROQ_API_KEY` is not read). README: PostgreSQL section and the live-test scripts. CLAUDE.md: store layout, new commands, field count 41 (was 46).
- `core/ranking.py` docstring: formula corrected to `1*match + 2*value_points + confidence - effort` (the code was right).
- `demo.py`: `ask()` could not answer "choice" questions, and the first question (state) is one; options are now listed and accepted by name or number.

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
