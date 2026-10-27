"""EX2-S009: Problem-loop orchestration (Packet 7 §1–2).

One step API wiring:
  next-actionable → capsule → (live) dispatch → verify → distill → checkpoint
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import AdapterOutcome, DecisionVerdict
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.api_backend import ObjectiveCycleApiBackend

ProblemStepMode = Literal[
    "tick_dry",
    "tick_live",
    "complete",
    "accept_problem",
    "rework_problem",
    "complete_problem",
    "archive_problem",
]
WI_TERMINAL = frozenset({"VERIFIED", "ARCHIVED", "FAILED", "BLOCKED"})
WI_SUCCESS_TERMINAL = frozenset({"VERIFIED", "ARCHIVED"})


class ProblemLoopService(Protocol):
    async def next_actionable(self, *, tenant_id: str, problem_id: str) -> list[str]: ...

    async def get_projection(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> dict[str, Any] | None: ...

    async def append_and_project(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> Any: ...

    async def build_capsule(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        max_tokens: int = 4000,
        repository: dict[str, Any] | None = None,
    ) -> Any: ...

    async def live_dispatch(self, **kwargs: Any) -> dict[str, Any]: ...

    async def apply_verification(self, **kwargs: Any) -> dict[str, Any]: ...

    async def distill_learning(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    async def capture_checkpoint(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        repository_state: dict[str, Any],
        open_proposals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

    async def resume(self, *, tenant_id: str, problem_id: str) -> Any: ...

    async def raise_proposal(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass
class ProblemStepResult:
    status: str
    problem_id: str
    mode: str
    work_item_id: str | None = None
    next_actionable: list[str] = field(default_factory=list)
    capsule_content_sha256: str | None = None
    dispatch: dict[str, Any] | None = None
    live: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    distilled: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None
    problem_ready_for_review: bool = False
    problem_status: str | None = None
    problem_transitions: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "problem_id": self.problem_id,
            "mode": self.mode,
            "work_item_id": self.work_item_id,
            "next_actionable": list(self.next_actionable),
            "capsule_content_sha256": self.capsule_content_sha256,
            "dispatch": self.dispatch,
            "live": self.live,
            "verification": self.verification,
            "distilled": list(self.distilled),
            "checkpoint": self.checkpoint,
            "proposal": self.proposal,
            "problem_ready_for_review": self.problem_ready_for_review,
            "problem_status": self.problem_status,
            "problem_transitions": list(self.problem_transitions),
            "notes": list(self.notes),
        }


async def _transition_wi(
    service: ProblemLoopService,
    *,
    tenant_id: str,
    work_item_id: str,
    status: str,
    command_id: str,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wi = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id
    )
    if wi is None:
        raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")
    payload = dict(wi.get("payload") or {})
    payload["status"] = status
    if wi.get("parent_id"):
        payload.setdefault("parent_id", wi["parent_id"])
    if extra_payload:
        payload.update(extra_payload)
    event = await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="work_item",
        aggregate_id=work_item_id,
        expected_sequence_number=int(wi["sequence_number"]) + 1,
        event_type=f"WI_{status}",
        command_id=command_id,
        payload=payload,
    )
    return {
        "id": event.id,
        "sequence_number": event.sequence_number,
        "event_type": event.event_type,
        "replayed": event.replayed,
        "status": status,
    }


async def _transition_problem(
    service: ProblemLoopService,
    *,
    tenant_id: str,
    problem_id: str,
    status: str,
    command_id: str,
    from_status: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    problem = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
    )
    if problem is None:
        raise ExecutionKernelRepositoryError(f"problem not found: {problem_id}")
    current = str(problem.get("status") or "")
    if from_status is not None and current != from_status:
        raise ExecutionKernelRepositoryError(
            f"problem status mismatch: expected {from_status}, got {current}"
        )
    payload = dict(problem.get("payload") or {})
    payload["status"] = status
    if problem.get("parent_id"):
        payload.setdefault("parent_id", problem["parent_id"])
    if extra_payload:
        payload.update(extra_payload)
    event = await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="problem",
        aggregate_id=problem_id,
        expected_sequence_number=int(problem["sequence_number"]) + 1,
        event_type=f"PROBLEM_{status}",
        command_id=command_id,
        payload=payload,
    )
    return {
        "id": event.id,
        "sequence_number": event.sequence_number,
        "event_type": event.event_type,
        "replayed": event.replayed,
        "from_status": current,
        "status": status,
    }


async def _ensure_problem_review(
    service: ProblemLoopService,
    *,
    tenant_id: str,
    problem_id: str,
    idempotency_key: str,
    result: ProblemStepResult,
) -> None:
    """When all WIs succeed: EXECUTING → REVIEW (Packet 3 §3)."""
    ready, _ = await _problem_review_state(service, tenant_id=tenant_id, problem_id=problem_id)
    result.problem_ready_for_review = ready
    problem = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
    )
    if problem is None:
        return
    result.problem_status = str(problem.get("status") or "")
    if not ready:
        return
    if result.problem_status == "EXECUTING":
        transition = await _transition_problem(
            service,
            tenant_id=tenant_id,
            problem_id=problem_id,
            status="REVIEW",
            from_status="EXECUTING",
            command_id=f"{idempotency_key}:problem_review",
        )
        result.problem_transitions.append(transition)
        result.problem_status = "REVIEW"
        result.notes.append("problem EXECUTING -> REVIEW (all work items success-terminal)")
    elif result.problem_status == "REVIEW":
        result.notes.append("problem already in REVIEW")


async def _problem_review_state(
    service: ProblemLoopService, *, tenant_id: str, problem_id: str
) -> tuple[bool, list[dict[str, Any]]]:
    resume = await service.resume(tenant_id=tenant_id, problem_id=problem_id)
    items = list(resume.work_items or [])
    if not items:
        return False, items
    if all(str(i.get("status") or "") in WI_SUCCESS_TERMINAL for i in items):
        return True, items
    return False, items


async def run_problem_step(
    service: ProblemLoopService,
    *,
    tenant_id: str,
    actor_id: str,
    problem_id: str,
    mode: ProblemStepMode,
    repository_state: dict[str, Any],
    idempotency_key: str,
    max_tokens: int = 4000,
    timeout_budget_seconds: int = 600,
    distill: bool = True,
    lesson_summary: str | None = None,
    promote_pattern: bool = False,
    # live
    cycle_backend: ObjectiveCycleApiBackend | None = None,
    workhub_task_id: str | None = None,
    task_class: str = "bounded_repair",
    candidate_sha: str | None = None,
    repository_id: str | None = None,
    definition_of_done: list[str] | None = None,
    allowed_files: list[str] | None = None,
    project_id: str | None = None,
    budgets: dict[str, int] | None = None,
    repository: dict[str, Any] | None = None,
    # complete / dry verify
    work_item_id: str | None = None,
    outcome: AdapterOutcome | None = None,
    decision: DecisionVerdict | None = None,
    waiver_id: str | None = None,
    waiver_rationale: str | None = None,
    waiver_approver_actor_id: str | None = None,
    waiver_approver_role: str | None = None,
    waiver_expires_at: str | None = None,
    required_evidence: bool = True,
    dry_auto_verify: bool = True,
    proposal_raise: dict[str, Any] | None = None,
) -> ProblemStepResult:
    if not str(idempotency_key or "").strip():
        raise ExecutionKernelRepositoryError("idempotency_key is required")
    key = str(idempotency_key).strip()
    result = ProblemStepResult(status="STARTED", problem_id=problem_id, mode=mode)

    if mode == "accept_problem":
        return await _run_accept_problem(
            service,
            result=result,
            tenant_id=tenant_id,
            problem_id=problem_id,
            repository_state=repository_state,
            idempotency_key=key,
        )
    if mode == "rework_problem":
        return await _run_rework_problem(
            service,
            result=result,
            tenant_id=tenant_id,
            problem_id=problem_id,
            repository_state=repository_state,
            idempotency_key=key,
        )
    if mode == "complete_problem":
        return await _run_problem_edge(
            service,
            result=result,
            tenant_id=tenant_id,
            problem_id=problem_id,
            repository_state=repository_state,
            idempotency_key=key,
            from_status="VERIFIED",
            to_status="COMPLETED",
            result_status="PROBLEM_COMPLETED",
            note="problem VERIFIED -> COMPLETED",
            command_suffix="problem_completed",
        )
    if mode == "archive_problem":
        return await _run_problem_edge(
            service,
            result=result,
            tenant_id=tenant_id,
            problem_id=problem_id,
            repository_state=repository_state,
            idempotency_key=key,
            from_status="COMPLETED",
            to_status="ARCHIVED",
            result_status="PROBLEM_ARCHIVED",
            note="problem COMPLETED -> ARCHIVED",
            command_suffix="problem_archived",
        )

    if mode == "complete":
        return await _run_complete(
            service,
            result=result,
            tenant_id=tenant_id,
            problem_id=problem_id,
            work_item_id=work_item_id,
            outcome=outcome,
            decision=decision,
            waiver_id=waiver_id,
            waiver_rationale=waiver_rationale,
            waiver_approver_actor_id=waiver_approver_actor_id,
            waiver_approver_role=waiver_approver_role,
            waiver_expires_at=waiver_expires_at,
            required_evidence=required_evidence,
            distill=distill,
            lesson_summary=lesson_summary,
            promote_pattern=promote_pattern,
            repository_state=repository_state,
            idempotency_key=key,
        )

    actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
    result.next_actionable = list(actionable)
    ready, _items = await _problem_review_state(service, tenant_id=tenant_id, problem_id=problem_id)
    result.problem_ready_for_review = ready

    if not actionable:
        await _ensure_problem_review(
            service, tenant_id=tenant_id, problem_id=problem_id, idempotency_key=key, result=result
        )
        result.checkpoint = await service.capture_checkpoint(
            tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
        )
        result.status = "PROBLEM_IN_REVIEW" if result.problem_status == "REVIEW" else (
            "PROBLEM_READY_FOR_REVIEW" if ready else "NO_ACTIONABLE"
        )
        result.notes.append("no pending work item with satisfied dependencies")
        return result

    selected = actionable[0]
    result.work_item_id = selected

    # Capsule + DISPATCHED
    capsule = await service.build_capsule(
        tenant_id=tenant_id,
        work_item_id=selected,
        max_tokens=max_tokens,
        repository=repository,
    )
    result.capsule_content_sha256 = capsule.capsule_content_sha256
    dispatched = await _transition_wi(
        service,
        tenant_id=tenant_id,
        work_item_id=selected,
        status="DISPATCHED",
        command_id=f"{key}:dispatched",
        extra_payload={"capsule_content_sha256": capsule.capsule_content_sha256},
    )
    result.dispatch = {
        "work_item_id": selected,
        "capsule_content_sha256": capsule.capsule_content_sha256,
        "event": dispatched,
    }

    if proposal_raise:
        result.proposal = await service.raise_proposal(
            tenant_id=tenant_id,
            problem_id=problem_id,
            reason=str(proposal_raise.get("reason") or ""),
            command_id=f"{key}:proposal",
            level=int(proposal_raise.get("level") or 2),
            work_item_id=selected,
            affected_capabilities=list(proposal_raise.get("affected_capabilities") or []),
            estimated_work_item_delta=int(proposal_raise.get("estimated_work_item_delta") or 0),
            risk=str(proposal_raise.get("risk") or "low"),
            proposal_id=proposal_raise.get("proposal_id"),
        )
        result.notes.append("proposal raised during problem step")

    if mode == "tick_live":
        if cycle_backend is None:
            raise ExecutionKernelRepositoryError("cycle_backend required for tick_live")
        if not str(workhub_task_id or "").strip():
            raise ExecutionKernelRepositoryError("workhub_task_id required for tick_live")
        wi = await service.get_projection(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=selected
        )
        if wi is None:
            raise ExecutionKernelRepositoryError(f"work_item not found: {selected}")
        live = await service.live_dispatch(
            tenant_id=tenant_id,
            actor_id=actor_id,
            work_item_id=selected,
            backend=cycle_backend,
            workhub_task_id=str(workhub_task_id),
            idempotency_key=f"{key}:live",
            max_tokens=max_tokens,
            timeout_budget_seconds=timeout_budget_seconds,
            task_class=task_class,
            candidate_sha=candidate_sha,
            repository_id=repository_id,
            definition_of_done=definition_of_done,
            allowed_files=allowed_files,
            project_id=project_id,
            budgets=budgets,
            expected_sequence_number=int(wi["sequence_number"]) + 1,
            mark_in_progress=True,
            repository=repository,
        )
        result.live = live
        result.checkpoint = await service.capture_checkpoint(
            tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
        )
        outcome_kind = (live.get("outcome") or {}).get("kind")
        if outcome_kind == "BLOCKED":
            result.status = "AWAITING_APPROVAL"
            result.notes.append("live cycle started; human/planner approval required before verify")
        elif outcome_kind in {"SUCCEEDED", "FAILED", "TIMED_OUT"}:
            result.status = f"LIVE_{outcome_kind}"
        else:
            result.status = "LIVE_IN_FLIGHT"
        result.next_actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
        return result

    # tick_dry — in-process adapter simulation without objective_cycle backend
    await _transition_wi(
        service,
        tenant_id=tenant_id,
        work_item_id=selected,
        status="IN_PROGRESS",
        command_id=f"{key}:in_progress",
    )

    if dry_auto_verify:
        verify_outcome = outcome or AdapterOutcome(
            kind="SUCCEEDED",
            evidence_refs=(f"dry:{selected}",),
            cycle_id=None,
        )
        verify_decision: DecisionVerdict = decision or "ACCEPT"
        wi = await service.get_projection(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=selected
        )
        assert wi is not None
        verified = await service.apply_verification(
            tenant_id=tenant_id,
            work_item_id=selected,
            outcome=verify_outcome,
            decision=verify_decision,
            expected_sequence_number=int(wi["sequence_number"]) + 1,
            command_id=f"{key}:verify",
            waiver_id=waiver_id,
            required_evidence=required_evidence,
        )
        result.verification = verified.get("verification")
        if distill and (result.verification or {}).get("work_item_status") in {"VERIFIED", "FAILED"}:
            result.distilled = await service.distill_learning(
                tenant_id=tenant_id,
                work_item_id=selected,
                decision=verify_decision,
                evidence_refs=list(verify_outcome.evidence_refs),
                lesson_summary=lesson_summary,
                promote_pattern=promote_pattern,
                command_id_prefix=f"{key}:distill",
            )
        result.status = str((result.verification or {}).get("work_item_status") or "DRY_DONE")
    else:
        result.status = "IN_PROGRESS"
        result.notes.append("dry_auto_verify=false; left work item IN_PROGRESS")

    result.checkpoint = await service.capture_checkpoint(
        tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
    )
    result.next_actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
    if not result.next_actionable:
        await _ensure_problem_review(
            service, tenant_id=tenant_id, problem_id=problem_id, idempotency_key=key, result=result
        )
        # Refresh checkpoint after problem transition.
        if result.problem_transitions:
            result.checkpoint = await service.capture_checkpoint(
                tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
            )
        if result.problem_status == "REVIEW":
            result.status = "PROBLEM_IN_REVIEW"
        elif result.problem_ready_for_review:
            result.status = "PROBLEM_READY_FOR_REVIEW"
    else:
        problem = await service.get_projection(
            tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
        )
        result.problem_status = str((problem or {}).get("status") or "") or None
    return result


async def _run_accept_problem(
    service: ProblemLoopService,
    *,
    result: ProblemStepResult,
    tenant_id: str,
    problem_id: str,
    repository_state: dict[str, Any],
    idempotency_key: str,
) -> ProblemStepResult:
    """REVIEW → VERIFIED (after ensuring REVIEW if all WIs already success-terminal)."""
    await _ensure_problem_review(
        service,
        tenant_id=tenant_id,
        problem_id=problem_id,
        idempotency_key=idempotency_key,
        result=result,
    )
    if result.problem_status != "REVIEW":
        raise ExecutionKernelRepositoryError(
            f"problem cannot be accepted from status={result.problem_status!r} "
            "(need all work items VERIFIED/ARCHIVED and problem in REVIEW)"
        )
    transition = await _transition_problem(
        service,
        tenant_id=tenant_id,
        problem_id=problem_id,
        status="VERIFIED",
        from_status="REVIEW",
        command_id=f"{idempotency_key}:problem_verified",
    )
    result.problem_transitions.append(transition)
    result.problem_status = "VERIFIED"
    result.checkpoint = await service.capture_checkpoint(
        tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
    )
    result.next_actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
    result.status = "PROBLEM_VERIFIED"
    result.notes.append("problem REVIEW -> VERIFIED")
    return result


async def _run_rework_problem(
    service: ProblemLoopService,
    *,
    result: ProblemStepResult,
    tenant_id: str,
    problem_id: str,
    repository_state: dict[str, Any],
    idempotency_key: str,
) -> ProblemStepResult:
    """REVIEW → EXECUTING (Packet 3 rollback edge)."""
    return await _run_problem_edge(
        service,
        result=result,
        tenant_id=tenant_id,
        problem_id=problem_id,
        repository_state=repository_state,
        idempotency_key=idempotency_key,
        from_status="REVIEW",
        to_status="EXECUTING",
        result_status="PROBLEM_EXECUTING",
        note="problem REVIEW -> EXECUTING (rework)",
        command_suffix="problem_rework",
    )


async def _run_problem_edge(
    service: ProblemLoopService,
    *,
    result: ProblemStepResult,
    tenant_id: str,
    problem_id: str,
    repository_state: dict[str, Any],
    idempotency_key: str,
    from_status: str,
    to_status: str,
    result_status: str,
    note: str,
    command_suffix: str,
) -> ProblemStepResult:
    problem = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
    )
    if problem is None:
        raise ExecutionKernelRepositoryError(f"problem not found: {problem_id}")
    result.problem_status = str(problem.get("status") or "")
    if result.problem_status != from_status:
        raise ExecutionKernelRepositoryError(
            f"problem {to_status.lower()} requires {from_status}, got {result.problem_status!r}"
        )
    transition = await _transition_problem(
        service,
        tenant_id=tenant_id,
        problem_id=problem_id,
        status=to_status,
        from_status=from_status,
        command_id=f"{idempotency_key}:{command_suffix}",
    )
    result.problem_transitions.append(transition)
    result.problem_status = to_status
    # Terminal problems still get a final checkpoint snapshot for audit/resume history.
    result.checkpoint = await service.capture_checkpoint(
        tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
    )
    result.next_actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
    result.status = result_status
    result.notes.append(note)
    return result


async def _run_complete(
    service: ProblemLoopService,
    *,
    result: ProblemStepResult,
    tenant_id: str,
    problem_id: str,
    work_item_id: str | None,
    outcome: AdapterOutcome | None,
    decision: DecisionVerdict | None,
    waiver_id: str | None,
    waiver_rationale: str | None = None,
    waiver_approver_actor_id: str | None = None,
    waiver_approver_role: str | None = None,
    waiver_expires_at: str | None = None,
    required_evidence: bool = True,
    distill: bool = True,
    lesson_summary: str | None,
    promote_pattern: bool,
    repository_state: dict[str, Any],
    idempotency_key: str,
) -> ProblemStepResult:
    if not work_item_id:
        raise ExecutionKernelRepositoryError("work_item_id required for complete mode")
    if outcome is None or decision is None:
        raise ExecutionKernelRepositoryError("outcome and decision required for complete mode")
    result.work_item_id = work_item_id
    wi = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id
    )
    if wi is None:
        raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")
    verified = await service.apply_verification(
        tenant_id=tenant_id,
        work_item_id=work_item_id,
        outcome=outcome,
        decision=decision,
        expected_sequence_number=int(wi["sequence_number"]) + 1,
        command_id=f"{idempotency_key}:verify",
        waiver_id=waiver_id,
        waiver_rationale=waiver_rationale,
        waiver_approver_actor_id=waiver_approver_actor_id,
        waiver_approver_role=waiver_approver_role,
        waiver_expires_at=waiver_expires_at,
        required_evidence=required_evidence,
    )
    result.verification = verified.get("verification")
    if distill and (result.verification or {}).get("work_item_status") in {"VERIFIED", "FAILED"}:
        result.distilled = await service.distill_learning(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            decision=decision,
            evidence_refs=list(outcome.evidence_refs),
            lesson_summary=lesson_summary,
            promote_pattern=promote_pattern,
            command_id_prefix=f"{idempotency_key}:distill",
        )
    result.checkpoint = await service.capture_checkpoint(
        tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
    )
    result.next_actionable = await service.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
    result.status = str((result.verification or {}).get("work_item_status") or "COMPLETE")
    if not result.next_actionable:
        await _ensure_problem_review(
            service,
            tenant_id=tenant_id,
            problem_id=problem_id,
            idempotency_key=idempotency_key,
            result=result,
        )
        if result.problem_transitions:
            result.checkpoint = await service.capture_checkpoint(
                tenant_id=tenant_id, problem_id=problem_id, repository_state=repository_state
            )
        if result.problem_status == "REVIEW":
            result.status = "PROBLEM_IN_REVIEW"
        elif result.problem_ready_for_review:
            result.status = "PROBLEM_READY_FOR_REVIEW"
    else:
        problem = await service.get_projection(
            tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
        )
        result.problem_status = str((problem or {}).get("status") or "") or None
        ready, _ = await _problem_review_state(service, tenant_id=tenant_id, problem_id=problem_id)
        result.problem_ready_for_review = ready
    return result


__all__ = [
    "ProblemStepMode",
    "ProblemStepResult",
    "run_problem_step",
    "WI_TERMINAL",
    "WI_SUCCESS_TERMINAL",
]
