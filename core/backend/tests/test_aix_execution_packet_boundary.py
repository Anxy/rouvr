from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.routers.integration_contract_router import _validate_execution_packet_if_present
from backend.services.aix_execution_packet_validator import (
    AixExecutionPacketValidationError,
    validate_aix_execution_packet,
)


def _valid_execution_packet() -> dict:
    return {
        "schema_name": "aix_execution_packet_current",
        "schema_version": "v1",
        "packet_id": "pkt-1",
        "correlation_id": "corr-1",
        "causation_id": "pkt-0",
        "tenant_id": "default",
        "project_id": "bh-migrated-7-aix",
        "prompt_profile": "planner.default.current",
        "sender": {"agent_id": "planner-1", "role": "planner"},
        "target": {"role": "coder"},
        "step": {
            "index": 1,
            "status": "queued",
            "summary": "Ship feature",
            "evidence_refs": [],
        },
    }


class TestExecutionPacketValidation:
    def test_validate_execution_packet_accepts_valid(self) -> None:
        schema_name = validate_aix_execution_packet(_valid_execution_packet(), locus="ingress")
        assert schema_name == "aix_execution_packet_current"

    def test_validate_execution_packet_rejects_invalid_step_index(self) -> None:
        payload = _valid_execution_packet()
        payload["step"]["index"] = 0
        with pytest.raises(AixExecutionPacketValidationError) as exc:
            validate_aix_execution_packet(payload, locus="ingress")
        assert exc.value.code == "execution_packet_invalid"
        assert exc.value.locus == "ingress"
        assert exc.value.retryable is False

    def test_validate_execution_packet_rejects_unknown_schema(self) -> None:
        payload = _valid_execution_packet()
        payload["schema_name"] = "unknown.current"
        with pytest.raises(AixExecutionPacketValidationError) as exc:
            validate_aix_execution_packet(payload, locus="ingress")
        assert exc.value.code == "execution_packet_schema_unknown"
        assert exc.value.schema_name == "unknown.current"
        assert exc.value.retryable is False

    def test_validate_execution_packet_rejects_missing_schema_name(self) -> None:
        payload = _valid_execution_packet()
        payload.pop("schema_name")
        with pytest.raises(AixExecutionPacketValidationError) as exc:
            validate_aix_execution_packet(payload, locus="ingress")
        assert exc.value.code == "execution_packet_required"
        assert exc.value.schema_name is None
        assert exc.value.retryable is False


class TestTypedErrorContract:
    def test_error_to_dict_includes_all_fields(self) -> None:
        error = AixExecutionPacketValidationError(
            code="execution_packet_schema_unknown",
            message="Unknown execution packet schema",
            locus="ingress",
            schema_name="bad.current",
            retryable=False,
        )
        detail = error.to_dict()
        assert detail["code"] == "execution_packet_schema_unknown"
        assert detail["message"] == "Unknown execution packet schema"
        assert detail["locus"] == "ingress"
        assert detail["schema_name"] == "bad.current"
        assert detail["retryable"] is False
        assert detail["status"] == 422
        assert detail["type"] == "about:blank"


class TestRouterIntegration:
    def test_router_accepts_valid_payload(self) -> None:
        schema_name = _validate_execution_packet_if_present(_valid_execution_packet(), locus="ingress")
        assert schema_name == "aix_execution_packet_current"

    def test_router_rejects_unknown_schema_with_http_exception(self) -> None:
        payload = _valid_execution_packet()
        payload["schema_name"] = "bad.current"
        with pytest.raises(HTTPException) as exc:
            _validate_execution_packet_if_present(payload, locus="ingress")
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "execution_packet_schema_unknown"
        assert exc.value.detail["retryable"] is False
        assert exc.value.detail["locus"] == "ingress"

    def test_router_rejects_invalid_step_index(self) -> None:
        payload = _valid_execution_packet()
        payload["step"]["index"] = 0
        with pytest.raises(HTTPException) as exc:
            _validate_execution_packet_if_present(payload, locus="ingress")
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "execution_packet_invalid"

    def test_router_returns_none_for_non_packet_payload_on_egress(self) -> None:
        schema_name = _validate_execution_packet_if_present({"hello": "world"}, locus="egress")
        assert schema_name is None
