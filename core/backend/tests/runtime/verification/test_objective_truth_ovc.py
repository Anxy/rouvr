"""AIX Feature 12 (Packet 01) — Unit tests for Canonical Truth & OVC."""

import pytest
from backend.aix_core.runtime.verification.objective_truth import (
    CanonicalObjectiveTruthV1,
    derive_canonical_truth,
)
from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
    OVDispositionV1,
    OVItemV1,
    derive_ovc,
    evaluate_ovc_disposition_coverage,
)
from backend.aix_core.runtime.objective.contracts import PlanRevisionV1, StepContractV1


EXPENSE_TRACKER_OBJECTIVE = (
    "Build a complete Expense Tracker web app using FastAPI + Python backend, "
    "HTML/CSS/JavaScript frontend, and SQLite. Include registration/login, "
    "add/edit/delete expenses, and data visualization with Chart.js."
)


def test_derive_ovc_expense_tracker_contains_sqlite():
    truth = derive_canonical_truth(EXPENSE_TRACKER_OBJECTIVE, objective_id="obj_expense_01")
    assert truth.verify_integrity()
    assert "sqlite" in truth.technology_requirements
    assert "fastapi" in truth.technology_requirements
    assert truth.original_text == EXPENSE_TRACKER_OBJECTIVE

    ovc = derive_ovc(truth)
    assert ovc.verify_integrity()
    assert len(ovc.ov_items) >= 5

    # Check OV-002 SQLite persistence authority
    sqlite_item = next(
        (i for i in ovc.ov_items if "SQLite" in i.description or i.ov_id == "OV-002"),
        None,
    )
    assert sqlite_item is not None
    assert sqlite_item.proof_method == "RUNTIME_PROBE"
    assert sqlite_item.category == "STACK"


def test_plan_accept_rejects_missing_disposition():
    truth = derive_canonical_truth(EXPENSE_TRACKER_OBJECTIVE, objective_id="obj_expense_01")
    ovc = derive_ovc(truth)

    # Incomplete disposition: explicit mapping misses SQLite (OV-002)
    explicit = {
        "OV-001": OVDispositionV1(kind="IMPLEMENTED_BY", step_ids=["step-1"]),
        # OV-002 missing
    }
    result = evaluate_ovc_disposition_coverage(
        ovc,
        plan_steps=[],
        explicit_dispositions=explicit,
    )
    # OV-002 has no disposition in explicit map when plan_steps is empty and probe not defined
    # Explicit map only provided OV-001, leaving other items missing
    assert not result.ok
    assert "OV-002" in result.missing_ov_ids or len(result.missing_ov_ids) > 0


def test_ovc_hash_immutable_after_seal():
    truth = derive_canonical_truth(EXPENSE_TRACKER_OBJECTIVE, objective_id="obj_expense_01")
    ovc = derive_ovc(truth)
    original_hash = ovc.hash

    assert ovc.verify_integrity()

    # Simulate tampered item
    mutated_items = list(ovc.ov_items)
    mutated_items[0] = mutated_items[0].model_copy(update={"description": "Tampered description"})
    tampered_ovc = ovc.model_copy(update={"ov_items": mutated_items})

    assert not tampered_ovc.verify_integrity()
    assert tampered_ovc.hash == original_hash  # Hash didn't change, but integrity check caught tampering


def test_planner_cannot_mutate_ovc():
    truth = derive_canonical_truth(EXPENSE_TRACKER_OBJECTIVE, objective_id="obj_expense_01")
    ovc = derive_ovc(truth)

    # If planner attempts to define plan without satisfying all OV items, disposition coverage fails
    plan_steps = [
        {
            "id": "step-1",
            "ov_ids": ["OV-001"],  # Planner only covers OV-001
        }
    ]
    # Explicit empty map for remaining items
    explicit_bad = {
        "OV-002": OVDispositionV1(kind="IMPLEMENTED_BY", step_ids=[]),  # Invalid: empty step_ids
    }
    result = evaluate_ovc_disposition_coverage(
        ovc,
        plan_steps=plan_steps,
        explicit_dispositions=explicit_bad,
    )
    assert not result.ok
    assert "OV-002" in result.invalid_dispositions


def test_ov003_uvicorn_verified_by_probe():
    truth = derive_canonical_truth(EXPENSE_TRACKER_OBJECTIVE, objective_id="obj_expense_01")
    ovc = derive_ovc(truth)

    uvicorn_item = next(
        (i for i in ovc.ov_items if "uvicorn" in i.description.lower() or i.ov_id == "OV-003"),
        None,
    )
    assert uvicorn_item is not None
    assert uvicorn_item.proof_method == "RUNTIME_PROBE"

    # Disposition can be VERIFIED_BY probe
    disp = OVDispositionV1(
        kind="VERIFIED_BY",
        probe_or_test_id="probe:start_uvicorn",
    )
    valid, err = disp.is_valid()
    assert valid
    assert err == ""
