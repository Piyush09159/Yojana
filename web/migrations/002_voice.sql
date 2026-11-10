CREATE TABLE IF NOT EXISTS case_grants (
 session_id TEXT NOT NULL REFERENCES case_sessions(id) ON DELETE CASCADE,
 owner_hash TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(session_id,owner_hash)
);
CREATE TABLE IF NOT EXISTS channel_routes (
 chat_hash TEXT PRIMARY KEY REFERENCES waha_chats(chat_hash) ON DELETE CASCADE,
 address_cipher TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_links (
 token_hash TEXT PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES case_sessions(id) ON DELETE CASCADE,
 chat_hash TEXT NOT NULL REFERENCES waha_chats(chat_hash) ON DELETE CASCADE,
 expires_at DOUBLE PRECISION NOT NULL, redeemed_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS voice_calls (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES case_sessions(id) ON DELETE CASCADE,
 status TEXT NOT NULL, transcript TEXT NOT NULL, summary TEXT,
 consent INTEGER NOT NULL DEFAULT 0,
 created_at DOUBLE PRECISION NOT NULL, ended_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS summary_outbox (
 call_id TEXT PRIMARY KEY REFERENCES voice_calls(id) ON DELETE CASCADE,
 chat_hash TEXT NOT NULL REFERENCES waha_chats(chat_hash) ON DELETE CASCADE,
 status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt DOUBLE PRECISION NOT NULL, created_at DOUBLE PRECISION NOT NULL,
 confirmed_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS voice_links_expiry ON voice_links(expires_at);
CREATE INDEX IF NOT EXISTS summary_outbox_due ON summary_outbox(status,next_attempt);
