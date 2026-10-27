"""C021 clarification policy tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.objective_contract import understand_objective
from backend.aix_core.runtime.objective.objective_contract.clarification import (
    ClarificationError,
    apply_clarification_answer,
    classify_ambiguity,
    mutation_allowed,
    open_clarification_session,
    reunderstand_on_objective_change,
)


def test_needs_clarification_blocks_mutation() -> None:
    c = understand_objective(
        tenant_id="t",
        raw_objective="somehow fix it, whatever works",
        definition_of_done=["fixed"],
    )
    session = open_clarification_session(tenant_id="t", objective_id="obj_1", contract=c)
    assert session.disposition == "NEEDS_CLARIFICATION"
    assert mutation_allowed(session, c) is False


def test_clarification_answer_clears_session() -> None:
    c = understand_objective(
        tenant_id="t",
        raw_objective="maybe rewrite the auth module",
        definition_of_done=["auth module rewritten"],
    )
    session = open_clarification_session(tenant_id="t", objective_id="obj_1", contract=c)
    turn = session.turns[0]
    apply_clarification_answer(
        session,
        turn_id=turn.turn_id,
        answer="Rewrite JWT validation in auth/jwt.py only",
        current_contract=c,
    )
    assert session.disposition == "CLEAR"


def test_stale_answer_rejected() -> None:
    c1 = understand_objective(
        tenant_id="t",
        raw_objective="maybe add logging",
        definition_of_done=["logging added"],
    )
    session = open_clarification_session(tenant_id="t", objective_id="obj_1", contract=c1)
    c2, disposition = reunderstand_on_objective_change(
        tenant_id="t",
        objective_id="obj_1",
        new_objective_text="maybe add metrics instead",
        definition_of_done=["metrics added"],
        previous=c1,
    )
    assert disposition == "REUNDERSTAND"
    with pytest.raises(ClarificationError) as exc:
        apply_clarification_answer(
            session,
            turn_id=session.turns[0].turn_id,
            answer="Add structured logging to api.py",
            current_contract=c2,
        )
    assert exc.value.code == "STALE_ANSWER"


def test_contradictory_answer_rejected() -> None:
    c = understand_objective(
        tenant_id="t",
        raw_objective="figure it out somehow",
        definition_of_done=["done"],
    )
    session = open_clarification_session(tenant_id="t", objective_id="obj_1", contract=c)
    with pytest.raises(ClarificationError) as exc:
        apply_clarification_answer(
            session,
            turn_id=session.turns[0].turn_id,
            answer="whatever works, somehow",
            current_contract=c,
        )
    assert exc.value.code == "CONTRADICTORY_ANSWER"


def test_clear_contract_has_clear_disposition() -> None:
    c = understand_objective(
        tenant_id="t",
        raw_objective="Add /health endpoint returning 200",
        definition_of_done=["GET /health returns 200"],
    )
    assert classify_ambiguity(c) == "CLEAR"
    session = open_clarification_session(tenant_id="t", objective_id="obj_1", contract=c)
    assert session.disposition == "CLEAR"
    assert mutation_allowed(session, c) is True
