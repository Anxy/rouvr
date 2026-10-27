from __future__ import annotations

from backend.services.plugin_runtime_policy_service import evaluate_plugin_runtime_policy


def test_evaluate_plugin_runtime_policy_allows_compliant_manifest() -> None:
    result = evaluate_plugin_runtime_policy(
        plugin_manifest={
            "signed": True,
            "sandboxed": True,
            "egress_hosts": ["api.hyeve.local"],
            "token_scopes": ["tasks.read"],
            "quota_per_hour": 100,
        },
        allowed_egress_hosts=["api.hyeve.local"],
        allowed_token_scopes=["tasks.read", "tasks.write"],
        max_quota_per_hour=500,
    )
    assert result["allowed"] is True
    assert result["violations"] == []


def test_evaluate_plugin_runtime_policy_blocks_escape_signals() -> None:
    result = evaluate_plugin_runtime_policy(
        plugin_manifest={
            "signed": False,
            "sandboxed": False,
            "egress_hosts": ["evil.example"],
            "token_scopes": ["admin.all"],
            "quota_per_hour": 5000,
        },
        allowed_egress_hosts=["api.hyeve.local"],
        allowed_token_scopes=["tasks.read"],
        max_quota_per_hour=500,
    )
    assert result["allowed"] is False
    codes = {item["code"] for item in result["violations"]}
    assert "PLUGIN_SIGNATURE_REQUIRED" in codes
    assert "PLUGIN_SANDBOX_REQUIRED" in codes
    assert "EGRESS_HOST_NOT_ALLOWLISTED" in codes
    assert "TOKEN_SCOPE_NOT_ALLOWED" in codes
    assert "QUOTA_EXCEEDED_OR_INVALID" in codes
