# CLAUDE.md: Scheme Saathi

Current product scope includes the public website, citizen dashboard, Rescue APIs and opt-in WAHA adapter. Later user instructions and README.md supersede the historical no-UI/no-WhatsApp scope below; the deterministic-engine and data-safety contracts still apply.

Read this whole file before changing anything. Then read `PROGRESS.md` to see what has been done so far.

---

## 0. Rules you must follow in every session

1. **Log every change in `PROGRESS.md`.** At the end of every session where you change code, data or docs, add a new entry at the top of the log. Use the entry format in that file. A session with no log entry is not finished.
2. **Run the tests before you finish:** `python -m pytest -q`. Put the pass/fail count in the log entry. Never leave the suite failing without saying so in the log.
3. **The LLM talks; code decides.** The LLM may understand the query, extract facts, find and read web pages, and explain results. Eligibility, ranking, name matching and status mapping are always deterministic Python in `core/`. Never let an LLM output "eligible" or "not eligible" directly.
4. **The LLM provider must stay swappable.** No module except `llm/providers/*` may import a vendor SDK (`anthropic`, `openai`, `google-genai`, ...). Everything else calls the `LLMClient` interface (section 5).
5. **Tests never touch the network.** Use the mock LLM provider and saved HTML/PDF fixtures. Live calls belong only in scripts under `scripts/` or behind `--live` flags.
6. **No secrets in the repo.** Keys come from environment variables. `.env.example` lists them with empty values.
7. **Never store or ask for Aadhaar numbers.** Names and dates of birth only.
8. **Every scheme answer carries its source URL and date.** Web-discovered rules are labelled unverified until a human checks them.

---

## 1. Reference document

`docs/blueprint.pdf` is the original project blueprint (16 pages). Use it as the reference for the problem, design and architecture. The most relevant parts for the current goal:

| Section | What it covers |
|---|---|
| 1, 2 | Problem: discovery, readiness (paperwork) and post-application silence. "Scheme overload": recommend ONE scheme first |
| 3.1 | The three layers: eligibility and sequencing, pre-flight check, status decoder |
| 3.3 | Out of scope: no real portal submission, no Aadhaar storage, causes are "likely" |
| 4 | Example conversation (the Sunil / Suneel demo story) |
| 8.1 | Agent loop rules: never decide eligibility, always call tools, reply in the user's language, cap tool iterations |
| 8.2 | Tool list (`next_best_question`, `check_eligibility`, `rank_schemes`, `preflight_check`, `decode_status`, ...) |
| 8.3 | YAML rule format and "do not trust any threshold from this document or from memory" |
| 8.4 | Name matching spec and evaluation target (100+ pairs, precision/recall) |
| 12 | Evaluation plan and metrics |
| 13 | Scheme list target (8 to 10) and build order |
| 15 | Risks, including "LLM drifts into deciding eligibility" |

**Where the current goal differs from the blueprint:**
- WhatsApp/WAHA, voice/STT, Postgres, Redis, the website and deployment (sections 5, 7, 8.5, 8.6, 9, 10) are **not** part of the current goal. Ignore them for now.
- The blueprint assumes 8 to 10 hand-written schemes. The current goal adds **web discovery of any scheme** (section 3 below).
- The blueprint names Claude as the LLM. The current goal requires the LLM, including the evaluator, to be **configurable** (section 5).
- Numbers and thresholds in the blueprint are not verified. Never copy a rule value from it.

---

## 2. What the project is

Scheme Saathi helps a person in rural India (Hindi / Marathi / English) find the welfare scheme they qualify for, check their paperwork before applying, and understand why a payment is stuck.

---

## 3. Current end goal

Build the **core engine**, with no WhatsApp and no UI:

```
User query (free text, hi/mr/en)
   │  e.g. "मेरी उम्र 62 है, 2 एकड़ ज़मीन है, BPL कार्ड है, कौन सी योजना मिलेगी?"
   ▼
[A] Understand: LLM extracts profile fields  ──►  validated by code against fields.yaml
   ▼
[B] Ask: core.next_best_question picks what is still missing (5 to 7 questions max)
   ▼
[C] Discover: two parts (see section 6 and docs/BUILD_PLAN.md)
        - schemes already in the database are always included
        - bulk ingestion (offline): crawl official scheme directories ─► fetch page/PDF
          ─► LLM extracts rules into the SAME YAML schema, with a source quote per rule
          ─► validated ─► data/schemes.db  (goal: as many schemes as possible)
        - live search (per user): only when the catalog has few matches for a profile
   ▼
[D] Decide: core.check_eligibility + core.rank_schemes run on curated + discovered
        (deterministic; discovered schemes are marked "unverified")
   ▼
[E] Prepare: core.preflight_check, build_checklist, decode_status for the chosen scheme
   ▼
[F] Explain: LLM writes a short reply in the user's language from the JSON result
   ▼
[G] Evaluate: an evaluator (configurable "judge" LLM + deterministic metrics) scores
        extraction faithfulness, eligibility accuracy and answer quality
```

