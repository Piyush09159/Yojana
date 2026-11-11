# Yojana-Pilot

**A public civic-tech website and citizen workspace for scheme discovery, eligibility guidance and application-blocker investigation.**

A person tells us a few things about themselves, in Hindi, Marathi or English. We search official
government websites, work out which schemes they qualify for, and rank them by what each one is
actually worth to *them*.

The hard part is not finding PM-KISAN. It is finding the Zilla Parishad scheme in their own district
that pays for a daughter's education and that almost nobody applies for.

---

## The one rule that shapes the whole codebase

> **The LLM reads and explains. Code decides.**

A language model never outputs "eligible" or "not eligible". It does three jobs: understand what a
person wrote, pull rules out of a web page, and write the reply. Every eligibility decision, every
ranking, every question is deterministic Python in `core/`, which means it is testable and can be
explained line by line.

Two supporting rules follow from it:

- **Every scraped rule carries the exact sentence from the page that states it.** If the quote is not
  found in the fetched text, the rule is thrown away. This is what stops a model inventing criteria.
- **Nothing scraped is trusted.** Every scheme we did not write by hand is marked `verified: false`
  and shown with a "not yet checked by a person" badge until a human confirms it.

---

## Where the project stands

| | |
|---|---|
| Schemes in the database | **267** (3 hand-written, 264 scraped) |
| Eligibility rules | 496 |
| Official sites mapped | 45 Maharashtra departments and Zilla Parishads |
| Scheme pages found | 920 (526 still waiting to be read) |
| Tests | **237 passing, all offline** (no network, no API keys needed) |

**Verified status.** Public landing page, citizen dashboard, Rescue Mode, conversation API and WAHA adapter are integrated. The offline suite and desktop/mobile browser checks pass. Model extraction requires your configured provider; hosted-model and external WhatsApp delivery are not part of the offline validation. Scraped rules remain unverified until reviewed. This is independent guidance, not a government service.

| Route | Purpose |
| --- | --- |
| `/` | Public landing page, English / Hindi / Marathi |
| `/app` | Citizen dashboard and conversation workspace |
| `/chat` | Redirect to `/app` |
| `/legacy` | Original form, investigation and streaming results |
| `/docs` | FastAPI API documentation |

See [website architecture](docs/WEBSITE_ARCHITECTURE.md), [dashboard details](docs/DASHBOARD_V22.md) and [security and data handling](SECURITY.md).

---

## Quick start

