# Demo-path failure triage (2026-11-01)

Base: full `core/backend/tests` run — 240 failed, 3978 passed, 90 skipped.
Demo path for the hackathon rehearsal: `rouvr start` → supervisor →
task → coder → review → close → receipt, plus the scheduled ops task.
That path runs `rouvr/`, `demo/`, `ledger/`, `workers/`, `cli/`,
`lane/tests`, and top-level `tests/` — the `make test-public` scope.

## Verdict: 0 of 240 failures are on the demo path

Every failing test file sits under `core/backend/` (the parallel backend
implementation the demo does not execute). The demo-path suites are all
green: `make test-public` = 413 passed, 4 skipped. Nothing in this file
required an on-path fix; every failing cluster below is off-path and
waits on an Owner decision.

## Failing clusters (all off-path)

Cluster = file (or directory for the 1-count tail). Call = proposed
next step: `fix-code` (product change), `re-spec` (test change),
`Owner` (needs a product-semantics decision first).

| Cluster | Count | On path | Call |
|---|---|---|---|
| test_model_gateway_router (resolve/planner drift) | 16 | n | Owner: planner/codeintel predicate semantics |
| test_aix_enveloped_run_service | 12 | n | Owner: investigate (pre-existing) |
| pd001_planner_authority | 11 | n | Owner: planner authority semantics |
| provenance_p6 | 11 | n | Owner: investigate |
| dsd001_durable_step_dispatcher | 10 | n | Owner: dispatcher semantics |
| equivalence_deep | 8 | n | Owner: investigate |
| pa002_planner_authority | 6 | n | Owner: planner authority semantics |
| cl2_verifier_hard_gate | 6 | n | Owner: verifier semantics |
| aix_v2_live_file_grounding | 5 | n | Owner: investigate |
| w6_governance_scope | 5 | n | Owner: governance semantics |
| authorization_required | 5 | n | Owner: investigate |
| exploration_gate_integration | 4 | n | Owner: investigate |
| repo_profile_models / persona_manifest_models / command_allowlist_models | 3+3+3 | n | re-spec: model surface drift (mechanical) |
| model_gateway_phase_router | 3 | n | Owner: investigate |
| objective_cycle tail: workhub_completion_ordering, phase1_terminalization, mpe10_24, mark_workhub_done, durable_approval, closed_loop_004 (p5/p4/p7), cancellation, full_lifecycle_e2e, recovery_failure, resume_running, stuck_cycle, plan_complete, plus ~30 more single-count files | ~120 | n | Owner: state-machine semantics, file by file |
| execution_kernel (ex5_s2, ex4_p10, restart_recovery leakage), framework models/wiring, objective_contract c024, bff_openapi, production_go_gate, c040_planner_proof, p0 provenance | ~15 | n | Owner: investigate |

## Already fixed this round (were failing, now green)

Gateway auth override (52→16 via lazy-router fix), MVP retirement
contract (30), router classification (9), retired WorkHub clients
(25), missing-asset quarantine with skip reasons (was 27 errors),
pyright dep, python3 shell-out.

## Green demo-path suites (kept green)

`ledger`, `demo` (incl. morning_report failure-injection legs),
`lane/tests`, `workers`, `cli`, top-level `tests/` (incl. S023 Hyeve,
ops, hidden-suite, decompose). Committed proof: `make test-public`
413 passed, 4 skipped.
