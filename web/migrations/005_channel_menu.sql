CREATE TABLE IF NOT EXISTS channel_menus (
 chat_hash TEXT PRIMARY KEY REFERENCES waha_chats(chat_hash) ON DELETE CASCADE,
 preferred_language TEXT,
 menu TEXT,
 menu_at DOUBLE PRECISION,
 hinted_language TEXT,
 pending_voice TEXT,
 pending_voice_at DOUBLE PRECISION
);
