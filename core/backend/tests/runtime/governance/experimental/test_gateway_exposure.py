from __future__ import annotations

from backend.aix_core.runtime.objective.endpoint_classification import EndpointInclusionClass
from backend.aix_core.runtime.governance.experimental.gateway_exposure import (
    GatewayExposureDecision,
    authorize_gateway_request,
    build_gateway_allowlist,
)


def _schema() -> dict:
    from backend.main import app

    app.openapi_schema = None
    return app.openapi()


def test_default_allowlist_contains_only_product_operator_admin() -> None:
    entries = build_gateway_allowlist(_schema())

    assert entries
    assert {entry.inclusion_class for entry in entries} <= {
        EndpointInclusionClass.PRODUCT,
        EndpointInclusionClass.OPERATOR,
        EndpointInclusionClass.ADMIN,
    }


def test_internal_provider_orchestrator_and_integration_routes_are_absent() -> None:
    identities = {(entry.method, entry.path_template) for entry in build_gateway_allowlist(_schema())}

    assert ("POST", "/api/aix/model-gateway/resolve") not in identities
    assert ("POST", "/api/aix/orchestrator-v2/runs") not in identities
    assert ("POST", "/api/aix/langgraph/run") not in identities
    assert ("POST", "/api/aix/integrations/bre/evaluate") not in identities


def test_compatibility_routes_require_explicit_exception() -> None:
    default = build_gateway_allowlist(_schema())
    migration = build_gateway_allowlist(_schema(), include_compatibility_exception=True)

    assert not any(entry.inclusion_class is EndpointInclusionClass.COMPAT for entry in default)
    compat = [entry for entry in migration if entry.inclusion_class is EndpointInclusionClass.COMPAT]
    assert compat
    assert all(entry.compatibility_policy_version for entry in compat)


def test_concrete_cycle_path_matches_template_and_rbac() -> None:
    entries = build_gateway_allowlist(_schema())
    allowed = authorize_gateway_request(
        entries=entries,
        method="POST",
        path="/aix/cycles/cycle-1/cancel",
        roles=("aix:cycle:command",),
    )
    denied = authorize_gateway_request(
        entries=entries,
        method="POST",
        path="/aix/cycles/cycle-1/cancel",
        roles=("aix:cycle:read",),
    )

    assert allowed.decision is GatewayExposureDecision.ALLOW
    assert denied.reason_code == "GATEWAY_ROUTE_RBAC_FORBIDDEN"


def test_unknown_and_internal_paths_are_denied_even_with_admin_role() -> None:
    entries = build_gateway_allowlist(_schema())
    for path in ("/unknown", "/api/aix/model-gateway/resolve"):
        result = authorize_gateway_request(
            entries=entries,
            method="POST",
            path=path,
            roles=("aix:cycle:admin",),
        )
        assert result.decision is GatewayExposureDecision.DENY
        assert result.reason_code == "GATEWAY_ROUTE_NOT_ALLOWLISTED"


def test_admin_route_requires_admin_role() -> None:
    entries = build_gateway_allowlist(_schema())
    path = "/api/outbox/audit/"

    denied = authorize_gateway_request(
        entries=entries,
        method="GET",
        path=path,
        roles=("aix:cycle:read",),
    )
    allowed = authorize_gateway_request(
        entries=entries,
        method="GET",
        path=path,
        roles=("aix:cycle:admin",),
    )

    assert denied.decision is GatewayExposureDecision.DENY
    assert allowed.decision is GatewayExposureDecision.ALLOW
