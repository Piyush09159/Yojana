-- Append-only migration. Separate from the citizen case/session tables.
-- Phone numbers are NOT stored here. WAHA recipient addresses remain encrypted.
CREATE TABLE IF NOT EXISTS scheme_alert_recipients (
 chat_hash TEXT PRIMARY KEY,
 address_cipher TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT '',
 district TEXT NOT NULL DEFAULT '',
 language TEXT NOT NULL DEFAULT 'en',
 subscribed_at DOUBLE PRECISION NOT NULL,
 updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS scheme_alert_outbox (
 campaign_id TEXT NOT NULL,
 chat_hash TEXT NOT NULL,
 scheme_id TEXT NOT NULL,
 text TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('pending','sent')),
 created_at DOUBLE PRECISION NOT NULL,
 updated_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(campaign_id,chat_hash),
 FOREIGN KEY(chat_hash) REFERENCES scheme_alert_recipients(chat_hash) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_alert_dispatch ON scheme_alert_outbox(campaign_id,status);
