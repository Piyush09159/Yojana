# Agent orchestration

How Yojana-Pilot's AI agents work, how they work together, and why the flow rarely breaks or
costs much. Read this before changing anything in `agents/`, `saathi/orchestrator.py` or `worker/`.

## The one idea

**Code runs the flow. Agents do only what code cannot.** An "agent" here is one model call with
one narrow job, a strict JSON contract and a code check on its output. Agents never call each
other and never decide eligibility (house rule 3). The orchestrator and the worker are plain
Python: they decide what happens next, call an agent when they need one, and pass work along
through the database.

Why not let an AI coordinate the agents? A code coordinator gives the same answer for the same
input, can be tested offline, costs nothing, and cannot invent a step.

## The five agents

| Agent | One job | When it runs | Lane | Code check after it | If every model fails |
|---|---|---|---|---|---|
| **Reader** `agents/reader.py` | citizen message -> facts + intent | only when code cannot read the reply | real-time | `core/fields.validate_profile` keeps or drops each fact | ask the question again; never guess facts |
| **Replier** `agents/replier.py` | free question -> short answer from the engine's JSON | intent is a question about schemes | real-time | plain text; every link must appear in the data | point to the scheme's official page |
| **Scout** `agents/scout.py` | profile group -> needs + scheme names to look up | a gap, at most once a week per group | gap | names deduped, limits | needs read from the profile by code |
| **Extractor** `agents/extractor.py` | official page -> rules, each with its source sentence | each new or changed page | gap, batch | quote must be on the page; fields from `fields.yaml`; village or institution programmes rejected | page stays queued, read again later |
| **Verifier** `agents/verifier.py` | flagged rule + its sentence -> supported? | only rules the code check flags | gap, batch | a rule the model skipped counts as unsupported | flagged rules move to "check yourself" |

The **field-quote check** (`ingest/extract.rule_needs_review`) is the code step between the
Extractor and the Verifier. A rule is flagged when its sentence mentions none of its field's words.
For example, "Residing within the Kerala State…" for `residence = rural` mentions nothing about
villages or towns. Unflagged rules never cost a model call.

## The runner: why one bad call cannot break a conversation

Every agent runs through `agents/runner.run_agent(spec, input)`, which **never raises**. It returns
an `AgentResult(ok, data, degraded, source, error, tokens, ms)`. In order:

1. **Cache.** An identical input seen before returns its stored output for 0 tokens. The key is a
   hash of the input, never the text.
2. **Budget.** If the lane's daily cloud-token cap is spent, skip the model and use the fallback.
3. **Model.** The role's **shared** client (`llm/config.shared_llm`), with:
   - a timeout per role (`ROLE_TIMEOUTS`)
   - a fallback chain that remembers which provider is capped (429), busy (503) or slow (timeout or
     504), and skips it:
     - capped: 10 minutes
     - busy: 30 s
     - slow: 5 minutes
   - fast roles (Reader, Replier) never wait on a busy provider, and never let an SDK sleep on a
     429; they move to the next provider at once
4. **Validate.** The agent's code check. Invalid output counts as a failure.
5. **Fallback.** The agent's deterministic `degrade()`, marked `degraded=True`.
6. **Record.** Tokens spent per lane and provider (`store/runtime.budget_add`), and the cache entry.

## The orchestrator: one turn of a conversation

`saathi/orchestrator.py` is used by WhatsApp (`web/waha_bridge.py`), `/app`
(`web/conversation_api.py`) and the terminal (`python -m saathi`).

```
message ─► guard: Aadhaar / bank / phone numbers refused before any model    (saathi/guard.py)
        ─► "restart" | "1".."5" (scheme details) | "ok"                      code, 0 tokens
        ─► short answer to the pending question ("ho", "45", "2", "महिला")    code, 0 tokens
           └─ otherwise Reader agent                                        ~2.5k tokens
        ─► catalog for the person's state + district (cached)               store
        ─► engine: eligibility + ranking                                    core/, no LLM
        ─► next question (most valuable, at most 6, none absurd for the person)
           or the top 5, good matches first                                 saathi/sufficiency.py
        ─► fewer than 5 good matches → one gapfill job per profile group per day
        ─► reply built by code in en/hi/mr                                  saathi/reply.py
```

A **good match** has status eligible or eligible_check_manually, record quality ≥ 30
(`core/quality.py`), and match strength ≥ 2. That last condition keeps out schemes matched on one
generic condition, such as "SC" alone.

## How agents work together: jobs in the database

Agents never pass data to each other in memory. Each hand-off is a row in the `job` table
(`store/runtime.py`), which works the same on Postgres and SQLite:

```
gapfill(segment)      Scout ─► searches (official sites only, daily cap) ─► extract job per new page
extract(url)          Extractor ─► quote check ─► person check ─► field-quote check ─► Verifier ─► save
notify(segment)       engine re-ranks for every conversation in the group ─► follow-up message
```

