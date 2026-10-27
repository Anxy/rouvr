from __future__ import annotations

from pathlib import Path

from backend.services.devexecutor_demo_harness_service import (
    evaluate_policy_conformance_gate,
    run_failure_golden_path_policy_deny,
    run_failure_golden_path_verifier_reject,
    run_golden_demo_harness,
)


def test_policy_conformance_gate_blocks_non_l2() -> None:
    gate = evaluate_policy_conformance_gate(
        autonomy_cap="L3",
        break_glass_order_ok=True,
        drift_guard_proof_present=True,
    )
    assert gate["passed"] is False
    assert "autonomy_cap_l2" in gate["failure_reasons"]


def test_golden_demo_harness_dry_run_returns_planned_states(tmp_path: Path) -> None:
    gate = evaluate_policy_conformance_gate(
        autonomy_cap="L2",
        break_glass_order_ok=True,
        drift_guard_proof_present=True,
    )
    result = run_golden_demo_harness(
        workspace_root=tmp_path,
        mode="dry_run",
        policy_gate=gate,
    )
    assert result["status"] == "planned"
    assert result["planned_states"][-1] == "ready_for_human"


def test_golden_demo_harness_full_run_reaches_ready_for_human(tmp_path: Path) -> None:
    gate = evaluate_policy_conformance_gate(
        autonomy_cap="L2",
        break_glass_order_ok=True,
        drift_guard_proof_present=True,
    )
    result = run_golden_demo_harness(
        workspace_root=tmp_path,
        mode="full_run",
        policy_gate=gate,
    )
    assert result["status"] == "completed"
    assert result["result"]["final_state"] == "ready_for_human"


def test_failure_golden_path_policy_deny_is_non_ready_and_has_evidence(tmp_path: Path) -> None:
    result = run_failure_golden_path_policy_deny(workspace_root=tmp_path)
    assert result["status"] == "completed"
    assert result["result"]["final_state"] == "failed"
    assert result["evidence_pack"]["status"] == "fail"
    assert result["evidence_pack"]["artifacts"][0]["exists"] is True


def test_failure_golden_path_verifier_reject_transitions_to_needs_fix(tmp_path: Path) -> None:
    result = run_failure_golden_path_verifier_reject(workspace_root=tmp_path)
    assert result["status"] == "completed"
    assert result["coder_result"]["final_state"] == "ready_for_human"
    assert result["verifier_result"]["final_state"] == "needs_fix"
    assert result["evidence_pack"]["status"] == "pass"
