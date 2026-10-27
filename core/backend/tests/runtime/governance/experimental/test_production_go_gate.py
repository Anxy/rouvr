from __future__ import annotations

from pathlib import Path

from backend.aix_core.runtime.governance.experimental.production_go_gate import (
    REQUIRED_PRODUCTION_CHILDREN,
    evaluate_production_gate,
    load_production_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[6]
EVIDENCE_ROOT = REPO_ROOT / "docs" / "current" / "benchmarks" / "AIX-E2E-CYCLE-001"


def _go_payloads() -> dict[str, dict]:
    candidate = "a" * 40
    payloads = {
        child: {
            "child": child,
            "candidate_sha": candidate,
            "status": "GO",
            "production": "NO_GO",
        }
        for child in REQUIRED_PRODUCTION_CHILDREN
    }
    payloads["001P-W10"].update(
        {
            "production": "GO",
            "human_signature_present": True,
            "named_rollback_owner_present": True,
            "monitoring_window_present": True,
        }
    )
    payloads["001P.5"].update(
        {
            "production": "GO",
            "independent_reviewer": "reviewer-1",
            "independent_decision": "GO",
        }
    )
    return payloads


def test_current_production_sprint_evidence_fails_closed() -> None:
    decision = evaluate_production_gate(load_production_evidence(EVIDENCE_ROOT))

    assert decision.decision == "NO_GO"
    assert "CANDIDATE_NOT_FROZEN_SHA" in decision.reason_codes
    assert "HUMAN_SIGNATURE_REQUIRED" in decision.reason_codes
    assert "INDEPENDENT_CHECKPOINT_GO_REQUIRED" in decision.reason_codes


def test_complete_same_sha_human_and_independent_evidence_can_pass() -> None:
    decision = evaluate_production_gate(_go_payloads())

    assert decision.decision == "GO"
    assert decision.reason_codes == ()
    assert decision.human_signature_present is True
    assert decision.independent_checkpoint_go is True


def test_candidate_drift_fails_closed() -> None:
    payloads = _go_payloads()
    payloads["001P-W03"]["candidate_sha"] = "b" * 40

    decision = evaluate_production_gate(payloads)

    assert decision.decision == "NO_GO"
    assert "CANDIDATE_SHA_DRIFT" in decision.reason_codes


def test_non_authority_child_cannot_claim_production_go() -> None:
    payloads = _go_payloads()
    payloads["001P-W01"]["production"] = "GO"

    decision = evaluate_production_gate(payloads)

    assert decision.decision == "NO_GO"
    assert "UNAUTHORIZED_PRODUCTION_GO:001P-W01" in decision.reason_codes


def test_missing_child_evidence_fails_closed() -> None:
    payloads = _go_payloads()
    payloads.pop("001P-W05")

    decision = evaluate_production_gate(payloads)

    assert decision.decision == "NO_GO"
    assert "EVIDENCE_MISSING:001P-W05" in decision.reason_codes
