-- Rouvr ledger (WorkHub-lite) schema. SQLite only (Postgres is not
-- supported: INTEGER PRIMARY KEY identity and cursor.lastrowid are
-- SQLite-specific, and make_app fails fast on a postgres URL).
-- No semicolons in comments: make_app splits this file on semicolons first.
-- Append-only by convention: evidence and receipts are never updated.
CREATE TABLE IF NOT EXISTS tasks (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  intent      TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'open',  -- open|claimed|done|blocked|failed
  owner       TEXT,
  tenant      TEXT NOT NULL,  -- S022: partition scope, mandatory, no default
  project     TEXT NOT NULL,  -- S022: partition scope, mandatory, no default
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  FOREIGN KEY (tenant, project) REFERENCES projects(tenant, project)
);
-- S022: tenants own projects and projects own tasks. A tenant has many
-- projects and a project has many tasks. Both created explicitly, never defaulted.
CREATE TABLE IF NOT EXISTS tenants (
  tenant     TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  tenant     TEXT NOT NULL REFERENCES tenants(tenant),
  project    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (tenant, project)
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
  idempotency_key TEXT NOT NULL,
  action          TEXT NOT NULL,   -- the mutation this receipt proves
  body            TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  UNIQUE (task_id, idempotency_key)  -- keys replay per task, not globally
);
-- T5: standing grants. Owner-approved bounded permission: scope + optional
-- contract_rev + expiry + approver. Schedules refuse to run without one.
CREATE TABLE IF NOT EXISTS grants (
  id           INTEGER PRIMARY KEY,
  scope        TEXT NOT NULL,
  contract_rev TEXT NOT NULL DEFAULT '',
  expires_at   TEXT NOT NULL,  -- UTC ISO, lexical compare is chronological
  approved_by  TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
-- S016: local user registry. Only the token hash is stored server-side.
-- S022: every user belongs to exactly one tenant (mandatory, no default)
-- and visibility is tenant-wide, not per-user.
CREATE TABLE IF NOT EXISTS users (
  username    TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  token_hash  TEXT NOT NULL,
  tenant      TEXT NOT NULL,
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
-- Knowledge Lite (hackathon cut): append-only versioned project memory.
-- Update inserts a new row whose supersedes_id points at the prior version.
-- Nothing is ever updated in place except the verified flag (confirm event).
CREATE TABLE IF NOT EXISTS knowledge_entries (
  id            INTEGER PRIMARY KEY,
  tenant        TEXT NOT NULL,
  project       TEXT NOT NULL,
  kind          TEXT NOT NULL,  -- user_profile|project_profile|note|decision|rule|snapshot
  title         TEXT NOT NULL,
  body          TEXT NOT NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  supersedes_id INTEGER REFERENCES knowledge_entries(id),
  verified      INTEGER NOT NULL DEFAULT 0,
  author        TEXT NOT NULL,
  visibility    TEXT NOT NULL DEFAULT 'company',  -- restricted|company|public
  allowed_users TEXT NOT NULL DEFAULT '[]',  -- JSON list, restricted members
  created_at    TEXT NOT NULL,
  FOREIGN KEY (tenant, project) REFERENCES projects(tenant, project)
);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(title, body);
CREATE TABLE IF NOT EXISTS external_identities (
  provider TEXT NOT NULL,
  subject  TEXT NOT NULL,
  username TEXT NOT NULL REFERENCES users(username),
  created_at TEXT NOT NULL,
  PRIMARY KEY (provider, subject)
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
