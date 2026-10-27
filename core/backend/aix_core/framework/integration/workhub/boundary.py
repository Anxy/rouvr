"""AIX Framework WorkHub Authority Boundary.

Sprint: F8 WorkHub Framework Boundary (F8-P1)
Grounded in: WORKHUB-AUTHORITY-BOUNDARY.md, agent_roles.py UNIVERSAL_PROHIBITIONS, config.py flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class WorkHubOperation(str, Enum):
    TASK_READ = "TASK_READ"
    CONTEXT_LOAD = "CONTEXT_LOAD"
    TASK_CREATE = "TASK_CREATE"
    EVIDENCE_ATTACH = "EVIDENCE_ATTACH"
    VERIFICATION_RECORD = "VERIFICATION_RECORD"
    STATE_TRANSITION = "STATE_TRANSITION"
    TASK_CLOSE = "TASK_CLOSE"


class WorkHubAuthority(str, Enum):
    WORKHUB_SYSTEM_OF_RECORD = "WORKHUB_SYSTEM_OF_RECORD"
    AIX_EXECUTION_ENGINE = "AIX_EXECUTION_ENGINE"


@dataclass(frozen=True)
class WorkHubAuthorityDecision:
    """Evaluation result for an attempted WorkHub operation."""

    operation: WorkHubOperation
    allowed: bool
    reason: str
    requires_flag: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "requires_flag": self.requires_flag,
        }


class WorkHubBoundary:
    """Enforces authority separation between AIX and WorkHub System of Record."""

    @classmethod
    def evaluate_operation(
        cls,
        operation: WorkHubOperation | str,
        *,
        create_task_flag: bool = False,
        mark_done_flag: bool = False,
        lifecycle_writes_enabled: bool = False,
    ) -> WorkHubAuthorityDecision:
        op = WorkHubOperation(operation) if isinstance(operation, str) else operation

        # 1. Read operations: AIX is authorized to read context
        if op in (WorkHubOperation.TASK_READ, WorkHubOperation.CONTEXT_LOAD):
            return WorkHubAuthorityDecision(
                operation=op,
                allowed=True,
                reason="WorkHub is System of Record; AIX is authorized to read task context.",
            )

        # 2. Task create (W12 exception or master lifecycle allow)
        if op == WorkHubOperation.TASK_CREATE:
            if create_task_flag or lifecycle_writes_enabled:
                return WorkHubAuthorityDecision(
                    operation=op,
                    allowed=True,
                    reason="Task creation authorized via explicit W12 / lifecycle flag.",
                    requires_flag=(
                        "AIX_ALLOW_WORKHUB_LIFECYCLE_WRITES"
                        if lifecycle_writes_enabled and not create_task_flag
                        else "OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK"
                    ),
                )
            return WorkHubAuthorityDecision(
                operation=op,
                allowed=False,
                reason="AIX cannot create WorkHub tasks autonomously without explicit W12 authorization.",
                requires_flag="OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK",
            )

        # 3. Evidence attach / Verification record
        if op in (WorkHubOperation.EVIDENCE_ATTACH, WorkHubOperation.VERIFICATION_RECORD):
            return WorkHubAuthorityDecision(
                operation=op,
                allowed=True,
                reason="AIX is authorized to attach execution evidence and verification results to WorkHub.",
            )

        # 4. State transition (W13 mark-done exception)
        if op == WorkHubOperation.STATE_TRANSITION:
            if mark_done_flag or lifecycle_writes_enabled:
                return WorkHubAuthorityDecision(
                    operation=op,
                    allowed=True,
                    reason="State transition authorized via W13 mark-done policy.",
                    requires_flag="OBJECTIVE_CYCLE_MARK_WORKHUB_DONE",
                )
            return WorkHubAuthorityDecision(
                operation=op,
                allowed=False,
                reason="WorkHub owns task lifecycle; AIX cannot mutate state without explicit W13 authorization.",
                requires_flag="OBJECTIVE_CYCLE_MARK_WORKHUB_DONE",
            )

        # 5. Task close / auto-accept: Hard fail-closed invariant INV-06
        if op == WorkHubOperation.TASK_CLOSE:
            return WorkHubAuthorityDecision(
                operation=op,
                allowed=False,
                reason="AIX is strictly prohibited from auto-closing or auto-accepting tasks (INV-06).",
            )

        return WorkHubAuthorityDecision(
            operation=op,
            allowed=False,
            reason=f"Unknown operation {op!r} denied fail-closed.",
        )
