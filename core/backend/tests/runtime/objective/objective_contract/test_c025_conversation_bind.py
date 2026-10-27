"""C025 conversation-to-objective binding tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.objective_contract.conversation_bind import (
    ConversationBindError,
    ConversationObjectiveBinder,
    classify_message,
)


def test_c025_greeting_and_status_do_not_admit_wrong_objective() -> None:
    binder = ConversationObjectiveBinder()
    assert classify_message("hello") == "greeting"
    r1 = binder.handle_message(tenant_id="t", conversation_id="c1", text="Hi there")
    assert r1.action == "ignore"
    r2 = binder.handle_message(
        tenant_id="t",
        conversation_id="c1",
        text="Add /health returning 200",
        definition_of_done=["GET /health returns 200"],
    )
    assert r2.action == "admit"
    oid = r2.objective_id
    r3 = binder.handle_message(tenant_id="t", conversation_id="c1", text="status please")
    assert r3.action == "status"
    assert r3.objective_id == oid


def test_c025_duplicate_admission_and_follow_up_binding() -> None:
    binder = ConversationObjectiveBinder()
    first = binder.handle_message(
        tenant_id="t",
        conversation_id="c2",
        text="Implement rate limiting",
        definition_of_done=["rate limit middleware exists"],
    )
    assert first.action == "admit"
    dup = binder.handle_message(
        tenant_id="t",
        conversation_id="c2",
        text="Implement rate limiting",
        definition_of_done=["rate limit middleware exists"],
    )
    assert dup.action == "duplicate"
    follow = binder.handle_message(tenant_id="t", conversation_id="c2", text="also add metrics")
    assert follow.action == "follow_up"
    assert follow.objective_id == first.objective_id


def test_c025_follow_up_without_objective_fails() -> None:
    binder = ConversationObjectiveBinder()
    with pytest.raises(ConversationBindError) as exc:
        binder.handle_message(tenant_id="t", conversation_id="c3", text="also do the thing")
    assert exc.value.code == "FOLLOW_UP_WITHOUT_OBJECTIVE"
