from __future__ import annotations

import os
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parents[2]
_AIX_ROOT = _TESTS_DIR.parents[2]
_MONOREPO_ROOT = _AIX_ROOT.parent


def _candidates() -> list[Path]:
    env = str(os.environ.get("HYEVE_TASK_CONTRACT_ENGINE_ROOT") or "").strip()
    paths: list[Path] = []
    if env:
        paths.append(Path(env))
    paths.append(_MONOREPO_ROOT / "hyeve" / "libs" / "hyeve_task_contract_engine")
    paths.append(_AIX_ROOT / "libs" / "hyeve_task_contract_engine")
    return paths


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook
    """AIX-CH-001 CH05: fail collection with an explicit path, not a nested import error."""
    config.addinivalue_line(
        "markers",
        "no_plan_bootstrap: do not seed an ACCEPTED plan after start_cycle (planner-authority negatives)",
    )
    # ENGINE-GATE DECISION (2026-09-06): NEVER_IMPLEMENTED. The
    # hyeve_task_contract_engine package is unpublished, absent from hyeve
    # history, and consumed by zero tests and zero routes (the router
    # degrades to 503 without it). The former hard collection gate made
    # 262 files unrunnable while hiding 132 pre-existing failures, so it
    # is removed rather than carried. If Hyeve ever publishes the package,
    # re-add an import here — no test will need changes.
    try:
        import hyeve_task_contract_engine  # noqa: F401
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _seed_plan_when_planner_disabled(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """AIX-PA-002: execute requires an ACCEPTED plan. Tests that fake execute with
    the planner flag off get a seeded-step-contract wrap unless they opt out."""
    if request.node.get_closest_marker("no_plan_bootstrap"):
        return

    from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
    from backend.hyeve_aix_service.config import settings
    from backend.tests.runtime.objective_cycle.plan_bootstrap import seed_accepted_bootstrap_contract

    original = DurableObjectiveCycleApiBackend.command

    async def _command(self, *args, **kwargs):  # noqa: ANN001
        result = await original(self, *args, **kwargs)
        operation = kwargs.get("operation")
        if operation is None and args:
            operation = args[0]
        if operation != "start_cycle" or not isinstance(result, dict) or not result.get("ok"):
            return result
        if bool(getattr(settings, "INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", False)):
            return result
        # Honor per-backend settings (e2e / FakeSettings) when the global config
        # still has the planner flag off.
        backend_settings = getattr(self, "_settings", None)
        if backend_settings is not None and bool(
            getattr(backend_settings, "INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", False)
        ):
            return result
        cycle_id = str(result.get("cycle_id") or "")
        cycle = self._cycles.get(cycle_id)  # noqa: SLF001
        objective_id = str(result.get("objective_id") or (cycle.objective_id if cycle is not None else ""))
        if not objective_id or not cycle_id:
            return result
        obj = self._objectives.get(objective_id)  # noqa: SLF001
        allowed = tuple(getattr(obj, "allowed_files", ()) or ()) if obj is not None else ()
        objective_text = str(getattr(obj, "objective", "") or "seeded execute bootstrap")
        # Prefer real admission paths as PlanTargets (mutation/scope authority).
        # Stems alone (`config` vs `backend/config.py`) break FILE_NOT_IN_APPROVED_SCOPE
        # and completion-gate semantics for multi-file repairs.
        # RUN_TESTS / cancellation proofs use @pytest.mark.no_plan_bootstrap and seed
        # their own validate-only contract — do not special-case suite wording here.
        if allowed:
            def _norm_admission(path: str) -> str:
                raw = str(path).replace("\\", "/").strip()
                while raw.startswith("./"):
                    raw = raw[2:]
                return raw.lstrip("/")

            targets = tuple(_norm_admission(p) for p in allowed if str(p).strip())
            schema_ref = "aix.step_contract.v1.generic_edit"
            validation_commands = ("true; use-repository-default-tests",)
        else:
            obj_cf = objective_text.casefold()
            # Do not match bare "pytest" — Windows pytest temp roots
            # (`.../pytest-of-tech/...`) appear inside absolute-path READ objectives.
            _run_tests_seed = "test" in obj_cf and any(
                tok in obj_cf for tok in ("run ", "running ", "execute ", "affected")
            )
            if not _run_tests_seed and (
                " run pytest" in obj_cf
                or obj_cf.startswith("pytest ")
                or " pytest " in obj_cf
            ):
                _run_tests_seed = True
            if _run_tests_seed:
                targets = ("pyproject.toml",)
                schema_ref = "aix.step_contract.v1.generic_edit"
                validation_commands = ("true; use-repository-default-tests",)
            else:
                targets = ("cycle-workspace",)
                schema_ref = "aix.step_contract.v1.proposal"
                validation_commands = ("true; use-repository-default-tests",)
        seed_accepted_bootstrap_contract(
            self,
            objective_id,
            cycle_id,
            target=targets[0],
            schema_ref=schema_ref,
            purpose=objective_text,
            validation_commands=validation_commands,
        )
        if len(targets) > 1 and objective_id in self._step_contracts:  # noqa: SLF001
            contract = self._step_contracts[objective_id]  # noqa: SLF001
            self._step_contracts[objective_id] = contract.model_copy(  # noqa: SLF001
                update={"target_scope": targets}
            )
        if obj is not None and cycle is not None:
            self._ensure_accepted_plan_for_seeded_contract(
                obj=obj, cycle=cycle, allow_test_bootstrap=True
            )
            self._project_run_sync(cycle)  # noqa: SLF001
        return result

    monkeypatch.setattr(DurableObjectiveCycleApiBackend, "command", _command)
