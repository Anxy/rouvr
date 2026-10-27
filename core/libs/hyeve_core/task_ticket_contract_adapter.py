"""Temporary compatibility re-exports for task ticket contract synthesis."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from hyeve_task_contract_engine.contract_synthesis import (
        attach_task_ticket_contract,
        synthesize_task_ticket_contract,
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

__all__ = [
    "attach_task_ticket_contract",
    "synthesize_task_ticket_contract",
]
