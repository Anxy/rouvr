"""C006 command/event contract tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.command_event_contract import (
    COMMAND_EVENT_CONTRACT_VERSION,
    KNOWN_COMMANDS,
    KNOWN_EVENT_TYPES,
    CommandEventContractError,
    build_command,
    build_event,
    round_trip_command,
    round_trip_event,
    validate_contract_version,
)


def test_known_commands_cover_router_surface() -> None:
    for name in (
        "admit_objective",
        "start_cycle",
        "clarify",
        "cancel",
        "reject",
        "block",
        "resume",
        "retry_step",
        "replan",
    ):
        assert name in KNOWN_COMMANDS


def test_unknown_contract_version_fails_closed() -> None:
    with pytest.raises(CommandEventContractError, match="UNKNOWN_CONTRACT_VERSION"):
        validate_contract_version("aix.command_event.v999")


def test_unknown_command_fails_closed() -> None:
    with pytest.raises(CommandEventContractError, match="UNKNOWN_COMMAND"):
        build_command(
            command_name="mutate_production_silently",
            tenant_id="t1",
            actor_id="a1",
            expected_version=0,
            idempotency_key="k1",
        )


def test_unknown_event_fails_closed() -> None:
    with pytest.raises(CommandEventContractError, match="UNKNOWN_EVENT_TYPE"):
        build_event(
            event_type="shadow.fake_completion",
            tenant_id="t1",
            actor_id="a1",
            sequence_number=1,
            command_id="cmd_1",
            correlation_id="corr_1",
        )


def test_command_requires_tenant_actor_idempotency() -> None:
    with pytest.raises(CommandEventContractError, match="TENANT_AND_ACTOR_REQUIRED"):
        build_command(
            command_name="admit_objective",
            tenant_id="",
            actor_id="a1",
            expected_version=0,
            idempotency_key="k1",
        )
    with pytest.raises(CommandEventContractError, match="IDEMPOTENCY_KEY_REQUIRED"):
        build_command(
            command_name="admit_objective",
            tenant_id="t1",
            actor_id="a1",
            expected_version=0,
            idempotency_key="  ",
        )


def test_round_trip_command_and_event() -> None:
    cmd = build_command(
        command_name="admit_objective",
        tenant_id="t1",
        actor_id="human-1",
        expected_version=0,
        idempotency_key="admit:obj:1",
        objective_id="obj_1",
        payload={"text": "fix login"},
    )
    assert cmd.contract_version == COMMAND_EVENT_CONTRACT_VERSION
    assert round_trip_command(cmd) == cmd

    ev = build_event(
        event_type="objective.admitted",
        tenant_id="t1",
        actor_id="human-1",
        sequence_number=1,
        command_id=cmd.idempotency_key,
        correlation_id=cmd.correlation_id,
        objective_id="obj_1",
        payload={"ok": True},
    )
    assert "objective.admitted" in KNOWN_EVENT_TYPES
    assert round_trip_event(ev) == ev


def test_completion_decision_event_is_registered() -> None:
    assert "completion.decision_recorded" in KNOWN_EVENT_TYPES
