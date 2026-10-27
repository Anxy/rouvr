"""P1: freeze UserIntent at admit; append UserTurns. Never mutate Intent.text."""

from __future__ import annotations

from typing import Literal

from backend.aix_core.runtime.objective.provenance.contracts import (
    UnderstandingClaim,
    UnderstandingRevision,
    UserIntent,
    UserTurn,
)
from backend.aix_core.runtime.objective.provenance.validators import USER_DERIVED_KINDS

ScopeTurnMode = Literal["extend", "replace"]

# Client/facet default DoD labels (objective_scope.extract_scope_requirements).
# These are not user-authored scope.
_FACET_DOD_LABELS: frozenset[str] = frozenset(
    {
        "frontend / ui surface",
        "backend / server runtime",
        "api / endpoints",
        "database / persistence",
        "schema migration",
        "tests / verification",
        "configuration",
        "external integration",
        "documentation",
    }
)


class IntentFrozenError(Exception):
    """Raised when a caller attempts to rewrite a frozen UserIntent."""


def infer_dod_user_authored(
    *,
    definition_of_done: tuple[str, ...],
    intent_text: str,
    explicit: bool | None = None,
) -> bool:
    """True iff DoD came from the user/chat, not client facet defaults."""
    if explicit is True:
        return True
    if explicit is False:
        return False
    if not definition_of_done:
        return False
    corpus = " ".join(str(intent_text or "").casefold().split())
    labels = tuple(" ".join(str(item or "").casefold().split()) for item in definition_of_done)
    if labels and all(label in _FACET_DOD_LABELS for label in labels):
        return False
    return all(label and label in corpus for label in labels)


def freeze_user_intent(
    *,
    objective_id: str,
    text: str,
    definition_of_done: tuple[str, ...] = (),
    dod_user_authored: bool | None = None,
    source_message_id: str | None = None,
    conversation_id: str | None = None,
) -> UserIntent:
    snapshot = str(text or "")
    if not snapshot.strip():
        raise ValueError("UserIntent.text must be non-empty")
    authored = (
        dod_user_authored
        if dod_user_authored is not None
        else infer_dod_user_authored(
            definition_of_done=definition_of_done,
            intent_text=snapshot,
        )
    )
    return UserIntent(
        objective_id=objective_id,
        text=snapshot,
        definition_of_done=tuple(definition_of_done),
        dod_user_authored=bool(authored),
        source_message_id=source_message_id,
        conversation_id=conversation_id,
    )


def original_objective_alias(intent: UserIntent) -> str:
    """`original_objective` is Intent.text until later migration."""
    return intent.text


def append_user_turn(
    *,
    intent: UserIntent,
    text: str,
    source_message_id: str | None = None,
) -> UserTurn:
    """Clarify/replan append a UserTurn. Does not and cannot mutate Intent."""
    body = str(text or "").strip()
    if not body:
        raise ValueError("UserTurn.text must be non-empty")
    return UserTurn(
        objective_id=intent.objective_id,
        intent_id=intent.id,
        text=body,
        source_message_id=source_message_id,
    )


def dod_texts_eligible_for_scope(intent: UserIntent) -> tuple[str, ...]:
    """DoD is not SCOPE unless the user authored it."""
    if not intent.dod_user_authored:
        return ()
    return intent.definition_of_done


def apply_scope_turn(
    *,
    intent: UserIntent,
    parent: UnderstandingRevision,
    turn: UserTurn,
    mode: ScopeTurnMode,
    new_claims: tuple[UnderstandingClaim, ...],
    replace_claim_ids: tuple[str, ...] = (),
) -> UnderstandingRevision:
    """Mechanical U7 operator. Does not interpret the turn (that is P2)."""
    if parent.intent_id != intent.id:
        raise IntentFrozenError("understanding does not cite this UserIntent")
    if turn.intent_id != intent.id or turn.objective_id != intent.objective_id:
        raise IntentFrozenError("UserTurn does not cite this UserIntent")
    if mode == "extend":
        claims = tuple(parent.claims) + tuple(new_claims)
    elif mode == "replace":
        replace_ids = set(replace_claim_ids)
        if not replace_ids:
            replace_ids = {
                c.id
                for c in parent.claims
                if c.status == "ACTIVE" and c.kind in USER_DERIVED_KINDS
            }
        rewritten: list[UnderstandingClaim] = []
        for claim in parent.claims:
            if claim.id in replace_ids and claim.status == "ACTIVE":
                rewritten.append(claim.model_copy(update={"status": "SUPERSEDED"}))
            else:
                rewritten.append(claim)
        claims = tuple(rewritten) + tuple(new_claims)
    else:
        raise ValueError(f"unknown scope turn mode: {mode!r}")
    return UnderstandingRevision(
        objective_id=parent.objective_id,
        intent_id=intent.id,
        parent_revision_id=parent.id,
        scope_mode=parent.scope_mode,
        claims=claims,
    )
