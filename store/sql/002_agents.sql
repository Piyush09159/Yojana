-- Agent orchestration state (agents/, worker/, saathi/orchestrator.py). Applied by `python -m store pg-init`.
--
-- 001 created placeholder job, conversation and segment_search tables that nothing used (all
-- empty). They are replaced here with the designs the orchestrator needs. Times in these runtime
-- tables are Unix seconds (DOUBLE PRECISION), the same as the SQLite store, so both backends share
-- one implementation in store/runtime.py.

DROP TABLE IF EXISTS job;
DROP TABLE IF EXISTS conversation;
DROP TABLE IF EXISTS segment_search;

-- Work handed from one agent to the next. A worker takes a job with FOR UPDATE SKIP LOCKED and
-- holds it until lease_until; a job whose worker died goes back to work when the lease runs out.
CREATE TABLE job (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,                  -- gapfill | extract | notify
    dedupe_key    TEXT,                           -- same key is never queued twice at once
    payload       JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    run_after     DOUBLE PRECISION NOT NULL,
    lease_until   DOUBLE PRECISION,
    result        JSONB,
    error         TEXT,
    created_at    DOUBLE PRECISION NOT NULL,
    finished_at   DOUBLE PRECISION
);
CREATE UNIQUE INDEX job_active_key ON job (dedupe_key) WHERE status IN ('queued', 'running');
CREATE INDEX job_ready ON job (kind, run_after) WHERE status IN ('queued', 'running');

-- One conversation's state (profile, pending question, what was shown). No message text.
CREATE TABLE conversation (
    chat_ref    TEXT PRIMARY KEY,
    state       JSONB NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL,
    expires_at  DOUBLE PRECISION NOT NULL
);

-- "Did we already search the web for people like this recently?"
CREATE TABLE segment_search (
    segment_key    TEXT PRIMARY KEY,
    last_searched  DOUBLE PRECISION NOT NULL,
    found_count    INTEGER NOT NULL DEFAULT 0
);

-- An agent's output for an input it has seen before (keyed by a hash of the input, never the text).
CREATE TABLE agent_cache (
    agent       TEXT NOT NULL,
    key         TEXT NOT NULL,
    output      JSONB NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (agent, key)
);

-- Daily spend per lane (realtime | gap | batch) and provider. Local models are recorded, not capped.
CREATE TABLE agent_budget (
    day       TEXT NOT NULL,                      -- YYYY-MM-DD, UTC
    lane      TEXT NOT NULL,
    provider  TEXT NOT NULL,
    calls     INTEGER NOT NULL DEFAULT 0,
    tokens    BIGINT NOT NULL DEFAULT 0,
    searches  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, lane, provider)
);
