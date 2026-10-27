"""Tests for F5-P2 VerificationPipeline & GateCatalog Parity with Runtime."""

from __future__ import annotations

import pytest
from backend.aix_core.framework.verification.catalog import build_default_catalog
from backend.aix_core.framework.verification.contract import (
    GateContext,
    VerificationContract,
)
from backend.aix_core.framework.verification.pipeline import VerificationPipeline
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates


def test_pipeline_parity_with_evaluate_completion_gates():
    contract = VerificationContract(
        contract_id="c1",
        objective_id="obj1",
        cycle_id="cyc1",
        step_id="step1",
        criteria=("criterion 1",),
        expected_checks=("check_1",),
        required_evidence=("ev_1",),
        continuity_required=False,
    )

    discovery = DiscoveryReport(manifests={"pyproject.toml": "[tool.poetry]"})
    diff_stats = {"src/app.py": {"additions": 10, "deletions": 2}}
    diff_text = "--- src/app.py\n+++ src/app.py\n@@ -1,2 +1,10 @@\n+line"
    test_result = {"passed": True, "exit_code": 0}

    ctx = GateContext(
        contract=contract,
        discovery=discovery,
        diff_stats=diff_stats,
        diff_text=diff_text,
        test_result=test_result,
        already_satisfied=False,
    )

    pipeline = VerificationPipeline()
    gate_results = pipeline.evaluate_gates(ctx)

    # Convert to legacy CompletionGateResult
    adapted_result = pipeline.to_completion_gate_result(gate_results, ctx)
    direct_result = evaluate_completion_gates(
        discovery=discovery,
        diff_stats=diff_stats,
        diff_text=diff_text,
        test_result=test_result,
        already_satisfied=False,
    )

    assert adapted_result.passed == direct_result.passed
    assert adapted_result.gates == direct_result.gates
    assert adapted_result.reasons == direct_result.reasons
    assert adapted_result.verification_verdict == direct_result.verification_verdict
