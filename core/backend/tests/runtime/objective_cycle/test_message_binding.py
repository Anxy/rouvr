"""AIX-REAL-EXECUTION-002: conversation -> objective message binding."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.message_binding import (
    MessageBindingError,
    bind_objective_source_message,
    is_pure_greeting,
)


def _msg(message_id: str, role: str, content: str, parent: str | None = None) -> dict:
    return {"message_id": message_id, "role": role, "content": content, "parent_message_id": parent}


def test_greeting_then_objective_binds_to_the_objective_message() -> None:
    messages = [
        _msg("m1", "user", "Hey"),
        _msg("m2", "user", "Check D:\\v2.1\\hyeve\\services\\hyeve-integration-service\\.env", parent="m1"),
    ]
    result = bind_objective_source_message(messages, conversation_id="conv_1")
    assert result.objective_source_message_id == "m2"
    assert result.message_id == "m2"
    assert result.selected_message_id == "m2"
    assert result.parent_message_id == "m1"
    assert result.objective_text.startswith("Check D:\\v2.1\\hyeve")
    assert result.conversation_id == "conv_1"


def test_pure_greeting_never_becomes_objective_text() -> None:
    assert is_pure_greeting("Hey")
    assert is_pure_greeting("hi!")
    assert is_pure_greeting("  Hello.  ")
    assert not is_pure_greeting("Hey, check this file")


def test_all_greetings_fail_closed_to_intent_unclear() -> None:
    messages = [_msg("m1", "user", "Hey"), _msg("m2", "user", "hello!")]
    with pytest.raises(MessageBindingError) as excinfo:
        bind_objective_source_message(messages)
    assert excinfo.value.code == "INTENT_UNCLEAR"


def test_no_user_messages_fail_closed() -> None:
    messages = [{"message_id": "m1", "role": "assistant", "content": "How can I help?"}]
    with pytest.raises(MessageBindingError) as excinfo:
        bind_objective_source_message(messages)
    assert excinfo.value.code == "INTENT_UNCLEAR"


def test_explicit_selected_message_overrides_latest() -> None:
    messages = [
        _msg("m1", "user", "Check file A"),
        _msg("m2", "user", "Actually check file B"),
    ]
    result = bind_objective_source_message(messages, selected_message_id="m1")
    assert result.objective_source_message_id == "m1"
    assert result.objective_text == "Check file A"


def test_explicit_selected_message_must_be_a_real_user_message() -> None:
    messages = [_msg("m1", "user", "Check file A")]
    with pytest.raises(MessageBindingError) as excinfo:
        bind_objective_source_message(messages, selected_message_id="does-not-exist")
    assert excinfo.value.code == "WRONG_SOURCE_MESSAGE"


def test_latest_objective_bearing_message_wins_over_earlier_ones() -> None:
    messages = [
        _msg("m1", "user", "Hey"),
        _msg("m2", "assistant", "Hi, how can I help?"),
        _msg("m3", "user", "thanks"),
        _msg("m4", "user", "Review the auth service"),
    ]
    result = bind_objective_source_message(messages)
    assert result.objective_source_message_id == "m4"
    assert result.objective_text == "Review the auth service"


def test_short_refinement_appends_to_prior_create_objective() -> None:
    messages = [
        _msg(
            "m1",
            "user",
            "Create a simple attendance registration system with a PHP backend "
            "and HTML frontend, allowing only the teacher to add attendance registrations.",
        ),
        _msg(
            "m2",
            "user",
            "thats too long it can be short as only teacher will add there registeration",
        ),
    ]
    result = bind_objective_source_message(messages)
    assert result.objective_source_message_id == "m1"
    assert "attendance registration system" in result.objective_text
    assert "PHP backend" in result.objective_text
    assert "Additional constraints from user:" in result.objective_text
    assert "too long" in result.objective_text


def test_new_create_task_still_replaces_prior_objective() -> None:
    messages = [
        _msg("m1", "user", "Create an attendance HTML page with a PHP backend."),
        _msg("m2", "user", "Create a login page in HTML and CSS instead."),
    ]
    result = bind_objective_source_message(messages)
    assert result.objective_source_message_id == "m2"
    assert result.objective_text == "Create a login page in HTML and CSS instead."
