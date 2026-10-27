from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.endpoint_classification import (
    EndpointClassificationError,
    EndpointInclusionClass,
    annotate_openapi_inclusion_classes,
    classify_operation,
)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/metrics", EndpointInclusionClass.OPERATOR),
        ("POST", "/aix/objectives/admit", EndpointInclusionClass.PRODUCT),
        ("GET", "/aix/cycles/cycle-1", EndpointInclusionClass.OPERATOR),
        ("POST", "/aix/tasks/execute", EndpointInclusionClass.COMPAT),
        ("GET", "/api/aix/runs", EndpointInclusionClass.OPERATOR),
        ("POST", "/api/aix/orchestrator-v2/runs", EndpointInclusionClass.INTERNAL),
        ("GET", "/api/aix/orchestrator-v2/runs/{run_id}", EndpointInclusionClass.OPERATOR),
        ("POST", "/api/aix/model-gateway/resolve", EndpointInclusionClass.INTERNAL),
        ("GET", "/api/aix/model-gateway/registry", EndpointInclusionClass.OPERATOR),
        ("POST", "/api/aix/model-gateway/registry/sync-from-integration", EndpointInclusionClass.ADMIN),
        ("GET", "/api/outbox/audit/", EndpointInclusionClass.ADMIN),
    ],
)
def test_classification_rules(method: str, path: str, expected: EndpointInclusionClass) -> None:
    assert classify_operation(method=method, path=path) is expected


def test_unknown_mounted_route_fails_openapi_gate() -> None:
    with pytest.raises(EndpointClassificationError, match="UNCLASSIFIED_MOUNTED_ENDPOINT"):
        annotate_openapi_inclusion_classes(
            {"paths": {"/unclassified": {"get": {"responses": {}}}}}
        )


def test_internal_route_is_not_browser_eligible() -> None:
    schema = annotate_openapi_inclusion_classes(
        {"paths": {"/api/aix/model-gateway/resolve": {"post": {"responses": {}}}}}
    )

    operation = schema["paths"]["/api/aix/model-gateway/resolve"]["post"]
    assert operation["x-aix-inclusion-class"] == "INTERNAL"
    assert operation["x-aix-public-browser-eligible"] is False


def test_live_application_openapi_classifies_every_operation() -> None:
    from backend.main import app

    app.openapi_schema = None
    schema = app.openapi()
    allowed = {item.value for item in EndpointInclusionClass}
    operations = []
    for path_item in schema["paths"].values():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                operations.append(operation)
                assert operation["x-aix-inclusion-class"] in allowed
    assert len(operations) >= 120
    assert schema["x-aix-endpoint-classification-gate"] == "PASS"
