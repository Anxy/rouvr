"""Tests for F5-P2 VerificationPipeline Fail-Closed Exception Handling."""

from __future__ import annotations

import pytest
from backend.aix_core.framework.verification.catalog import (
    GateCatalog,
    GateDefinition,
)
from backend.aix_core.framework.verification.contract import (
    GateContext,
    GateResult,
    VerificationContract,
)
from backend.aix_core.framework.verification.pipeline import (
    VerificationPipeline,
)


def _exploding_evaluator(ctx: GateContext) -> GateResult:
    raise RuntimeError("Unexpected disk IO error during gate evaluation")


def test_pipeline_captures_exceptions_as_failing_gates():
    catalog = GateCatalog()
    catalog.register(
        GateDefinition(
            gate_id="exploding_gate",
            ordinal=10,
            evaluator=_exploding_evaluator,
            mandatory=True,
        )
    )
    catalog.freeze()

    pipeline = VerificationPipeline(catalog=catalog)

    contract = VerificationContract(
        contract_id="c1",
        objective_id="obj1",
        cycle_id="cyc1",
        step_id="step1",
        criteria=(),
        expected_checks=(),
        required_evidence=(),
        continuity_required=False,
    )
    ctx = GateContext(contract=contract)

    results = pipeline.evaluate_gates(ctx)
    assert len(results) == 1
    assert results[0].gate_id == "exploding_gate"
    assert results[0].passed is False
    assert "evaluator raised exception" in results[0].reasons[0]
