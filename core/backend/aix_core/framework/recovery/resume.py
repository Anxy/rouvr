"""AIX Framework Idempotent Resume Protocol.

Sprint: F6 Recovery Framework (F6-P3)
Grounded in: durable_api_backend.py resume control paths, idempotent command processing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.aix_core.framework.recovery.checkpoint import (
    CheckpointEventV1,
    DurableCheckpointStore,
    ObjectiveCycleCheckpointStore,
)


class ResumeDisposition(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    IN_PROGRESS = "IN_PROGRESS"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ResumeRequest:
    """Strongly-typed resume request with deterministic idempotency key."""

    tenant_id: str
    cycle_id: str
    checkpoint_id: str
    action: str = "RESUME"
    command_id: str = ""
    expected_cycle_version: int | None = None
    approval_request_id: str | None = None

    def idempotency_key(self) -> str:
        ident = self.approval_request_id or self.command_id or "default"
        raw = f"{self.tenant_id}|{self.cycle_id}|{self.checkpoint_id}|{self.action}|{ident}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResumeResult:
    """Outcome of evaluating or applying a ResumeRequest."""

    resume_key: str
    disposition: ResumeDisposition
    checkpoint_id: str
    message: str = ""
    projection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_key": self.resume_key,
            "disposition": self.disposition.value,
            "checkpoint_id": self.checkpoint_id,
            "message": self.message,
            "projection": self.projection,
        }


class ResumeProtocol:
    """Idempotent cycle resumption protocol governing checkpoint rehydration and deduplication."""

    def __init__(
        self,
        checkpoint_store: DurableCheckpointStore | None = None,
        repository: Any = None,
    ) -> None:
        self._store = checkpoint_store or ObjectiveCycleCheckpointStore(repository=repository)
        self._applied_resumes: dict[str, ResumeResult] = {}
        self._in_flight: set[str] = set()

    async def resume(self, request: ResumeRequest) -> ResumeResult:
        key = request.idempotency_key()

        # 1. Deduplication check: return identical result if already applied
        if key in self._applied_resumes:
            prior = self._applied_resumes[key]
            return ResumeResult(
                resume_key=key,
                disposition=ResumeDisposition.ALREADY_APPLIED,
                checkpoint_id=prior.checkpoint_id,
                message=f"Resume already applied for key {key}",
                projection=prior.projection,
            )

        # 2. Concurrency lock: reject overlapping in-flight attempts
        if key in self._in_flight:
            return ResumeResult(
                resume_key=key,
                disposition=ResumeDisposition.IN_PROGRESS,
                checkpoint_id=request.checkpoint_id,
                message="Resume execution currently in progress for this key",
            )

        self._in_flight.add(key)
        try:
            # 3. Load & validate target checkpoint
            checkpoint = await self._store.by_id(request.tenant_id, request.checkpoint_id)
            if checkpoint is None:
                # Check if checkpoint exists under latest
                latest = await self._store.latest(request.tenant_id, request.cycle_id)
                if latest and latest.checkpoint_id == request.checkpoint_id:
                    checkpoint = latest
                else:
                    return ResumeResult(
                        resume_key=key,
                        disposition=ResumeDisposition.BLOCKED,
                        checkpoint_id=request.checkpoint_id,
                        message=f"Checkpoint {request.checkpoint_id!r} not found in store",
                    )

            if not checkpoint.validate_integrity():
                return ResumeResult(
                    resume_key=key,
                    disposition=ResumeDisposition.BLOCKED,
                    checkpoint_id=request.checkpoint_id,
                    message="Checkpoint integrity check failed: hash mismatch",
                )

            # 4. Version check: prevent resuming from stale cursor
            if (
                request.expected_cycle_version is not None
                and checkpoint.event_sequence != request.expected_cycle_version
            ):
                return ResumeResult(
                    resume_key=key,
                    disposition=ResumeDisposition.STALE,
                    checkpoint_id=request.checkpoint_id,
                    message=(
                        f"Cycle version mismatch: expected {request.expected_cycle_version}, "
                        f"found {checkpoint.event_sequence}"
                    ),
                    projection={"current_sequence": checkpoint.event_sequence},
                )

            # 5. Success: construct applied resume result
            result = ResumeResult(
                resume_key=key,
                disposition=ResumeDisposition.APPLIED,
                checkpoint_id=checkpoint.checkpoint_id,
                message="Cycle resumed successfully from checkpoint",
                projection={
                    "cycle_id": request.cycle_id,
                    "event_sequence": checkpoint.event_sequence,
                    "workflow": checkpoint.workflow,
                    "dispatcher": checkpoint.dispatcher,
                    "decision_state": checkpoint.decision_state,
                },
            )
            self._applied_resumes[key] = result
            return result
        finally:
            self._in_flight.discard(key)
