-- Additive only. Confirmed scheme results, WhatsApp PDF guide delivery and consented follow-up calls.
-- No plaintext phone numbers or addresses: phone_cipher and address_cipher use channel_security.seal.
-- chat_hash is the keyed digest already used by waha_chats. Rows are not FK-cascaded from waha_chats
-- so STOP can mark them cancelled (audit) while scrubbing every cipher and snapshot.
CREATE TABLE IF NOT EXISTS result_reviews (
 id TEXT PRIMARY KEY,
 chat_hash TEXT NOT NULL,
 session_id TEXT,
 version INTEGER NOT NULL,
 facts_hash TEXT NOT NULL,
 fields TEXT NOT NULL,
 language TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('awaiting','changing','confirmed','superseded','cancelled')),
 requested_event TEXT,
 confirm_event TEXT,
 created_at DOUBLE PRECISION NOT NULL,
 confirmed_at DOUBLE PRECISION,
 closed_at DOUBLE PRECISION,
 close_reason TEXT,
 UNIQUE(chat_hash, version)
);
CREATE INDEX IF NOT EXISTS result_reviews_chat ON result_reviews(chat_hash, version);
CREATE TABLE IF NOT EXISTS result_deliveries (
 id TEXT PRIMARY KEY,
 review_id TEXT NOT NULL UNIQUE,
 chat_hash TEXT NOT NULL,
 version INTEGER NOT NULL,
 language TEXT NOT NULL,
 address_cipher TEXT,
 snapshot TEXT,
 scheme_count INTEGER NOT NULL DEFAULT 0,
 guide_count INTEGER NOT NULL DEFAULT 0,
 schemes_event TEXT,
 schemes_status TEXT NOT NULL,
 schemes_sent_at DOUBLE PRECISION,
 pdf_status TEXT NOT NULL,
 pdf_attempts INTEGER NOT NULL DEFAULT 0,
 pdf_next_attempt DOUBLE PRECISION,
 pdf_error TEXT,
 pdf_sent_at DOUBLE PRECISION,
 call_offer TEXT NOT NULL DEFAULT 'none',
 call_offer_attempts INTEGER NOT NULL DEFAULT 0,
 call_offered_at DOUBLE PRECISION,
 lease_owner TEXT,
 lease_until DOUBLE PRECISION,
 created_at DOUBLE PRECISION NOT NULL,
 updated_at DOUBLE PRECISION NOT NULL,
 cancelled_at DOUBLE PRECISION,
 cancel_reason TEXT,
 UNIQUE(chat_hash, version)
);
CREATE INDEX IF NOT EXISTS result_deliveries_pdf_due ON result_deliveries(pdf_status, pdf_next_attempt);
CREATE INDEX IF NOT EXISTS result_deliveries_event ON result_deliveries(schemes_event);
CREATE TABLE IF NOT EXISTS call_consents (
 id TEXT PRIMARY KEY,
 chat_hash TEXT NOT NULL,
 delivery_id TEXT NOT NULL,
 purpose TEXT NOT NULL,
 decision TEXT NOT NULL CHECK (decision IN ('granted','declined')),
 event_id TEXT NOT NULL,
 created_at DOUBLE PRECISION NOT NULL,
 revoked_at DOUBLE PRECISION,
 revoke_reason TEXT,
 UNIQUE(delivery_id, purpose)
);
CREATE TABLE IF NOT EXISTS followup_calls (
 id TEXT PRIMARY KEY,
 delivery_id TEXT NOT NULL UNIQUE,
 chat_hash TEXT NOT NULL,
 consent_id TEXT NOT NULL,
 phone_cipher TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK (status IN ('queued','dialing','started','completed','no_answer','failed','cancelled','skipped')),
 due_at DOUBLE PRECISION NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT,
 lease_until DOUBLE PRECISION,
 execution_id TEXT UNIQUE,
 provider_status TEXT,
 detail TEXT,
 created_at DOUBLE PRECISION NOT NULL,
 updated_at DOUBLE PRECISION NOT NULL,
 started_at DOUBLE PRECISION,
 finished_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS followup_calls_due ON followup_calls(status, due_at);
CREATE TABLE IF NOT EXISTS delivery_idempotency (
 chat_hash TEXT NOT NULL,
 version INTEGER NOT NULL,
 kind TEXT NOT NULL,
 created_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(chat_hash, version, kind)
)
