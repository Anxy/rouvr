# Sprint S022 — Tenant/project partitions in WorkHub

## Sprint ID

`S022`

## Sprint Title

Mandatory tenant/project scope on every task and workflow; tenant-scoped visibility

## 1. Objective

Enforce partitions: a tenant has many projects, a project has many tasks
and workflows. Tenant and project are separate mandatory fields — no
defaults anywhere on the create path. A caller sees only the projects
and tasks of its own tenant identity (stack identity sees all).
Workflow binding verifies scope and fails closed on mismatch.

## 2. Roadmap Mapping

```text
Roadmap sections: WorkHub multi-tenancy (docs/roadmap.md Next:
  "WorkHub-lite: principals/auth, leases, multi-tenancy"); MASTER §6.
Related components: ledger/schema.sql, ledger/app.py (tenants/projects
  endpoints, scope-required creates, tenant visibility, user tenant),
  rouvr/workhub/ledger.py (create_task scope), rouvr/mcp_server.py
  (create tenant/project/task tools), rouvr/activities.py
  (verify_binding scope check, ops_prepare scope), rouvr/contracts.py
  (OpsInput scope), rouvr/starter.py (task-scope input), rouvr/api.py
  (scope admission), demo/morning_report.py (scope posts)
```

## 3. In Scope

- `tenants` + `projects` tables; `tasks.tenant/.project` mandatory;
  `users.tenant` mandatory for new users.
- `POST /v1/tenants`, `POST /v1/projects` (stack-only, idempotent),
  `GET /v1/projects` (tenant visibility).
- Task creates (both endpoints) require tenant+project, refuse unknown
  scope, refuse cross-tenant writes for user callers.
- List/get visibility by caller tenant; stack sees all.
- `verify_binding` rejects scope mismatch (fail closed, no retry).
- MCP: scope args on create (no env default), tenant/project tools.
- Starter/api/ops/demo carry explicit scope, never defaults.
- New `tests/test_tenant_project.py`; all existing creators updated.

## 4. Out of Scope

- Grant scopes stay task-global (task ids are globally unique).
- Container sandbox, live Meta/WorkHub (prior verdicts' standing items).
- Legacy DBs with unscoped rows: startup refuses with an explicit
  assignment message (no silent backfill, no defaults).

## 5. Target Files

```text
ledger/schema.sql, ledger/app.py, rouvr/workhub/ledger.py,
rouvr/mcp_server.py, rouvr/activities.py, rouvr/contracts.py,
rouvr/starter.py, rouvr/api.py, demo/morning_report.py,
tests/test_tenant_project.py (new), tests/test_mcp_server.py,
tests/test_ledger_contract.py, tests/test_user_profiles.py,
tests/test_perf_evidence.py, tests/test_ops.py,
tests/test_api_personas.py, SUPERVISOR.md, docs/roadmap.md
```

## 6. Tasks

### Task 22.1 — Ledger partitions (schema + endpoints + visibility)

- AC: tenant/project created mandatorily and in order; task create
  without scope → 422; unknown scope → 404; cross-tenant read/write →
  403; lists filtered by caller tenant; stack sees all; legacy unscoped
  rows refuse startup explicitly.
- Tests-first: `tests/test_tenant_project.py` ledger cases.

### Task 22.2 — Creation-path wiring (MCP/client/ops/demo/api/starter)

- AC: every creation call carries explicit scope; no env/project
  default remains on any create path; starter binds the task's scope;
  binding mismatch blocks without retry.
- Tests-first: binding mismatch test, MCP signature tests, updated
  existing creators.

### Task 22.3 — Docs + index + review submission

- AC: sheet + index/lifecycle entries, SUPERVISOR + roadmap lines,
  lane submission; gates green before ACCEPT.
- Tests-first: hygiene gate (docs scanned).

## 7. Closeout Report

(Reviewer fills at closeout per guidelines.)
