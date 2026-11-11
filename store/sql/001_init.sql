-- Yojana-Pilot scheme database, PostgreSQL. Applied by `python -m store pg-init`.
-- One versioned file per change; never edit a file that has already been applied anywhere.
--
-- `scheme.data` is the engine's scheme dict (the same shape the SQLite store and the old YAML
-- files held). Every other scheme table is derived from it by store/derive.py and written in the
-- same transaction (store/pg.py save), so the structured rows can never disagree with `data`.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------- geography ----------------------------------------------------------------------
-- `key` is the fields.yaml value the engine uses (e.g. maharashtra, beed). `lgd_code` is the
-- official Local Government Directory code, filled in when the LGD list is imported.
CREATE TABLE geo_state (
    key         TEXT PRIMARY KEY,
    lgd_code    INTEGER UNIQUE,
    name_en     TEXT NOT NULL,
    name_local  TEXT,
    aliases     TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE geo_district (
    state_key   TEXT NOT NULL REFERENCES geo_state(key),
    key         TEXT NOT NULL,
    lgd_code    INTEGER UNIQUE,
    name_en     TEXT NOT NULL,
    name_local  TEXT,
    aliases     TEXT[] NOT NULL DEFAULT '{}',
    auto_added  BOOLEAN NOT NULL DEFAULT FALSE,   -- registered on the fly by a scraped scheme
    PRIMARY KEY (state_key, key)
);

-- ---------- schemes ------------------------------------------------------------------------
CREATE TABLE scheme (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    name_norm         TEXT NOT NULL,
    level             TEXT NOT NULL CHECK (level IN ('central', 'state', 'district')),
    state_key         TEXT REFERENCES geo_state(key),
    district_key      TEXT,
    agency_type       TEXT,
    agency_name       TEXT,
    ministry          TEXT,
    categories        TEXT[] NOT NULL DEFAULT '{}',
    benefit_kind      TEXT,                       -- cash | insurance | in_kind | unknown
    annual_value_inr  INTEGER NOT NULL DEFAULT 0, -- comparable rupees a year (core/benefit.py)
    benefit_personal  BOOLEAN NOT NULL DEFAULT TRUE,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed', 'unknown')),
    origin            TEXT NOT NULL CHECK (origin IN ('curated', 'discovered')),
    verified          BOOLEAN NOT NULL DEFAULT FALSE,
    awareness         TEXT,
    quality_score     INTEGER NOT NULL DEFAULT 0, -- 0-100, core/quality.py
    rule_count        INTEGER NOT NULL DEFAULT 0,
    unmapped_count    INTEGER NOT NULL DEFAULT 0,
    source_url        TEXT,
    source_site       TEXT,
    content_hash      TEXT,
    data              JSONB NOT NULL,
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked      TIMESTAMPTZ,
    next_refresh      TIMESTAMPTZ,
    FOREIGN KEY (state_key, district_key) REFERENCES geo_district(state_key, key),
    CHECK (district_key IS NULL OR state_key IS NOT NULL)
);
CREATE INDEX scheme_scope      ON scheme (state_key, district_key) WHERE status <> 'closed';
CREATE INDEX scheme_level      ON scheme (level);
CREATE INDEX scheme_quality    ON scheme (quality_score);
CREATE INDEX scheme_review     ON scheme (origin, verified);
CREATE INDEX scheme_by_url     ON scheme (source_url);
CREATE INDEX scheme_categories ON scheme USING gin (categories);
CREATE INDEX scheme_name_trgm  ON scheme USING gin (name_norm gin_trgm_ops);

-- Every eligibility rule, one row each, with the sentence from the page that states it.
CREATE TABLE scheme_rule (
    scheme_id     TEXT NOT NULL REFERENCES scheme(id) ON DELETE CASCADE,
    block         TEXT NOT NULL CHECK (block IN ('all', 'any')),
    position      INTEGER NOT NULL,
    field         TEXT NOT NULL,
    op            TEXT NOT NULL,
    value         JSONB NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    source_quote  TEXT NOT NULL DEFAULT '',
    near_miss     DOUBLE PRECISION,
    promoted      BOOLEAN NOT NULL DEFAULT FALSE,  -- turned into a rule from unmapped text by code
    PRIMARY KEY (scheme_id, block, position)
);
CREATE INDEX scheme_rule_field ON scheme_rule (field, op);

-- Who a scheme is for, derived in code from the rules that let people in.
-- e.g. (gender, female), (occupation, farmer), (social_category, sc), (age_band, 60+).
CREATE TABLE scheme_target (
    scheme_id  TEXT NOT NULL REFERENCES scheme(id) ON DELETE CASCADE,
    dimension  TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (scheme_id, dimension, value)
);
CREATE INDEX scheme_target_lookup ON scheme_target (dimension, value);

-- Names in every language and spelling, for lookup and dedupe.
CREATE TABLE scheme_name (
    scheme_id  TEXT NOT NULL REFERENCES scheme(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    lang       TEXT NOT NULL DEFAULT 'en',
    name_norm  TEXT NOT NULL,
    PRIMARY KEY (scheme_id, name)
);
CREATE INDEX scheme_name_norm_trgm ON scheme_name USING gin (name_norm gin_trgm_ops);

-- Conditions no field could express: a person must check them. Never counted as a pass.
CREATE TABLE scheme_unmapped (
    scheme_id  TEXT NOT NULL REFERENCES scheme(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    text       TEXT NOT NULL,
    PRIMARY KEY (scheme_id, position)
);

CREATE TABLE scheme_document (
    scheme_id  TEXT NOT NULL REFERENCES scheme(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    name       TEXT NOT NULL,
    key        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (scheme_id, position)
);

-- One scheme can be published on many official pages (e.g. the same state scheme on several
-- Zilla Parishad sites). Dedupe adds a source instead of creating a second scheme.
CREATE TABLE scheme_source (
    scheme_id     TEXT NOT NULL REFERENCES scheme(id) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    site          TEXT NOT NULL DEFAULT '',
    fetched_at    TIMESTAMPTZ,
    content_hash  TEXT,
    is_primary    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (scheme_id, url)
);
CREATE INDEX scheme_source_url ON scheme_source (url);

-- ---------- crawling -----------------------------------------------------------------------
CREATE TABLE source_site (
    host          TEXT PRIMARY KEY,
    state_key     TEXT REFERENCES geo_state(key),
    district_key  TEXT,
    agency_type   TEXT,
    agency_name   TEXT,
    needs         TEXT[] NOT NULL DEFAULT '{}',
    robots_ok     BOOLEAN,
    last_probe    TIMESTAMPTZ
);

CREATE TABLE crawl_url (
    url        TEXT PRIMARY KEY,
    seq        BIGSERIAL,              -- insertion order: pages are read first-found, first-read
    status     TEXT NOT NULL,          -- queued | scheme | not_scheme | fetch_error | skipped
    scheme_id  TEXT,
    note       TEXT,
    priority   INTEGER NOT NULL DEFAULT 0,   -- raised for coverage gaps (Phase 4)
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX crawl_url_queue ON crawl_url (priority DESC, seq) WHERE status = 'queued';

-- ---------- orchestration (used from Phase 3) ----------------------------------------------
-- A profile group (state, district, occupation, gender, category, age band); no personal data.
CREATE TABLE coverage_event (
    id              BIGSERIAL PRIMARY KEY,
    segment_key     TEXT NOT NULL,
    dims            JSONB NOT NULL,
    db_good_count   INTEGER NOT NULL,
    web_triggered   BOOLEAN NOT NULL DEFAULT FALSE,
    web_new_count   INTEGER NOT NULL DEFAULT 0,
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX coverage_event_segment ON coverage_event (segment_key, at DESC);

CREATE TABLE segment_search (
    segment_key    TEXT PRIMARY KEY,
    last_searched  TIMESTAMPTZ NOT NULL,
    found_count    INTEGER NOT NULL DEFAULT 0
);

-- Background work, taken with SELECT ... FOR UPDATE SKIP LOCKED.
CREATE TABLE job (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    run_after   TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at   TIMESTAMPTZ,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX job_ready ON job (run_after) WHERE status = 'queued';

-- WhatsApp conversation state. chat_ref is an encrypted chat id; no message text is stored.
CREATE TABLE conversation (
    chat_ref    TEXT PRIMARY KEY,
    profile     JSONB NOT NULL DEFAULT '{}',
    asked       JSONB NOT NULL DEFAULT '[]',
    pending     JSONB,
    lang        TEXT NOT NULL DEFAULT 'en',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE review_task (
    id          BIGSERIAL PRIMARY KEY,
    scheme_id   TEXT NOT NULL REFERENCES scheme(id) ON DELETE CASCADE,
    reason      TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dismissed')),
    reviewer    TEXT,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
