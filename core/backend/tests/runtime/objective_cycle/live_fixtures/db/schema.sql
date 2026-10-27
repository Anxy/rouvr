-- Controlled live-fixture schema (SQLite). Do not point this at production DBs.
CREATE TABLE IF NOT EXISTS fixture_users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT ''
);