Done means: `python -m saathi "<query>"` runs A to F end to end on the terminal with any configured LLM, and `python -m saathi.eval` produces the metrics in section 7. After that, a FastAPI service exposes the same conversation over HTTP (no WhatsApp or UI). The full phased plan is `docs/BUILD_PLAN.md`.

---

## 4. What already exists (Phase 1, all working)

```
scheme_saathi/
  CLAUDE.md            this file
  PROGRESS.md          change log, newest first. UPDATE IT EVERY SESSION
  README.md            quick start
  docs/blueprint.pdf   reference blueprint
  docs/BUILD_PLAN.md   agreed backend build plan (phases A to G)
  requirements.txt     pyyaml, rapidfuzz, pytest, python-dotenv, jsonschema, vendor SDKs
  llm/                 swappable LLM layer (section 5): base.py, config.py, log.py, providers/
  ingest/              sources.yaml (scrape policy), fetch.py, extract.py, __main__.py (python -m ingest)
  discovery/           search.py (SerpAPI/Tavily/Fake), catalog.py (curated + discovered), discover.py
  store/               the scheme database: db.py picks SQLite (data/schemes.db, default) or
                       PostgreSQL (SCHEME_DATABASE_URL); sqlite.py, pg.py (same functions), derive.py
                       (rule/target/name rows from a scheme dict), pg_admin.py, sql/*.sql, migrate.py
  saathi/              conversation: understand.py, chat.py, shortlist.py, explain.py (python -m saathi)
                       investigator.py (v0.8 reasoning loop), questioner.py (reason-based questions)
  web/                 FastAPI app (app.py, session.py) + static/ page (python -m web, http://localhost:8000)
  ingest/sources_mh.yaml, source_map.py, crawl.py   Maharashtra agency/ZP source map, probe, site crawler
  core/awareness.py, schemes/flagship.yaml          awareness high/medium/low for "lesser-known" results
  agents/              the five agents (reader, replier, scout, extractor, verifier) + runner.py:
                       run_agent() never raises (cache, budget, shared client, validate, degrade)
  saathi/orchestrator.py  the one conversation state machine (WhatsApp, /app, terminal);
                       short_answer.py (code reads short replies), reply.py (en/hi/mr templates),
                       sufficiency.py (good match, segment), guard.py, senders.py
  worker/              job lanes: gapfill -> extract -> notify (store/runtime.py job queue)
  docs/AGENT_ORCHESTRATION.md  how the agents work together (read before changing agents)
  scripts/llm_smoke.py live check of configured providers (uses the network)
  eval.py              prints accuracy numbers
  demo.py              interactive terminal demo (no LLM)
  data/schemes.db      ALL schemes live here (SQLite). The scheme YAML files no longer exist;
                       `tests/fixtures/schemes/` keeps 3 hand-verified ones so tests stay offline.
  schemes/             vocabulary and configuration only, not data:
    questions.yaml     question bank in en/hi/mr; one question can fill several fields
    fields.yaml        canonical profile field vocabulary (types, allowed values, descriptions)
    flagship.yaml      widely known schemes -> awareness "high" (never shown as "lesser-known")
  core/
    loader.py          load_schemes(), load_questions(); validates ops, actions, fields and value types
    fields.py          field vocabulary: load_fields, check_value, validate_profile, profile_json_schema
    eligibility.py     check_eligibility(profile) -> eligible / near_miss / needs_info / ineligible + rule trace
    ranking.py         rank_schemes(results) -> start_with + queue (score = 2*value_score - effort)
    questions.py       next_best_question(profile), apply_answer(profile, q, answer)
    name_match.py      compare_names(a, b), compare_dob(a, b); Devanagari transliteration, spelling variants
    preflight.py       preflight_check(scheme_id, records, profile) -> ordered fixes + flags
    status_decoder.py  decode_status(scheme_id, text, flags) -> top-3 likely causes (flags boost causes)
    checklist.py       build_checklist(scheme_id, have)
    pipeline.py        assist(profile, records, status, scheme_id) -> one JSON dict with everything
  tests/
    personas.yaml      12 personas with expected results
    name_pairs.yaml    20 labelled name pairs
    test_*.py          135 tests, all passing (test_llm.py: LLM layer; test_fields.py: vocabulary)
```

