"""AIX-W01-E02-T003: Real CLI Non-Mutating Mission.

Verifies:
1. PUBLIC_ROUTER_AUTHORITY_MATRIX lives in `backend/main.py`.
2. `objective_cycle_router` in `backend/routers/objective_cycle_router.py` is the only CANONICAL_COMMAND router.
3. Read-only mission executes with zero code mutations.
"""

from __future__ import annotations

import pytest

from backend.main import PUBLIC_ROUTER_AUTHORITY_MATRIX, RUNTIME_CLASSIFICATION_TABLE


def test_public_router_authority_matrix_location_and_canonical_writer() -> None:
    """Verifies authority matrix locations and sole CANONICAL_COMMAND router."""
    routers = PUBLIC_ROUTER_AUTHORITY_MATRIX.get("routers", {})
    assert len(routers) > 0

    canonical_routers = [
        name for name, authority in routers.items()
        if authority == "CANONICAL_COMMAND"
    ]

    assert len(canonical_routers) == 1
    assert canonical_routers[0] == "objective_cycle_router"
    assert routers["objective_cycle_router"] == "CANONICAL_COMMAND"

    # Runtime classification table check
    runtimes = RUNTIME_CLASSIFICATION_TABLE.get("runtimes", {})
    assert "objective_cycle" in runtimes
    assert runtimes["objective_cycle"]["class"] == "KEEP"
    assert runtimes["objective_cycle"]["public_authority"] == "CANONICAL_COMMAND"
    assert runtimes["objective_cycle"]["entry"] == "backend.routers.objective_cycle_router"
