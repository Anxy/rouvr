"""Feature-flag and policy gate for optional voice NLP."""

from __future__ import annotations

from typing import Any


def evaluate_voice_nlp_enablement(
    *,
    feature_flag_enabled: bool,
    policy_checks_passed: bool,
    rollback_drill_recent: bool,
    compliance_approved: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    allowed = True
    if not feature_flag_enabled:
        allowed = False
        reasons.append("FEATURE_FLAG_DISABLED")
    if not policy_checks_passed:
        allowed = False
        reasons.append("POLICY_CHECKS_FAILED")
    if not rollback_drill_recent:
        allowed = False
        reasons.append("ROLLBACK_DRILL_REQUIRED")
    if not compliance_approved:
        allowed = False
        reasons.append("COMPLIANCE_APPROVAL_REQUIRED")
    return {
        "allowed": allowed,
        "reasons": reasons if reasons else ["VOICE_NLP_ENABLED"],
        "default_state": "disabled",
    }


def simulate_voice_nlp_rollback_drill(*, incident_id: str, actor_id: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "incident_id": incident_id,
        "actor_id": actor_id,
        "rollback_actions": [
            "disable_feature_flag",
            "drain_voice_ingress",
            "verify_text_fallback_path",
        ],
    }
