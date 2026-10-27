"""F4 deep execution equivalence harness.

Compares durable-backend execute entry points with:
  FLAG OFF → run_real_execute
  FLAG ON  → ExecutionPort → RealExecuteAdapter → run_real_execute
"""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

from backend.aix_core.framework.execution.real_execute_adapter import RealExecuteAdapter
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot


COMPARISON_KEYS = (
    "tenant_id",
    "cycle_id",
    "objective_id",
    "objective",
    "workhub_task_id",
    "project_id",
    "task_number",
    "repository_id",
    "attachments",
    "require_workhub_load",
    "workspace_base",
    "model_override",
    "caller_authorization",
    "actor_id",
    "budget_config",
    "apply_edits",
    "task_family",
    "execution_context",
    "allowed_files",
    "admission_allowed_files",
    "demo_capability",
    "original_objective",
    "step_contract",
    "plan_steps",
    "context_capsule",
    "workspace_step_id",
    "repair_directive",
    "session_id",
)


VOLATILE_KEYS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "cycle_id",
        "objective_id",
        "understanding_evidence_id",
        "intent_id",
        "plan_id",
        "step_id",
        "workspace_attempt_id",
        "workspace_step_id",
        "action_id",
        "recorded_at",
        "timestamp",
        "obligation_refs",
    }
)

# Cross-run backends mint fresh ULIDs; strip for structural parity.
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_OBJ_ID_RE = re.compile(r"^obj_[0-9A-HJKMNP-TV-Z]{26}$")


def _normalize_id_string(value: str) -> str:
    if _ULID_RE.match(value) or _OBJ_ID_RE.match(value):
        return "<id>"
    return value


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _normalize_id_string(value)
    if callable(value):
        return "<callable>"
    if is_dataclass(value) and not isinstance(value, type):
        return {
            k: _serialize(v)
            for k, v in asdict(value).items()
            if k not in VOLATILE_KEYS
        }
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")
            if isinstance(dumped, dict):
                return {
                    str(k): _serialize(v)
                    for k, v in sorted(dumped.items(), key=lambda kv: str(kv[0]))
                    if str(k) not in VOLATILE_KEYS
                }
            return _serialize(dumped)
        except Exception:
            return repr(value)
    if isinstance(value, dict):
        return {
            str(k): _serialize(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
            if str(k) not in VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return repr(value)


def normalize_execute_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in COMPARISON_KEYS:
        if key not in kwargs:
            continue
        if key in {"cycle_id", "objective_id"}:
            out[f"{key}_present"] = bool(kwargs.get(key))
            continue
        out[key] = _serialize(kwargs[key])
    if "workspace_attempt_id" in kwargs:
        out["workspace_attempt_id_present"] = bool(kwargs.get("workspace_attempt_id"))
    return out


def stub_result(*, ok: bool = True, code: str = "REAL_EXECUTE_OK") -> RealExecuteResult:
    diff = (
        "--- a/helper.py\n+++ b/helper.py\n@@ -1,1 +1,2 @@\n line\n+added\n"
        if ok
        else ""
    )
    return RealExecuteResult(
        ok=ok,
        code=code if ok else (code if code != "REAL_EXECUTE_OK" else "REAL_EXECUTE_FAILED"),
        workspace="ws-eq",
        evidence_refs=["file://ws-eq/evidence/report.json"],
        workhub_loaded=False,
        workhub_lifecycle_authority=False,
        detail="" if ok else "forced failure",
        artifacts={"report": "file://ws-eq/evidence/report.json"},
        changed_source_files=(
            [{"path": "helper.py", "additions": 1, "deletions": 0}] if ok else []
        ),
        diff_text=diff,
        completion_gates={
            "diff_exists": bool(ok),
            "verification_passed": bool(ok),
            "objective_coverage": True,
            "structural_effects": True,
        },
        events=[{"event": "eq.stub", "ok": ok}],
        executor="objective_cycle.real_execute.v1",
        target_files_modified=1 if ok else 0,
        real_operations=1,
        execution_status="COMPLETED" if ok else "FAILED",
        verification_status="PASSED" if ok else "FAILED",
        objective_status="COMPLETED" if ok else "FAILED",
    )


def _seed(backend: DurableObjectiveCycleApiBackend, objective_id: str, cycle_id: str) -> None:
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id,
        cycle_id=cycle_id,
        files_read=("helper.py",),
        observed_problem="test evidence",
        understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id=cycle_id,
        tenant_id="tenant-1",
        step_index=0,
        purpose="seeded execute",
        target_scope=("helper.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("helper works",),
        validation_commands=("python -m py_compile helper.py",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


async def run_execute_cycle(
    *,
    execution_port: bool,
    result: RealExecuteResult | None = None,
    raise_exc: BaseException | None = None,
    require_workhub_load: bool = False,
) -> dict[str, Any]:
    """Drive admit → start → approve through durable backend; capture run_real_execute kwargs."""
    calls: list[dict[str, Any]] = []
    canned = result or stub_result()

    async def _capture(**kwargs: Any) -> RealExecuteResult:
        calls.append(dict(kwargs))
        if raise_exc is not None:
            raise raise_exc
        return canned

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=require_workhub_load,
        execution_port=RealExecuteAdapter(),
        framework_flags=FrameworkFlagsSnapshot(execution_port=execution_port),
    )

    with (
        patch(
            "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
            new=AsyncMock(side_effect=_capture),
        ),
        patch(
            "backend.aix_core.framework.execution.real_execute_adapter.run_real_execute",
            new=AsyncMock(side_effect=_capture),
        ),
    ):
        admit = await backend.command(
            operation="admit_objective",
            tenant_id="tenant-1",
            actor_id="human-1",
            resource_id=None,
            body={
                "workhub_task_id": "task-1",
                "objective": "Add a helper function",
                "task_class": "single_file_helper",
                "definition_of_done": ("done",),
                "repository_id": "aix-frontend",
                "allowed_files": (),
                "project_id": "proj-1",
            },
            idempotency_key=f"admit-{'port' if execution_port else 'direct'}",
            expected_version=None,
        )
        started = await backend.command(
            operation="start_cycle",
            tenant_id="tenant-1",
            actor_id="human-1",
            resource_id=admit["objective_id"],
            body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
            idempotency_key=f"start-{'port' if execution_port else 'direct'}",
            expected_version=None,
        )
        _seed(backend, admit["objective_id"], started["cycle_id"])
        approved = await backend.command(
            operation="validate_approval_reference",
            tenant_id="tenant-1",
            actor_id="human-1",
            resource_id=started["cycle_id"],
            body={"approval_reference": "workhub://approvals/local"},
            idempotency_key=f"approve-{'port' if execution_port else 'direct'}",
            expected_version=started["version"],
        )

    cycle = backend._cycles[started["cycle_id"]]  # noqa: SLF001
    journal = getattr(backend, "_attempt_journal", None)
    return {
        "response": approved,
        "calls": calls,
        "normalized": [normalize_execute_kwargs(c) for c in calls],
        "objective_id": admit["objective_id"],
        "cycle_id": started["cycle_id"],
        "phase": cycle.phase,
        "execution_port": execution_port,
        "journal": journal,
        "backend": backend,
    }


async def compare_off_on(**kwargs: Any) -> dict[str, Any]:
    off = await run_execute_cycle(execution_port=False, **kwargs)
    on = await run_execute_cycle(execution_port=True, **kwargs)
    return {"off": off, "on": on}
