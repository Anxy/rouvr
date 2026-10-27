"""Deterministic containment checks for objective-cycle execution plans.

Models may propose plans, but they do not certify that a plan can perform the
requested work.  This module deliberately has no model or I/O dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


EXECUTABLE_CATEGORY = "EXECUTABLE"

# AIX-UGEF-001: derived from the canonical foundation capability registry.
# No longer a separate manually-maintained set.
from backend.aix_core.runtime.foundation.capability_registry import all_registered_capability_ids

REGISTERED_CAPABILITIES = all_registered_capability_ids()


@dataclass(frozen=True)
class PlanValidationResult:
    valid: bool
    reason_code: str | None = None
    detail: str = ""


def validate_executable_plan(
    steps: Iterable[Mapping[str, Any]],
    *,
    execution_required: bool,
    target_resource: str | None = None,
) -> PlanValidationResult:
    """Reject execution objectives that contain no target-bound executable step.

    Merely adding an unrelated executable action is insufficient: when a target
    resource is known, at least one executable step must name that same resource.
    """

    normalized_steps = list(steps)
    if not execution_required:
        return PlanValidationResult(valid=True)

    executable = [
        step
        for step in normalized_steps
        if str(step.get("category") or "").strip().upper() == EXECUTABLE_CATEGORY
    ]
    if not executable:
        return PlanValidationResult(
            valid=False,
            reason_code="NO_EXECUTABLE_STEPS",
            detail="execution objective has no EXECUTABLE plan step",
        )

    expected = str(target_resource or "").strip()
    if expected and not any(str(step.get("target_resource") or "").strip() == expected for step in executable):
        return PlanValidationResult(
            valid=False,
            reason_code="NO_TARGET_RESOURCE_ACCESSED",
            detail="no EXECUTABLE plan step targets the requested resource",
        )

    return PlanValidationResult(valid=True)


def validate_plan_capabilities(steps: Iterable[Mapping[str, Any]]) -> PlanValidationResult:
    """Reject a plan whose step names a capability with no registered implementation.

    Checked in step order; the first unregistered capability determines the result
    (deterministic, reproducible -- never picks a different offender on a re-check of
    the same plan).
    """
    for step in steps:
        capability = str(step.get("capability") or step.get("action") or "").strip()
        if capability and capability not in REGISTERED_CAPABILITIES:
            return PlanValidationResult(
                valid=False,
                reason_code="CAPABILITY_NOT_REGISTERED",
                detail=f"capability {capability!r} has no registered implementation",
            )
    return PlanValidationResult(valid=True)


__all__ = [
    "EXECUTABLE_CATEGORY",
    "REGISTERED_CAPABILITIES",
    "PlanValidationResult",
    "validate_executable_plan",
    "validate_plan_capabilities",
]