- **Idempotent.** Keys are `gapfill:<segment>`, `extract:<url>` and `notify:<segment>`. The same
  work is never queued twice.
- **Crash-safe.** A job is leased. If its worker dies, the lease runs out and any worker takes it
  again. Failures retry after 1, 2 and 4 minutes, then the job stays `failed` with its error.
- **Budget-aware.** Over budget, the work waits; nothing is lost.

## The three lanes

| Lane | Trigger | Agents | Preferred models | Limits |
|---|---|---|---|---|
| Real-time | a person's message | Reader (sometimes), Replier (rarely) | fastest (Groq) | 15 s / 25 s timeouts |
| Gap | fewer than 5 good matches | Scout ─► Extractor ─► Verifier | free and accurate | ≤2 searches, ≤4 pages per gap; once a day per group |
| Batch | `python -m worker --batch N` | Extractor, Verifier | local GPU first if configured, else cloud | daily token cap |

## How many agents, and what was retired

Before this work, the project had 8 model jobs on 4 roles, wired three different ways. Now there
are 5:

- **Scout** replaces the name brainstorm and the investigator's "analyse" step.
- The investigator's "reflect" step and the model-written questions are gone from the user path:
  the engine picks questions, and `questions.yaml` has their text.
- The model-written 5-scheme summary is now a code template.
- The configured but unused "judge" became the **Verifier**.

## Cost per path (free tiers: Rs 0; tokens show quota use)

| Path | Model calls | Tokens |
|---|---|---|
| Short answer, "ok", "1".."5", restart | 0 | 0 |
| Free-text message | 1 Reader | ~2.5k |
| Same message seen before | 0 (cache) | 0 |
| Free question | 1 Replier | ~1–3k |
| Gap-fill, at most once a day per profile group | 1 Scout (weekly cache) + ≤4 Extractor + flagged-only Verifier | ~25–30k; ~0 with a local GPU |

## Measured (2026-11-11, free tiers)

| Check | Result |
|---|---|
| Live conversation, Karnataka construction worker | 7 turns: 1 Reader call (2,588 tokens, 3.3 s on Groq; Gemini took 78.5 s before); 6 answers read by code; 1 gap-fill (Scout + 2 searches, 9 s) |
| Offline conversation, Beed SC widow (real catalog) | 1 Reader call for the whole conversation; top result Indira Gandhi National Widow Pension; the atrocity-victim scheme (matched on "SC" alone) no longer shown |
| Reader eval, 12 messages en/hi/mr, paced | 52/53 facts (98%), language 12/12, every call answered (bar: 90%) |
| Reader eval, unpaced burst | Groq 429 after 4 calls; the old chain then lost every case (an SDK retry slept 23 s, Gemini returned 503/504, local timed out at 15 s). Fixed: no SDK retry for fast roles, slow-provider handling, a second Groq model, extra load time for local models |
| Verifier eval, 8 flagged rules | caught 5/5 wrong rules, wrongly dropped 0/3 correct ones (bar: 90% / 10%) |
| Batch lane, 3 ZP pages | village-fund page rejected as "not for a person"; listing page rejected; nothing junk saved |

## Settings

Set in `.env`; every one is listed with an empty value in `.env.example`.

- **Agent roles:** `READER_*`, `SCOUT_*` and `VERIFIER_*`. Each falls back to `EXTRACTOR_*`,
  `PLANNER_*` and `JUDGE_*` respectively when unset. The Replier uses `AGENT_*`.
- **Timeouts:** `<ROLE>_TIMEOUT`.
- **Budgets:** `BUDGET_TOKENS_GAP`, `BUDGET_TOKENS_BATCH`, `BUDGET_SEARCHES_DAY` (default 8 a day).
- **Switches:** `VERIFY_RULES=0` skips the Verifier. `NOTIFY_DELAY` sets the follow-up debounce.

## Testing

- `python -m pytest -q`: offline. Tests default every role to the mock provider, so nothing can
  reach a real model.
- `python -m agents.eval --live --pace 15`: the Reader and Verifier against hand-labelled sets in
  `tests/fixtures/agent_eval/`.
- `python scripts/bench_extract.py <provider:model>`: the Extractor on real pages, rule by rule.

## Known limits

- **Live web search finds few official pages.** Google through SerpAPI ignores `site:` and
  `as_sitesearch` filters (measured: 0–2 official results in 10), so gap-fill asks for 30 results per
  search and keeps only official ones. Known official sites per state (source maps) remain the
  better source.
- **Groq's free tier caps tokens per minute.** About 4 Reader calls in 10 s hit a 429. The fallback
  chain moves to a second Groq model, then Gemini, then local.
- **`/legacy` is not on the orchestrator yet.** It still runs `web/session.py` + `saathi/investigator.py`.
- **The ranking's "why" line is English only.**
