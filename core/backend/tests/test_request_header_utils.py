from __future__ import annotations

import base64
import json
import time

from backend.shared.request_header_utils import (
    build_service_headers,
    diagnose_service_token,
    inspect_service_token_claims,
    resolve_project_header,
    resolve_tenant_header,
)


def _make_jwt(payload: dict) -> str:
    """Build a minimal unsigned JWT for testing (header.payload.sig)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.fakesig"


def test_build_service_headers_uses_fallback_project_and_correlation() -> None:
    headers = build_service_headers(
        service_token="token-1",
        tenant_id="default",
        fallback_project_id="bh-migrated-7-aix",
        fallback_correlation_id="corr-fallback",
        require_auth=True,
        require_tenant=True,
    )
    assert headers["Authorization"] == "Bearer token-1"
    assert headers["X-Tenant-ID"] == "default"
    assert headers["X-Project-ID"] == "bh-migrated-7-aix"
    assert headers["X-Correlation-ID"] == "corr-fallback"


def test_build_service_headers_auto_generates_correlation() -> None:
    headers = build_service_headers(
        service_token="token-1",
        tenant_id="default",
        project_id="bh-migrated-7-aix",
        auto_generate_correlation=True,
        correlation_factory=lambda: "corr-generated",
        require_auth=True,
        require_tenant=True,
    )
    assert headers["X-Correlation-ID"] == "corr-generated"
    assert headers["X-Project-ID"] == "bh-migrated-7-aix"


def test_resolve_tenant_header_accepts_both_aliases() -> None:
    assert resolve_tenant_header({"X-Tenant-ID": "tenant-a"}) == "tenant-a"
    assert resolve_tenant_header({"X-Tenant-Id": "tenant-b"}) == "tenant-b"
    assert resolve_tenant_header({"X-Hyeve-Tenant-Id": "tenant-c"}) == "tenant-c"


def test_resolve_project_header_accepts_both_aliases() -> None:
    assert resolve_project_header({"X-Project-ID": "proj-a"}) == "proj-a"
    assert resolve_project_header({"X-Project-Id": "proj-b"}) == "proj-b"


# #1852 – Auth contract hardening: mixed-case header aliases + stale-token diagnostics


def test_build_service_headers_emits_canonical_tenant_header() -> None:
    # build_service_headers must always use X-Tenant-ID (not X-Tenant-Id) so outbound
    # service-to-service calls are consistent with the documented canonical form.
    headers = build_service_headers(service_token="tok", tenant_id="default")
    assert "X-Tenant-ID" in headers
    assert "X-Tenant-Id" not in headers


def test_build_service_headers_emits_canonical_project_header() -> None:
    # build_service_headers must always use X-Project-ID (not X-Project-Id).
    headers = build_service_headers(service_token="tok", project_id="bh-migrated-7-aix")
    assert "X-Project-ID" in headers
    assert "X-Project-Id" not in headers


def test_diagnose_service_token_reports_expired_jwt() -> None:
    expired_token = _make_jwt({"sub": "aix-runner", "exp": int(time.time()) - 3600})
    result = diagnose_service_token(expired_token)
    assert result["token_kind"] == "jwt"
    assert result["expired"] is True
    assert result["seconds_until_expiry"] < 0
    assert "expired" in result["diagnostic"]


def test_diagnose_service_token_reports_valid_jwt() -> None:
    valid_token = _make_jwt({"sub": "aix-runner", "exp": int(time.time()) + 3600})
    result = diagnose_service_token(valid_token)
    assert result["token_kind"] == "jwt"
    assert result["expired"] is False
    assert result["seconds_until_expiry"] > 0
    assert "valid" in result["diagnostic"]


def test_diagnose_service_token_handles_opaque_token() -> None:
    # Static service tokens that are not JWTs should not raise and should report opaque.
    result = diagnose_service_token("some-static-service-secret")
    assert result["token_kind"] == "opaque"
    assert result["expired"] is False
    assert result["exp"] is None


def test_diagnose_service_token_handles_empty_token() -> None:
    result = diagnose_service_token(None)
    assert result["token_kind"] == "empty"
    assert result["expired"] is True


def test_inspect_service_token_claims_reports_service_roles() -> None:
    token = _make_jwt(
        {
            "sub": "aix-v2-runtime",
            "tenant_id": "default",
            "roles": ["agent-worker"],
            "service": True,
        }
    )

    result = inspect_service_token_claims(token)

    assert result["token_kind"] == "jwt"
    assert result["claims_decoded"] is True
    assert result["service"] is True
    assert result["roles"] == ["agent-worker"]
    assert result["tenant_id"] == "default"
    assert result["sub"] == "aix-v2-runtime"


def test_inspect_service_token_claims_marks_operator_token_as_non_service() -> None:
    token = _make_jwt(
        {
            "sub": "trusted-operator",
            "tenant_id": "default",
            "roles": ["admin"],
        }
    )

    result = inspect_service_token_claims(token)

    assert result["token_kind"] == "jwt"
    assert result["claims_decoded"] is True
    assert result["service"] is False
    assert result["roles"] == ["admin"]
