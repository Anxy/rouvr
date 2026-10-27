"""AIX-E2E-CYCLE-001H-W06: bounded replan.

Consumes a `REPLAN_REQUIRED` classification from `failure_taxonomy.py` and produces a
`ReplanDecisionV1` (the contract already built in `001A-W05`/`contracts.py`). This
module's job is narrower than the contract's own validators: the contract already
enforces `objective_unchanged: Literal[True]` and "scope widening requires approval" at
construction time (see `contracts.py::ReplanDecisionV1`) — this module adds the *policy*
layer on top: whether a replan is authorized at all given how many replans this cycle has
already used, and building the `ReplanDecisionV1` from the taxonomy's classification
without ever touching the failed attempt's own (immutable) evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.aix_core.runtime.objective.contracts import ReplanDecisionV1
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1

ReplanAuthorization = Literal["AUTHORIZED", "DENIED"]


class BoundedReplanError(ValueError):
    pass


@dataclass(frozen=True)
class ReplanAuthorizationV1:
    disposition: ReplanAuthorization
    reason_codes: tuple[str, ...]


class BoundedReplanPolicy:
    def authorize(
        self,
        classification: FailureClassificationV1,
        *,
        replans_used: int,
        max_replans: int,
    ) -> ReplanAuthorizationV1:
        if classification.failure_class != "REPLAN_REQUIRED":
            return ReplanAuthorizationV1(
                disposition="DENIED",
                reason_codes=(f"NOT_REPLAN_ELIGIBLE_CLASS:{classification.failure_class}",),
            )
        if replans_used >= max_replans:
            return ReplanAuthorizationV1(disposition="DENIED", reason_codes=("REPLAN_BUDGET_EXHAUSTED",))
        return ReplanAuthorizationV1(disposition="AUTHORIZED", reason_codes=("REPLAN_AUTHORIZED",))

    def propose(
        self,
        *,
        cycle_id: str,
        tenant_id: str,
        triggering_evidence_ref: str,
        reason: str,
        retained_step_ids: tuple[str, ...],
        added_step_ids: tuple[str, ...] = (),
        replaced_step_ids: tuple[str, ...] = (),
        removed_step_ids: tuple[str, ...] = (),
        scope_comparison: str,
        policy_decision_ref: str,
        decided_by: str,
        authorization: ReplanAuthorizationV1,
        force_approval: bool = True,
    ) -> ReplanDecisionV1:
        """Build a `ReplanDecisionV1` for an already-`AUTHORIZED` replan. Raises
        `BoundedReplanError` if `authorization.disposition` is not `AUTHORIZED` — this
        method never silently authorizes on the caller's behalf; `authorize()` must be
        called first and its result passed in explicitly.

        `force_approval` defaults `True` (this policy's original, stricter-than-the-
        contract default — every replan is approval-gated regardless of scope).
        Autonomous Closed-Loop Lane, Packet AL-10 asks for the contract's own actual
        rule instead: same-scope replans (`added_step_ids` empty) continue without a
        human; only scope-widening ones (`added_step_ids` non-empty, which
        `ReplanDecisionV1`'s own validator already refuses to admit with
        `requires_approval=False` — see `contracts.py`) require approval. Pass
        `force_approval=False` to get that looser, contract-matching behavior
        explicitly — never the default, so no existing caller's behavior changes
        just because this parameter was added.
        """
        if authorization.disposition != "AUTHORIZED":
            raise BoundedReplanError(
                f"cannot propose a replan without prior AUTHORIZED authorization "
                f"(got {authorization.disposition!r}: {authorization.reason_codes})"
            )
        requires_approval = True if force_approval else bool(added_step_ids)
        return ReplanDecisionV1(
            cycle_id=cycle_id,
            tenant_id=tenant_id,
            triggering_evidence_ref=triggering_evidence_ref,
            reason=reason,
            retained_step_ids=retained_step_ids,
            added_step_ids=added_step_ids,
            replaced_step_ids=replaced_step_ids,
            removed_step_ids=removed_step_ids,
            scope_comparison=scope_comparison,
            policy_decision_ref=policy_decision_ref,
            decided_by=decided_by,
            requires_approval=requires_approval,
        )


__all__ = [
    "ReplanAuthorization",
    "BoundedReplanError",
    "ReplanAuthorizationV1",
    "BoundedReplanPolicy",
]
