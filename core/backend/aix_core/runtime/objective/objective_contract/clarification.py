"""C021: clarification and human confirmation policy.

DoD: unresolved material ambiguity blocks mutation; REUNDERSTAND on changed objective.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

from backend.aix_core.runtime.objective.objective_contract import (
    ObjectiveContractV1,
    understand_objective,
)

ClarificationDisposition = Literal[
    "CLEAR",
    "NEEDS_CLARIFICATION",
    "STALE_ANSWER",
    "CONTRADICTORY_ANSWER",
    "REUNDERSTAND",
]


class ClarificationError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class ClarificationTurn:
    turn_id: str
    question: str
    answer: str | None = None
    answered_at: str | None = None
    objective_hash_at_ask: str = ""


@dataclass
class ClarificationSession:
    session_id: str
    tenant_id: str
    objective_id: str
    contract_id: str
    turns: list[ClarificationTurn] = field(default_factory=list)
    disposition: ClarificationDisposition = "NEEDS_CLARIFICATION"
    created_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "objective_id": self.objective_id,
            "contract_id": self.contract_id,
            "disposition": self.disposition,
            "created_at": self.created_at,
            "turns": [t.__dict__ for t in self.turns],
        }


def classify_ambiguity(contract: ObjectiveContractV1) -> ClarificationDisposition:
    if contract.admission == "ADMITTED" and not any(u.blocks_planning for u in contract.unknowns):
        return "CLEAR"
    return "NEEDS_CLARIFICATION"


def open_clarification_session(
    *,
    tenant_id: str,
    objective_id: str,
    contract: ObjectiveContractV1,
) -> ClarificationSession:
    if classify_ambiguity(contract) == "CLEAR":
        return ClarificationSession(
            session_id=f"cls_{new_ulid()[:12]}",
            tenant_id=tenant_id,
            objective_id=objective_id,
            contract_id=contract.contract_id,
            disposition="CLEAR",
        )
    questions = [u.statement for u in contract.unknowns if u.blocks_planning] or [
        "Please clarify the objective outcomes and constraints."
    ]
    session = ClarificationSession(
        session_id=f"cls_{new_ulid()[:12]}",
        tenant_id=tenant_id,
        objective_id=objective_id,
        contract_id=contract.contract_id,
        disposition="NEEDS_CLARIFICATION",
    )
    for q in questions:
        session.turns.append(
            ClarificationTurn(
                turn_id=f"trn_{new_ulid()[:10]}",
                question=q,
                objective_hash_at_ask=contract.content_hash,
            )
        )
    return session


def apply_clarification_answer(
    session: ClarificationSession,
    *,
    turn_id: str,
    answer: str,
    current_contract: ObjectiveContractV1,
) -> ClarificationSession:
    turn = next((t for t in session.turns if t.turn_id == turn_id), None)
    if turn is None:
        raise ClarificationError("CLARIFICATION_TURN_NOT_FOUND", turn_id)
    if turn.objective_hash_at_ask and turn.objective_hash_at_ask != current_contract.content_hash:
        session.disposition = "STALE_ANSWER"
        raise ClarificationError("STALE_ANSWER", "objective changed since question was asked")
    text = str(answer or "").strip()
    if not text:
        raise ClarificationError("EMPTY_CLARIFICATION_ANSWER")
    # Contradictory: answer re-asserts ambiguity markers without resolving.
    lowered = text.lower()
    if any(m in lowered for m in ("somehow", "whatever", "tbd", "figure it out")):
        session.disposition = "CONTRADICTORY_ANSWER"
        raise ClarificationError("CONTRADICTORY_ANSWER", text[:120])
    turn.answer = text
    turn.answered_at = _utc()
    if all(t.answer for t in session.turns):
        session.disposition = "CLEAR"
    return session


def reunderstand_on_objective_change(
    *,
    tenant_id: str,
    objective_id: str,
    new_objective_text: str,
    definition_of_done: list[str] | None = None,
    previous: ObjectiveContractV1 | None = None,
) -> tuple[ObjectiveContractV1, ClarificationDisposition]:
    """REUNDERSTAND when objective text changes."""
    fresh = understand_objective(
        tenant_id=tenant_id,
        raw_objective=new_objective_text,
        definition_of_done=definition_of_done or [],
        objective_id=objective_id,
    )
    if previous is not None and previous.content_hash != fresh.content_hash:
        return fresh, "REUNDERSTAND"
    return fresh, classify_ambiguity(fresh)


def mutation_allowed(session: ClarificationSession | None, contract: ObjectiveContractV1) -> bool:
    if contract.admission != "ADMITTED":
        return False
    if session is None:
        return classify_ambiguity(contract) == "CLEAR"
    return session.disposition == "CLEAR"


__all__ = [
    "ClarificationDisposition",
    "ClarificationError",
    "ClarificationTurn",
    "ClarificationSession",
    "classify_ambiguity",
    "open_clarification_session",
    "apply_clarification_answer",
    "reunderstand_on_objective_change",
    "mutation_allowed",
]
