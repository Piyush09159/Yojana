CREATE TABLE IF NOT EXISTS channel_languages (chat_hash TEXT PRIMARY KEY REFERENCES waha_chats(chat_hash) ON DELETE CASCADE, language TEXT NOT NULL);
