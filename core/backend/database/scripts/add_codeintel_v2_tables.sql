-- Wave 4 Agent1: CodeIntel v2 registration tables (advisory-only system).
-- Apply manually with psql (no migration runner exists in this repo):
--   psql "$DATABASE_URL" -f add_codeintel_v2_tables.sql
-- Mirrors: backend/prisma/service.prisma (CodeIntelRepo, CodeIntelIndexJob,
-- CodeIntelSnapshot) and backend/models/codeintel_v2_models.py (CodeIntelRepo).

CREATE TABLE IF NOT EXISTS codeintel_repos (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    repo_url TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT,
    updated_by TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_codeintel_repos_tenant_url
    ON codeintel_repos (tenant_id, repo_url);
CREATE INDEX IF NOT EXISTS idx_codeintel_repos_tenant
    ON codeintel_repos (tenant_id);

CREATE TABLE IF NOT EXISTS codeintel_index_jobs (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    error_class TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_codeintel_index_jobs_repo
    ON codeintel_index_jobs (repo_id);

CREATE TABLE IF NOT EXISTS codeintel_snapshots (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_codeintel_snapshots_repo_commit
    ON codeintel_snapshots (repo_id, commit_sha);
