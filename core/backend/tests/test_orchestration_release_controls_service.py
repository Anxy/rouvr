from __future__ import annotations

from backend.services.orchestration_release_controls_service import (
    ROLLOUT_FLAG_SEQUENCE,
    ValidationCheckResult,
    build_rollback_powershell_commands,
    build_rollout_plan,
    evaluate_release_controls,
)


def test_release_controls_gate_passes_when_all_sections_present() -> None:
    contract = [ValidationCheckResult(name="contract", passed=True, duration_ms=10, command="pytest contract")]
    replay = [ValidationCheckResult(name="replay", passed=True, duration_ms=8, command="pytest replay")]
    result = evaluate_release_controls(
        contract_checks=contract,
        replay_checks=replay,
        rollout_flags=list(ROLLOUT_FLAG_SEQUENCE),
        rollback_script_path="C:/dev/hyeve/_runlogs/rollback.ps1",
    )
    assert result["ready"] is True
    assert result["checks"]["contract_tests"] is True
    assert result["checks"]["replay_failure_recovery"] is True


def test_release_controls_gate_fails_when_replay_missing() -> None:
    contract = [ValidationCheckResult(name="contract", passed=True, duration_ms=10, command="pytest contract")]
    result = evaluate_release_controls(
        contract_checks=contract,
        replay_checks=[],
        rollout_flags=list(ROLLOUT_FLAG_SEQUENCE),
        rollback_script_path="C:/dev/hyeve/_runlogs/rollback.ps1",
    )
    assert result["ready"] is False
    assert result["checks"]["replay_failure_recovery"] is False


def test_rollout_plan_is_staged() -> None:
    plan = build_rollout_plan()
    assert len(plan) == 3
    assert plan[0]["stage"] == "stage_1_dark_launch"
    assert "HYEVE_AIX_SERVICE_TEMPORAL_ENABLED" in plan[0]["set_flags"]


def test_rollback_commands_disable_all_flags_and_restart() -> None:
    lines = build_rollback_powershell_commands()
    assert any("HYEVE_AIX_SERVICE_TEMPORAL_ENABLED" in line for line in lines)
    assert any("HYEVE_AIX_SERVICE_LANGGRAPH_ENABLED" in line for line in lines)
    assert any("HYEVE_AIX_SERVICE_HAYSTACK_RETRIEVAL_ENABLED" in line for line in lines)
    assert lines[-1].startswith("powershell -ExecutionPolicy Bypass -File")
