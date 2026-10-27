"""Bounded MVP-to-cycle API compatibility and human-controlled sunset policy."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

COMPATIBILITY_POLICY_VERSION = "aix.objective-cycle.api-compatibility.v1"
COMPATIBILITY_OWNER = "AIX Product API Owner"


class CompatibilityState(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATION_ELIGIBLE = "DEPRECATION_ELIGIBLE"
    REMOVAL_APPROVAL_REQUIRED = "REMOVAL_APPROVAL_REQUIRED"


class CompatibilityRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str
    legacy_path: str
    replacement_method: str
    replacement_path: str
    inclusion_class: str = "COMPAT"
    lifecycle_mutation_authority: bool = False


class CompatibilityReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    replacement_api_live: bool
    frontend_bff_migrated: bool
    sdk_cli_migrated: bool
    no_legacy_traffic_days: int = Field(ge=0)
    contract_tests_green: bool
    operator_runbook_updated: bool
    named_human_removal_approval: bool = False


class CompatibilityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = COMPATIBILITY_POLICY_VERSION
    owner: str = COMPATIBILITY_OWNER
    state: CompatibilityState
    reason_codes: tuple[str, ...] = Field(min_length=1)
    removal_allowed: bool = False


COMPATIBILITY_ROUTES: tuple[CompatibilityRoute, ...] = (
    CompatibilityRoute(
        method="POST",
        legacy_path="/aix/tasks/admit",
        replacement_method="POST",
        replacement_path="/aix/objectives/admit",
    ),
    CompatibilityRoute(
        method="POST",
        legacy_path="/aix/tasks/execute",
        replacement_method="POST",
        replacement_path="/aix/objectives/{objective_id}/cycles",
    ),
    CompatibilityRoute(
        method="GET",
        legacy_path="/aix/tasks/{id}/status",
        replacement_method="GET",
        replacement_path="/aix/cycles/{cycle_id}",
    ),
    CompatibilityRoute(
        method="GET",
        legacy_path="/aix/tasks/{id}/evidence",
        replacement_method="GET",
        replacement_path="/aix/cycles/{cycle_id}/evidence",
    ),
    CompatibilityRoute(
        method="GET",
        legacy_path="/aix/tasks/{id}/review-packet",
        replacement_method="GET",
        replacement_path="/aix/cycles/{cycle_id}/review-packet",
    ),
    CompatibilityRoute(
        method="POST",
        legacy_path="/aix/tasks/{id}/review-decision",
        replacement_method="POST",
        replacement_path="WORKHUB_HUMAN_REVIEW_DISPOSITION",
    ),
    CompatibilityRoute(
        method="POST",
        legacy_path="/aix/replay/{evidence_id}",
        replacement_method="GET",
        replacement_path="/aix/cycles/{cycle_id}/replay/{evidence_id}",
    ),
)


def compatibility_headers(route: CompatibilityRoute) -> dict[str, str]:
    return {
        "Deprecation": "true",
        "Link": f'<{route.replacement_path}>; rel="successor-version"',
        "X-AIX-Inclusion-Class": route.inclusion_class,
        "X-AIX-Compatibility-Policy": COMPATIBILITY_POLICY_VERSION,
    }


def evaluate_compatibility_sunset(readiness: CompatibilityReadiness) -> CompatibilityDecision:
    checks = {
        "REPLACEMENT_API_NOT_LIVE": readiness.replacement_api_live,
        "FRONTEND_BFF_NOT_MIGRATED": readiness.frontend_bff_migrated,
        "SDK_CLI_NOT_MIGRATED": readiness.sdk_cli_migrated,
        "LEGACY_TRAFFIC_WINDOW_INCOMPLETE": readiness.no_legacy_traffic_days >= 90,
        "CONTRACT_TESTS_NOT_GREEN": readiness.contract_tests_green,
        "OPERATOR_RUNBOOK_NOT_UPDATED": readiness.operator_runbook_updated,
    }
    failed = tuple(code for code, passed in checks.items() if not passed)
    if failed:
        return CompatibilityDecision(state=CompatibilityState.ACTIVE, reason_codes=failed)
    if not readiness.named_human_removal_approval:
        return CompatibilityDecision(
            state=CompatibilityState.REMOVAL_APPROVAL_REQUIRED,
            reason_codes=("NAMED_HUMAN_REMOVAL_APPROVAL_REQUIRED",),
        )
    return CompatibilityDecision(
        state=CompatibilityState.DEPRECATION_ELIGIBLE,
        reason_codes=("ALL_SUNSET_GATES_SATISFIED",),
        removal_allowed=True,
    )
