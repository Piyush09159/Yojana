# Yojana-Pilot: implementation report

**Date:** 2026-11-11 · **Branch:** `feature/database-setup` (database work committed in `ed47494`; orchestration work in the working tree, not yet committed)
**Detail:** `PROGRESS.md` entries v2.4 and v2.5 · `docs/AGENT_ORCHESTRATION.md`

---

## 1. Summary

- **Database:** PostgreSQL is live on Neon with 270 schemes. On every check, it gives exactly the same answers as the original SQLite store: all 267 imported records, 7 location scopes, statistics, and the engine's results for 16 profiles. SQLite stays the default for development and tests.
- **Core engine:** verified end to end with real models on a Karnataka and a Maharashtra profile. The live tests found 7 problems; the critical ones are fixed (district matching, wrong-fit schemes in the top 5, non-schemes being saved, wrong rules passing the quote check).
- **Agent orchestration:** 8 AI jobs wired three different ways became **5 single-purpose agents**, run by **one code orchestrator**, handing work to each other through **jobs in the database**. A typical conversation now costs **one AI call**.
- **Measured quality:**
  - Reader agent: **98%** of facts correct (52/53, en/hi/mr), language detected 12/12.
  - Verifier agent: caught **5/5** wrong rules and kept **3/3** correct ones.
  - Engine regression: unchanged (36/36 decisions, 12/12 start-with picks).
- **Cost:** ₹0 on the free tiers (Groq, Gemini, SerpAPI, Neon). The constraint is quota, not money (details in §6).
- **Tests:** **314 passing offline**, with network access blocked. The 9 Postgres tests pass on a separate Neon test database.

---

## 2. Scope delivered

| Task from the brief | Status |
|---|---|
| Large, well-organised database, by state, district, occupation, gender and more | ✅ Postgres on Neon; a "who is it for" index derived from the rules |
| Database first; web search when the database falls short; results saved back | ✅ The gap lane: Scout → search → read → verify → save → follow-up |
| Check that the core engine works properly | ✅ Tested live; problems found and fixed |
| Efficient orchestration of the system and the AI, with few failures | ✅ One orchestrator, a runner that never raises, timeouts, fallbacks |
| Know how many agents run and how many are needed | ✅ 8 before → 5 now, each with its own measurement |
| Cheap database updates | ✅ Background lanes with caching, deduplication and daily budgets |

---

## 3. Database

**Why PostgreSQL rather than MongoDB.** The data is relational: state → district, and scheme → rules, targets, sources and names. Deduplication needs transactions. JSONB keeps each scheme as a flexible document, so the engine code didn't change. The same database also covers fuzzy name search (`pg_trgm`), a future semantic search (`pgvector`), and the job queue (`SKIP LOCKED`), so no second system is needed.

**Design**
- `store/db.py` picks the backend per call: SQLite by default, Postgres when `DATABASE_URL` is set. No caller changed.
- Saving a scheme writes its record **and** all the derived tables in one transaction, so they can never disagree:
  - rules, each with its source sentence
  - who the scheme targets
  - names, documents and sources
  - a 0–100 quality score
- The "who is it for" index (gender, occupation, social category, age band, income limit) is **derived in code from the rules**, never tagged by an AI.
- Migrations: `store/sql/001_init.sql` (schemes) and `002_agents.sql` (jobs, conversations, cache, budgets).
- The store survives Neon dropping idle connections: it reconnects and retries once, with a 10-second connect timeout.

**What the data looks like today (insight)**

| Measure | Value |
|---|---|
| Schemes | 270 (67 central, 180 state, 20 district) |
| Median quality score | 48/100; 54 schemes score under 30 |
| Schemes with no qualifying rule | 63 |
| Schemes that explicitly target women | 13 |
| District coverage | 7 of 36 Maharashtra districts |
| Pages waiting to be read | 523 |

> **Data quality is a bigger problem than retrieval.** Most records are thin: few rules, many conditions written as free text, missing amounts. That's why the quality score and the Verifier were built.

