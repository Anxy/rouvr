"""AIX-GAEF-001 governed autonomous execution foundation."""

from backend.aix_core.runtime.foundation.capability_executor import (
    actions_from_step_contract,
    execute_action,
    execute_action_loop,
)
from backend.aix_core.runtime.foundation.capability_registry import (
    catalog_for_planner,
    resolve_capability,
)
from backend.aix_core.runtime.foundation.execution_context import (
    ExecutionContextV1,
    PrincipalV1,
    build_execution_context,
)
from backend.aix_core.runtime.foundation.policy_gateway import authorize_action

__all__ = [
    "ExecutionContextV1",
    "PrincipalV1",
    "actions_from_step_contract",
    "authorize_action",
    "build_execution_context",
    "catalog_for_planner",
    "execute_action",
    "execute_action_loop",
    "resolve_capability",
]
