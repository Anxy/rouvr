"""AIX v2 contracts package (Wave 1)."""

from backend.aix_core.contracts.task_contract import (
    ContractAmendment,
    ContractAmendmentRejected,
    TaskContract,
    apply_amendment,
    check_filesystem_scope,
)
from typing import Any

__all__ = [
    "ContractAmendment",
    "ContractAmendmentRejected",
    "TaskContract",
    "apply_amendment",
    "check_filesystem_scope",
    "from_execution_dict",
]


def __getattr__(name: str) -> Any:
    """Lazy export: avoids a sibling-cycle (adapter imports task_contract)."""
    if name == "from_execution_dict":
        from backend.aix_core.contract_adapter import from_execution_dict

        return from_execution_dict
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
