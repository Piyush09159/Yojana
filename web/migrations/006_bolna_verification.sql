CREATE TABLE IF NOT EXISTS verification_calls (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES case_sessions(id) ON DELETE CASCADE,
 scheme_id TEXT NOT NULL,
 phone_cipher TEXT NOT NULL,
 status TEXT NOT NULL,
 execution_id TEXT,
 result TEXT,
 created_at DOUBLE PRECISION NOT NULL,
 updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS verification_calls_exec ON verification_calls(execution_id)