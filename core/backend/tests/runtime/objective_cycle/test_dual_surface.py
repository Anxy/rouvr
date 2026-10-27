from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aix_core.runtime.objective.dual_surface import (
    ContinuationAction,
    DualSurfaceError,
    DualSurfaceLink,
    DualSurfaceRegistry,
    decide_post_admit_continuation,
)
from backend.routers.aix_mvp_product_router import router as mvp_router


def _link(**changes) -> DualSurfaceLink:
    payload = {
        "tenant_id": "tenant-1",
        "workhub_task_id": "task-1",
        "objective_id": "objective-1",
        "cycle_id": "cycle-1",
        "legacy_run_id": "run-1",
        "idempotency_key": "key-1",
    }
    payload.update(changes)
    return DualSurfaceLink(**payload)


def test_registry_links_task_objective_cycle_and_run() -> None:
    registry = DualSurfaceRegistry()
    registry.register(_link())

    assert registry.resolve_task(tenant_id="tenant-1", task_id="task-1").cycle_id == "cycle-1"
    assert registry.resolve_cycle(tenant_id="tenant-1", cycle_id="cycle-1").legacy_run_id == "run-1"


def test_registration_is_idempotent_for_same_payload() -> None:
    registry = DualSurfaceRegistry()
    first = registry.register(_link())
    second = registry.register(_link())

    assert first[1] is False
    assert second[1] is True
    assert second[0] == first[0]


def test_reused_idempotency_key_with_different_identity_is_rejected() -> None:
    registry = DualSurfaceRegistry()
    registry.register(_link())

    with pytest.raises(DualSurfaceError, match="DUAL_SURFACE_IDEMPOTENCY_CONFLICT"):
        registry.register(_link(objective_id="objective-2"))


def test_links_are_tenant_scoped() -> None:
    registry = DualSurfaceRegistry()
    registry.register(_link())
    registry.register(
        _link(
            tenant_id="tenant-2",
            idempotency_key="key-2",
        )
    )

    assert registry.resolve_task(tenant_id="tenant-2", task_id="task-1").tenant_id == "tenant-2"
    with pytest.raises(DualSurfaceError, match="DUAL_SURFACE_LINK_NOT_FOUND"):
        registry.resolve_cycle(tenant_id="tenant-1", cycle_id="missing")


def test_cycle_api_is_preferred_after_admit() -> None:
    decision = decide_post_admit_continuation(
        objective_id="objective-1",
        cycle_api_available=True,
        compatibility_execute_available=True,
        legacy_task_id="task-1",
    )

    assert decision.action is ContinuationAction.START_CYCLE
    assert decision.admit_only_success is False


def test_compat_execute_prevents_dead_end_during_migration() -> None:
    decision = decide_post_admit_continuation(
        objective_id=None,
        cycle_api_available=False,
        compatibility_execute_available=True,
        legacy_task_id="task-1",
    )

    assert decision.action is ContinuationAction.EXECUTE_COMPAT
    assert decision.legacy_task_id == "task-1"
    assert decision.admit_only_success is False


def test_no_execution_surface_returns_honest_blocked_state() -> None:
    decision = decide_post_admit_continuation(
        objective_id="objective-1",
        cycle_api_available=False,
        compatibility_execute_available=False,
        legacy_task_id="task-1",
    )

    assert decision.action is ContinuationAction.BLOCKED_NO_EXECUTION_SURFACE
    assert decision.admit_only_success is False


def test_live_mvp_admit_response_exposes_human_triggered_compat_continuation() -> None:
    app = FastAPI()
    app.include_router(mvp_router)
    response = TestClient(app).post(
        "/aix/tasks/admit",
        headers={
            "Authorization": "Bearer test",
            "X-Tenant-ID": "tenant-1",
            "X-Actor-ID": "human-1",
        },
        json={
            "id": "task-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "repo_ref": "aix@candidate",
            "task_kind": "docs_only_change",
            "description": "Update bounded documentation.",
            "risk_hint": "medium",
            "user_id": "human-1",
            "created_at": "2026-07-30T00:00:00Z",
            "bounded_scope": ["docs/current/example.md"],
            "validation_commands": ["python -m pytest tests/test_docs.py -q"],
        },
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "MVP_ADMIT_RETIRED"
    assert response.json()["detail"]["sub_code"] == "USE_OBJECTIVE_CYCLE"
