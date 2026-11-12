CREATE TABLE IF NOT EXISTS case_sessions (
    id TEXT PRIMARY KEY,
    owner_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS case_sessions_expiry ON case_sessions(expires_at);
CREATE TABLE IF NOT EXISTS case_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES case_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS case_messages_session ON case_messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS case_approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES case_sessions(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    purpose TEXT NOT NULL,
    facts_hash TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS waha_chats (
    chat_hash TEXT PRIMARY KEY,
    session_id TEXT REFERENCES case_sessions(id) ON DELETE SET NULL,
    opted_in INTEGER NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS waha_events (
    id TEXT PRIMARY KEY,
    chat_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT NOT NULL,
    reply TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS waha_events_expiry ON waha_events(updated_at);
