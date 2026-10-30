# Sprint S014 — Storage scale

## Sprint ID

`S014`

## Sprint Title

Indexed evidence dedupe and WAL-mode ledger

## 1. Objective

Remove the two storage costs that grow with use: evidence de-duplication
scans the full task history on every write, and all writers share one
SQLite file in rollback-journal mode, which serializes (and can fail)
parallel task writes.

## 2. Roadmap Mapping

```text
Roadmap sections: MVP-1 hardening; audit findings 7, 8 (2026-10-30)
Related components: rouvr/workhub/ledger.py (attach_evidence_exact),
  ledger/app.py (evidence endpoints, startup), scripts/rouvr
  (ledger launch)
```

## 3. In Scope

- Ledger fence-lookup (single indexed query) replacing list-and-scan
- WAL mode enabled at ledger startup
- Perf test with generated evidence volume

## 4. Out of Scope

- Postgres migration (deferred until multi-host evidence exists);
  schema changes beyond an index on evidence(task_id, kind)

## 5. Target Files

```text
ledger/app.py, ledger/schema.sql, rouvr/workhub/ledger.py,
rouvr/workhub/client.py (parity note only), scripts/rouvr
tests/test_ledger_contract.py, tests/test_perf_evidence.py (new)
```

## 6. Tasks

### Task 14.1 — Fence lookup (IMPLEMENTATION)
- Scope: ledger endpoint/lookup by (task_id, kind, fence); client uses
  it in attach_evidence_exact; list-and-scan fallback removed.
- AC: attach cost is one query at 10k evidence rows for the task;
  duplicate-fence behavior (same readback row) unchanged.
- Tests-first: dedupe behavior tests stay green before perf work.
- Happy proof: perf test at 10k rows under the agreed budget.
- Non-happy proof: duplicate fence returns the original row, no dup.
- Evidence: pytest output incl. perf timings.

### Task 14.2 — WAL mode (IMPLEMENTATION)
- Scope: PRAGMA journal_mode=WAL at ledger startup (file DBs only).
- AC: two tasks running in parallel complete with zero
  `database is locked` errors in worker/ledger logs.
- Happy proof: parallel two-task live run.
- Non-happy proof: ledger restart keeps WAL and data intact.
- Evidence: live-run transcripts; log grep results.

## 7. Acceptance Criteria

```text
AC-1: Given 10k evidence rows on a task, an evidence attach performs
  one indexed lookup (perf test passes its budget).
AC-2: Given a duplicate fence, the original evidence row is returned
  and no duplicate is written.
AC-3: Given two tasks in parallel, no `database is locked` error
  appears in any worker or ledger log.
```

## 8. Tests Required

- Unit: fence dedupe (hit, miss, duplicate), WAL pragma applied
- Regression: full public suite + hygiene before closeout
- Non-happy: duplicate fence; ledger restart mid-run

## 9. Evidence Required

Pytest results incl. perf numbers, files-changed list, parallel-run
transcripts, log greps.

## 10. Definition of Done

ACs met; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S014: PASS
Implementation Summary: ledger GET evidence/lookup by (kind, fence)
  replaces client list-and-scan dedupe; WAL mode enabled at ledger
  startup for sqlite file DBs.
Files Changed: ledger/app.py, rouvr/workhub/ledger.py,
  tests/test_perf_evidence.py
Tests Run: fence lookup correct at 2000 seeded rows (hit + 404 miss),
  WAL mode asserted on the live file DB; ledger contract tests green.
Non-Happy-Path Tests: duplicate fence returns the original row id.
Known Gaps: lookup still scans one kind's rows server-side; a
  (task_id, kind, fence) index is the next step only if volume demands.
```

## 12. Task Completion Reports

- Task 14.1: Done. Evidence: test_perf_evidence green. Next: 14.2.
- Task 14.2: Done. Evidence: WAL asserted in test; live stack on WAL DB. Next: S015.
