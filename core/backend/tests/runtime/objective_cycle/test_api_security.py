from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.aix_core.runtime.objective.api_security import (
    CycleApiSecurityError,
    CycleApiSecurityPolicy,
    CyclePermission,
    GatewayAssertion,
    sign_gateway_assertion,
)

NOW = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
SECRET = b"test-only-gateway-secret"


def _assertion(**changes) -> GatewayAssertion:
    payload = {
        "authorization": "Bearer upstream-validated-token",
        "gateway_id": "hyeve-gateway",
        "tenant_id": "tenant-1",
        "actor_id": "human-1",
        "roles": (CyclePermission.READ.value, CyclePermission.COMMAND.value),
        "issued_at_epoch": NOW_EPOCH,
        "request_id": "request-1",
    }
    payload.update(changes)
    payload.setdefault(
        "signature",
        sign_gateway_assertion(
            secret=SECRET,
            gateway_id=payload["gateway_id"],
            tenant_id=payload["tenant_id"],
            actor_id=payload["actor_id"],
            roles=payload["roles"],
            issued_at_epoch=payload["issued_at_epoch"],
            request_id=payload["request_id"],
        ),
    )
    return GatewayAssertion(**payload)


def _policy() -> CycleApiSecurityPolicy:
    return CycleApiSecurityPolicy(
        secret=SECRET,
        allowed_gateway_ids=("hyeve-gateway",),
        clock=lambda: NOW,
    )


def test_valid_signed_gateway_command_context_is_authorized() -> None:
    context = _policy().authorize(_assertion(), required_permission=CyclePermission.COMMAND)

    assert context.tenant_id == "tenant-1"
    assert context.gateway_id == "hyeve-gateway"


def test_direct_backend_request_without_gateway_identity_is_denied() -> None:
    with pytest.raises(CycleApiSecurityError, match="GATEWAY_IDENTITY_FORBIDDEN"):
        _policy().authorize(_assertion(gateway_id="direct-client"), required_permission=CyclePermission.READ)


def test_invalid_signature_is_denied() -> None:
    with pytest.raises(CycleApiSecurityError, match="GATEWAY_ASSERTION_INVALID"):
        _policy().authorize(_assertion(signature="0" * 64), required_permission=CyclePermission.READ)


def test_expired_assertion_is_denied() -> None:
    with pytest.raises(CycleApiSecurityError, match="GATEWAY_ASSERTION_EXPIRED"):
        _policy().authorize(
            _assertion(issued_at_epoch=NOW_EPOCH - 61),
            required_permission=CyclePermission.READ,
        )


def test_assertion_replay_is_denied() -> None:
    policy = _policy()
    assertion = _assertion()
    policy.authorize(assertion, required_permission=CyclePermission.READ)

    with pytest.raises(CycleApiSecurityError, match="GATEWAY_ASSERTION_REPLAYED"):
        policy.authorize(assertion, required_permission=CyclePermission.READ)


def test_cross_tenant_resource_access_is_denied() -> None:
    with pytest.raises(CycleApiSecurityError, match="TENANT_SCOPE_MISMATCH"):
        _policy().authorize(
            _assertion(),
            required_permission=CyclePermission.READ,
            resource_tenant_id="tenant-2",
        )


def test_read_role_cannot_issue_command() -> None:
    assertion = _assertion(roles=(CyclePermission.READ.value,))

    with pytest.raises(CycleApiSecurityError, match="CYCLE_RBAC_FORBIDDEN"):
        _policy().authorize(assertion, required_permission=CyclePermission.COMMAND)


def test_admin_role_can_read_or_command() -> None:
    assertion = _assertion(roles=(CyclePermission.ADMIN.value,))

    context = _policy().authorize(assertion, required_permission=CyclePermission.COMMAND)
    assert context.roles == (CyclePermission.ADMIN.value,)


def test_missing_bearer_token_is_denied() -> None:
    with pytest.raises(CycleApiSecurityError, match="AUTH_BEARER_REQUIRED"):
        _policy().authorize(_assertion(authorization="Basic upstream-token"), required_permission=CyclePermission.READ)