---

## 4. Agent orchestration

### Before
- **Three separate conversation flows:**
  - `/app` + WhatsApp: no web fallback, and **the WhatsApp reply never contained the schemes**
  - `/legacy`: up to ~35 AI calls per person
  - the terminal flow
- **No timeouts.** One Gemini call took **78.5 s**.
- **A new AI client per message,** so the fallback chain forgot which provider was rate-limited.
- **One role for two jobs.** Fast message reading and slow page reading had to use the same model.
- **Exceptions ended turns.** No budgets.

### Now: 5 agents, 1 runner, 1 orchestrator, 3 lanes

| Agent | One job | Runs when | Code check after it | If every model fails |
|---|---|---|---|---|
| Reader | message → facts + intent | only when code can't read the reply | fields validated against the vocabulary | asks again; never guesses facts |
| Replier | free question → answer from engine data | free questions only | every link must be in the data | points to the official page |
| Scout | profile group → what to search for | a gap; once a week per profile group | limits, deduplication | needs taken from the profile by code |
| Extractor | official page → rules with source sentences | new or changed pages | quote check; not-for-a-person filter | page retried later |
| Verifier | second opinion on suspicious rules | only rules the code check flags | a skipped rule counts as unsupported | doubtful rules become "check yourself" |

- **The runner** (`agents/runner.py`) is the only place agents run, and it **never raises**. Each call goes: cache → daily budget → shared model client (timeout + fallback chain) → validation → deterministic fallback → record tokens.
- **The orchestrator** (`saathi/orchestrator.py`) is used by WhatsApp, `/app` and the terminal.
  - Short replies ("ho", "45", "2") are parsed in code.
  - Questions, the top 5, scheme details and follow-ups are code templates in English, Hindi and Marathi.
  - A conversation can't loop on one question, and questions that make no sense for the person are filtered out.
