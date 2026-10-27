"""Strangler adapter for the canonical legacy execution function."""

from __future__ import annotations

from backend.aix_core.framework.execution.port import ExecuteStepRequest
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult, run_real_execute


class RealExecuteAdapter:
    """Delegate exactly once without translating results or exceptions."""

    async def execute_step(self, request: ExecuteStepRequest) -> RealExecuteResult:
        return await run_real_execute(**request.to_legacy_kwargs())


__all__ = ["RealExecuteAdapter"]
