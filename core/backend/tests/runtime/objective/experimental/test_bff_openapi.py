from __future__ import annotations

import json
from pathlib import Path

from backend.aix_core.runtime.objective.experimental.bff_openapi import (
    BFF_OPENAPI_VERSION,
    BFF_ROUTE_CONTRACTS,
    build_bff_openapi,
)

BACKEND_ROOT = Path(__file__).resolve().parents[4]
PUBLISHED = BACKEND_ROOT / "docs" / "aix_frontend_bff_openapi_v1.json"


def test_published_contract_matches_registry_identity_and_count() -> None:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))

    assert published["info"]["version"] == BFF_OPENAPI_VERSION
    assert published == build_bff_openapi()
    assert published["x-aix-route-contract-count"] == len(BFF_ROUTE_CONTRACTS) == 22


def test_every_route_has_openapi_operation_and_required_extensions() -> None:
    schema = build_bff_openapi()

    for route in BFF_ROUTE_CONTRACTS:
        operation = schema["paths"][route.bff_path][route.method.lower()]
        assert operation["x-aix-upstream-service"] == route.upstream_service
        assert operation["x-aix-upstream-path"] == route.upstream_path
        assert operation["x-aix-propagates-auth"] is route.propagates_auth
        assert operation["x-aix-propagates-tenant"] is route.propagates_tenant
        assert operation["x-aix-inclusion-class"] == route.inclusion_class


def test_route_method_pairs_are_unique() -> None:
    identities = [(route.method, route.bff_path) for route in BFF_ROUTE_CONTRACTS]

    assert len(identities) == len(set(identities))


def test_all_current_aix_bff_route_templates_are_present() -> None:
    paths = {route.bff_path for route in BFF_ROUTE_CONTRACTS if route.upstream_service == "AIX"}

    assert {
        "/api/aix/admit",
        "/api/aix/tasks/execute",
        "/api/aix/tasks/{taskId}/status",
        "/api/aix/tasks/{taskId}/evidence",
        "/api/aix/tasks/{taskId}/review-packet",
        "/api/aix/tasks/{taskId}/review-decision",
        "/api/aix/replay/{evidenceId}",
        "/api/aix/runs",
        "/api/aix/runs/{runId}",
        "/api/aix/policies",
        "/api/aix/cycles/{cycleId}/{action}",
        "/api/aix/cycles/{cycleId}/panels/{panel}",
    } <= paths


def test_dynamic_cycle_action_is_allowlisted() -> None:
    schema = build_bff_openapi()

    assert schema["x-aix-cycle-actions"] == ["clarify", "cancel", "resume", "retry-step", "replan"]
    assert "approve" not in schema["x-aix-cycle-actions"]


def test_dynamic_cycle_panel_is_allowlisted() -> None:
    schema = build_bff_openapi()

    assert schema["x-aix-cycle-panels"] == ["attempts", "events", "budgets", "agents"]
    assert "review-packet" not in schema["x-aix-cycle-panels"]


def test_dependency_health_route_is_operator_classified_without_claiming_auth() -> None:
    schema = build_bff_openapi()
    operation = schema["paths"]["/api/deps/health"]["get"]

    assert operation["x-aix-inclusion-class"] == "OPERATOR"
    assert operation["x-aix-propagates-auth"] is False
    assert operation["x-aix-propagates-tenant"] is False


def test_internal_provider_temporal_and_executor_paths_are_absent() -> None:
    upstream_text = " ".join(route.upstream_path for route in BFF_ROUTE_CONTRACTS).lower()

    assert "/temporal" not in upstream_text
    assert "provider/resolve" not in upstream_text
    assert "devexecutor" not in upstream_text
