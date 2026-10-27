"""Fail-closed inclusion classification for every mounted OpenAPI operation."""

from __future__ import annotations

from enum import StrEnum


class EndpointInclusionClass(StrEnum):
    PRODUCT = "PRODUCT"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"
    COMPAT = "COMPAT"
    INTERNAL = "INTERNAL"
    FORBIDDEN_PUBLIC = "FORBIDDEN_PUBLIC"


class EndpointClassificationError(ValueError):
    pass


_ADMIN_AUDIT_PREFIXES = (
    "/api/admin-audit-log/audit",
    "/api/aix-escalation-policies/audit",
    "/api/aix-model-gateway-policies/audit",
    "/api/aix-model-health-status/audit",
    "/api/aix-model-provider-bindings/audit",
    "/api/aix/model-gateway-policies/audit",
    "/api/feature-flags/audit",
    "/api/idempotency-keys/audit",
    "/api/inbox/audit",
    "/api/outbox/audit",
    "/api/service-config/audit",
)

_MODEL_GATEWAY_OPERATOR_PATHS = {
    "/api/aix/model-gateway/registry",
    "/api/aix/model-gateway/ledger",
    "/api/aix/model-gateway/trace/correlation/{correlation_id}",
    "/api/aix/model-gateway/feedback/intake",
}


def classify_operation(*, method: str, path: str) -> EndpointInclusionClass:
    verb = method.upper()
    if path in {"/health", "/ready", "/live", "/metrics"}:
        return EndpointInclusionClass.OPERATOR
    if path in {"/", "/_version"}:
        return EndpointInclusionClass.INTERNAL
    if path.startswith("/aix/tasks/") or path.startswith("/aix/replay/"):
        return EndpointInclusionClass.COMPAT
    if path == "/aix/objectives/admit" or path.endswith("/cycles") and path.startswith("/aix/objectives/"):
        return EndpointInclusionClass.PRODUCT
    if path.startswith("/aix/objectives/intelligence"):
        return EndpointInclusionClass.OPERATOR
    if path.startswith("/aix/objectives"):
        return EndpointInclusionClass.PRODUCT
    if path.startswith("/aix/cycles"):
        return EndpointInclusionClass.OPERATOR
    if path == "/aix/execution-kernel/health":
        return EndpointInclusionClass.OPERATOR
    if path.startswith("/aix/execution-kernel/"):
        # Internal problem/dispatch/capsule/knowledge engine -- not a product or
        # operator-facing surface; same treatment as /api/aix/integrations/.
        return EndpointInclusionClass.INTERNAL
    if path.startswith("/api/aix/runs"):
        return EndpointInclusionClass.OPERATOR
    if path.startswith("/api/aix/orchestrator-v2"):
        if verb == "GET":
            return EndpointInclusionClass.OPERATOR
        return EndpointInclusionClass.INTERNAL
    if path.startswith("/api/aix/integrations/") or path == "/api/aix/langgraph/run":
        return EndpointInclusionClass.INTERNAL
    if any(path.startswith(prefix) for prefix in _ADMIN_AUDIT_PREFIXES):
        return EndpointInclusionClass.ADMIN
    if path == "/api/aix/model-gateway/resolve":
        return EndpointInclusionClass.INTERNAL
    if path in _MODEL_GATEWAY_OPERATOR_PATHS:
        return EndpointInclusionClass.OPERATOR
    if path.startswith("/api/aix/model-gateway/"):
        return EndpointInclusionClass.ADMIN
    raise EndpointClassificationError(f"UNCLASSIFIED_MOUNTED_ENDPOINT:{verb}:{path}")


def annotate_openapi_inclusion_classes(schema: dict) -> dict:
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                continue
            inclusion = classify_operation(method=method, path=path)
            operation["x-aix-inclusion-class"] = inclusion.value
            operation["x-aix-public-browser-eligible"] = inclusion in {
                EndpointInclusionClass.PRODUCT,
                EndpointInclusionClass.OPERATOR,
                EndpointInclusionClass.ADMIN,
                EndpointInclusionClass.COMPAT,
            }
    schema["x-aix-endpoint-classification-gate"] = "PASS"
    return schema
