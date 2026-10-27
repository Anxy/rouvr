from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.experimental.client_contracts import (
    OPERATION_CONTRACTS,
    ClientOperation,
    CycleClientContractError,
    CycleClientRequestBuilder,
)


def _builder(base_url: str = "https://gateway.example") -> CycleClientRequestBuilder:
    return CycleClientRequestBuilder(
        gateway_base_url=base_url,
        bearer_token="token",
        tenant_id="tenant-1",
    )


def test_every_declared_client_operation_has_one_contract() -> None:
    assert set(OPERATION_CONTRACTS) == set(ClientOperation)


def test_client_surface_contains_no_accept_close_provider_or_temporal_operation() -> None:
    operation_text = " ".join(item.value for item in ClientOperation)

    assert "accept" not in operation_text
    assert "close" not in operation_text
    assert "provider" not in operation_text
    assert "temporal" not in operation_text


def test_read_request_uses_gateway_auth_and_tenant_without_command_headers() -> None:
    request = _builder().build(
        ClientOperation.GET_CYCLE,
        path_parameters={"cycle_id": "cycle-1"},
    )

    assert request.method == "GET"
    assert request.url == "https://gateway.example/aix/cycles/cycle-1"
    assert request.headers["Authorization"] == "Bearer token"
    assert request.headers["X-Tenant-ID"] == "tenant-1"
    assert "Idempotency-Key" not in request.headers


def test_command_requires_idempotency_and_version_when_state_changes() -> None:
    with pytest.raises(CycleClientContractError, match="CLIENT_IDEMPOTENCY_KEY_REQUIRED"):
        _builder().build(ClientOperation.CANCEL, path_parameters={"cycle_id": "cycle-1"})
    with pytest.raises(CycleClientContractError, match="CLIENT_EXPECTED_VERSION_REQUIRED"):
        _builder().build(
            ClientOperation.CANCEL,
            path_parameters={"cycle_id": "cycle-1"},
            idempotency_key="key-1",
        )

    request = _builder().build(
        ClientOperation.CANCEL,
        path_parameters={"cycle_id": "cycle-1"},
        body={"reason": "human requested"},
        idempotency_key="key-1",
        expected_version=7,
    )
    assert request.headers["If-Match"] == 'W/"7"'
    assert request.headers["Idempotency-Key"] == "key-1"


def test_start_cycle_requires_idempotency_but_not_existing_version() -> None:
    request = _builder().build(
        ClientOperation.START_CYCLE,
        path_parameters={"objective_id": "objective-1"},
        idempotency_key="key-1",
        body={},
    )

    assert request.method == "POST"
    assert "If-Match" not in request.headers


def test_non_local_plain_http_base_url_is_rejected() -> None:
    with pytest.raises(CycleClientContractError, match="GATEWAY_HTTPS_REQUIRED"):
        _builder("http://gateway.example")


def test_localhost_http_is_allowed_for_local_no_go_testing() -> None:
    request = _builder("http://127.0.0.1:8000").build(ClientOperation.LIST_CYCLES)

    assert request.url == "http://127.0.0.1:8000/aix/cycles"


def test_path_parameters_must_match_template_exactly() -> None:
    with pytest.raises(CycleClientContractError, match="CLIENT_PATH_PARAMETERS_INVALID"):
        _builder().build(ClientOperation.GET_CYCLE, path_parameters={"objective_id": "wrong"})
