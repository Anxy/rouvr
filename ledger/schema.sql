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
-- S016: local user registry. Only the token hash is stored server-side.
CREATE TABLE IF NOT EXISTS users (
  username    TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  token_hash  TEXT NOT NULL,
  disabled    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);
-- R3 parity: assignment / lease / attempt / result (WorkHub-lite shape).
CREATE TABLE IF NOT EXISTS assignments (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  principal   TEXT NOT NULL,
  attempt_no  INTEGER NOT NULL DEFAULT 1,
  status      TEXT NOT NULL DEFAULT 'active',  -- active|released
  created_at  TEXT NOT NULL,
  UNIQUE (task_id, attempt_no)
);
CREATE TABLE IF NOT EXISTS leases (
  task_id     INTEGER PRIMARY KEY REFERENCES tasks(id),
  holder      TEXT NOT NULL,
  lease_epoch INTEGER NOT NULL DEFAULT 1,
  expires_at  TEXT NOT NULL,   -- UTC ISO, lexical compare is chronological
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  attempt_no  INTEGER NOT NULL,
  lease_epoch INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',
  created_at  TEXT NOT NULL,
  UNIQUE (task_id, attempt_no)
);
CREATE TABLE IF NOT EXISTS results (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  attempt_id  INTEGER NOT NULL,
  lease_epoch INTEGER NOT NULL,
  verdict     TEXT NOT NULL,
  body        TEXT NOT NULL,   -- canonical JSON of the result core
  created_at  TEXT NOT NULL,
  UNIQUE (task_id, attempt_id)
);
