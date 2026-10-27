"""Compatibility re-exports for task ticket contract APIs.

Pure contract data models and structural validation now live in:
- ``hyeve_core.task_contract_types``
- ``hyeve_core.task_contract_validation``

Orchestration policy and synthesis now live in ``hyeve_task_contract_engine``.
This module remains only as a temporary migration shim.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hyeve_core.task_contract_types import (
    TaskTicketContractCurrent,
    TaskTicketExecutionSlice,
    TaskTicketScope,
    TaskTicketTargetSurface,
)
from hyeve_core.task_contract_validation import (
    handoff_requires_execution_slice,
    validate_task_ticket_contract,
)
from hyeve_core.task_ticket_contract_legacy_bridge import (
    ExecutionIntentCurrent,
    ObservationPacketCurrent,
    attach_structured_task_packet,
)

try:
    from hyeve_task_contract_engine.contract_synthesis import (
        attach_task_ticket_contract,
        synthesize_task_ticket_contract,
    )
    from hyeve_task_contract_engine.readiness_policy import (
        evaluate_dispatch_readiness,
        should_enforce_task_ticket,
    )
except ModuleNotFoundError:
    _HYEVE_ROOT = Path(__file__).resolve().parents[1]
    _ENGINE_PACKAGE_ROOT = _HYEVE_ROOT / "libs" / "hyeve_task_contract_engine"
    if str(_ENGINE_PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(_ENGINE_PACKAGE_ROOT))
    from hyeve_task_contract_engine.contract_synthesis import (
        attach_task_ticket_contract,
        synthesize_task_ticket_contract,
    )
    from hyeve_task_contract_engine.readiness_policy import (
        evaluate_dispatch_readiness,
        should_enforce_task_ticket,
    )

__all__ = [
    "ExecutionIntentCurrent",
    "ObservationPacketCurrent",
    "TaskTicketContractCurrent",
    "TaskTicketExecutionSlice",
    "TaskTicketScope",
    "TaskTicketTargetSurface",
    "attach_structured_task_packet",
    "attach_task_ticket_contract",
    "evaluate_dispatch_readiness",
    "handoff_requires_execution_slice",
    "should_enforce_task_ticket",
    "synthesize_task_ticket_contract",
    "validate_task_ticket_contract",
]
