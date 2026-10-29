# Sprint S009 — hyeve-lane back-port (A2)

## Sprint ID

`S009`

## Sprint Title

Retire the TaskLoop direct launcher; hyeve-lane consumes Rouvr

## 1. Objective

Bump hyeve-lane to the Rouvr orchestrator (or vendor it at a tag), delete
the direct-launcher path, and prove the same R10 script green against real
WorkHub from the lane host. Canary stays untouched until this passes.

## 2. Roadmap Mapping

```text
Roadmap sections: A2 gate 4; depends_on: S008
Related components: hyeve-lane (lanes/temporal, tools/aix_host_runner.py),
  rouvr/ package + worker
```

## 3. In Scope

- hyeve-lane dependency bump to Rouvr modules (pin/tag) or vendoring
- Remove the direct-launcher call path (route_task/aix_host_runner spawn)
- Lane-host worker deployment (per-queue processes)
- Same R10 script green against real WorkHub from the lane host

## 4. Out of Scope

- Canary behaviour changes; WorkHub fixes; new Rouvr features

## 5. Target Files

```text
hyeve-lane repo (separate checkout): lanes/temporal/workflows.py,
tools/aix_host_runner.py, deploy config
rouvr: no code changes expected (consumer only)
docs/r10-runs/<lane-ts>/
```

## 6. Tasks

### Task 9.1 — Dependency bump + launcher retirement (IMPLEMENTATION)
- Scope: hyeve-lane files above only.
- AC: lane imports Rouvr Temporal/workflow modules at a pinned version; no
  code path spawns a lifecycle workflow outside Rouvr; legacy lane tests pass.
- Non-happy proof: removing the direct-launcher path breaks nothing (legacy
  suite green); a dual-start attempt is rejected (REJECT_DUPLICATE).
- Evidence: hyeve-lane test output; grep showing the retired path is gone.

### Task 9.2 — Lane-host R10 against real WorkHub (VERIFICATION)
- Scope: deployment + rehearsal run; no code.
- AC: R10 script exits 0 from the lane host against staging; done + 1 PR +
  same-attempt retry; transcript recorded.
- Evidence: transcript dir; workflow result; history dump.
- Non-happy proof: mid-turn kill lands via the prompt-file trigger.

## 7. Acceptance Criteria

```text
AC-1: hyeve-lane depends on Rouvr at a tag; the direct launcher is deleted.
AC-2: Legacy lane test suite passes after the retirement.
AC-3: R10 script green against real WorkHub from the lane host.
AC-4: No workflow starts outside Rouvr (grep + negative probe).
```

## 8. Tests Required

- Regression: hyeve-lane suites; Rouvr public suite
- E2E: rehearsal script on lane host
- Non-happy: duplicate start rejected; retired-path grep empty

## 9. Evidence Required

Test outputs, grep output, transcript, files-changed list (both repos).

## 10. Definition of Done

ACs met; canary untouched; INDEX regenerated; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S009: (open; depends on S008)
```

## 12. Task Completion Reports

- Task 9.1: Pending. Next: 9.2.
- Task 9.2: Pending. Next: S010 (independent, may run in parallel by another worker).
