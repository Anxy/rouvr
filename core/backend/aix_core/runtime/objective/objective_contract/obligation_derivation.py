"""C022: obligation and acceptance derivation from ObjectiveContract.

DoD: each stated outcome/constraint has a stable obligation ID and proof requirement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from backend.aix_core.runtime.objective.objective_contract import ObjectiveContractV1


class ObligationDerivationError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


ProofKind = Literal["test", "artifact", "human_review", "invariant"]


@dataclass(frozen=True)
class DerivedObligation:
    obligation_id: str
    statement: str
    source: Literal["outcome", "constraint"]
    proof_required: ProofKind
    contract_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "statement": self.statement,
            "source": self.source,
            "proof_required": self.proof_required,
            "contract_id": self.contract_id,
        }


def _stable_id(prefix: str, contract_id: str, statement: str) -> str:
    digest = hashlib.sha256(f"{contract_id}:{statement}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def derive_from_contract(contract: ObjectiveContractV1) -> tuple[DerivedObligation, ...]:
    if not contract.outcomes and not contract.constraints:
        raise ObligationDerivationError("NO_DERIVABLE_OBLIGATIONS", contract.contract_id)
    out: list[DerivedObligation] = []
    for outcome in contract.outcomes:
        out.append(
            DerivedObligation(
                obligation_id=_stable_id("ob", contract.contract_id, outcome.statement),
                statement=outcome.statement,
                source="outcome",
                proof_required="test" if outcome.measurable else "human_review",
                contract_id=contract.contract_id,
            )
        )
    for constraint in contract.constraints:
        out.append(
            DerivedObligation(
                obligation_id=_stable_id("ob", contract.contract_id, constraint.statement),
                statement=constraint.statement,
                source="constraint",
                proof_required="invariant" if constraint.hard else "artifact",
                contract_id=contract.contract_id,
            )
        )
    # Stability: same contract yields same IDs.
    return tuple(out)


def coverage_complete(
    *,
    contract: ObjectiveContractV1,
    obligations: tuple[DerivedObligation, ...],
) -> bool:
    required = {o.statement for o in contract.outcomes} | {c.statement for c in contract.constraints}
    covered = {o.statement for o in obligations}
    return required <= covered and len(obligations) >= len(required)


def assert_coverage(contract: ObjectiveContractV1, obligations: tuple[DerivedObligation, ...]) -> None:
    if not coverage_complete(contract=contract, obligations=obligations):
        raise ObligationDerivationError("OBLIGATION_COVERAGE_INCOMPLETE", contract.contract_id)


__all__ = [
    "ObligationDerivationError",
    "ProofKind",
    "DerivedObligation",
    "derive_from_contract",
    "coverage_complete",
    "assert_coverage",
]
