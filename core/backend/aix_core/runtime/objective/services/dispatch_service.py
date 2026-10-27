"""AIX Objective Dispatch Service.

Extracts step selection, claim lease management, and dispatcher observability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.aix_core.runtime.boundaries.dispatcher import (
    log_plan_observed,
    log_step_claimed,
    log_step_ready,
    observe_accepted_plan,
)
from backend.aix_core.runtime.objective.contracts import (
    PlanRevisionV1,
    StepContractV1,
    StepRuntimeStateV1,
)
from backend.aix_core.runtime.objective.step_scheduler import select_next_step
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

logger = logging.getLogger(__name__)


@dataclass
class ClaimResult:
    claimed: bool
    step_id: str | None
    claim_id: str | None
    attempt_number: int
    lease_expires_at: str | None
    reason: str | None


class DispatchService:
    """Handles step selection, lease allocation, and dispatcher boundary observation."""

    def __init__(self, default_lease_seconds: int = 300) -> None:
        self.default_lease_seconds = default_lease_seconds

    def select_step(
        self,
        *,
        plan: PlanRevisionV1,
        runtime_states: dict[str, StepRuntimeStateV1],
    ) -> StepContractV1 | None:
        """Selects the next eligible step from the active plan."""
        return select_next_step(plan, runtime_states)

    def claim_step(
        self,
        *,
        objective_id: str,
        cycle_id: str,
        plan_id: str | None,
        step: StepContractV1,
        current_attempt: int = 1,
        lease_seconds: int | None = None,
    ) -> ClaimResult:
        """Issues an execution claim lease for the selected step."""
        claim_id = f"claim_{new_ulid()}"
        seconds = lease_seconds or self.default_lease_seconds
        expires = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

        log_step_claimed(
            objective_id=objective_id,
            plan_id=plan_id,
            step_id=step.id,
            attempt=current_attempt,
        )

        return ClaimResult(
            claimed=True,
            step_id=step.id,
            claim_id=claim_id,
            attempt_number=current_attempt,
            lease_expires_at=expires,
            reason="step_ready",
        )
