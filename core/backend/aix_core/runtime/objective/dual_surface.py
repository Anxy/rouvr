"""Idempotent linkage between MVP task/run and objective/cycle surfaces."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

DUAL_SURFACE_VERSION = "aix.objective-cycle.dual-surface.v1"


class ContinuationAction(StrEnum):
    START_CYCLE = "START_CYCLE"
    EXECUTE_COMPAT = "EXECUTE_COMPAT"
    BLOCKED_NO_EXECUTION_SURFACE = "BLOCKED_NO_EXECUTION_SURFACE"


class DualSurfaceError(ValueError):
    pass


class DualSurfaceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = DUAL_SURFACE_VERSION
    tenant_id: str = Field(min_length=1)
    workhub_task_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    cycle_id: str | None = None
    legacy_run_id: str | None = None
    idempotency_key: str = Field(min_length=1)


class PostAdmitContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ContinuationAction
    reason_code: str
    objective_id: str | None = None
    cycle_id: str | None = None
    legacy_task_id: str | None = None
    admit_only_success: bool = False


class DualSurfaceRegistry:
    def __init__(self) -> None:
        self._by_idempotency: dict[tuple[str, str], DualSurfaceLink] = {}
        self._by_task: dict[tuple[str, str], DualSurfaceLink] = {}
        self._by_objective: dict[tuple[str, str], DualSurfaceLink] = {}
        self._by_cycle: dict[tuple[str, str], DualSurfaceLink] = {}

    def register(self, link: DualSurfaceLink) -> tuple[DualSurfaceLink, bool]:
        key = (link.tenant_id, link.idempotency_key)
        existing = self._by_idempotency.get(key)
        if existing:
            if existing != link:
                raise DualSurfaceError("DUAL_SURFACE_IDEMPOTENCY_CONFLICT")
            return existing, True
        identities = (
            (self._by_task, (link.tenant_id, link.workhub_task_id)),
            (self._by_objective, (link.tenant_id, link.objective_id)),
        )
        for index, identity in identities:
            if identity in index:
                raise DualSurfaceError("DUAL_SURFACE_IDENTITY_CONFLICT")
        if link.cycle_id and (link.tenant_id, link.cycle_id) in self._by_cycle:
            raise DualSurfaceError("DUAL_SURFACE_IDENTITY_CONFLICT")
        self._by_idempotency[key] = link
        self._by_task[(link.tenant_id, link.workhub_task_id)] = link
        self._by_objective[(link.tenant_id, link.objective_id)] = link
        if link.cycle_id:
            self._by_cycle[(link.tenant_id, link.cycle_id)] = link
        return link, False

    def resolve_task(self, *, tenant_id: str, task_id: str) -> DualSurfaceLink:
        try:
            return self._by_task[(tenant_id, task_id)]
        except KeyError as exc:
            raise DualSurfaceError("DUAL_SURFACE_LINK_NOT_FOUND") from exc

    def resolve_cycle(self, *, tenant_id: str, cycle_id: str) -> DualSurfaceLink:
        try:
            return self._by_cycle[(tenant_id, cycle_id)]
        except KeyError as exc:
            raise DualSurfaceError("DUAL_SURFACE_LINK_NOT_FOUND") from exc


def decide_post_admit_continuation(
    *,
    objective_id: str | None,
    cycle_api_available: bool,
    compatibility_execute_available: bool,
    legacy_task_id: str | None,
) -> PostAdmitContinuation:
    if cycle_api_available and str(objective_id or "").strip():
        return PostAdmitContinuation(
            action=ContinuationAction.START_CYCLE,
            reason_code="CYCLE_API_AVAILABLE",
            objective_id=str(objective_id).strip(),
        )
    if compatibility_execute_available and str(legacy_task_id or "").strip():
        return PostAdmitContinuation(
            action=ContinuationAction.EXECUTE_COMPAT,
            reason_code="COMPATIBILITY_EXECUTE_REQUIRED",
            legacy_task_id=str(legacy_task_id).strip(),
        )
    return PostAdmitContinuation(
        action=ContinuationAction.BLOCKED_NO_EXECUTION_SURFACE,
        reason_code="ADMITTED_BUT_NO_EXECUTION_SURFACE",
    )
