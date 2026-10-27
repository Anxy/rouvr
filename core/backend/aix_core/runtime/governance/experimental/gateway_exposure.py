"""Gateway exposure allowlist derived from classified OpenAPI operations."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict

from backend.aix_core.runtime.objective.experimental.api_compatibility import COMPATIBILITY_POLICY_VERSION
from backend.aix_core.runtime.objective.endpoint_classification import EndpointInclusionClass

GATEWAY_ALLOWLIST_VERSION = "aix.objective-cycle.gateway-allowlist.v1"


class GatewayExposureDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class GatewayAllowlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str
    path_template: str
    inclusion_class: EndpointInclusionClass
    required_permission: str
    compatibility_policy_version: str | None = None


class GatewayAuthorizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: GatewayExposureDecision
    reason_code: str
    matched_entry: GatewayAllowlistEntry | None = None


_PUBLIC_CLASSES = {
    EndpointInclusionClass.PRODUCT,
    EndpointInclusionClass.OPERATOR,
    EndpointInclusionClass.ADMIN,
}


def build_gateway_allowlist(
    openapi_schema: dict,
    *,
    include_compatibility_exception: bool = False,
) -> tuple[GatewayAllowlistEntry, ...]:
    entries: list[GatewayAllowlistEntry] = []
    for path, path_item in openapi_schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            inclusion = EndpointInclusionClass(operation["x-aix-inclusion-class"])
            if inclusion not in _PUBLIC_CLASSES:
                if not (
                    include_compatibility_exception
                    and inclusion is EndpointInclusionClass.COMPAT
                ):
                    continue
            permission = (
                "aix:cycle:admin"
                if inclusion is EndpointInclusionClass.ADMIN
                else "aix:cycle:read"
                if method.upper() == "GET"
                else "aix:cycle:command"
            )
            entries.append(
                GatewayAllowlistEntry(
                    method=method.upper(),
                    path_template=path,
                    inclusion_class=inclusion,
                    required_permission=permission,
                    compatibility_policy_version=(
                        COMPATIBILITY_POLICY_VERSION
                        if inclusion is EndpointInclusionClass.COMPAT
                        else None
                    ),
                )
            )
    return tuple(sorted(entries, key=lambda item: (item.path_template, item.method)))


def authorize_gateway_request(
    *,
    entries: Iterable[GatewayAllowlistEntry],
    method: str,
    path: str,
    roles: Iterable[str],
) -> GatewayAuthorizationResult:
    normalized_method = method.upper()
    role_set = set(roles)
    for entry in entries:
        if entry.method != normalized_method or not _template_matches(entry.path_template, path):
            continue
        if entry.required_permission not in role_set and "aix:cycle:admin" not in role_set:
            return GatewayAuthorizationResult(
                decision=GatewayExposureDecision.DENY,
                reason_code="GATEWAY_ROUTE_RBAC_FORBIDDEN",
                matched_entry=entry,
            )
        return GatewayAuthorizationResult(
            decision=GatewayExposureDecision.ALLOW,
            reason_code="GATEWAY_ROUTE_ALLOWLIST_MATCH",
            matched_entry=entry,
        )
    return GatewayAuthorizationResult(
        decision=GatewayExposureDecision.DENY,
        reason_code="GATEWAY_ROUTE_NOT_ALLOWLISTED",
    )


def _template_matches(template: str, concrete_path: str) -> bool:
    parts = re.split(r"(\{[^{}]+\})", template)
    pattern = "".join(r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.fullmatch(pattern, concrete_path) is not None
