from __future__ import annotations

import pytest

from backend.models.aix_execution_packet_models import AixExecutionPacketCurrent
from backend.services.aix_execution_packet_validator import (
    AixExecutionPacketValidationError,
    validate_aix_execution_packet,
)


def _valid_packet() -> dict:
    return {
        "schema_name": "aix_execution_packet_current",
        "schema_version": "v1",
        "packet_id": "pkt-1",
        "correlation_id": "corr-1",
        "causation_id": "cause-1",
        "tenant_id": "default",
        "project_id": "bh-migrated-7-aix",
        "idempotency_key": "idem-1",
        "prompt_profile": "default",
        "sender": {"agent_id": "runner-1", "role": "orchestrator"},
        "target": {"role": "executor"},
        "step": {
            "index": 1,
            "status": "queued",
            "summary": "queued for execution",
            "evidence_refs": [],
        },
    }


def test_execution_packet_model_accepts_valid_payload() -> None:
    model = AixExecutionPacketCurrent.model_validate(_valid_packet())
    assert model.schema_name == "aix_execution_packet_current"
    assert model.step.index == 1
    assert model.target.role == "executor"


def test_execution_packet_validator_accepts_valid_payload() -> None:
    assert validate_aix_execution_packet(_valid_packet(), locus="runtime") == "aix_execution_packet_current"


def test_execution_packet_validator_rejects_unknown_schema() -> None:
    payload = _valid_packet()
    payload["schema_name"] = "legacy_execution_packet_current"
    with pytest.raises(AixExecutionPacketValidationError) as exc:
        validate_aix_execution_packet(payload, locus="runtime")
    assert exc.value.code == "execution_packet_schema_unknown"
    assert exc.value.message == "Unknown execution packet schema"


def test_execution_packet_validator_rejects_missing_target() -> None:
    payload = _valid_packet()
    payload["target"] = {}
    with pytest.raises(AixExecutionPacketValidationError) as exc:
        validate_aix_execution_packet(payload, locus="runtime")
    assert exc.value.code == "execution_packet_invalid"
    assert exc.value.message == "Invalid execution packet"


def test_execution_packet_validator_rejects_extra_metadata_field() -> None:
    payload = _valid_packet()
    payload["metadata"] = {"validation_command": "pytest"}
    with pytest.raises(AixExecutionPacketValidationError) as exc:
        validate_aix_execution_packet(payload, locus="runtime")
    assert exc.value.code == "execution_packet_invalid"
    assert exc.value.message == "Invalid execution packet"
