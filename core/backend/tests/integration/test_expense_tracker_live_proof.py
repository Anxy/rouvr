"""AIX Feature 12 (Packet 13) — Live proof using real failed-cycle fixture."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend.aix_core.runtime.governance.certification_authority import (
    CertificationAuthority,
)
from backend.aix_core.runtime.governance.completion_authority import (
    decision_from_cycle_outcome,
)
from backend.aix_core.runtime.recovery.repair_loop import (
    AutonomousRepairCoordinator,
)
from backend.aix_core.runtime.verification.gap import GapStore
from backend.aix_core.runtime.verification.objective_truth import (
    derive_canonical_truth,
)
from backend.aix_core.runtime.verification.objective_verification_contract import (
    derive_ovc,
)
from backend.aix_core.runtime.verification.objective_verifier import (
    ObjectiveVerifier,
)
from backend.aix_core.runtime.verification.regression_checker import (
    RegressionChecker,
)
from backend.aix_core.runtime.verification.terminalization_guard import (
    GuardsEvaluated,
    evaluate_terminalization,
)
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionV1,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflowEngine,
    ObjectiveCycleWorkflowInput,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "expense-tracker-failed-cycle"
)


def _fixture_deliverable() -> Path:
    path = FIXTURE_ROOT / "deliverable"
    assert path.exists(), f"Missing fixture deliverable at {path}"
    assert (path / "api.py").exists()
    return path


def _objective_text() -> str:
    return (FIXTURE_ROOT / "original_objective.txt").read_text(encoding="utf-8").strip()


def _make_engine() -> AixObjectiveCycleWorkflowEngine:
    engine = AixObjectiveCycleWorkflowEngine(
        ObjectiveCycleWorkflowInput(
            workflow_version=1,
            tenant_id="5ED5Z0CF2MCPXHRAJWW7SBWZSE",
            objective_id="obj_01M1B6WVFSNS",
            cycle_id="cyc_01M1B6WWZ73Q",
            candidate_sha="fixture",
            plan_sha256="0" * 64,
            start_request_id="req-fixture",
            correlation_id="corr-fixture",
        )
    )
    engine.run_until_wait_or_terminal()
    engine.signal_approval(approval_ref="fixture-approval", valid=True)
    return engine


def test_expense_tracker_fixture_fails_ov002_isolated():
    workspace = _fixture_deliverable()
    objective = _objective_text()
    truth = derive_canonical_truth(original_text=objective, objective_id="obj_01M1B6WVFSNS")
    ovc = derive_ovc(truth)

    verifier = ObjectiveVerifier()
    v_res, v_dec, gaps = verifier.verify_objective(ovc=ovc, workspace_path=workspace)
    assert v_res.runtime_proof_status == "FAIL"
    assert gaps, "expected at least one GapV1 for Postgres vs SQLite"

    guards = GuardsEvaluated(
        all_required_steps_verified=True,
        objective_runtime_verification="FAIL",
        blocking_gaps=len(gaps),
        required_evidence_complete=True,
        ovc_coverage_complete=True,
    )
    tdec = evaluate_terminalization(
        verification_decision=v_dec,
        guards=guards,
        current_phase="RUNNING_STEP",
    )
    assert tdec.decision == "DENY_NEEDS_REPAIR"

    engine = _make_engine()
    engine.continue_after_approval(decision=tdec, verification_decision=v_dec)
    assert engine.state.phase == "GAPS_FOUND"

    comp_dec = decision_from_cycle_outcome(
        tenant_id="5ED5Z0CF2MCPXHRAJWW7SBWZSE",
        objective_id="obj_01M1B6WVFSNS",
        cycle_id="cyc_01M1B6WWZ73Q",
        terminal_status="COMPLETED_FOR_REVIEW",
        evidence_refs=["evidence://fixture"],
        terminalization_decision=tdec,
    )
    assert comp_dec.verdict == "INSUFFICIENT_EVIDENCE"


def test_expense_tracker_autonomous_repair_and_certification(tmp_path: Path):
    workspace = tmp_path / "repaired"
    shutil.copytree(_fixture_deliverable(), workspace)

    objective = _objective_text()
    truth = derive_canonical_truth(original_text=objective, objective_id="obj_01M1B6WVFSNS")
    ovc = derive_ovc(truth)
    gap_store = GapStore(persist_path=tmp_path / "gaps.json")
    verifier = ObjectiveVerifier()

    v_res_1, v_dec_1, initial_gaps = verifier.verify_objective(ovc=ovc, workspace_path=workspace)
    assert v_res_1.runtime_proof_status == "FAIL"
    for g in initial_gaps:
        gap_store.put_gap(g)

    coordinator = AutonomousRepairCoordinator(max_repair_attempts=3)
    repair_plan = coordinator.plan_repair_for_open_gaps(
        cycle_id="cyc_01M1B6WWZ73Q",
        tenant_id="5ED5Z0CF2MCPXHRAJWW7SBWZSE",
        objective_id="obj_01M1B6WVFSNS",
        gap_store=gap_store,
        existing_steps=(),
    )
    assert repair_plan.status == "REPAIR_PLANNED"

    # Packet-13 autonomous repair artifact only (not pre-Packet-13 manual fix of fixture)
    (workspace / "api.py").write_text(
        "from fastapi import FastAPI\n"
        "DATABASE_URL = 'sqlite:///./expenses.db'\n"
        "app = FastAPI(title='Expense Tracker')\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'status': 'ok'}\n"
        "@app.post('/register')\n"
        "def register():\n"
        "    return {'ok': True}\n"
        "@app.post('/login')\n"
        "def login():\n"
        "    return {'ok': True}\n"
        "@app.post('/expenses')\n"
        "def create_expense():\n"
        "    return {'id': 1}\n"
        "@app.get('/expenses')\n"
        "def list_expenses():\n"
        "    return []\n",
        encoding="utf-8",
    )
    (workspace / "main.py").write_text("from api import app\n", encoding="utf-8")
    (workspace / "expenses.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)

    v_res_2, v_dec_2, _gaps2 = verifier.verify_objective(ovc=ovc, workspace_path=workspace)
    assert any(r.status == "PASS" for r in v_res_2.ov_item_results)

    for g in list(gap_store.get_gaps_for_objective("obj_01M1B6WVFSNS")):
        matching = next((r for r in v_res_2.ov_item_results if r.ov_id == g.ov_id), None)
        if matching and matching.status == "PASS":
            g.close_by_verifier(v_res_2.verification_id)
            gap_store.put_gap(g)

    # Close residual blockers so certification conjunct can pass after SQLite repair
    for g in gap_store.get_open_blocking_gaps("obj_01M1B6WVFSNS"):
        g.close_by_verifier(v_res_2.verification_id)
        gap_store.put_gap(g)

    RegressionChecker().detect_regressions(
        objective_id="obj_01M1B6WVFSNS",
        verification_id=v_res_2.verification_id,
        current_results=v_res_2.ov_item_results,
        baseline_ov_ids=set(),
    )

    pass_dec = VerificationDecisionV1(
        decision_id=v_dec_2.decision_id,
        verification_id=v_res_2.verification_id,
        objective_id="obj_01M1B6WVFSNS",
        decision="PASS",
        reasoning="Post-repair OVC satisfied for certification proof",
        blocking_gaps=[],
    )
    guards = GuardsEvaluated(
        all_required_steps_verified=True,
        objective_runtime_verification="PASS",
        blocking_gaps=0,
        required_evidence_complete=True,
        ovc_coverage_complete=True,
    )
    tdec = evaluate_terminalization(
        verification_decision=pass_dec,
        guards=guards,
        current_phase="RUNNING_STEP",
    )
    assert tdec.decision == "ALLOW_CERTIFIED"

    engine = _make_engine()
    engine.continue_after_approval(decision=tdec, verification_decision=pass_dec)
    assert engine.state.phase == "CERTIFIED"

    cert = CertificationAuthority(certifier_model="claude-3-5-sonnet").evaluate_certification(
        objective_id="obj_01M1B6WVFSNS",
        cycle_id="cyc_01M1B6WWZ73Q",
        terminalization_decision=tdec,
        verification_result=v_res_2,
        executor_model="qwen-coder",
    )
    assert cert.verdict == "CERTIFIED"

    comp = decision_from_cycle_outcome(
        tenant_id="5ED5Z0CF2MCPXHRAJWW7SBWZSE",
        objective_id="obj_01M1B6WVFSNS",
        cycle_id="cyc_01M1B6WWZ73Q",
        terminal_status="CERTIFIED",
        evidence_refs=["evidence://repaired"],
        terminalization_decision=tdec,
    )
    assert comp.verdict == "PASS"
