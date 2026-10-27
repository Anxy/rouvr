-- Rouvr ledger (WorkHub-lite) schema. Postgres, also runs on SQLite for tests.
-- Append-only by convention: evidence and receipts are never updated.
CREATE TABLE IF NOT EXISTS tasks (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  intent      TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'open',  -- open|claimed|done|blocked|failed
  owner       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contracts (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  body        TEXT NOT NULL,       -- canonical JSON of the done-contract
  sha256      TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE (task_id, sha256)
);
CREATE TABLE IF NOT EXISTS evidence (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  kind        TEXT NOT NULL,       -- e.g. test_output, artifact, check
  body        TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
  id              INTEGER PRIMARY KEY,
  task_id         INTEGER NOT NULL REFERENCES tasks(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  action          TEXT NOT NULL,   -- the mutation this receipt proves
  body            TEXT NOT NULL,
  created_at      TEXT NOT NULL
);
