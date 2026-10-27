"""AIX Objective Execution Service.

Extracts tool execution invocation, diff validation, mutation summary aggregation,
and step attempt journaling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.aix_core.framework.execution.port import ExecuteStepRequest, ExecutionPort
from backend.aix_core.framework.execution.real_execute_adapter import RealExecuteAdapter
from backend.aix_core.runtime.boundaries.executor import (
    log_execution_completed,
    log_execution_started,
)
from backend.aix_core.runtime.objective.contracts import StepContractV1
from backend.aix_core.runtime.objective.provenance.validators import validate_execute_diff
from backend.aix_core.runtime.objective.step_attempt import build_step_attempt
from backend.aix_core.runtime.verification.mutation_summary import build_mutation_summary

logger = logging.getLogger(__name__)


@dataclass
class StepExecutionResult:
    step_id: str
    ok: bool
    execute_result: dict[str, Any]
    mutation_summary: dict[str, Any] | None
    attempt_record: dict[str, Any] | None
    error: str | None = None


class ExecutionService:
    """Coordinates tool execution, diff validation, and mutation tracking."""

    def __init__(self, execution_port: ExecutionPort | None = None) -> None:
        self.execution_port = execution_port or RealExecuteAdapter()

    async def execute_step(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        step: StepContractV1,
        workspace_path: Path,
        caller_auth: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> StepExecutionResult:
        """Executes a single step via the execution port."""
        log_execution_started(
            cycle_id=cycle_id,
            step_id=step.id,
            schema_ref=step.schema_ref,
        )

        request = ExecuteStepRequest(
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            step_id=step.id,
            schema_ref=step.schema_ref,
            target=step.target,
            target_scope=list(step.target_scope),
            validation_commands=list(step.validation_commands),
            workspace_path=str(workspace_path),
            caller_auth=caller_auth,
            context=context or {},
        )

        raw_result = await self.execution_port.execute_step(request)
        is_ok = bool(raw_result.get("ok", False))

        mutation_summary = None
        attempt_record = None
        if is_ok:
            mutation_summary = build_mutation_summary(
                workspace_path=workspace_path,
                execute_result=raw_result,
            )
            attempt_record = build_step_attempt(
                step_id=step.id,
                result=raw_result,
            )

        log_execution_completed(
            cycle_id=cycle_id,
            step_id=step.id,
            ok=is_ok,
            reason=raw_result.get("reason"),
        )

        return StepExecutionResult(
            step_id=step.id,
            ok=is_ok,
            execute_result=raw_result,
            mutation_summary=mutation_summary,
            attempt_record=attempt_record,
            error=raw_result.get("error"),
        )
