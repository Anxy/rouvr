"""Canonical AIX-Frontend BFF route registry and OpenAPI builder."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

BFF_OPENAPI_VERSION = "aix.frontend-bff.openapi.v1"


class BffRouteContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str
    bff_path: str
    upstream_service: str
    upstream_path: str
    propagates_auth: bool
    propagates_tenant: bool
    inclusion_class: str


def _route(
    method: str,
    bff_path: str,
    service: str,
    upstream: str,
    *,
    auth: bool = True,
    tenant: bool = True,
    inclusion: str = "PRODUCT",
) -> BffRouteContract:
    return BffRouteContract(
        method=method,
        bff_path=bff_path,
        upstream_service=service,
        upstream_path=upstream,
        propagates_auth=auth,
        propagates_tenant=tenant,
        inclusion_class=inclusion,
    )


BFF_ROUTE_CONTRACTS: tuple[BffRouteContract, ...] = (
    _route("POST", "/api/auth/login", "AUTH", "/login", auth=False, tenant=True),
    _route("GET", "/api/auth/logout", "LOCAL_SESSION", "LOCAL", auth=False, tenant=False),
    _route("POST", "/api/auth/logout", "LOCAL_SESSION", "LOCAL", auth=False, tenant=False),
    _route("GET", "/api/auth/session", "AUTH", "/validate|/refresh"),
    _route("POST", "/api/auth/tenant", "LOCAL_SESSION", "LOCAL", auth=False),
    _route(
        "GET",
        "/api/deps/health",
        "DEPENDENCY_HEALTH",
        "AUTH|WORKHUB|AIX|GATEWAY|TEMPORAL|MEMURAI",
        auth=False,
        tenant=False,
        inclusion="OPERATOR",
    ),
    _route("POST", "/api/wh/tasks", "WORKHUB", "/tasks"),
    _route("GET", "/api/wh/tasks/blocked", "WORKHUB", "/tasks?state=blocked", inclusion="OPERATOR"),
    _route("POST", "/api/files/upload", "FILE_ASSETS", "/api/file-assets/upload"),
    _route("POST", "/api/comms/notify", "COMMS", "/api/notifications/send"),
    _route("POST", "/api/aix/admit", "AIX", "/aix/tasks/admit", inclusion="COMPAT"),
    _route("POST", "/api/aix/tasks/execute", "AIX", "/aix/tasks/execute", inclusion="COMPAT"),
    _route("GET", "/api/aix/tasks/{taskId}/status", "AIX", "/aix/tasks/{taskId}/status", inclusion="COMPAT"),
    _route("GET", "/api/aix/tasks/{taskId}/evidence", "AIX", "/aix/tasks/{taskId}/evidence", inclusion="COMPAT"),
    _route(
        "GET",
        "/api/aix/tasks/{taskId}/review-packet",
        "AIX",
        "/aix/tasks/{taskId}/review-packet",
        inclusion="COMPAT",
    ),
    _route(
        "POST",
        "/api/aix/tasks/{taskId}/review-decision",
        "AIX",
        "/aix/tasks/{taskId}/review-decision",
        inclusion="COMPAT",
    ),
    _route("POST", "/api/aix/replay/{evidenceId}", "AIX", "/aix/replay/{evidenceId}", inclusion="COMPAT"),
    _route("GET", "/api/aix/runs", "AIX", "/api/aix/runs", inclusion="OPERATOR"),
    _route("GET", "/api/aix/runs/{runId}", "AIX", "/api/aix/runs/{runId}", inclusion="OPERATOR"),
    _route(
        "GET",
        "/api/aix/policies",
        "AIX",
        "/api/aix-escalation-policies/audit/|/api/aix-model-gateway-policies/audit/",
        inclusion="ADMIN",
    ),
    _route(
        "POST",
        "/api/aix/cycles/{cycleId}/{action}",
        "AIX",
        "/aix/cycles/{cycleId}/{action}",
        inclusion="OPERATOR",
    ),
    _route(
        "GET",
        "/api/aix/cycles/{cycleId}/panels/{panel}",
        "AIX",
        "/aix/cycles/{cycleId}/{panel}",
        inclusion="OPERATOR",
    ),
)


def build_bff_openapi() -> dict:
    paths: dict[str, dict] = {}
    for route in BFF_ROUTE_CONTRACTS:
        operation_id = (
            f"{route.method.lower()}_"
            + route.bff_path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
        )
        paths.setdefault(route.bff_path, {})[route.method.lower()] = {
            "operationId": operation_id,
            "responses": {"200": {"description": "Proxied response"}, "default": {"description": "Upstream error"}},
            "x-aix-upstream-service": route.upstream_service,
            "x-aix-upstream-path": route.upstream_path,
            "x-aix-propagates-auth": route.propagates_auth,
            "x-aix-propagates-tenant": route.propagates_tenant,
            "x-aix-inclusion-class": route.inclusion_class,
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "AIX-Frontend BFF", "version": BFF_OPENAPI_VERSION},
        "paths": paths,
        "x-aix-source": "backend.aix_core.runtime.objective.experimental.bff_openapi.build_bff_openapi",
        "x-aix-route-contract-count": len(BFF_ROUTE_CONTRACTS),
        "x-aix-canonical-frontend-recon": "D:\\v2.1\\AIX-Frontend",
        "x-aix-cycle-actions": ["clarify", "cancel", "resume", "retry-step", "replan"],
        "x-aix-cycle-panels": ["attempts", "events", "budgets", "agents"],
    }
