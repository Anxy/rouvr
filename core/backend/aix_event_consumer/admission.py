"""Admission seam for the maintained AIX event consumer.

The consumer admits an event only through an injected policy. The default
policy gates on tenant and event-type allowlists plus a task-reference
requirement; #72 plugs in denying policies through the same seam.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .events import AIXEvent


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason_code: str


class AdmissionPolicy(Protocol):
    def decide(self, event: AIXEvent) -> AdmissionDecision: ...


@dataclass(frozen=True)
class DefaultAdmissionPolicy:
    allowed_tenants: tuple[str, ...] | None = None
    allowed_event_types: tuple[str, ...] | None = None
    require_task_ref: bool = True

    def decide(self, event: AIXEvent) -> AdmissionDecision:
        if self.allowed_tenants is not None and event.tenant_id not in self.allowed_tenants:
            return AdmissionDecision(admitted=False, reason_code="TENANT_NOT_ALLOWED")
        if self.allowed_event_types is not None and event.event_type not in self.allowed_event_types:
            return AdmissionDecision(admitted=False, reason_code="EVENT_TYPE_NOT_ALLOWED")
        if self.require_task_ref and event.task_id is None and event.task_number is None:
            return AdmissionDecision(admitted=False, reason_code="TASK_REF_REQUIRED")
        return AdmissionDecision(admitted=True, reason_code="ADMITTED")
