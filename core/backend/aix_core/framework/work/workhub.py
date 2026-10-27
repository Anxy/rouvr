"""F1-P5: WorkHub bridge — read-only external ref resolution.

Never mutates WorkHub lifecycle. Uses WorkHubBoundary for authority checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.framework.integration.workhub.boundary import (
    WorkHubBoundary,
    WorkHubOperation,
)
from backend.aix_core.framework.work.models import ExternalWorkRef, Work
from backend.aix_core.runtime.objective.contracts import ObjectiveEnvelopeV1


@dataclass(frozen=True)
class WorkHubReadContract:
    """Read-only projection of WorkHub task fields for framework consumers."""

    system: str
    external_task_id: str
    project_id: str
    correlation_id: str
    read_contract_endpoint: str | None
    authority_allowed: bool
    authority_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "external_task_id": self.external_task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "read_contract_endpoint": self.read_contract_endpoint,
            "authority_allowed": self.authority_allowed,
            "authority_reason": self.authority_reason,
        }


class WorkHubTaskBridge:
    """F1-AC-05: resolve ExternalWorkRef without lifecycle mutation."""

    DEFAULT_READ_ENDPOINT = "/workhub/v1/tasks/{task_id}/contract"

    @classmethod
    def resolve_external_ref(
        cls,
        ref: ExternalWorkRef | None,
        *,
        lifecycle_writes_enabled: bool = False,
    ) -> WorkHubReadContract | None:
        if ref is None:
            return None
        decision = WorkHubBoundary.evaluate_operation(
            WorkHubOperation.TASK_READ,
            lifecycle_writes_enabled=lifecycle_writes_enabled,
        )
        endpoint = ref.read_contract_endpoint or cls.DEFAULT_READ_ENDPOINT.format(
            task_id=ref.external_task_id
        )
        return WorkHubReadContract(
            system=ref.system,
            external_task_id=ref.external_task_id,
            project_id=ref.project_id,
            correlation_id=ref.correlation_id,
            read_contract_endpoint=endpoint,
            authority_allowed=bool(decision.allowed),
            authority_reason=str(decision.reason),
        )

    @classmethod
    def from_envelope(cls, envelope: ObjectiveEnvelopeV1) -> WorkHubReadContract:
        ref = ExternalWorkRef(
            system="workhub",
            external_task_id=envelope.workhub_task_ref,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
        )
        resolved = cls.resolve_external_ref(ref)
        assert resolved is not None
        return resolved

    @classmethod
    def from_work(cls, work: Work) -> WorkHubReadContract | None:
        return cls.resolve_external_ref(work.external_ref)

    @classmethod
    def assert_read_only(cls) -> None:
        """Fail closed if a write operation would be authorized without flags."""
        for op in (
            WorkHubOperation.TASK_CREATE,
            WorkHubOperation.STATE_TRANSITION,
            WorkHubOperation.TASK_CLOSE,
        ):
            decision = WorkHubBoundary.evaluate_operation(op)
            if decision.allowed:
                raise RuntimeError(
                    f"WorkHub bridge must not authorize lifecycle write {op.value} by default"
                )


# PACKET-CODE-MAP alias
WorkHubBridge = WorkHubTaskBridge


__all__ = [
    "WorkHubReadContract",
    "WorkHubTaskBridge",
    "WorkHubBridge",
]
