"""Plugin runtime sandbox policy evaluator."""

from __future__ import annotations

from typing import Any


def evaluate_plugin_runtime_policy(
    *,
    plugin_manifest: dict[str, Any],
    allowed_egress_hosts: list[str],
    allowed_token_scopes: list[str],
    max_quota_per_hour: int,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []

    if not bool(plugin_manifest.get("signed")):
        violations.append({"code": "PLUGIN_SIGNATURE_REQUIRED"})
    if not bool(plugin_manifest.get("sandboxed", True)):
        violations.append({"code": "PLUGIN_SANDBOX_REQUIRED"})

    requested_hosts = [
        str(item).strip().lower() for item in (plugin_manifest.get("egress_hosts") or []) if str(item).strip()
    ]
    allow_hosts = {str(item).strip().lower() for item in allowed_egress_hosts if str(item).strip()}
    for host in requested_hosts:
        if host not in allow_hosts:
            violations.append({"code": "EGRESS_HOST_NOT_ALLOWLISTED", "host": host})

    requested_scopes = [str(item).strip() for item in (plugin_manifest.get("token_scopes") or []) if str(item).strip()]
    allow_scopes = {str(item).strip() for item in allowed_token_scopes if str(item).strip()}
    for scope in requested_scopes:
        if scope not in allow_scopes:
            violations.append({"code": "TOKEN_SCOPE_NOT_ALLOWED", "scope": scope})

    requested_quota = int(plugin_manifest.get("quota_per_hour") or 0)
    if requested_quota <= 0 or requested_quota > int(max_quota_per_hour):
        violations.append(
            {
                "code": "QUOTA_EXCEEDED_OR_INVALID",
                "requested": requested_quota,
                "max_allowed": int(max_quota_per_hour),
            }
        )

    return {
        "allowed": len(violations) == 0,
        "violations": violations,
        "enforced_controls": [
            "signed_plugin",
            "sandbox_isolation",
            "egress_allowlist",
            "scoped_tokens",
            "quota_limit",
        ],
    }
