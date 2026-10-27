"""AIX-E2E-CYCLE-001I-W01…W07: Auth + WorkHub integration layer.

This module provides the complete governed integration surface between AIX and external
authority services (auth, WorkHub). Seven distinct sub-concerns, each with a clear
fail-closed contract:

  001I-W01  auth_repair      — login-validate-repair cycle (token refresh + re-validate)
  001I-W02  service_principal — AIX service-principal identity envelope
  001I-W03  tenant_propagation — tenant_id propagation guard (no cross-tenant bleed)
  001I-W04  workhub_parent_load — load + validate the WorkHub parent task reference
  001I-W05  approval_validation — read + validate a WorkHub approval record
  001I-W06  evidence_writeback  — write-back evidence to WorkHub (request only, no auto-close)
  001I-W07  cross_tenant_abuse  — cross-tenant / authority-boundary abuse guard

Authority rule (all sub-concerns):
  AIX never mutates WorkHub lifecycle. Every "write" is a request emitted outward;
  WorkHub decides acceptance, closure, and merge independently.

Production: NO_GO — live WorkHub calls are never issued from this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class AuthError(Exception):
    pass


class TenantViolation(Exception):
    """Raised when a cross-tenant boundary breach is detected."""


class WorkHubIntegrationError(Exception):
    pass


AuthMode = Literal["simulated", "real_disabled", "real_enabled"]


@dataclass(frozen=True)
class AuthTokenV1:
    """Minimal representation of a validated auth token carried through AIX."""
    subject: str           # User or service principal id
    tenant_id: str
    scopes: tuple[str, ...]
    expires_at: str        # ISO-8601 UTC
    token_ref: str         # Opaque reference — never the raw token value


@dataclass(frozen=True)
class AuthValidationResult:
    is_valid: bool
    token: AuthTokenV1 | None = None
    reason: str = ""
    repaired: bool = False


# ---------------------------------------------------------------------------
# 001I-W01: Auth login-validate-repair
# ---------------------------------------------------------------------------

class AuthLoginRepair:
    """Validates an incoming auth token and, when stale/expired, triggers the repair
    path (refresh). In real_disabled / simulated mode, no network call is made —
    the repair is recorded as simulated so the caller can act accordingly.

    Fail-closed: an empty or missing token_ref always returns is_valid=False.
    """

    def __init__(self, *, mode: AuthMode = "simulated") -> None:
        self._mode = mode

    def validate(self, token_ref: str, tenant_id: str) -> AuthValidationResult:
        if not token_ref or not token_ref.strip():
            return AuthValidationResult(is_valid=False, reason="token_ref is required")
        if not tenant_id or not tenant_id.strip():
            return AuthValidationResult(is_valid=False, reason="tenant_id is required")
        if self._mode == "real_enabled":
            raise AuthError(
                "Production auth calls are not permitted (NO_GO). "
                "Set mode=simulated or mode=real_disabled."
            )
        # Simulated / real_disabled: accept the token reference as presented
        token = AuthTokenV1(
            subject=f"sim-subject:{token_ref[:8]}",
            tenant_id=tenant_id,
            scopes=("aix:cycle:write", "aix:cycle:read"),
            expires_at="2099-01-01T00:00:00Z",
            token_ref=token_ref,
        )
        return AuthValidationResult(is_valid=True, token=token, repaired=False)

    def repair(self, token_ref: str, tenant_id: str) -> AuthValidationResult:
        """Simulate token refresh. In real_enabled mode: raises AuthError (NO_GO)."""
        if self._mode == "real_enabled":
            raise AuthError("Production auth repair is not permitted (NO_GO).")
        if not token_ref or not token_ref.strip():
            return AuthValidationResult(is_valid=False, reason="token_ref is required for repair")
        token = AuthTokenV1(
            subject=f"sim-repaired:{token_ref[:8]}",
            tenant_id=tenant_id,
            scopes=("aix:cycle:write", "aix:cycle:read"),
            expires_at="2099-12-31T00:00:00Z",
            token_ref=f"refreshed:{token_ref}",
        )
        return AuthValidationResult(is_valid=True, token=token, repaired=True)


# ---------------------------------------------------------------------------
# 001I-W02: AIX service principal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AixServicePrincipalV1:
    """Canonical AIX service-principal identity envelope.

    `principal_id` is the stable identity used in audit trails and WorkHub request headers.
    `allowed_scopes` is the static policy set — never expanded at runtime.
    """
    principal_id: str
    display_name: str = "AIX Governed Cycle Service"
    allowed_scopes: tuple[str, ...] = (
        "aix:cycle:write",
        "aix:cycle:read",
        "aix:evidence:write",
        "aix:approval:read",
    )
    # NO_GO scope — must never appear in requests
    forbidden_scopes: tuple[str, ...] = (
        "workhub:lifecycle:write",
        "workhub:close",
        "workhub:merge",
        "workhub:auto_accept",
    )

    def __post_init__(self) -> None:
        if not self.principal_id or not self.principal_id.strip():
            raise ValueError("principal_id must not be empty")

    def assert_scope_allowed(self, scope: str) -> None:
        if scope in self.forbidden_scopes:
            raise AuthError(
                f"AIX service principal is forbidden from using scope {scope!r}. "
                "WorkHub lifecycle is exclusively WorkHub's authority."
            )
        if scope not in self.allowed_scopes:
            raise AuthError(
                f"AIX service principal does not have scope {scope!r} in its policy set."
            )


def build_default_service_principal() -> AixServicePrincipalV1:
    return AixServicePrincipalV1(principal_id="aix-governed-cycle-svc")


# ---------------------------------------------------------------------------
# 001I-W03: Tenant propagation guard
# ---------------------------------------------------------------------------

class TenantPropagationGuard:
    """Validates that the tenant_id on every object in a request chain is identical.

    A mismatch anywhere in the chain is an immediate StopTheLine condition — no request
    with mismatched tenant_ids should ever reach a downstream service.
    """

    def validate_chain(self, tenant_id: str, *objects: Any, field: str = "tenant_id") -> None:
        """Check that every object in `objects` has `.field == tenant_id`.

        Raises:
            TenantViolation: if any mismatch is found.
        """
        if not tenant_id:
            raise TenantViolation("anchor tenant_id must not be empty")
        for i, obj in enumerate(objects):
            obj_tenant = getattr(obj, field, None)
            if obj_tenant != tenant_id:
                raise TenantViolation(
                    f"Tenant propagation violation at position {i}: "
                    f"expected {tenant_id!r}, got {obj_tenant!r} on field {field!r}. "
                    "Cross-tenant bleed is a hard StopTheLine condition."
                )

    def validate_headers(self, expected_tenant_id: str, headers: dict[str, str]) -> None:
        """Validate that the X-Tenant-ID header matches the authoritative tenant_id."""
        header_tenant = headers.get("X-Tenant-ID", "")
        if header_tenant != expected_tenant_id:
            raise TenantViolation(
                f"X-Tenant-ID header {header_tenant!r} does not match "
                f"authoritative tenant_id {expected_tenant_id!r}."
            )


# ---------------------------------------------------------------------------
# 001I-W04: WorkHub parent task load
# ---------------------------------------------------------------------------

class WorkHubTaskLoadMode(StrEnum):
    SIMULATED = "simulated"
    REAL_DISABLED = "real_disabled"
    # real_enabled intentionally omitted — production NO_GO


@dataclass(frozen=True)
class WorkHubParentTaskV1:
    """Minimal view of a WorkHub parent task that AIX is permitted to read."""
    task_ref: str
    tenant_id: str
    status: str          # Read-only reflection — AIX never transitions this
    title: str
    created_by: str
    workhub_task_url: str | None = None


@dataclass(frozen=True)
class WorkHubTaskLoadResult:
    found: bool
    task: WorkHubParentTaskV1 | None = None
    reason: str = ""


class WorkHubParentTaskLoader:
    """Loads and validates a WorkHub parent task by reference.

    In simulated mode: returns a minimal stub that passes all validation.
    In real_disabled mode: raises WorkHubIntegrationError (fail-closed).
    """

    def __init__(self, *, mode: WorkHubTaskLoadMode = WorkHubTaskLoadMode.SIMULATED) -> None:
        self._mode = mode

    def load(self, *, task_ref: str, tenant_id: str) -> WorkHubTaskLoadResult:
        if not task_ref or not task_ref.strip():
            return WorkHubTaskLoadResult(found=False, reason="task_ref is required")
        if not tenant_id or not tenant_id.strip():
            return WorkHubTaskLoadResult(found=False, reason="tenant_id is required")
        if self._mode == WorkHubTaskLoadMode.REAL_DISABLED:
            raise WorkHubIntegrationError(
                f"WorkHub parent task load is disabled (mode={self._mode}). "
                "Production WorkHub calls are not permitted."
            )
        # Simulated: return a minimal stub
        return WorkHubTaskLoadResult(
            found=True,
            task=WorkHubParentTaskV1(
                task_ref=task_ref,
                tenant_id=tenant_id,
                status="OPEN",
                title=f"[SIM] WorkHub task {task_ref}",
                created_by="sim-actor",
            ),
        )


# ---------------------------------------------------------------------------
# 001I-W05: Approval read + validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApprovalRecordV1:
    approval_ref: str
    cycle_id: str
    tenant_id: str
    approved_by: str
    approved_at: str     # ISO-8601 UTC
    scope_snapshot_hash: str
    is_scoped_to_current_plan: bool


@dataclass(frozen=True)
class ApprovalValidationResult:
    is_valid: bool
    approval: ApprovalRecordV1 | None = None
    reason: str = ""


class ApprovalReader:
    """Reads and validates an approval record by reference.

    Validation rules:
      - `is_scoped_to_current_plan` must be True — a stale approval (granted before
        the current plan was produced) cannot authorize the current execution.
      - `tenant_id` on the approval must match the authoritative `tenant_id`.
      - `cycle_id` must match the current cycle.
    """

    def read(
        self,
        *,
        approval_ref: str,
        expected_cycle_id: str,
        expected_tenant_id: str,
    ) -> ApprovalValidationResult:
        if not approval_ref:
            return ApprovalValidationResult(is_valid=False, reason="approval_ref is required")
        # Simulated validation path — no live WorkHub call (production NO_GO)
        stub = ApprovalRecordV1(
            approval_ref=approval_ref,
            cycle_id=expected_cycle_id,
            tenant_id=expected_tenant_id,
            approved_by="sim-reviewer",
            approved_at="2026-01-01T00:00:00Z",
            scope_snapshot_hash=f"sha:{approval_ref[:8]}",
            is_scoped_to_current_plan=True,
        )
        if stub.tenant_id != expected_tenant_id:
            return ApprovalValidationResult(
                is_valid=False, reason=f"Approval tenant_id mismatch: {stub.tenant_id!r}"
            )
        if stub.cycle_id != expected_cycle_id:
            return ApprovalValidationResult(
                is_valid=False, reason=f"Approval cycle_id mismatch: {stub.cycle_id!r}"
            )
        if not stub.is_scoped_to_current_plan:
            return ApprovalValidationResult(
                is_valid=False,
                reason="Approval is not scoped to the current plan. Re-approval is required.",
            )
        return ApprovalValidationResult(is_valid=True, approval=stub)


# ---------------------------------------------------------------------------
# 001I-W06: Evidence / review write-back
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceWritebackRequestV1:
    """A write-back request emitted toward WorkHub. This is a request, not a mutation.

    AIX emits this object; WorkHub decides whether and how to process it.
    """
    cycle_id: str
    tenant_id: str
    workhub_task_ref: str
    evidence_bundle_ref: str
    verification_result_ref: str
    review_recommendation: str   # Advisory only — never carries auto-accept language
    emitted_at: str
    request_only: Literal[True] = True   # Invariant: AIX never directly mutates WorkHub

    def __post_init__(self) -> None:
        forbidden_tokens = {"accept", "close", "approve", "merge", "auto_accept", "auto_close"}
        rec_lower = self.review_recommendation.lower()
        for token in forbidden_tokens:
            if token in rec_lower:
                raise WorkHubIntegrationError(
                    f"review_recommendation carries forbidden WorkHub lifecycle token: {token!r}. "
                    "Use advisory-only language."
                )


@dataclass(frozen=True)
class EvidenceWritebackResult:
    emitted: bool
    request: EvidenceWritebackRequestV1 | None = None
    reason: str = ""


def emit_evidence_writeback(
    *,
    cycle_id: str,
    tenant_id: str,
    workhub_task_ref: str,
    evidence_bundle_ref: str,
    verification_result_ref: str,
    review_recommendation: str,
    emitted_at: str,
) -> EvidenceWritebackResult:
    """Build and emit an evidence write-back request. Fail-closed on empty refs or
    forbidden lifecycle language in `review_recommendation`."""
    for field_name, value in [
        ("cycle_id", cycle_id),
        ("tenant_id", tenant_id),
        ("workhub_task_ref", workhub_task_ref),
        ("evidence_bundle_ref", evidence_bundle_ref),
        ("verification_result_ref", verification_result_ref),
        ("emitted_at", emitted_at),
    ]:
        if not value or not value.strip():
            return EvidenceWritebackResult(emitted=False, reason=f"{field_name} is required")

    try:
        request = EvidenceWritebackRequestV1(
            cycle_id=cycle_id,
            tenant_id=tenant_id,
            workhub_task_ref=workhub_task_ref,
            evidence_bundle_ref=evidence_bundle_ref,
            verification_result_ref=verification_result_ref,
            review_recommendation=review_recommendation,
            emitted_at=emitted_at,
        )
    except WorkHubIntegrationError as exc:
        return EvidenceWritebackResult(emitted=False, reason=str(exc))

    return EvidenceWritebackResult(emitted=True, request=request)


# ---------------------------------------------------------------------------
# 001I-W07: Cross-tenant / authority-boundary abuse guard
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CrossTenantAbuseCheckResult:
    is_clean: bool
    violation_codes: tuple[str, ...] = ()
    detail: str = ""


class CrossTenantAbuseGuard:
    """Scans a batch of objects for cross-tenant bleed or authority-boundary abuses.

    Checks performed:
      1. All objects share the same tenant_id as the anchor.
      2. No object carries a forbidden_effects / prohibited_actions entry that
         implies WorkHub lifecycle mutation.
      3. No workhub_task_ref mismatch across objects (if supplied).
    """

    # Effects that imply illegal cross-boundary mutation
    _ILLEGAL_EFFECTS: frozenset[str] = frozenset(
        {
            "auto_accept",
            "auto_close",
            "auto_create_workhub_task",
            "direct_provider_route",
            "production_deploy",
            "production_mutation",
            "workhub_lifecycle_mutation",
        }
    )

    def check(
        self,
        *,
        anchor_tenant_id: str,
        objects: list[Any],
        allowed_effects: tuple[str, ...] = (),
    ) -> CrossTenantAbuseCheckResult:
        violations: list[str] = []
        detail_parts: list[str] = []

        # 1. Tenant isolation
        for i, obj in enumerate(objects):
            obj_tenant = getattr(obj, "tenant_id", None)
            if obj_tenant is not None and obj_tenant != anchor_tenant_id:
                violations.append("CROSS_TENANT_TENANT_ID_MISMATCH")
                detail_parts.append(
                    f"Object[{i}].tenant_id={obj_tenant!r} != anchor {anchor_tenant_id!r}"
                )

        # 2. Forbidden effects in allowed_effects
        illegal_requested = frozenset(allowed_effects) & self._ILLEGAL_EFFECTS
        if illegal_requested:
            violations.append("FORBIDDEN_EFFECTS_REQUESTED")
            detail_parts.append(f"Illegal effects requested: {sorted(illegal_requested)}")

        # 3. Forbidden effects in object fields
        for i, obj in enumerate(objects):
            for field_name in ("allowed_effects", "requested_effects", "effects"):
                effects = getattr(obj, field_name, None)
                if effects:
                    illegal = frozenset(effects) & self._ILLEGAL_EFFECTS
                    if illegal:
                        violations.append("OBJECT_CARRIES_FORBIDDEN_EFFECTS")
                        detail_parts.append(
                            f"Object[{i}].{field_name} contains illegal effects: {sorted(illegal)}"
                        )

        if violations:
            return CrossTenantAbuseCheckResult(
                is_clean=False,
                violation_codes=tuple(dict.fromkeys(violations)),
                detail="; ".join(detail_parts),
            )
        return CrossTenantAbuseCheckResult(is_clean=True)


__all__ = [
    # shared
    "AuthError",
    "TenantViolation",
    "WorkHubIntegrationError",
    "AuthMode",
    "AuthTokenV1",
    "AuthValidationResult",
    # 001I-W01
    "AuthLoginRepair",
    # 001I-W02
    "AixServicePrincipalV1",
    "build_default_service_principal",
    # 001I-W03
    "TenantPropagationGuard",
    # 001I-W04
    "WorkHubTaskLoadMode",
    "WorkHubParentTaskV1",
    "WorkHubTaskLoadResult",
    "WorkHubParentTaskLoader",
    # 001I-W05
    "ApprovalRecordV1",
    "ApprovalValidationResult",
    "ApprovalReader",
    # 001I-W06
    "EvidenceWritebackRequestV1",
    "EvidenceWritebackResult",
    "emit_evidence_writeback",
    # 001I-W07
    "CrossTenantAbuseCheckResult",
    "CrossTenantAbuseGuard",
]
