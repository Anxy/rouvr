"""001G-W02: Orchestrator V2 step adapter — map one V2-bounded run to one attempt."""
from __future__ import annotations

from hashlib import sha256

from backend.aix_core.runtime.objective.experimental.adapters.base import (
    AdapterResultV1,
    MoveTokenGuard,
    StepAdapterRequestV1,
)


class OrchestratorV2StepAdapter:
    executor_id = "orchestrator_v2"

    def __init__(self, token_guard: MoveTokenGuard | None = None) -> None:
        self._tokens = token_guard or MoveTokenGuard()

    def execute(self, request: StepAdapterRequestV1) -> AdapterResultV1:
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
        if request.network_allowed:
            return AdapterResultV1(
                disposition="FAILED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=None,
                artifact_digest=None,
                reason_codes=("NETWORK_NOT_PERMITTED_IN_V1_ADAPTER",),
            )
        # Dry-run bounded simulation of one Orchestrator V2 step attempt.
        payload = f"{request.cycle_id}:{request.step_id}:{request.attempt_id}:orch_v2"
        digest = sha256(payload.encode("utf-8")).hexdigest()
        return AdapterResultV1(
            disposition="SUCCEEDED",
            executor_id=self.executor_id,
            attempt_id=request.attempt_id,
            evidence_ref=f"evidence://orchestrator_v2/{request.attempt_id}",
            artifact_digest=digest,
            reason_codes=reasons + ("ORCHESTRATOR_V2_STEP_BOUND",),
        )
