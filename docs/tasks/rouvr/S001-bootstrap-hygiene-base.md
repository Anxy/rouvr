# Sprint S001 — Bootstrap, hygiene gates, package base + ledger parity

## Sprint ID

`S001`

## Sprint Title

Bootstrap refs, R1 hygiene, R3 package base and ledger contract suite

## 1. Objective

Establish buildable ground: pinned upstream refs, tenant-neutrality gates,
the `rouvr/` package skeleton, and a ledger backend that satisfies the
WorkHub contract suite. No workflow or model code.

## 2. Roadmap Mapping

```text
Roadmap sections: Step 1 (refs), Step 2 (R1), Step 5 (R3)
Related components: scripts/, .github/, ledger/, rouvr/workhub/, tests/
```

## 3. In Scope

- Pin `hyeve-lane` / `hyeve-workhub-service` refs; confirm Temporal reachability
- R1 scrub (no history rewrite) + `hygiene.sh` + allowlist + CI job + gitleaks + history note
- `rouvr/` base: config/errors/ids/contracts, workhub routes/port/client/ledger
- Ledger parity tables/endpoints; contract suite green on the ledger arm

## 4. Out of Scope

- Temporal workflows, leases, launcher, gate, runner, verifier (S003–S006)
- Staging mutations (S002); rehearsal (S007)

## 5. Target Files

```text
scripts/hygiene.sh, .github/hygiene-allowlist.txt, .github/workflows/ci.yml
docs/release-notes/r1-history-hygiene.md
rouvr/config.py, rouvr/errors.py, rouvr/ids.py, rouvr/contracts.py
rouvr/workhub/{routes,port,client,ledger}.py, ledger/{app,schema}.py
tests/test_ledger_contract.py, core/backend/rouvr_models/providers.py
```

## 6. Tasks

### Task 1.1 — Pin refs, confirm paths/Temporal (PLANNING)
- Scope: read-only fetch + probes. No mutations.
- AC: pins recorded; router paths listed; Temporal grpc reachable.
- Tests-first: n/a (read-only); validation = `git log`, `curl /health`.
- Evidence: pins `d2e39d8` / `9798926`; local stack up.

### Task 1.2 — R1 scrub + gates (IMPLEMENTATION)
- Scope: exact files in §5 (hygiene + CI + release note + provider preset).
- AC: `hygiene.sh` clean; gitleaks clean on tree + history; history intact;
  meta provider preset env-only (no hardcoded endpoint/model).
- Tests-first: hygiene negative control (seeded violation must fail).
- Happy proof: gates green. Non-happy proof: seeded violation fails;
  `get_provider("meta")` without env raises.
- Evidence: gate outputs; provider tests.

### Task 1.3 — Package base + ledger parity (IMPLEMENTATION)
- Scope: `rouvr/` base + `ledger/` + parity suite.
- AC: contract suite green on ledger arm (claim/attempt/renew/evidence/result).
- Tests-first: `tests/test_ledger_contract.py` before/while implementing.
- Evidence: pytest output; public suite count at close.

## 7. Acceptance Criteria

```text
AC-1: Given the repo, hygiene.sh reports clean and gitleaks reports no leaks.
AC-2: Given a seeded private string, hygiene.sh fails (negative control).
AC-3: Given no Meta env, get_provider("meta") raises (no silent default).
AC-4: Given the ledger arm, the full contract flow passes (claim→result).
```

## 8. Tests Required

- Unit: provider preset tests; hygiene negative probe
- Integration: `tests/test_ledger_contract.py` (ledger arm)
- Non-happy: seeded-violation fail; missing-env raise; stale-renew MOVED

## 9. Evidence Required

Gate outputs, pytest results, files-changed list, known gaps.

## 10. Definition of Done

ACs met; tests pass; no scope expansion; closeout report produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S001: PASS
Implementation Summary: refs pinned; R1 gates live; package base + ledger parity green.
Files Changed: scripts/hygiene.sh, .github/*, docs/release-notes/r1-history-hygiene.md,
  rouvr/{config,errors,ids,contracts}.py, rouvr/workhub/*.py, ledger/*,
  core/backend/rouvr_models/providers.py + tests
Tests Run: public suite (65 passed at the time), provider tests, ledger contract suite
Non-Happy-Path Tests: seeded hygiene violation fails; meta-without-env raises; stale renew MOVED
Deviations: review-001 follow-up moved the meta preset to env-only and widened the
  gate with llama.com|Maverick inside this sprint's scope (same files, no expansion).
Known Gaps: stage arm of the contract suite needs the attempt-scope grant (→ S007/A2).
```

## 12. Task Completion Reports

- Task 1.1: Done. Evidence: pins + reachable stack. Next: 1.2.
- Task 1.2: Done. Evidence: hygiene clean (1955 files at final re-run),
  gitleaks no leaks, 4 provider tests pass. Next: 1.3.
- Task 1.3: Done. Evidence: ledger contract suite green. Next: S002.
