# Sprint S020 — Review-10-31 trust + packaging fixes (F1/F2/F3/F6/F7)

## Sprint ID

`S020`

## Sprint Title

Real hidden suites, compose sqlite, fail-closed MCP token, inbox reasons, docs sync

## 1. Objective

Close the coder-actionable gaps from the 2026-10-31 repo review
(`docs/rouvr-repo-review-2026-10-31.md`, HEAD `3ab4af0`): hidden suites
must be authored, never `assert True` (F1); compose must not advertise a
postgres ledger the app refuses (F2a); the MCP port must fail closed
without a token (F2b); the supervisor inbox must show escalation reasons
(F3); roadmaps must match the INDEX (F6); schedules documented as
coding-only (F7).

## 2. Roadmap Mapping

```text
Roadmap sections: review-10-31 findings 1,2,3,6,7; rouvr-roadmap R/A/P2;
  MASTER §§6-9. T2-final (real suites), T5-packaging, T7-hygiene/doc-sync.
Related components: rouvr/mcp_server.py (workhub_create_task,
  workhub_list_tasks waiting reason, __main__ token gate),
  demo/docker-compose.yml, rouvr/starter.py (schedule doc),
  docs/roadmap.md, docs/ROUVR-MASTER-SPRINT.md, docs/ecosystem.md,
  docs/rouvr-repo-review-2026-10-30.md (supersede note),
  docs/tasks/rouvr/S017-loop-integrity-fixes.md (stale known_gaps line)
```

## 3. In Scope

- F1: `workhub_create_task` never writes placeholders. Suite comes from
  a `test_author` model turn (new module-level author hook, coder-blind:
  files go straight to `HIDDEN_TESTS_ROOT`, never the ws); when no
  model is available the create is refused with a setup hint, not
  degraded to `assert True`. MCP tool signature unchanged.
- F2a: compose ledger URL → `sqlite:///`; drop the ledger `depends_on db`
  claim (keep the `db` service only if another service needs it).
- F2b: MCP HTTP refuses to start without `ROUVR_LEDGER_TOKEN` unless
  explicit `ROUVR_ALLOW_ANON=1`. Small extracted seam, tested.
- F3: waiting list `json.loads` the escalation `body` before reading
  `payload.reason` (same as the show-task path).
- F6: Oct-30 review marked superseded; MASTER §6–7 + roadmap checkboxes
  synced to INDEX (19/19 PASS); ecosystem auth/leases lines refreshed;
  S017 stale known_gaps line corrected.
- F7: schedules documented as coding-only (starter docstring + sheet
  note); ops runs go through `OpsTaskWorkflow` directly.

## 4. Out of Scope (deferred with rationale, not skipped silently)

- F5 default-container coder: review itself sequences it post-demo;
  flipping the default now would break host-assuming tests and the
  frictionless demo. Host default stays an explicit, documented choice.
- F8 machine-specific defaults: dev ergonomics already overridable
  (S019 `ROUVR_SPEC_REPO/BASE`); requiring env now breaks `make setup`
  frictionlessness for zero demo gain. Nice-to-have, left as is.
- F4/F9/F10 live Meta, real WorkHub, recordings: owner-run/external,
  not coder-achievable. Left for the owner.

## 5. Target Files

```text
rouvr/mcp_server.py, tests/test_mcp_server.py,
demo/docker-compose.yml, scripts/rouvr (only if compose references it),
rouvr/starter.py (docstring only),
docs/roadmap.md, docs/ROUVR-MASTER-SPRINT.md, docs/ecosystem.md,
docs/rouvr-repo-review-2026-10-30.md (supersede note),
docs/tasks/rouvr/S017-loop-integrity-fixes.md (one known_gaps line)
```

## 6. Tasks

### Task 20.1 — Real hidden suites or refusal (F1)

- AC: no `assert True` placeholder is ever written; criteria become
  model-authored checks via the test-author hook; coder ws untouched;
  no-model create fails closed with a setup hint naming the provider env.
- Tests-first: fake-author test (bodies land verbatim, `created_by=test_author`);
  refusal test (no model → RuntimeError, nothing written);
  malformed-model-output test (no partial suite left behind).

### Task 20.2 — Compose sqlite ledger (F2a)

- AC: compose ledger starts against sqlite; no `depends_on db` claim for
  the ledger path; `make_app` sqlite path unchanged.
- Tests-first: compose static test (no `postgresql` in ledger env).

### Task 20.3 — MCP token fail-closed (F2b)

- AC: HTTP serve without `ROUVR_LEDGER_TOKEN` and without
  `ROUVR_ALLOW_ANON=1` refuses to start with a one-line hint; explicit
  allow-anon preserves the old open behavior deliberately.
- Tests-first: seam unit test (token set / unset / allow-anon).

### Task 20.4 — Inbox escalation reasons (F3)

- AC: blocked task with escalation evidence lists
  `escalation:<reason>`; malformed body still falls back to `blocked`,
  never raises.
- Tests-first: waiting-list test with JSON-string body.

### Task 20.5 — Docs sync (F6)

- AC: Oct-30 review carries a superseded-by-10-31 note; MASTER §6–7 and
  roadmap checkboxes match INDEX 19/19 PASS; ecosystem no longer claims
  no-auth/no-leases; S017 stale line fixed. Hygiene stays clean.
- Tests-first: hygiene gate itself (docs are scanned).

### Task 20.6 — Schedules documented coding-only (F7)

- AC: starter schedule path + one doc line state schedules start
  `TaskWorkflow`; ops packs route via `OpsTaskWorkflow` directly.
- Tests-first: docstring/static presence test.

## 7. Closeout Report

SPRINT CLOSEOUT REPORT — S020: PASS (scribed from review-008 VERDICT ACCEPT + CLOSEOUT, 2026-10-31).
Implementation: F1 test_author hook + refusal, F2a compose sqlite, F2b token gate, F3 inbox reasons, F6 doc sync, F7 coding-only docs; F5/F8/F4/F9/F10 deferred per sheet §4.
Verification: at ca53884 — tests/ 295 passed; broad set 389 passed, 2 skipped; hygiene clean; lifecycle valid. Ledger review approved + closeout recorded with review-008 as authority. No code beyond the sheet scope.