### Scheme YAML schema (all schemes, curated or discovered, must follow it)

```yaml
id: pm_kisan                      # unique, snake_case
name: ...                         # plus name_hi, name_mr when available
source_url: https://...           # official page or PDF
last_verified: 2026-10-21
benefit_summary: "..."
benefit_annual_inr: 6000          # 0 if not cash
rank: {value_score: 1-5, effort: 1-5}
eligibility:
  all: [ {field, op, value, reason, near_miss?} ]   # every rule must pass
  any: [ ... ]                                      # at least one must pass (optional)
dependencies: [ {id, label, fix} ]                  # fix = key in actions
documents: [ {name, issued_by, key} ]
records_to_match: [ [doc_key_a, doc_key_b] ]
actions: { key: {text, where} }
status_map: { "status text": [ {action, weight, boost_if?} ] }
```

Ops: `eq ne gt gte lt lte in not_in`. A missing profile field gives `unknown`, which gives `needs_info`. A hard failure overrides unknowns. A failure within `near_miss` tolerance gives `near_miss`.

**Fields:** `schemes/fields.yaml` is the only allowed vocabulary (41 fields: personal, location incl. all 36 states/UTs, money, work, education, family, land, housing, exclusions, scheme lists, dependency flags). `core/fields.py` has `load_fields`, `check_value`, `validate_profile` and `profile_json_schema` (give this schema to the extractor LLM). The loader rejects a rule whose field is unknown, whose value has the wrong type or enum value, an order op on a non-numeric field, a bad `near_miss`, a dependency id that is not a field, and questions that set unknown fields. Add a field only when several schemes need it; otherwise use `unmapped_criteria`.

---

## 5. LLM layer: swappable provider (built in v0.5)

Fallback (v0.7): `LLM_FALLBACK_PROVIDER/MODEL` (or per role) wraps the client in `llm/fallback.py`; on 429/quota/413/503 it switches for 10 minutes. Other errors are raised.

How it works now: providers subclass `llm.base.ProviderBase` and implement only `_call()`. `ProviderBase.complete()` adds the JSON schema to the system prompt, parses and validates with `jsonschema`, retries once with the error, wraps vendor errors in `LLMError`, and logs metadata only (no message text). Tests use `MockProvider(responses={substring: reply})` or `queue=[...]`. Anthropic provider: no `temperature` sent (current Claude models reject it), 8000 tokens of thinking headroom added to `max_tokens`, `fallbacks="default"` on models that support it, refusal raises `LLMError`.

```
llm/
  base.py          LLMClient protocol + dataclasses
  config.py        reads env, returns a client per ROLE
  providers/
    anthropic_provider.py
    openai_provider.py         # also covers OpenAI-compatible APIs (Groq, Together, Ollama, vLLM) via base_url
    gemini_provider.py
    mock_provider.py           # deterministic, for tests; returns canned JSON by prompt key
```

Interface (keep it this small):

```python
class LLMClient(Protocol):
    name: str          # "anthropic:claude-sonnet-5-5"
    def complete(self, messages: list[dict], *, system: str | None = None,
                 json_schema: dict | None = None, temperature: float = 0.0,
                 max_tokens: int = 2000) -> LLMResponse: ...

@dataclass
class LLMResponse:
    text: str
    data: dict | None      # parsed JSON when json_schema was given (validated)
    model: str
    usage: dict
```

**Roles.** Each role can use a different provider and model:

| Role | Used for | Env vars |
|---|---|---|
| `extractor` | free text -> profile fields; web page -> scheme YAML | `EXTRACTOR_PROVIDER`, `EXTRACTOR_MODEL` |
| `agent` | conversation and final explanation | `AGENT_PROVIDER`, `AGENT_MODEL` |
| `judge` | the AI evaluator | `JUDGE_PROVIDER`, `JUDGE_MODEL` |

Fallbacks: `LLM_PROVIDER` / `LLM_MODEL` apply to any role not set. Keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `GEMINI_API_KEY`. Usage: `get_llm("judge")`.

Rules:
- JSON output is always validated in code (`jsonschema` or pydantic). On invalid output, retry once with the validation error, then fail clearly.
- Log provider, model, tokens and latency per call to `logs/llm_calls.jsonl` with PII redacted.
- Print a warning when `judge` uses the same model as `extractor`, because the model would be grading its own work.

