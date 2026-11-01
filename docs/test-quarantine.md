# Test quarantine list

Suites that skip (or must skip) in this cut because the asset or
environment they need is not present. Nothing here is a product-code
failure. To un-quarantine a row, provide the asset-or-env-needed and
remove the corresponding skip; the owner column says who can do that.

All paths below are relative to the repo root unless noted.

| suite | reason | asset-or-env-needed | owner |
| --- | --- | --- | --- |
| `core/backend/tests/aix_core/test_runtime_observability_alerts.py` (module skip) | persisted_runtime_state drill artifacts not in this cut | `docs/tasks/executor-brokerage/artifacts/AIX-EXP-RUNTIME-009/persisted_runtime_state/` | drill-artifact producer (executor-brokerage) |
| `core/backend/tests/framework/test_framework_invariants_documented.py` (module skip) | F0 roadmap validator not in this cut | `docs/current/AIX-Framework-Roadmap/scripts/validate_f0_baseline.py` | framework-roadmap owners |
| `core/backend/tests/integration/test_expense_tracker_live_proof.py` (module skip) | live-proof fixture not in this cut | `core/backend/fixtures/expense-tracker-failed-cycle/` | fixture owner (expense-tracker live proof) |
| `core/backend/tests/integration/test_f12_live_cycle.py` (module skip) | live-cycle fixture not in this cut | `core/backend/fixtures/expense-tracker-failed-cycle/` | fixture owner (F12 live cycle) |
| `core/backend/tests/test_aix_model_ingress_artifact_service.py` (module skip) | model-ingress schemas unpublished | `schemas/aix/model_ingress/` (checked at repo root, `core/`, `core/backend/`) | schema publisher (model ingress) |
| `core/backend/tests/test_aix_model_ingress_gate_service.py` (module skip) | model-ingress schemas unpublished | `schemas/aix/model_ingress/` (same three locations) | schema publisher (model ingress) |
| `core/backend/tests/test_devexecutor_patch_engine_service.py` (fixture skip) | legacy monorepo profiles not in this cut | `projects/AIX/hyeve-aix-service/backend/config/repo_profiles/` (cwd-relative) | monorepo-cut producer |
| `core/backend/tests/test_devexecutor_planner_bridge_service.py` (module skip) | model-ingress schemas unpublished | `schemas/aix/model_ingress/` (same three locations) | schema publisher (model ingress) |
| `core/backend/tests/test_e1_artifact_normalizer.py` (module skip) | live-run outputs not in this cut | `core/backend/artifacts/run_fire/` | run-output producer (codeintel live runs) |
| `core/backend/tests/test_repo_profile_resolver_service.py` (fixture skip) | legacy monorepo profiles not in this cut | `projects/AIX/hyeve-aix-service/backend/config/repo_profiles/` (cwd-relative) | monorepo-cut producer |
| `core/backend/tests/test_session_factory_sdk_execution_worker.py` (module skip) | private SDK tarball not in this cut | `core/backend/vendor/hyeve-agent-sdk-0.9.0-pilot.2.tgz` | SDK vendor / license holder |
| `core/backend/tests/test_workhub_command_broker.py` (`projection_type` fixture skip) | projection companion unpublished | `backend.workhub_projection` module, or set `AIX_PROJECTION_MODULE` to the reviewed companion file | reviewed-companion author (WorkHub projection) |
| `core/backend/tests/aix_core/control_plane_reachability/test_reachability_manifest.py::test_write_and_reload_manifest` (in-test skip) | monorepo-scale manifest needs static extras not in this cut | component_count >= 100 (static extras that register the missing components) | reachability-manifest owners |
| `tests/test_hyeve_demo.py` live legs (env-gated skip, S023) | live Hyeve legs need a live backend plus credentials | `ROUVR_HYEVE_LIVE=1`, `HYEVE_AUTH_BASE_URL`, `HYEVE_WORKHUB_BASE_URL`, `ROUVR_HYEVE_CREDS_FILE` | live-Hyeve environment owner |
| `tests/test_ops.py::test_scheduled_duplicate_tick_deduped` (skip, rev3) | time-skipping test server races two concurrent completions (`temporalio_sdk_core ... Task not found when completing`); flakes ~1/3 of full-file runs, ~1/10 solo; not a product-code failure | 50 consecutive green runs or a pinned SDK without the race; T5 dedupe also covered live (duplicate schedule ticks attach to one task) | test-server/SDK owners |

## Known quarantine hole (not yet skipping)

- `core/backend/tests/test_workhub_command_broker.py::test_real_daemon_entrypoint_external_projection_restart`
  reads `os.environ['AIX_PROJECTION_MODULE']` directly (line ~350) with no
  skip guard, so it errors with `KeyError` instead of skipping when the env
  var is unset. It needs the same guard as the `projection_type` fixture
  (skip when the var is unset or the companion is absent).

## Notes

- The repo-profile guards use a cwd-relative path
  (`projects/AIX/hyeve-aix-service/...`), so they only resolve when the
  working directory is the monorepo root. Keep that in mind when
  un-quarantining: the check is directory-shape dependent, not just
  asset dependent.
- `test_repository_tools.py` (`python3` allowlist) and the bare `pyright`
  entry in `requirements.txt` are environment-compat fixes, not quarantine,
  and are therefore not listed above.
