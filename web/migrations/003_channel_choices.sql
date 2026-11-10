CREATE TABLE IF NOT EXISTS channel_preferences (
 chat_hash TEXT PRIMARY KEY REFERENCES waha_chats(chat_hash) ON DELETE CASCADE,
 mode TEXT NOT NULL
);