---

## 6. Web discovery (built in v0.7)

**Status (v0.7):** working end to end. `ingest/fetch.py` (robots.txt, 2 s/domain, disk cache, HTML via BeautifulSoup, PDF via pypdf), `ingest/extract.py` (LLM -> rules with quote check; dropped rules add a "check manually" note; whole `any` block dropped if one alternative is lost; default near_miss for age 2 / income 10%), `discovery/catalog.py` (dedupe by normalised name per state, curated wins), `discovery/discover.py` (LLM brainstorms scheme NAMES only -> search -> official results only -> fetch -> extract -> save; processed URLs in cache/discovery_index.json). **myscheme.gov.in is never fetched: its Terms of Use forbid bots** (see ingest/sources.yaml). india.gov.in blocks bots. Search results are cached forever in cache/search/ (SerpAPI free = 250/month).

Discovery has two parts that share fetch, extract and validate code:
- **Bulk ingestion** (`ingest/`, offline, run on a schedule): crawl official scheme directories listed in `ingest/sources.yaml` (only sources whose robots.txt and terms allow it) and write them to `data/schemes.db`. Two phases: `--map-urls` finds scheme pages for free, then
  `--extract-queued` reads them with the LLM. This is how the catalog reaches thousands of schemes.
- **Live search** (`discovery/`, per user): runs only when the catalog has few candidates for a profile.

```
ingest/
  sources.yaml     each source with robots/ToS verdict and access method
  crawl.py         per-source adapter listing scheme detail URLs; resumable
  run.py           python -m ingest --source X --limit N
discovery/
  search.py        SearchProvider protocol; providers: tavily, brave, serpapi, google_cse (env SEARCH_PROVIDER)
  fetch.py         httpx GET with cache (cache/web/), robots.txt check, rate limit, PDF via pdfplumber, HTML via trafilatura
  extract.py       LLM (role=extractor): page text -> scheme dict in the YAML schema
  catalog.py       merge curated + discovered, dedupe by name/aliases, curated wins
  discover.py      discover_schemes(profile, state) -> list[scheme dict]
```

**Search strategy:**
- Build queries from the profile, for example `"{state} government scheme for {occupation} {age group} eligibility site:gov.in"`, `"{category} scholarship {state} eligibility"` and `myscheme.gov.in {keywords}`.
- Prefer official domains: `*.gov.in`, `*.nic.in`, `myscheme.gov.in`. Rank them above others. Use a non-official page only to find the official one, never as the rule source.
- Check the terms of use and robots.txt of each site before fetching, especially myscheme.gov.in. Do not bypass blocks.

**Extraction contract.** This is the most important part:
- The extractor may only use **fields from `schemes/fields.yaml`**. If a criterion has no matching field, put it under `unmapped_criteria: [text]` instead of inventing a field. The engine treats unmapped criteria as "check manually" and never as a pass.
- Every rule must carry `source_quote`, the exact sentence from the page it came from. Code checks that the quote really appears in the fetched text. Rules whose quote is not found are dropped.
- Add these to every discovered scheme: `origin: discovered`, `verified: false`, `extracted_at`, `extractor_model`, `source_url`, `fetched_at`, `content_hash`.
- The output must pass `core/loader._validate`, plus checks that fields are known, values have the right type and quotes are present.
- Cache in `schemes/discovered/<id>.yaml`. Re-extract only when `content_hash` changes.

**Engine changes needed:**
- `check_eligibility` must report `origin` and `verified`, and a scheme with `unmapped_criteria` can be at best `eligible_check_manually`.
- `rank_schemes` must prefer verified schemes when scores are equal.
- The final reply must say "found on <site>, not yet verified" for discovered schemes.

---

## 7. Evaluator (`saathi/eval/`)

There are two parts. Both must run from one command.

**Deterministic metrics**, extending today's `eval.py`:
- Eligibility accuracy on labelled personas (target: 30 personas, blueprint section 12)
- Mean questions asked per persona (target: 5 to 7)
- Name-mismatch precision and recall (target: 100+ labelled pairs)
- Status decoder top-3 cause hit rate on a labelled set

**LLM judge** (role `judge`, model set by env, so it can be changed without code edits):
- *Extraction faithfulness*: for each discovered rule, does `source_quote` support `field/op/value`? Score 0/1 with a reason.
- *Profile extraction*: compare extracted fields to gold labels for 30+ free-text queries in hi/mr/en.
- *Discovery recall*: for each gold persona, did discovery find the schemes on its hand-made expected list?
- *Reply quality*: rubric of language match, one scheme recommended, no invented rules, source mentioned, length.
- Judge prompts live in `saathi/eval/prompts/*.md`. Output goes to `eval_results/<timestamp>_<judge_model>.json` so you can compare judges.

