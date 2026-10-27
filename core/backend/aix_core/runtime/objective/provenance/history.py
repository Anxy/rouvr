"""Append-only in-memory provenance history. Intent is write-once per objective."""

from __future__ import annotations

from backend.aix_core.runtime.objective.provenance.admit import IntentFrozenError
from backend.aix_core.runtime.objective.provenance.contracts import (
    AttemptEvidence,
    ExplorationEvidence,
    ExplorationNeed,
    GovernanceRevision,
    PlanRevision,
    UnderstandingRevision,
    UserIntent,
    UserTurn,
)


class ProvenanceHistory:
    """Keyed by objective_id. Revisions are referenced, never overwritten in place."""

    def __init__(self) -> None:
        self._intents: dict[str, UserIntent] = {}
        self._turns: dict[str, list[UserTurn]] = {}
        self._understandings: dict[str, list[UnderstandingRevision]] = {}
        self._needs: dict[str, list[ExplorationNeed]] = {}
        self._evidence: dict[str, list[ExplorationEvidence]] = {}
        self._governance: dict[str, list[GovernanceRevision]] = {}
        self._plans: dict[str, list[PlanRevision]] = {}
        self._attempts: dict[str, list[AttemptEvidence]] = {}

    def put_intent(self, intent: UserIntent) -> UserIntent:
        existing = self._intents.get(intent.objective_id)
        if existing is None:
            self._intents[intent.objective_id] = intent
            return intent
        if existing.text != intent.text:
            raise IntentFrozenError(
                f"UserIntent.text is frozen for {intent.objective_id}; "
                "append a UserTurn instead of rewriting Intent"
            )
        return existing

    def get_intent(self, objective_id: str) -> UserIntent | None:
        return self._intents.get(objective_id)

    def require_intent(self, objective_id: str) -> UserIntent:
        intent = self.get_intent(objective_id)
        if intent is None:
            raise IntentFrozenError(f"no frozen UserIntent for {objective_id}")
        return intent

    def append_turn(self, turn: UserTurn) -> UserTurn:
        intent = self.require_intent(turn.objective_id)
        if turn.intent_id != intent.id:
            raise IntentFrozenError("UserTurn.intent_id must equal the frozen UserIntent.id")
        if intent.text != self._intents[turn.objective_id].text:
            raise IntentFrozenError("UserIntent.text changed after freeze")
        self._turns.setdefault(turn.objective_id, []).append(turn)
        return turn

    def turns(self, objective_id: str) -> tuple[UserTurn, ...]:
        return tuple(self._turns.get(objective_id, ()))

    def user_corpus(self, objective_id: str) -> str:
        intent = self.require_intent(objective_id)
        parts = [intent.text, *[turn.text for turn in self.turns(objective_id)]]
        return "\n".join(parts)

    def assert_intent_unchanged(self, objective_id: str, expected_text: str) -> None:
        intent = self.require_intent(objective_id)
        if intent.text != expected_text:
            raise IntentFrozenError("UserIntent.text is not byte-for-byte the admit snapshot")

    def put_understanding(self, revision: UnderstandingRevision) -> UnderstandingRevision:
        intent = self.require_intent(revision.objective_id)
        if revision.intent_id != intent.id:
            raise IntentFrozenError("UnderstandingRevision.intent_id must equal the frozen UserIntent.id")
        self._understandings.setdefault(revision.objective_id, []).append(revision)
        return revision

    def understandings(self, objective_id: str) -> tuple[UnderstandingRevision, ...]:
        return tuple(self._understandings.get(objective_id, ()))

    def latest_understanding(self, objective_id: str) -> UnderstandingRevision | None:
        items = self._understandings.get(objective_id) or []
        return items[-1] if items else None

    def put_need(self, need: ExplorationNeed) -> ExplorationNeed:
        self.require_intent(need.objective_id)
        self._needs.setdefault(need.objective_id, []).append(need)
        return need

    def needs(self, objective_id: str) -> tuple[ExplorationNeed, ...]:
        return tuple(self._needs.get(objective_id, ()))

    def put_evidence(self, item: ExplorationEvidence) -> ExplorationEvidence:
        self.require_intent(item.objective_id)
        known = {need.id for need in self.needs(item.objective_id)}
        if item.need_id not in known:
            raise IntentFrozenError(f"evidence {item.id} need_id does not resolve")
        self._evidence.setdefault(item.objective_id, []).append(item)
        return item

    def evidence(self, objective_id: str) -> tuple[ExplorationEvidence, ...]:
        return tuple(self._evidence.get(objective_id, ()))

    def put_governance(self, revision: GovernanceRevision) -> GovernanceRevision:
        self.require_intent(revision.objective_id)
        self._governance.setdefault(revision.objective_id, []).append(revision)
        return revision

    def latest_governance(self, objective_id: str) -> GovernanceRevision | None:
        items = self._governance.get(objective_id) or []
        return items[-1] if items else None

    def put_plan(self, plan: PlanRevision) -> PlanRevision:
        self.require_intent(plan.objective_id)
        self._plans.setdefault(plan.objective_id, []).append(plan)
        return plan

    def plans(self, objective_id: str) -> tuple[PlanRevision, ...]:
        return tuple(self._plans.get(objective_id, ()))

    def latest_plan(self, objective_id: str) -> PlanRevision | None:
        items = self._plans.get(objective_id) or []
        return items[-1] if items else None

    def put_attempt(self, objective_id: str, item: AttemptEvidence) -> AttemptEvidence:
        self.require_intent(objective_id)
        self._attempts.setdefault(objective_id, []).append(item)
        return item

    def attempts(self, objective_id: str) -> tuple[AttemptEvidence, ...]:
        return tuple(self._attempts.get(objective_id, ()))
