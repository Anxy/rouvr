from __future__ import annotations

from backend.services.voice_nlp_policy_service import evaluate_voice_nlp_enablement, simulate_voice_nlp_rollback_drill


def test_evaluate_voice_nlp_enablement_default_denies() -> None:
    result = evaluate_voice_nlp_enablement(
        feature_flag_enabled=False,
        policy_checks_passed=False,
        rollback_drill_recent=False,
        compliance_approved=False,
    )
    assert result["allowed"] is False
    assert "FEATURE_FLAG_DISABLED" in result["reasons"]


def test_simulate_voice_nlp_rollback_drill_produces_actions() -> None:
    result = simulate_voice_nlp_rollback_drill(incident_id="inc-123", actor_id="user-1")
    assert result["status"] == "completed"
    assert "disable_feature_flag" in result["rollback_actions"]
