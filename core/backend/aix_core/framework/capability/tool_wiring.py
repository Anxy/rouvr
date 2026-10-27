"""F2-P3 Tool wiring — LOCAL_TOOL adapter over live capability_executor.

Does not invent a second tool executor. Delegates to
``runtime.foundation.capability_executor.execute_action`` and
``intelligence.repository.repository_tools.RepositoryToolbox``.
"""

from __future__ import annotations

from typing import Any, Callable

from backend.aix_core.framework.capability.models import (
    CapabilityExecutionRequest,
    CapabilityExecutionResult,
    ProviderKind,
)
from backend.aix_core.framework.capability.provider import CapabilityProviderResolver
from backend.aix_core.runtime.foundation.capability_executor import execute_action
from backend.aix_core.runtime.objective.contracts import ActionObservationV1, ActionRequestV1


ExecuteActionFn = Callable[..., ActionObservationV1]


class ToolCapabilityAdapter:
    """CapabilityProvider for ProviderKind.LOCAL_TOOL (and HYEVE local-exec paths)."""

    def __init__(
        self,
        *,
        repository_id: str,
        settings: object | None = None,
        allowed_files: tuple[str, ...] = (),
        apply_edits: bool = True,
        action_budget_remaining: int = 8,
        mutation_budget_remaining: int = 8,
        approved: bool = True,
        step_approval_required: bool = False,
        toolbox: Any = None,
        execution_context: Any = None,
        execute_fn: ExecuteActionFn | None = None,
    ) -> None:
        self._repository_id = repository_id
        self._settings = settings
        self._allowed_files = allowed_files
        self._apply_edits = apply_edits
        self._action_budget_remaining = action_budget_remaining
        self._mutation_budget_remaining = mutation_budget_remaining
        self._approved = approved
        self._step_approval_required = step_approval_required
        self._toolbox = toolbox
        self._execution_context = execution_context
        self._execute_fn = execute_fn or execute_action

    async def execute(self, request: CapabilityExecutionRequest) -> CapabilityExecutionResult:
        return self.execute_sync(request)

    def execute_sync(self, request: CapabilityExecutionRequest) -> CapabilityExecutionResult:
        action = ActionRequestV1(
            action_id=request.request_id or "tool-action",
            capability=request.capability_id,
            operation=request.operation,
            arguments=dict(request.arguments or {}),
        )
        obs = self._execute_fn(
            action,
            repository_id=self._repository_id,
            settings=self._settings,
            allowed_files=self._allowed_files,
            apply_edits=self._apply_edits,
            action_budget_remaining=self._action_budget_remaining,
            mutation_budget_remaining=self._mutation_budget_remaining,
            approved=self._approved,
            step_approval_required=self._step_approval_required,
            toolbox=self._toolbox,
            execution_context=self._execution_context,
        )
        success = str(obs.status) in {"SUCCESS", "SUCCEEDED"}
        return CapabilityExecutionResult(
            request_id=request.request_id or action.action_id,
            success=success,
            changed=bool(obs.changed),
            output=dict(obs.observation or {}),
            evidence_paths=tuple(obs.evidence or ()),
            error_code=obs.error_code,
            error_message=obs.error_message,
        )


def bind_local_tool_provider(
    resolver: CapabilityProviderResolver,
    *,
    repository_id: str,
    settings: object | None = None,
    allowed_files: tuple[str, ...] = (),
    apply_edits: bool = True,
    **kwargs: Any,
) -> ToolCapabilityAdapter:
    """Register LOCAL_TOOL (+ HYEVE_SERVICE local-exec) adapters on a resolver."""
    adapter = ToolCapabilityAdapter(
        repository_id=repository_id,
        settings=settings,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        **kwargs,
    )
    resolver.register_provider(ProviderKind.LOCAL_TOOL, adapter)
    # Foundation labels network/ci/deployment as HYEVE_SERVICE but today they still
    # execute through capability_executor — share the same adapter until F9 wires remotes.
    resolver.register_provider(ProviderKind.HYEVE_SERVICE, adapter)
    return adapter


__all__ = [
    "ToolCapabilityAdapter",
    "bind_local_tool_provider",
]