Install a standard Python 3.13 from [python.org](https://www.python.org/downloads/), then clone the published `main` branch. No private agent runtime is required.

### Windows PowerShell

```powershell
git clone --branch main https://github.com/ShrE333/Yojana-Pilot.git
Set-Location Yojana-Pilot
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m web --no-browser
```

If `py` is not available, use `python -m venv .venv` with your installed Python. Open http://127.0.0.1:8000/ for the public site and http://127.0.0.1:8000/app for the workspace. No model key is required to view the website, run tests, or use deterministic Rescue checks. Configure your model only when you want live conversation.

### macOS / Linux

```bash
git clone --branch main https://github.com/ShrE333/Yojana-Pilot.git
cd Yojana-Pilot
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
cp .env.example .env
.venv/bin/python -m pytest -q
.venv/bin/python -m web --no-browser
```

Never replace an existing `.env` containing your configuration. Virtual environments are disposable and machine-specific; recreate them if their base Python no longer exists.

The Windows launchers use `.venv\Scripts\python.exe` explicitly:

```powershell
.\start.ps1            # public website; dashboard at /app
.\start.ps1 -Cli       # terminal chat
.\start.ps1 -Test      # offline suite
.\start.ps1 -Stats     # catalog summary
```

### Optional browser and security checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts/check_dashboard_browser.py
```

Run browser checks in a second terminal while the backend is running. Set `DASHBOARD_URL` for a different port or `BROWSER_EXECUTABLE` to select an existing browser. Screenshots are written to ignored `artifacts/browser/`.

### Keys you need

You need **one** configured language service for live AI conversation; the public website and offline engine run without keys. The database already ships with 267 schemes, so you do not
need to scrape anything to try it.

| Key | Used for | Notes |
|---|---|---|
| `GEMINI_API_KEY` | reading scheme pages, planning | [Provider key dashboard](https://aistudio.google.com/apikey); check current quotas |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | chat replies | Compatible with configured providers, for example: `https://api.groq.com/openai/v1` |
| `SERPAPI_API_KEY` | live web search | Only needed for finding *new* schemes. Check current provider quotas |
| `ANTHROPIC_API_KEY` | optional alternative | |

Models are chosen per **role**, so you can mix providers:

```env
PLANNER_PROVIDER=gemini          # reasoning: what does this person need, where do we look
EXTRACTOR_PROVIDER=gemini        # reading scheme pages into rules
AGENT_PROVIDER=openai            # writing replies
EXTRACTOR_FALLBACKS=gemini:gemini-3.1-flash-lite,openai:openai/gpt-oss-120b
```

Fallbacks matter in practice: free tiers run out. The layer tells apart a **busy** provider
(HTTP 503 — wait and retry the same one) from a **capped** one (HTTP 429 quota — switch and bench it
for ten minutes).

---

## How it works

### Agent orchestration (one flow for WhatsApp, /app and the terminal)

Code runs the conversation; five narrow agents do only what code cannot. Agents never call each
other and never decide eligibility. Full explanation: [docs/AGENT_ORCHESTRATION.md](docs/AGENT_ORCHESTRATION.md).

| Agent | One job | Lane |
|---|---|---|
| Reader (`agents/reader.py`) | citizen message -> facts + intent (only when code cannot read the reply) | real-time |
| Replier (`agents/replier.py`) | free question -> answer from the engine's JSON, links checked | real-time |
| Scout (`agents/scout.py`) | profile group -> needs + scheme names to look up (once a week per group) | gap |
| Extractor (`agents/extractor.py`) | official page -> rules with source quotes; village/institution pages rejected | gap, batch |
| Verifier (`agents/verifier.py`) | second opinion on rules whose quote does not mention their field | gap, batch |

```bash
python -m saathi --worker --debug      # chat in the terminal; background jobs run after each turn
python -m worker                       # background lanes: gap-fill, page reading, follow-ups
python -m worker --batch 20            # batch lane: read 20 pages from the crawl queue
python -m worker --status              # jobs and today's spend per lane
python -m agents.eval --live --pace 15 # each agent against its hand-labelled set (uses tokens)
```


### Main citizen workspace (`/app`)

A free-text message goes to `/api/v2/conversation`, profile facts are validated, deterministic rules generate a shortlist, and relevant missing eligibility facts drive follow-up questions. Scheme cards show sources and verification warnings. Rescue Mode calls the separate deterministic preflight and status-interpretation APIs.

### Legacy investigation flow (`/legacy`)

```
Browser: form (state, district, age, gender, income, caste, work ...)
   |
   v
Code validates every value against schemes/fields.yaml
   |  anything unknown is dropped, never guessed
   v
Instant results from the database               <- under a second
   |
   v
INVESTIGATOR LOOP            (<= 3 rounds, <= 12 searches, <= 25 pages, <= 3 minutes)
   |
   |- ANALYSE    planner LLM: "landless SC widow in Beed, two daughters"
   |               -> needs: income support, girl-child aid, housing, health
   |               (it decides what to look for, never who is eligible)
   |
   |- PLAN       code: match those needs to real agencies
   |               -> Zilla Parishad Beed, Social Justice Dept, Mahatma Phule Corporation
   |
   |- EXECUTE    crawl + search -> fetch (robots.txt, cache) -> pre-filter in code
   |               -> extractor LLM -> QUOTE CHECK -> save to the database
   |
   |- EVALUATE   code: run every scheme against her profile
   |
   `- REFLECT    planner LLM: "housing still uncovered, chase these leads" -> loop or stop
   |
   v
REASONED QUESTIONS
   Code scores every unknown fact by how much it would unlock:
       money x usefulness x how little-known the schemes are
   The planner picks one of the top candidates and writes it with its reason.
   It cannot ask about anything outside that scored list.
   |
   v
RESULTS   Start with this | Lesser-known you may be missing | Almost | Check the official page
          Every card: what it is worth per year, why it ranks there, source link, verified badge
```

Everything streams to the page live, so you watch it reason and search.

### Building the scheme catalog (offline)

Scraping is split into two phases, which is what makes it practical:

```bash
python -m ingest --probe-sources mh             # 1. which official domains work?  (no LLM, free)
python -m ingest --map-urls mh                  # 2. find every scheme page        (no LLM, free)
python -m ingest --extract-queued --limit 200   # 3. read them into rules          (LLM, resumable)
```

Phase 2 found **920 scheme pages across 45 sites without spending a single token**. Only phase 3
costs money, it works through a queue, and you can stop and resume it at any time.

Why this matters: a single district site is rich. ZP Palghar exposes 82 scheme pages, Beed 73,
Satara 68 — these are exactly the schemes that search engines bury.

**Scraping rules we follow:**

- Only `*.gov.in` and `*.nic.in` pages are used as rule sources
- `robots.txt` is honoured; if it cannot be read, the site is skipped
- At most one request every 2 seconds per domain; pages cached for 30 days
- **myscheme.gov.in is never scraped** — its Terms of Use forbid bots without written permission.
  It is India's biggest scheme directory, so getting that permission is a worthwhile thing for the
  team to pursue. See `ingest/sources.yaml`.

---

## The ranking system

This is the part worth understanding, because the obvious approach gives bad answers.

A scheme is ranked on four things, all computed in code:

```
score  =  match  +  2 x value  +  confidence  -  effort
```

**match** — how squarely the scheme is aimed at *this* person. Conditions about who they are (caste,
disability, widowhood, occupation, age, BPL) count double. Two things deliberately do **not** count:

- *Exclusions.* Passing "you are not an income-tax payer" says nothing about being targeted.
  PM-KISAN has seven such rules; counting them inflated its match score to 9.
- *Scope rules.* `state = maharashtra` says **where** a scheme applies, not **who** qualifies. A
  girl-child scheme whose only readable rules were "Maharashtra, Sangli" was being recommended to a
  28-year-old man until we fixed this.

**value** — what the person would actually receive, per year. Scheme pages state money in
incompatible ways, so `core/benefit.py` normalises it:

| The page says | Counted as |
|---|---|
| Rs 60 per month for 10 months | Rs 600 / year |
| One-time Rs 20,000 | Rs 6,666 / year (spread over 3 years) |
| Rs 1,30,000 for house construction | Rs 43,333 / year — not annual income |
| Rs 1,01,000 until the girl turns 18 | Rs 5,611 / year |
| Rs 5,00,000 hospital cover | Rs 20,000 / year — a ceiling is not income |
| Rs 20,00,000 per village | **Rs 0** — no person receives this |

That last row matters: village budgets and awards to institutions were outranking real personal
benefits before this existed.

**confidence** — verified by a human beats fully-read rules, which beats a scheme with conditions we
could not parse. **effort** — documents required and how hard it is to apply.

Every result explains itself. For a 45-year-old SC widow in Beed:

> **Indira Gandhi National Widow Pension** — worth about Rs 18,000 a year
> - you meet 4 of its conditions including who it is meant for
> - found on an official site, not yet checked by a person

It beats Swadhar Greh, which pays **more** (Rs 40,000/yr) but matches her far less specifically.

---

## Project layout

```
core/          The brain. No LLM anywhere in here.
  fields.py        validates every value against the 41 allowed profile fields
  eligibility.py   profile + rules -> eligible / near miss / needs info / check manually
  ranking.py       the scoring above; picks ONE scheme to start with
  benefit.py       turns any benefit into comparable rupees per year
  awareness.py     is this scheme widely known? (drives the "lesser-known" list)
  questions.py     scores which unknown fact is worth asking about
  name_match.py    Aadhaar vs land record name comparison, Devanagari aware
  preflight.py     cross-document checks -> ordered list of fixes
  status_decoder.py  "eKYC pending" -> ranked likely causes and next steps

store/         SQLite database (data/schemes.db) - schemes and crawl history
ingest/        source_map.py (45 mapped sites), fetch.py (robots, cache, HTML/PDF),
               crawl.py (listing pages -> scheme pages), extract.py (page -> rules + quote check)
discovery/     search.py (SerpAPI/Tavily), catalog.py, discover.py
saathi/        the conversation: understand.py, investigator.py, questioner.py,
               shortlist.py, explain.py, chat.py
web/           FastAPI APIs, public landing page, citizen dashboard and preserved legacy form
llm/           swappable providers: anthropic / openai-compatible / gemini / local Ollama / mock
schemes/       vocabulary and config only, NOT scheme data:
               fields.yaml (the 41 allowed fields), questions.yaml (en/hi/mr), flagship.yaml
tests/         237 tests, all offline
```

### Where the schemes live

Schemes used to be YAML files. They are now rows in **`data/schemes.db`** (SQLite, committed to the
repo so you get the catalog without scraping). Filtering by state and district happens in SQL, so a
session reads about 50 rows instead of loading thousands of files.

```bash
python -m store stats            # what the database holds
python -m store export           # dump everything to readable YAML for review
python -m store verify <id>      # mark a scheme as checked by a person
```

The scheme shape is unchanged — the same dictionary the old YAML held, stored as JSON in a column
with the ranking and filtering fields indexed beside it.

### PostgreSQL (for deployment)

SQLite stays the default. Set `DATABASE_URL` in `.env` to use PostgreSQL instead (any host; tested
on Neon). Every module keeps calling `from store import db`; `store/db.py` picks the backend.

```bash
python -m store pg-init                 # create the schema (store/sql/*.sql) and seed states/districts
python -m store pg-import               # copy every scheme and crawl record from data/schemes.db
python scripts/check_pg_parity.py       # prove Postgres gives the same answers as SQLite
python -m store quality                 # quality scores, levels, "who is it for" targets, topics
```

Postgres keeps each scheme's dict as JSONB (what the engine reads) plus tables derived from it in
the same transaction: one row per rule with its source sentence, the "who is it for" index
(`scheme_target`: gender, occupation, social category, age band, income limit), names, documents,
sources and a 0-100 quality score. The targets are derived in code from the rules, never by an LLM.

The opt-in Postgres tests need a throwaway database whose name ends in `_test`:

```bash
TEST_DATABASE_URL=postgresql://user:password@host/yojana_test python -m pytest -q tests/test_pg_store.py
```

Live end-to-end check of one message (LLM read, database, engine, top 5, gap handling, cost):

```bash
python scripts/try_flow.py "I am a 60 year old farmer in Pune, Maharashtra"
python scripts/try_flow.py --web 2 "<message>"      # live web search when fewer than 5 good matches
python scripts/try_flow.py --update 3 "<message>"   # also read 3 queued official pages into the database
python scripts/query_cost.py --since <UTC time>     # tokens per model for the calls since then
python scripts/bench_extract.py local:<model>       # extraction accuracy of a model on real pages
```

---

## Testing

```bash
python -m pytest -q      # 237 tests, no network, no keys required
python eval.py           # accuracy numbers
python demo.py           # rules-only walkthrough, no LLM
```

Tests never touch the network. They use a mock LLM provider, saved HTML fixtures and a temporary
database seeded from `tests/fixtures/schemes/`. Any live call belongs in `scripts/`.

Current eval: eligibility 36/36 on 12 personas, start-with 12/12 on the labelled set, name-mismatch
precision 1.00 and recall 1.00 on 20 pairs. These are small, self-labelled sets — treat them as
regression protection, not proof of accuracy.

---

## What is left to build

Roughly in the order I would do it.

1. Validate live conversation prompts with a configured model and test deployment before public traffic.
2. Add authentication, request limits, durable sessions and a background queue for production traffic.
3. Review scraped catalog rules against current official sources before presenting them as verified.
4. Validate WAHA delivery on an explicitly configured, opted-in test account.
5. Implement and validate LiveKit voice assistance; current calling controls are placeholders.
6. Expand independently labelled evaluation data; the small existing fixtures do not establish real-world accuracy.

**Smaller gaps:** option values in the form still show in English in Hindi/Marathi mode; the catalog
is Maharashtra-only (other states fall back to live search); about 1 in 5 pages yields no
machine-readable rule; five districts have no ZP site found (Pune, Ahmednagar, Akola, Aurangabad,
Sindhudurg).

---

## Working on this code

Read **`CLAUDE.md`** before changing anything — it holds the design rules and the field vocabulary
contract. **`PROGRESS.md`** is the change log, newest first; every session that changes code adds an
entry, including what broke and why.

House rules:

1. Log every change in `PROGRESS.md`
2. Run `python -m pytest -q` before you finish and put the count in the log
3. The LLM talks; code decides
4. Only `llm/providers/*` may import a vendor SDK — a test enforces this
5. Tests never touch the network
6. No secrets in the repo; keys come from `.env`
7. Never ask for or store Aadhaar numbers
8. Every scheme answer carries its source URL and date

### Adding a scheme by hand

1. Write the scheme dict and save it with `store.db.save_scheme()`, or add a YAML file and run
   `python -m store migrate`
2. Check every rule against the official guideline; set `source_url` and `last_verified`
3. Only use fields from `schemes/fields.yaml` — anything else goes in `unmapped_criteria`, which the
   engine treats as "the person must check this" and never as a pass
4. Add 2-3 personas to `tests/personas.yaml` with expected results, then run pytest

---

## Background

The design comes from `docs/blueprint.pdf`. The problem it identifies is not only that people do not
know about schemes — it is that they fail on paperwork consistency and then get stuck silently after
applying. Hence the three layers: find the scheme, check the documents *before* applying, and decode
why a payment is stuck.

One finding shapes the whole product: a CEGA pilot found that telling someone about **one** scheme led
to roughly one extra application, while showing many schemes caused overload and *fewer*
applications. That is why the UI recommends one scheme to start with, and queues the rest.