Allow `--judge-provider` and `--judge-model` CLI flags to override env.

---

## 8. Task order for the next sessions

Full detail in `docs/BUILD_PLAN.md`.

1. ~~`llm/` layer with mock provider and tests~~ (done v0.5).
2. ~~`schemes/fields.yaml` and loader field validation~~ (done v0.6).
3. ~~Source survey~~, 4. ~~ingestion~~, 5. ~~engine changes + conversation + CLI~~, 6. ~~live search~~ (all done v0.7).
   Open follow-ups: grow the catalog (`python -m ingest` per state), preflight/status decoder inside the chat,
   human verification workflow for discovered schemes, data.gov.in as a second source.
7. FastAPI service with SQLite sessions and the confirmation gate.
8. Run ingestion across all allowed sources.
9. Evaluator (section 7) and larger test data: 30 personas, 100+ name pairs, labelled status cases.

Do one task per session where possible, and log it.

---

## 9. Known issues carried over (see PROGRESS.md for detail)

- (v0.8) Investigator, questioner and web flow are tested offline only (mock LLM); not yet run live by design
  (save Groq tokens). First live runs may need prompt tuning.
- (v0.8) Source map: 45 usable Maharashtra sites (16 agencies, 29 ZPs). All `<district>.gov.in` collector
  portals disallow bots. ZP sites for Ahmednagar, Akola, Aurangabad, Pune, Sindhudurg were not found.

- (v0.7) Discovered schemes are LLM-extracted and unverified. The quote check stops invented rules, but mapping can still be wrong (e.g. Maharashtra "yellow ration card" vs the ration_card_type enum; age limits about a child). `ingest.extract.promote_unmapped` turns clear applicant-age wording into age rules and skips wording about children/students.
- (v0.7) Name dedupe misses spelling variants across district sites ("Lek Ladki" vs "Lake Ladki").
- (v0.7) Groq free tier: 200k tokens/day per model. Bulk ingestion uses most of it; set a fallback model.
- (v0.7) Preflight and status decoder work for curated schemes only and are not yet called from the chat.

- Curated rules were written from search snippets and secondary sites, not a line-by-line read of the official PDFs. Verify before the demo.
- IGNOAPS has no Maharashtra state top-up. Helpline numbers are marked "verify". Status strings are not yet checked against the live PM-KISAN page.
- When the combined exclusions question gets a "yes", `_needs_followup` is set but no follow-up exists yet.
- The PM-KISAN inheritance exception to the 2019 cut-off is not modelled.
- The 100% eval scores come from small self-labelled sets (12 personas, 20 name pairs).

## 10. Commands

```bash
pip install -r requirements.txt
python -m pytest -q
python eval.py
python demo.py
python scripts/llm_smoke.py            # live: checks each role's provider (needs keys)
python scripts/try_query.py "<query>"  # live: text -> LLM fields -> validation -> engine decision
python -m saathi [--no-web] [--debug]  # live: the interactive assistant
python -m ingest --state maharashtra --max-searches 10   # live: bulk-scrape official sites
python -m ingest --stats               # catalog size by origin and state
python -m web                          # web UI (default of start.ps1)
python -m ingest --probe-sources mh    # 1. check which source domains work (no LLM)
python -m ingest --map-urls mh         # 2. find every scheme page and queue it (no LLM, free)
python -m ingest --extract-queued --limit 200       # 3. read queued pages with the LLM (resumable)
python -m store migrate | export | stats | verify <id>   # database: import, dump, counts, mark checked
python -m store pg-init | pg-import | quality            # PostgreSQL (SCHEME_DATABASE_URL): schema, copy, report
python scripts/check_pg_parity.py                        # Postgres answers == SQLite answers
python scripts/try_flow.py [--web N] [--update N] "<msg>" # live: one message through every step
python -m saathi [--worker] [--debug]                   # live: chat through the orchestrator
python -m worker [--once|--batch N|--status]            # live: background agent lanes
python -m agents.eval --live [--pace S] [reader|verifier] # live: score each agent on its gold set
python scripts/bench_llm.py [provider:model ...]   # compare models (uses tokens)
.\start.ps1 [-NoWeb|-Update|-Stats|-Test|-Debug]   # Windows launcher (start.bat = double-click)
```
