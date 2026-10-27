from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.verifier_decision_models import validate_verifier_decision
from backend.services.verifier_decision_service import build_verifier_decision


def test_build_verifier_decision_verified_happy_path() -> None:
    payload = build_verifier_decision(
        decision="Verified",
        summary="All acceptance checks passed.",
        acceptance_mapping=[{"acceptance_id": "AC-1", "met": True}],
        patch_refs=["diff.patch"],
        test_refs=["tests.log"],
    )
    assert payload["decision"] == "verified"
    assert payload["reason_code"] == "VERIFIED"
    assert payload["actionable_items_count"] == 0
    assert payload["patch_refs"] == ["diff.patch"]
    assert payload["evidence_refs"] == ["diff.patch", "tests.log"]
    assert payload["failed_acceptance_items"] == []
    assert payload["capability_contract"]["reviewer_tier"] == "verifier"
    assert "mutate_code" in payload["capability_contract"]["forbidden_actions"]


def test_build_verifier_decision_needs_fix_requires_line_referenced_reasons() -> None:
    payload = build_verifier_decision(
        decision="NeedsFix",
        summary="Two issues found.",
        reasons=[
            {
                "kind": "bug",
                "message": "Null guard missing",
                "file_path": "app/service.py",
                "line": 42,
                "acceptance_id": "AC-2",
            }
        ],
        acceptance_mapping=[{"acceptance_id": "AC-2", "met": False}],
    )
    assert payload["decision"] == "needs_fix"
    assert payload["reason_code"] == "VERIFIER_NEEDS_FIX"
    assert payload["actionable_items_count"] == 1
    assert payload["failed_acceptance_items"] == [
        {
            "acceptance_id": "AC-2",
            "message": "Null guard missing",
            "file_path": "app/service.py",
            "line": 42,
        }
    ]
    assert payload["capability_contract"]["recovery_route"] == "onlineai_or_plannerai"


def test_build_verifier_decision_needs_fix_without_reasons_is_rejected() -> None:
    with pytest.raises(ValueError, match="needs_fix requires at least one line-referenced reason"):
        build_verifier_decision(
            decision="needs_fix",
            summary="Issues found.",
            reasons=[],
        )


def test_build_verifier_decision_allows_unlocated_reasons_when_explicitly_flagged() -> None:
    payload = build_verifier_decision(
        decision="needs_fix",
        summary="Planner blocked before code execution.",
        reasons=[{"kind": "acceptance_gap", "message": "Planner blocked before code execution."}],
        metadata={"allow_unlocated_reasons": True},
    )

    assert payload["decision"] == "needs_fix"
    assert payload["actionable_items_count"] == 1
    assert payload["reasons"] == [
        {
            "kind": "acceptance_gap",
            "message": "Planner blocked before code execution.",
            "file_path": None,
            "line": None,
            "acceptance_id": None,
        }
    ]


def test_build_verifier_decision_verified_requires_acceptance_mapping_when_enforced() -> None:
    with pytest.raises(ValueError, match="verified requires non-empty acceptance_mapping"):
        build_verifier_decision(
            decision="verified",
            summary="looks green",
            acceptance_mapping=[],
            metadata={"require_acceptance_mapping": True},
        )


def test_build_verifier_decision_verified_requires_all_acceptance_mapping_items_met_when_enforced() -> None:
    with pytest.raises(ValueError, match="verified requires all acceptance_mapping items met"):
        build_verifier_decision(
            decision="verified",
            summary="looks green",
            acceptance_mapping=[{"acceptance_id": "AC-1", "met": False}],
            metadata={"require_acceptance_mapping": True},
        )


def test_build_verifier_decision_supports_canonical_routing_fields() -> None:
    payload = build_verifier_decision(
        decision="needs_fix",
        summary="Retry with a bounded fix.",
        reasons=[
            {
                "kind": "acceptance_gap",
                "message": "Acceptance criterion failed",
                "file_path": "backend/service.py",
                "line": 12,
                "acceptance_id": "AC-9",
            }
        ],
        acceptance_mapping=[{"acceptance_id": "AC-9", "met": False}],
        patch_refs=["local://patch.diff"],
        test_refs=["local://pytest.log"],
        evidence_refs=["local://run.json"],
        reason_code="QUALITY_GATE_REJECTED",
        recovery_kind="retry",
        requeueable=True,
    )

    assert payload["reason_code"] == "QUALITY_GATE_REJECTED"
    assert payload["recovery_kind"] == "retry"
    assert payload["requeueable"] is True
    assert payload["evidence_refs"] == ["local://run.json", "local://patch.diff", "local://pytest.log"]
    assert payload["capability_contract"]["allowed_actions"] == [
        "evaluate_acceptance_contract",
        "attach_bounded_evidence",
        "emit_verified_or_needs_fix",
    ]


def test_validate_verifier_decision_rejects_invalid_reason_shape() -> None:
    with pytest.raises(ValidationError):
        validate_verifier_decision(
            {
                "schema_version": "verifier.decision.current",
                "decision": "needs_fix",
                "summary": "bad reason",
                "reasons": [{"kind": "bug", "message": "x", "file_path": "", "line": 0}],
                "acceptance_mapping": [],
                "patch_refs": [],
                "test_refs": [],
                "capability_contract": {
                    "reviewer_tier": "verifier",
                    "allowed_actions": ["evaluate_acceptance_contract"],
                    "forbidden_actions": ["mutate_code"],
                    "recovery_route": "onlineai_or_plannerai",
                },
                "metadata": {},
            }
        )
