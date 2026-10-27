"""EX2-S008: live bind from Work Item dispatch → bounded objective_cycle run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import AdapterOutcome, DispatchRequest
from backend.aix_core.runtime.executor.context_builder import ContextCapsule
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.api_backend import ObjectiveCycleApiBackend

# Phases / status tokens treated as still in flight (not AdapterOutcome yet).
_IN_FLIGHT = frozenset(
    {
        "RUNNING",
        "PLANNING",
        "EXECUTING",
        "IN_PROGRESS",
        "WAITING_CLARIFICATION",
    }
)
_WAITING_HUMAN = frozenset({"WAITING_APPROVAL", "AWAITING_APPROVAL", "WAITING_HUMAN"})
_SUCCESS = frozenset({"SUCCEEDED", "COMPLETED", "COMPLETE", "DONE", "VERIFIED"})
_FAILED = frozenset({"FAILED", "REJECTED", "CANCELLED", "CANCELED", "TIMED_OUT", "TIMEOUT"})
_BLOCKED = frozenset({"BLOCKED"})


@dataclass(frozen=True)
class AdapterHandle:
    cycle_id: str
    objective_id: str
    phase: str
    work_item_id: str
    tenant_id: str
    capsule_content_sha256: str
    candidate_sha: str


def _phase_token(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("phase", "status", "state"):
            if value.get(key):
                return str(value[key]).strip().upper()
        return ""
    return str(value or "").strip().upper()


def outcome_from_cycle_view(*, cycle_id: str, phase: str, status: Any = None) -> AdapterOutcome | None:
    """Map a cycle phase/status to AdapterOutcome, or None if still in flight."""
    token = _phase_token(phase) or _phase_token(status)
    if not token:
        return None
    if token in _IN_FLIGHT:
        return None
    if token in _WAITING_HUMAN:
        return AdapterOutcome(
            kind="BLOCKED",
            reason_code="WAITING_APPROVAL",
            proposal_hint="Cycle awaits human/planner approval before execute",
            cycle_id=cycle_id,
            evidence_refs=(f"cycle:{cycle_id}",),
        )
    if token in _BLOCKED:
        return AdapterOutcome(kind="BLOCKED", reason_code="CYCLE_BLOCKED", cycle_id=cycle_id)
    if token in _SUCCESS:
        return AdapterOutcome(
            kind="SUCCEEDED",
            evidence_refs=(f"cycle:{cycle_id}",),
            cycle_id=cycle_id,
        )
    if token in _FAILED:
        kind = "TIMED_OUT" if "TIME" in token else "FAILED"
        return AdapterOutcome(kind=kind, reason_code=token, cycle_id=cycle_id, evidence_refs=(f"cycle:{cycle_id}",))
    # Unknown non-empty phase: treat as in-flight to avoid false VERIFIED path.
    return None


def build_admit_body_from_capsule(
    *,
    capsule: ContextCapsule,
    workhub_task_id: str,
    task_class: str = "bounded_repair",
    repository_id: str | None = None,
    definition_of_done: list[str] | None = None,
    allowed_files: list[str] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    title = str(capsule.work_item.get("title") or capsule.work_item.get("work_item_id") or "Work item")
    charter = str(capsule.problem.get("charter_summary") or "")
    objective = f"{title}. {charter}".strip()
    repo = repository_id or str(capsule.repository.get("repository_id") or "").strip()
    if not repo:
        raise ExecutionKernelRepositoryError("repository_id required for live objective_cycle dispatch")
    dod = definition_of_done or [str(x) for x in capsule.acceptance if str(x).strip()]
    if not dod:
        dod = ["Work item acceptance criteria met"]
    body: dict[str, Any] = {
        "workhub_task_id": workhub_task_id,
        "objective": objective,
        "task_class": task_class,
        "definition_of_done": tuple(dod),
        "repository_id": repo,
        "allowed_files": tuple(allowed_files or ()),
    }
    if project_id:
        body["project_id"] = project_id
    return body


class ObjectiveCycleExecutorAdapter:
    """v1 executor adapter: admit + start a bounded objective_cycle under a Work Item."""

    def __init__(self, backend: ObjectiveCycleApiBackend) -> None:
        self._backend = backend

    async def dispatch(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        capsule: ContextCapsule,
        dispatch: DispatchRequest,
        workhub_task_id: str,
        idempotency_key: str,
        task_class: str = "bounded_repair",
        candidate_sha: str | None = None,
        repository_id: str | None = None,
        definition_of_done: list[str] | None = None,
        allowed_files: list[str] | None = None,
        project_id: str | None = None,
        budgets: dict[str, int] | None = None,
    ) -> tuple[AdapterHandle, AdapterOutcome | None]:
        if not str(workhub_task_id or "").strip():
            raise ExecutionKernelRepositoryError("workhub_task_id is required for live dispatch")
        sha = (
            candidate_sha
            or str(capsule.repository.get("current_commit_sha") or capsule.repository.get("commit_sha") or "")
            or "0000000"
        )
        if len(sha) < 7:
            raise ExecutionKernelRepositoryError("candidate_sha must be at least 7 characters")

        admit_body = build_admit_body_from_capsule(
            capsule=capsule,
            workhub_task_id=workhub_task_id,
            task_class=task_class,
            repository_id=repository_id,
            definition_of_done=definition_of_done,
            allowed_files=allowed_files,
            project_id=project_id,
        )
        admit = await self._backend.command(
            operation="admit_objective",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=None,
            body=admit_body,
            idempotency_key=f"{idempotency_key}:admit",
            expected_version=None,
        )
        if not admit.get("ok", True):
            err = admit.get("error") if isinstance(admit.get("error"), dict) else {}
            code = str((err or {}).get("code") or "ADMIT_FAILED")
            raise ExecutionKernelRepositoryError(f"objective_cycle admit failed: {code}")

        objective_id = str(admit.get("objective_id") or "")
        if not objective_id:
            raise ExecutionKernelRepositoryError("objective_cycle admit missing objective_id")

        start_budgets = dict(budgets or {})
        if dispatch.timeout_budget_seconds and "max_wall_seconds" not in start_budgets:
            start_budgets["max_wall_seconds"] = int(dispatch.timeout_budget_seconds)

        start_body: dict[str, Any] = {"candidate_sha": sha, "budgets": start_budgets}
        if dispatch.model_routes:
            # EX5-S2: only start with enforced routes (route_id + enforced flag)
            routes = dict(dispatch.model_routes)
            executor = routes.get("executor") if isinstance(routes.get("executor"), dict) else None
            if executor is None or not executor.get("enforced") or not executor.get("route_id"):
                raise ExecutionKernelRepositoryError(
                    "EXECUTION_ROUTE_REQUIRED: bind/enforce route before live dispatch"
                )
            if "verifier" in routes:
                verifier = routes.get("verifier")
                if not isinstance(verifier, dict) or not verifier.get("enforced"):
                    raise ExecutionKernelRepositoryError(
                        "VERIFIER_ROUTE_ENFORCEMENT_REQUIRED"
                    )
            start_body["model_routes"] = routes
            start_body["execution_route_id"] = executor.get("route_id")
        started = await self._backend.command(
            operation="start_cycle",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=objective_id,
            body=start_body,
            idempotency_key=f"{idempotency_key}:start",
            expected_version=None,
        )
        if not started.get("ok", True):
            err = started.get("error") if isinstance(started.get("error"), dict) else {}
            code = str((err or {}).get("code") or "START_FAILED")
            raise ExecutionKernelRepositoryError(f"objective_cycle start failed: {code}")

        cycle_id = str(started.get("cycle_id") or "")
        phase = str(started.get("phase") or "")
        if not cycle_id:
            raise ExecutionKernelRepositoryError("objective_cycle start missing cycle_id")

        handle = AdapterHandle(
            cycle_id=cycle_id,
            objective_id=objective_id,
            phase=phase,
            work_item_id=dispatch.work_item_id,
            tenant_id=tenant_id,
            capsule_content_sha256=dispatch.capsule_content_sha256,
            candidate_sha=sha,
        )
        outcome = outcome_from_cycle_view(cycle_id=cycle_id, phase=phase, status=started.get("status"))
        return handle, outcome

    async def poll(self, *, tenant_id: str, handle: AdapterHandle) -> AdapterOutcome | None:
        view = await self._backend.query(
            operation="get_cycle",
            tenant_id=tenant_id,
            resource_id=handle.cycle_id,
            child_id=None,
            filters={},
        )
        if not view.get("ok", True) and view.get("error"):
            return AdapterOutcome(
                kind="FAILED",
                reason_code=str((view.get("error") or {}).get("code") or "CYCLE_QUERY_FAILED"),
                cycle_id=handle.cycle_id,
            )
        phase = str(view.get("phase") or handle.phase)
        return outcome_from_cycle_view(cycle_id=handle.cycle_id, phase=phase, status=view.get("status"))


__all__ = [
    "AdapterHandle",
    "ObjectiveCycleExecutorAdapter",
    "build_admit_body_from_capsule",
    "outcome_from_cycle_view",
]
