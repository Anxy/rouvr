from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueueBinding:
    namespace: str
    plan_queue: str
    execution_queue: str
    verification_queue: str


def build_queue_binding(settings: object) -> QueueBinding:
    # Wave 2: one live queue. Plan/verification queues are declared but no
    # worker polls them and no call site routes to them; enabling separate
    # queues without a worker per queue strands work. Collapsed unless
    # TEMPORAL_SEPARATE_QUEUES_ENABLED is explicitly true (with workers).
    separate = bool(getattr(settings, "TEMPORAL_SEPARATE_QUEUES_ENABLED", False))
    execution = str(getattr(settings, "TEMPORAL_TASK_QUEUE_EXECUTION", "aix.execution.current"))
    if not separate:
        return QueueBinding(
            namespace=str(getattr(settings, "TEMPORAL_NAMESPACE", "hyeve-aix")),
            plan_queue=execution,
            execution_queue=execution,
            verification_queue=execution,
        )
    return QueueBinding(
        namespace=str(getattr(settings, "TEMPORAL_NAMESPACE", "hyeve-aix")),
        plan_queue=str(getattr(settings, "TEMPORAL_TASK_QUEUE_PLAN", "aix.plan.current")),
        execution_queue=execution,
        verification_queue=str(getattr(settings, "TEMPORAL_TASK_QUEUE_VERIFICATION", "aix.verification.current")),
    )


def select_task_queue(
    *,
    workflow_kind: str,
    binding: QueueBinding,
    priority: str | None = None,
) -> str:
    normalized_kind = workflow_kind.strip().lower()
    normalized_priority = (priority or "").strip().upper()

    if normalized_kind in {"plan", "planworkflow"}:
        base = binding.plan_queue
    elif normalized_kind in {"execution", "executionworkflow"}:
        base = binding.execution_queue
    elif normalized_kind in {"verification", "verificationworkflow"}:
        base = binding.verification_queue
    else:
        raise ValueError(f"Unsupported workflow kind: {workflow_kind}")

    if normalized_priority in {"P0", "P1"}:
        return f"{base}.priority"
    return base
