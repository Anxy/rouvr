"""001G-W04: DevExecutor step adapter — tool manifest, lease, timeout, cancel, evidence."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from backend.aix_core.runtime.objective.experimental.adapters.base import (
    AdapterResultV1,
    MoveTokenGuard,
    StepAdapterRequestV1,
)


@dataclass(frozen=True)
class DevExecutorLeaseV1:
    lease_id: str
    attempt_id: str
    timeout_seconds: int
    cancelled: bool = False


class DevExecutorStepAdapter:
    executor_id = "devexecutor"
    DEFAULT_TOOL_MANIFEST = frozenset({"read_file", "apply_patch", "run_tests", "git_diff"})

    def __init__(self, token_guard: MoveTokenGuard | None = None) -> None:
        self._tokens = token_guard or MoveTokenGuard()
        self._leases: dict[str, DevExecutorLeaseV1] = {}

    def execute(
        self,
        request: StepAdapterRequestV1,
        *,
        tools_requested: tuple[str, ...] = ("read_file", "git_diff"),
        cancel: bool = False,
    ) -> AdapterResultV1:
        if request.move_token.executor_id != self.executor_id:
            return AdapterResultV1(
                disposition="DENIED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=None,
                artifact_digest=None,
                reason_codes=("EXECUTOR_MISMATCH",),
            )
        ok, reasons = self._tokens.authorize(request.move_token)
        if not ok:
            return AdapterResultV1(
                disposition="DENIED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=None,
                artifact_digest=None,
                reason_codes=reasons,
            )
        unknown = [t for t in tools_requested if t not in self.DEFAULT_TOOL_MANIFEST]
        if unknown:
            return AdapterResultV1(
                disposition="FAILED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=None,
                artifact_digest=None,
                reason_codes=("TOOL_NOT_IN_MANIFEST",),
            )
        if request.timeout_seconds <= 0:
            return AdapterResultV1(
                disposition="FAILED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=None,
                artifact_digest=None,
                reason_codes=("TIMEOUT_INVALID",),
            )
        lease = DevExecutorLeaseV1(
            lease_id=f"lease:{request.attempt_id}",
            attempt_id=request.attempt_id,
            timeout_seconds=request.timeout_seconds,
            cancelled=cancel,
        )
        self._leases[lease.lease_id] = lease
        if cancel:
            return AdapterResultV1(
                disposition="FAILED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=f"evidence://devexecutor/{request.attempt_id}/cancelled",
                artifact_digest=None,
                reason_codes=reasons + ("CANCELLED_BEFORE_EXECUTION",),
            )
        payload = f"{lease.lease_id}:{','.join(tools_requested)}"
        digest = sha256(payload.encode("utf-8")).hexdigest()
        return AdapterResultV1(
            disposition="SUCCEEDED",
            executor_id=self.executor_id,
            attempt_id=request.attempt_id,
            evidence_ref=f"evidence://devexecutor/{request.attempt_id}",
            artifact_digest=digest,
            reason_codes=reasons + ("DEVEXECUTOR_STEP_BOUND", "LEASE_ISSUED"),
        )
