"""Logical AIX service boundaries (in-process modules, not microservices).

intent → planner → dispatcher → executor → verifier
"""

from backend.aix_core.runtime.boundaries.log import BoundaryLogger, boundary_log
from backend.aix_core.runtime.boundaries.dispatcher import (
    DispatchObservation,
    observe_accepted_plan,
)

__all__ = [
    "BoundaryLogger",
    "DispatchObservation",
    "boundary_log",
    "observe_accepted_plan",
]