- **The worker** (`worker/`) runs the background lanes as database jobs: `gapfill → extract → notify`. Jobs are idempotent and leased (a crashed worker's job is picked up again), and they retry after 1, 2 and 4 minutes before being kept as failed.
- **Model routing:**
  - Reader, Replier and Scout: Groq first, then a second Groq model (its own quota), then Gemini, then local.
  - Extractor: Gemini.
  - Verifier: a different vendor from the Extractor.
  - Failures are sorted into three kinds: *capped* (429, rest 10 minutes), *busy* (503, rest 30 seconds), *slow* (timeout or 504, move on at once and rest 5 minutes).

---

## 5. Results

| Check | Result |
|---|---|
| Live conversation, Karnataka construction worker | 7 turns, **1 AI call** (2,588 tokens, 3.3 s on Groq); 6 answers parsed in code; gap lane ran (Scout + 2 searches, 9 s) |
| Beed SC widow, on the real catalog | top result **Indira Gandhi National Widow Pension**; the wrong atrocity-victim scheme is gone |
| Reader eval, 12 messages (en/hi/mr), paced | **52/53 facts (98%)**, language 12/12 (bar: 90%) |
| Verifier eval, 8 flagged rules | **5/5** wrong rules caught, **0/3** correct ones dropped (bars: 90% / 10%) |
| Batch lane, live | a ZP village-fund page was rejected as not for a person; before, such pages were saved as schemes |
| Postgres parity | identical on every check |
| Tests | 314 offline (no network attempts); 9/9 Postgres tests pass on Neon |

---

## 6. Insights for planning

1. **Free tiers limit by tokens per minute, not per day.** Groq returned 429 after about 4 Reader calls in 10 seconds, because each call is about 2.5k tokens. The fallback chain now spreads the load across a second Groq model and then Gemini, but **real traffic will need a paid tier or more providers.**
2. **Gemini is too slow for chat** (78.5 s, plus 504 errors under demand), but it's fine for background page reading. Groq answers in 2–4 s.
3. **Live web search finds few official pages.** Through SerpAPI, Google ignores `site:gov.in OR site:nic.in`, a single `site:gov.in`, and `as_sitesearch`. Measured: 0–2 official results in 10. Each search now asks for 30 results for the same credit. **Lists of official sites per state remain the better source**, and permission to use myScheme's data would be the biggest single gain.
4. **A local model's context length decides whether it works.** `qwen2.5:7b` on the team laptop failed at Ollama's default 4k context and passed at 8k (13 s per page, 5/5 valid). But it **invents rule mappings**, which the quote check alone can't catch. That's why the field–quote check and the Verifier exist.
5. **Code answers most turns.** Reading short replies in code and writing every message from templates makes most of a conversation free and predictable.

---

## 7. Known gaps and risks

| Item | Impact | Next action |
|---|---|---|
| `/legacy` still uses its own investigator flow | a second code path to maintain | move it onto the orchestrator |
| Ranking reasons are English only | mixed-language replies | translate the reason templates |
| Some fits are still questionable (fisheries, poultry) | their stored rules really do match | re-extract weak schemes through the Verifier |
| Karnataka and other states are thin | few good matches outside Maharashtra | add a source list per state |
| WhatsApp through WAHA is unofficial | the number can be banned | plan for the official API before scaling |
| Web finds are shown without human review | wrong advice is possible | quote check, field–quote check and Verifier for now; review queue later |
| 3 junk records from an earlier local-model run are in Neon | minor | delete when approved |

---

## 8. Decisions needed

1. **Paid model tier vs. more free providers** before public traffic (see §6.1).
2. **Retire `/legacy` or migrate it** onto the orchestrator.
3. **Priority states** for building official source lists, and whether to request myScheme data access.
4. **Approval to delete** the 3 junk records in Neon.

---

## 9. How to run and verify

```powershell
cd "D:\Yojana Pilot\Yojana-Pilot"
.\.venv\Scripts\python.exe -m pytest -q                          # offline tests
.\.venv\Scripts\python.exe -m saathi --worker --debug            # chat; background jobs after each turn
.\.venv\Scripts\python.exe -m worker --batch 5                   # read 5 queued pages into the database
.\.venv\Scripts\python.exe -m worker --status                    # jobs and today's spend per lane
.\.venv\Scripts\python.exe -m agents.eval --live --pace 15       # score the agents (uses tokens)
.\.venv\Scripts\python.exe scripts\check_pg_parity.py            # Postgres = SQLite
```

Settings live in `.env`. Every setting is listed, with an empty value, in `.env.example`.

---

## Appendix: files added or changed

| Area | Files |
|---|---|
| Agents (new) | `agents/runner.py`, `reader.py`, `replier.py`, `scout.py`, `extractor.py`, `verifier.py`, `eval.py` |
| Orchestrator (new) | `saathi/orchestrator.py`, `short_answer.py`, `reply.py`, `sufficiency.py`, `guard.py`, `senders.py` |
| Worker (new) | `worker/jobs.py`, `loop.py`, `__main__.py` |
| Database | `store/db.py` (switch), `sqlite.py`, `pg.py`, `derive.py`, `runtime.py`, `pg_admin.py`, `sql/001_init.sql`, `sql/002_agents.sql`; `core/quality.py` |
| Model layer | `llm/config.py` (agent roles, timeouts, shared clients), `llm/fallback.py` (slow-provider handling), all providers (timeouts) |
| Extraction | `ingest/extract.py` (`benefit_recipient`, field–quote check, readable ids), `discovery/discover.py`, `discovery/search.py` |
| Engine | `core/fields.py` + `schemes/geo/districts.yaml` (district matching) |
| Entry points | `web/conversation_api.py`, `web/waha_bridge.py`, `saathi/__main__.py` |
| Tests | `tests/test_orchestration.py`, `test_derive_quality.py`, `test_pg_store.py`, `test_store_switch.py`, `fixtures/agent_eval/`; `conftest.py` hardened |
| Docs | `docs/AGENT_ORCHESTRATION.md`, `README.md`, `CLAUDE.md`, `PROGRESS.md`, `.env.example` |
