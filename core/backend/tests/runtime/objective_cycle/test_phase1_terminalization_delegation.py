"""Phase 1 refactor: durable backend delegates verification/terminalization to services."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.services.terminalization_service import (
    TerminalizationEvaluationResult,
)
from backend.aix_core.runtime.verification.gap import GapStore, GapV1
from backend.aix_core.runtime.verification.terminalization_guard import GuardsEvaluated
from backend.aix_core.runtime.verification.verification_decision import VerificationDecisionV1
from backend.workflows.temporal.objective_cycle_contracts import ObjectiveCycleWorkflowInput
from backend.workflows.temporal.objective_cycle_workflow import AixObjectiveCycleWorkflowEngine


def _engine() -> AixObjectiveCycleWorkflowEngine:
    plan_sha = hashlib.sha256(b"obj:cyc:sha12345").hexdigest()
    wf_input = ObjectiveCycleWorkflowInput(
        workflow_version=1,
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cyc-1",
        candidate_sha="sha12345",
        plan_sha256=plan_sha,
        start_request_id="req-1",
        correlation_id="corr-1",
    )
    engine = AixObjectiveCycleWorkflowEngine(wf_input)
    engine.run_until_wait_or_terminal()
    engine.signal_approval(approval_ref="approval-1", valid=True)
    return engine


def test_backend_wires_terminalization_and_verification_services() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    assert backend._terminalization_service is not None
    assert backend._verification_service is not None
    assert backend._terminalization_service.gap_store is backend._gap_store


def test_evaluate_cycle_terminalization_delegates_to_service() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    engine = _engine()
    from backend.aix_core.runtime.objective.durable_api_backend import _CycleRecord

    cycle = _CycleRecord(
        cycle_id="cyc-1",
        objective_id="obj-1",
        tenant_id="tenant-1",
        engine=engine,
        version=3,
    )
    backend._cycles["cyc-1"] = cycle

    with patch.object(
        backend._terminalization_service,
        "evaluate_guards",
        wraps=backend._terminalization_service.evaluate_guards,
    ) as evaluate_guards:
        term = backend._evaluate_cycle_terminalization(cycle)
        evaluate_guards.assert_called_once()
        assert term.decision in {
            "DENY_NEEDS_REPAIR",
            "DENY_TERMINALIZATION",
            "ALLOW_VERIFYING_OBJECTIVE",
            "ALLOW_EXECUTION_COMPLETE",
            "ALLOW_CERTIFIED",
        }


def test_apply_terminalization_uses_service_engine_path() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    engine = _engine()
    from backend.aix_core.runtime.objective.durable_api_backend import _CycleRecord

    cycle = _CycleRecord(
        cycle_id="cyc-1",
        objective_id="obj-1",
        tenant_id="tenant-1",
        engine=engine,
        version=3,
    )
    backend._cycles["cyc-1"] = cycle

    v_dec = VerificationDecisionV1(
        decision_id="vdec_test",
        verification_id="v_test",
        objective_id="obj-1",
        decision="REPAIR",
        reasoning="blocking gaps remain",
        blocking_gaps=["gap-1"],
    )
    guards = GuardsEvaluated(
        all_required_steps_verified=True,
        objective_runtime_verification="FAIL",
        blocking_gaps=1,
        required_evidence_complete=True,
        ovc_coverage_complete=True,
    )
    from backend.aix_core.runtime.verification.terminalization_guard import (
        evaluate_terminalization,
    )

    term_dec = evaluate_terminalization(
        verification_decision=v_dec,
        guards=guards,
        current_phase=str(engine.state.phase),
    )
    evaluation = TerminalizationEvaluationResult(
        terminalization_decision=term_dec,
        verification_decision=v_dec,
        guards_evaluated=guards,
        all_steps_verified=True,
        blocking_gaps_count=1,
    )

    with patch.object(
        backend._terminalization_service,
        "apply_terminalization_to_engine",
        wraps=backend._terminalization_service.apply_terminalization_to_engine,
    ) as apply_to_engine:
        result = backend._apply_terminalization_to_engine(
            cycle,
            evaluation=evaluation,
        )
        apply_to_engine.assert_called_once()
        assert result.decision == "DENY_NEEDS_REPAIR"
        assert str(engine.state.phase) == "GAPS_FOUND"


def test_reverify_uses_verification_service_for_objective_verify(tmp_path: Path) -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", gap_store_path=tmp_path / "gaps.json")
    engine = _engine()
    from backend.aix_core.runtime.objective.durable_api_backend import _CycleRecord
    from backend.aix_core.runtime.verification.objective_verification_contract import derive_ovc
    from backend.aix_core.runtime.verification.objective_truth import derive_canonical_truth

    truth = derive_canonical_truth(original_text="verify sqlite app", objective_id="obj-1")
    ovc = derive_ovc(truth)
    backend._objective_verification_contracts["obj-1"] = ovc

    cycle = _CycleRecord(
        cycle_id="cyc-1",
        objective_id="obj-1",
        tenant_id="tenant-1",
        engine=engine,
        version=3,
    )
    backend._cycles["cyc-1"] = cycle

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "api.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    cycle.real_execute = {"workspace": str(ws)}

    with patch.object(
        backend._verification_service,
        "verify_objective_at_workspace",
        wraps=backend._verification_service.verify_objective_at_workspace,
    ) as verify_at_workspace:
        # Force gaps so we do not need full OVC probe setup; still proves delegation.
        store = GapStore()
        gap = GapV1(
            gap_id="gap-open",
            objective_id="obj-1",
            verification_id="v1",
            category="RUNTIME_FAILURE",
            severity="BLOCKER",
            expected="runtime pass",
            observed="runtime fail",
            status="OPEN",
        )
        store.put_gap(gap)
        backend._gap_store = store
        backend._terminalization_service.gap_store = store
        backend._verification_service.gap_store = store

        async def _run() -> dict:
            return await backend._reverify_and_terminalize(
                tenant_id="tenant-1",
                cycle_id="cyc-1",
                expected_version=3,
                body={},
                command_id="rev-1",
                actor_id="tester",
            )

        import asyncio

        with patch(
            "backend.aix_core.runtime.objective.durable_api_backend._resolve_cycle_workspace",
            return_value=ws,
        ):
            result = asyncio.get_event_loop().run_until_complete(_run())
        verify_at_workspace.assert_called_once()
        assert result["operation"] == "reverify_and_terminalize"
