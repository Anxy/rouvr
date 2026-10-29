# Sprint S003 — R4 TaskWorkflow + R5 lease/fencing

## Sprint ID

`S003`

## Sprint Title

Durable build→test→verify→close loop with fenced ownership

## 1. Objective

Deliver the Temporal workflow (bounded rework, one task→one workflow) and
the lease/fencing layer (claim → renew-first retry → same-attempt resume),
exercised by fake-activity workflow tests and the ledger arm.

## 2. Roadmap Mapping

```text
Roadmap sections: Step 6 (R4), Step 7 (R5)
Related components: rouvr/workflows.py, rouvr/starter.py, rouvr/api.py,
  rouvr/activities.py (skeleton), rouvr/lease.py, rouvr/host.py, cli/
```

## 3. In Scope

- TaskWorkflow + ScheduledRunWorkflow, starter (REJECT_DUPLICATE), thin endpoint, `rouvr start`
- `_wrap`, `assign_task` skeleton, `claim_assignment_and_lease`,
  `open_or_reuse_attempt`, `resume_or_claim`, `LeaseKeeper`, `run_fenced`
- Workflow + ownership tests; live double-start probe

## 4. Out of Scope

- Builder-turn interior, launcher, gitops (S004); gate/runner/verifier (S005–S006)

## 5. Target Files

```text
rouvr/workflows.py, rouvr/starter.py, rouvr/api.py, rouvr/activities.py
rouvr/lease.py, rouvr/host.py, rouvr/contracts.py, cli/rouvr_cli/__main__.py
tests/test_workflow.py, tests/test_r5_ownership.py
```

## 6. Tasks

### Task 3.1 — Workflow + start path (IMPLEMENTATION)
- Scope: workflows/starter/api/CLI + thin activities.
- AC: double-start returns `(id, True)` then `(id, False)` live; rework loop
  test passes (request_changes → approve, resume sid carried).
- Non-happy proof: rework-exhausted blocks; cancel signal blocks.
- Evidence: `test_workflow.py`, live probe output.

### Task 3.2 — Ownership + fencing (IMPLEMENTATION)
- Scope: lease.py, host.py, assign_task skeleton, contract Lease field.
- AC: claim→attempt→fenced-work flow green on ledger; retry renews a live
  lease, else reclaims + claims on the same attempt; re-claim conflict → MOVED.
- Non-happy proof: renew-after-expiry falls back; non-MOVED renew errors propagate.
- Evidence: `test_r5_ownership.py`, parity suite.

## 7. Acceptance Criteria

```text
AC-1: Second start of the same task+rev returns started=False with the same id.
AC-2: request_changes once then approve yields done in 2 attempts with resume sid.
AC-3: Retry with a live lease renews (no new epoch); with a dead lease reclaims+claims.
AC-4: Renew failure with AUTHZ propagates (no silent fallback to claim).
```

## 8. Tests Required

- Integration: `tests/test_workflow.py` (time-skipping server, fake activities)
- Integration: `tests/test_r5_ownership.py` (ledger arm + FakePort retry cases)
- Live: double-start probe on local Temporal

## 9. Evidence Required

Pytest results, probe output, files-changed list.

## 10. Definition of Done

ACs met; workflow + ownership tests pass; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S003: PASS
Implementation Summary: workflow + start path live-probed; ownership layer with
  renew-first retry; SDK hang fixed via return annotations.
Files Changed: rouvr/workflows.py, rouvr/starter.py, rouvr/api.py, rouvr/activities.py,
  rouvr/lease.py, rouvr/host.py, rouvr/contracts.py, cli/, tests/test_workflow.py,
  tests/test_r5_ownership.py
Tests Run: 2 workflow tests + 5 ownership tests pass; double-start probe (id,True)→(id,False)
Non-Happy-Path Tests: rework-exhausted blocks; cancel blocks; MOVED fallback; AUTHZ propagates
Known Gaps: none in sprint scope.
```

## 12. Task Completion Reports

- Task 3.1: Done. Evidence: workflow tests + live probe. Next: 3.2.
- Task 3.2: Done. Evidence: ownership tests + parity suite. Next: S004.
