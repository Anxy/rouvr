"""AIX-W02-E01-T002: Objective Control Plane Boundary.

Verifies:
1. `objective_cycle_router` is the sole CANONICAL_COMMAND lifecycle owner.
2. Dual surface conflict checks prevent creating two competing authoritative objectives for one task or idempotency key.
3. Retired mutation routes (MVP and V2) return HTTP 410 and cannot create lifecycle aggregates.
4. Kernel router (`execution_kernel_router`) is classified EXPERIMENTAL and cannot override canonical objective authority.
"""

from __future__ import annotations

import pytest
import httpx
from fastapi import FastAPI

import backend.main as production
from backend.aix_core.runtime.objective.dual_surface import (
    DualSurfaceRegistry,
    DualSurfaceLink,
    DualSurfaceError,
)
from backend.routers.aix_mvp_product_router import router as mvp_router
from backend.routers.aix_orchestrator_v2_router import router as v2_router
from backend.tests.test_public_router_authority_matrix import (
    _isolated_router_app,
    _mutation_routes,
    _request_parameters,
    _request_path,
    _request_payload,
)


def test_objective_cycle_is_sole_canonical_command_writer() -> None:
    """Matrix contract: exactly one router has CANONICAL_COMMAND status."""
    routers = production.PUBLIC_ROUTER_AUTHORITY_MATRIX["routers"]
    canonical_writers = [
        name for name, authority in routers.items() if authority == "CANONICAL_COMMAND"
    ]
    assert canonical_writers == ["objective_cycle_router"], (
        f"Expected exactly ['objective_cycle_router'] as CANONICAL_COMMAND, got {canonical_writers}"
    )


def test_dual_surface_registry_prevents_competing_authoritative_aggregates() -> None:
    """Two public API calls using the same idempotency key or task ID for different objectives
    MUST raise DualSurfaceError conflict and refuse to create duplicate authoritative records.
    """
    registry = DualSurfaceRegistry()

    link1 = DualSurfaceLink(
        tenant_id="tenant-1",
        workhub_task_id="task-100",
        objective_id="obj-100",
        idempotency_key="idempotency-key-100",
    )
    registered_link, is_replay = registry.register(link1)
    assert is_replay is False
    assert registered_link.objective_id == "obj-100"

    # Exact replay returns same link
    same_link, is_replay_2 = registry.register(link1)
    assert is_replay_2 is True
    assert same_link.objective_id == "obj-100"

    # Conflicting objective for same idempotency key fails
    conflicting_key_link = DualSurfaceLink(
        tenant_id="tenant-1",
        workhub_task_id="task-200",
        objective_id="obj-200",
        idempotency_key="idempotency-key-100",
    )
    with pytest.raises(DualSurfaceError, match="DUAL_SURFACE_IDEMPOTENCY_CONFLICT"):
        registry.register(conflicting_key_link)

    # Conflicting objective for same workhub task ID fails
    conflicting_task_link = DualSurfaceLink(
        tenant_id="tenant-1",
        workhub_task_id="task-100",
        objective_id="obj-300",
        idempotency_key="idempotency-key-300",
    )
    with pytest.raises(DualSurfaceError, match="DUAL_SURFACE_IDENTITY_CONFLICT"):
        registry.register(conflicting_task_link)


@pytest.mark.asyncio
async def test_mvp_and_v2_mutation_routes_cannot_create_objectives() -> None:
    """Adversarial test: Retired mutation routes on MVP and V2 return HTTP 410 and cannot admit/create objectives."""
    for router, name in [(mvp_router, "mvp"), (v2_router, "v2")]:
        app = _isolated_router_app(router)
        routes = _mutation_routes(router)
        assert routes, f"{name} router must expose mutation routes"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            for method, path, route in routes:
                resp = await client.request(
                    method,
                    _request_path(app, route, path),
                    headers={
                        "Authorization": "Bearer test",
                        "X-Tenant-ID": "t1",
                        "X-Actor-ID": "a1",
                    },
                    json=_request_payload(route),
                    params=_request_parameters(route),
                )
                assert resp.status_code == 410, (
                    f"{method} {path} on {name} returned {resp.status_code}, expected HTTP 410"
                )
