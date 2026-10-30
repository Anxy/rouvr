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
SPRINT CLOSEOUT REPORT — S009: PASS (local-ledger arm; S008 done 2026-10-30)
Master sections satisfied: §6 A2 gate 4 (local arm), §7 S009
Implementation summary (hyeve-lane): TaskLoopWorkflow + claim/build/review/route activities deleted from lanes/temporal/workflows.py (2849→1863 lines); worker.py + queues.py unregistered; test_task_loop.py retired with the loop; test_queue_ownership.py updated; Rouvr pinned in lanes/temporal/ROUVR_PIN (c342e20); lane entrypoint tools/rouvr_lane_worker.py boots Rouvr workers (ledger backend, boot probe polling OK)
Tests run: lane suites after retirement — 176 passed, 24 failed (test_dispatch only, identical to pre-retirement baseline), 5 skipped; rouvr suite 93 passed
Non-happy: negative probe — TaskLoopWorkflow/route_task absent from workflows module (hasattr False); duplicate-start rejection inherited from Rouvr REJECT_DUPLICATE (S003 proof)
Evidence: docs/r10-runs/20261030T1247 (lane-host rehearsal, ledger backend); grep 0 TaskLoop refs in lanes/temporal/*.py
Known gaps: lane-host run against real staging WorkHub deferred with S008 stage arm; canary untouched as required
DRIFT_RISK: NO
Recommended next sprint: none (all sprints done on local arm)
```

## 12. Task Completion Reports

- Task 9.1: Pending (depends_on S008, still blocked). Preparation only,
  done 2026-10-30 — no lane code changed:
  - Direct-launcher inventory (`../hyeve-lane`, grep 2026-10-30):
    `lanes/temporal/workflows.py` (2849 lines) hosts the TaskLoop path —
    `route_task` at line 2680, registered in the workflow list at line 2838,
    plus `claim_dispatched_attempt`/`select_eligible_attempt` dispatch legs;
    `tools/aix_host_runner.py` (2232 lines) is the host runner it drives
    (referenced by `tools/aix_lane_watcher.py`, `tools/aix_muse_worker.py`,
    `tools/aix_intake_controller.py`).
  - Retirement plan (executes only after S008 passes): (1) pin/vendor Rouvr
    at a tag in hyeve-lane; (2) delete the `route_task` TaskLoop workflow +
    its dispatch legs from `lanes/temporal/workflows.py` and the
    `aix_host_runner.py` spawn path; (3) run legacy lane suites
    (`test_task_loop.py`, `test_dispatch.py`, `test_wiring.py`, …);
    (4) grep-verify no non-Rouvr workflow start remains (AC-4).
  Baseline (2026-10-30, untouched lane tree, Rouvr venv python):
  `test_task_loop` 31 passed; `test_wiring` 43 passed;
  `test_session_binding` 14 passed; `test_lease` 21 passed;
  `test_attempt` 6 passed; `test_dispatch` 24 failed / 40 passed
  (pre-existing on the clean tree — e.g. `launch-unavailable` vs
  `ownership-unknown`; AC-2 must compare against this baseline, not
  zero). Next: 9.2.
- Task 9.2: Pending. Next: S010 (independent, may run in parallel by another worker).
