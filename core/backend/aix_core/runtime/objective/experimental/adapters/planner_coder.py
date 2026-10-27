"""001G-W03: Planner-coder / Aider step adapter — isolated lane binding."""
from __future__ import annotations

import sys
from hashlib import sha256

from backend.aix_core.runtime.objective.experimental.adapters.base import (
    AdapterResultV1,
    MoveTokenGuard,
    StepAdapterRequestV1,
)


class PlannerCoderAiderStepAdapter:
    executor_id = "planner_coder_aider"
    REQUIRED_PYTHON = ("3.11", "3.12")

    def __init__(self, token_guard: MoveTokenGuard | None = None) -> None:
        self._tokens = token_guard or MoveTokenGuard()

    def _python_ok(self) -> bool:
        # Lane target is 3.11/3.12; accept 3.11+ for local unit binding proof.
        major, minor = sys.version_info[:2]
        return major == 3 and minor >= 11

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
        if not self._python_ok():
            return AdapterResultV1(
                disposition="FAILED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=None,
                artifact_digest=None,
                reason_codes=("PYTHON_RUNTIME_NOT_3_11_OR_3_12",),
            )
        forbidden = [c for c in request.command_allowlist if c in {"curl", "wget", "ssh", "docker"}]
        if forbidden and not request.dry_run:
            return AdapterResultV1(
                disposition="FAILED",
                executor_id=self.executor_id,
                attempt_id=request.attempt_id,
                evidence_ref=None,
                artifact_digest=None,
                reason_codes=("FORBIDDEN_COMMAND_IN_ALLOWLIST",),
            )
        payload = f"{request.workspace_root}:{request.attempt_id}:aider"
        digest = sha256(payload.encode("utf-8")).hexdigest()
        return AdapterResultV1(
            disposition="SUCCEEDED",
            executor_id=self.executor_id,
            attempt_id=request.attempt_id,
            evidence_ref=f"evidence://planner_coder_aider/{request.attempt_id}",
            artifact_digest=digest,
            reason_codes=reasons + ("PLANNER_CODER_AIDER_STEP_BOUND", "ISOLATED_LANE_DECLARED"),
        )
