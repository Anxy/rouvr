"""CapabilityRegistry: general, reusable registration/lookup/versioning for CapabilityV1.

Deliberately infra-only. Every registration requires a `source_ref` -- a concrete
pointer to where the capability's content actually came from (a file:line, a
generated-at-runtime extraction, or an explicit "no real content exists" marker).
This is a structural choice, not a formality: it makes it impossible to register a
capability with invented domain prose (a "Finance Domain" or "Security" capability,
for example) without that fabrication being visible and honest in the registry
itself. See docs/current/architecture/AIX-GOVERNED-PERSONA-RUNTIME.md for why this
registry ships with exactly the real capabilities that exist today and nothing more.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from backend.aix_core.persona_runtime.entities import CapabilityV1

CapabilityStatus = Literal["active", "deprecated"]

NO_REAL_CONTENT_SOURCE_REF = "NO_REAL_CONTENT_EXISTS_IN_REPOSITORY"


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CapabilityVersionRecord:
    capability_id: str
    capability_version: str
    capability: CapabilityV1
    source_ref: str
    status: CapabilityStatus = "active"
    registered_at: str = field(default_factory=_utc)


class CapabilityRegistryError(ValueError):
    pass


class CapabilityRegistry:
    """In-process capability registry. Mirrors the register/deprecate shape of
    objective_cycle/prompt_registry.py's PromptRegistry (same versioning pattern,
    extended with the mandatory source_ref this registry adds)."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], CapabilityVersionRecord] = {}
        self._active_version: dict[str, str] = {}
        self._history: list[dict[str, str]] = []

    def register(self, capability: CapabilityV1, *, source_ref: str) -> CapabilityVersionRecord:
        if not source_ref.strip():
            raise CapabilityRegistryError(
                f"CAPABILITY_SOURCE_REF_REQUIRED:{capability.capability_id} -- every registered "
                "capability must declare where its content came from. Use "
                "NO_REAL_CONTENT_SOURCE_REF if the capability genuinely has no real content "
                "(tools-only), never leave this blank to imply otherwise."
            )
        key = (capability.capability_id, capability.capability_version)
        if key in self._versions:
            raise CapabilityRegistryError(f"CAPABILITY_VERSION_ALREADY_REGISTERED:{key}")
        record = CapabilityVersionRecord(
            capability_id=capability.capability_id,
            capability_version=capability.capability_version,
            capability=capability,
            source_ref=source_ref.strip(),
        )
        self._versions[key] = record
        self._active_version[capability.capability_id] = capability.capability_version
        self._history.append(
            {
                "event": "register",
                "capability_id": capability.capability_id,
                "version": capability.capability_version,
                "source_ref": record.source_ref,
                "at": record.registered_at,
            }
        )
        return record

    def get(self, capability_id: str) -> CapabilityV1:
        version = self._active_version.get(capability_id)
        if version is None:
            raise CapabilityRegistryError(f"CAPABILITY_NOT_REGISTERED:{capability_id}")
        return self._versions[(capability_id, version)].capability

    def get_record(self, capability_id: str) -> CapabilityVersionRecord:
        version = self._active_version.get(capability_id)
        if version is None:
            raise CapabilityRegistryError(f"CAPABILITY_NOT_REGISTERED:{capability_id}")
        return self._versions[(capability_id, version)]

    def resolve(self, capability_ids: tuple[str, ...]) -> tuple[CapabilityV1, ...]:
        """Resolves requested ids to registered capabilities. Mirrors compose()'s own
        missing-capability semantics deliberately: ids not found here are simply
        omitted, not raised on -- compose() already reports missing_capability_ids
        for whatever it's handed, so this registry doesn't need a second error path
        for the same condition."""
        resolved: list[CapabilityV1] = []
        for capability_id in capability_ids:
            version = self._active_version.get(capability_id)
            if version is not None:
                resolved.append(self._versions[(capability_id, version)].capability)
        return tuple(resolved)

    def deprecate(self, capability_id: str, *, reason: str) -> CapabilityVersionRecord:
        record = self.get_record(capability_id)
        updated = CapabilityVersionRecord(
            capability_id=record.capability_id,
            capability_version=record.capability_version,
            capability=record.capability,
            source_ref=record.source_ref,
            status="deprecated",
            registered_at=record.registered_at,
        )
        self._versions[(record.capability_id, record.capability_version)] = updated
        del self._active_version[capability_id]
        self._history.append(
            {"event": "deprecate", "capability_id": capability_id, "reason": reason, "at": _utc()}
        )
        return updated

    def list_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active_version))

    def evidence(self) -> tuple[dict[str, str], ...]:
        return tuple(self._history)


def build_default_registry() -> CapabilityRegistry:
    """Populates the registry from real manifest data for all five objective-cycle
    roles via manifest_adapter.capabilities_from_manifest -- the same real source used
    by the composer/renderer parity tests. Every capability here traces to either real
    extracted PlannerPromptBuilder content (executor_coder) or an honest
    NO_REAL_CONTENT_SOURCE_REF marker (the other four roles) -- nothing invented."""
    from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId, FIVE_ROLE_DECISIONS
    from backend.aix_core.persona_runtime.manifest_adapter import (
        MISSING_REAL_CONTENT,
        capabilities_from_manifest,
        load_real_manifests,
    )

    registry = CapabilityRegistry()
    manifests = load_real_manifests()
    for decision in FIVE_ROLE_DECISIONS:
        role = decision.role_id
        for capability in capabilities_from_manifest(manifests[role], role):
            source_ref = (
                "Extracted verbatim at runtime from PlannerPromptBuilder.system_prompt(lane='generic') "
                "via manifest_adapter._real_executor_coder_tool_invocation_protocol; requested_tools "
                f"from backend/config/persona_manifests/PM-OBJECTIVE-CYCLE-{role.value.upper()}-V1.json"
                if role is AgentRoleId.EXECUTOR_CODER
                else (
                    f"requested_tools from the real manifest file; no knowledge/verification_rules/"
                    f"tool_invocation_protocol content exists in the repo for this role -- "
                    f"{MISSING_REAL_CONTENT.get(role, '')} ({NO_REAL_CONTENT_SOURCE_REF})"
                )
            )
            registry.register(capability, source_ref=source_ref)
    return registry
