"""Tests for F5-P2 GateCatalog Immutability & Law C Protections."""

from __future__ import annotations

import pytest
from backend.aix_core.framework.verification.catalog import (
    GateCatalog,
    GateDefinition,
    ImmutableGateError,
    build_default_catalog,
)
from backend.aix_core.framework.verification.contract import GateContext, GateResult, VerificationContract


def test_default_catalog_contains_immutable_law_c():
    catalog = build_default_catalog()
    assert catalog.is_frozen() is True

    law_c = catalog.get("continuity_law_c")
    assert law_c.ordinal == 40
    assert law_c.mandatory is True
    assert law_c.immutable is True


def test_frozen_catalog_rejects_modifications():
    catalog = build_default_catalog()

    with pytest.raises(ImmutableGateError, match="frozen"):
        catalog.register(
            GateDefinition(
                gate_id="custom_gate",
                ordinal=99,
                evaluator=lambda ctx: GateResult(gate_id="custom_gate", passed=True),
            )
        )

    with pytest.raises(ImmutableGateError, match="frozen"):
        catalog.unregister("plan_contract")


def test_unfrozen_catalog_protects_immutable_gates():
    catalog = GateCatalog()
    law_c = GateDefinition(
        gate_id="continuity_law_c",
        ordinal=40,
        evaluator=lambda ctx: GateResult(gate_id="continuity_law_c", passed=True),
        mandatory=True,
        immutable=True,
    )
    catalog.register(law_c)

    # Cannot unregister immutable gate
    with pytest.raises(ImmutableGateError, match="immutable and cannot be unregistered"):
        catalog.unregister("continuity_law_c")

    # Cannot overwrite immutable gate
    with pytest.raises(ImmutableGateError, match="immutable and cannot be replaced"):
        catalog.register(
            GateDefinition(
                gate_id="continuity_law_c",
                ordinal=40,
                evaluator=lambda ctx: GateResult(gate_id="continuity_law_c", passed=True),
                mandatory=True,
                immutable=False,
            )
        )


def test_catalog_ordering_and_applicability():
    catalog = GateCatalog()
    catalog.register(
        GateDefinition(
            gate_id="gate_20",
            ordinal=20,
            evaluator=lambda ctx: GateResult(gate_id="gate_20", passed=True),
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="gate_10",
            ordinal=10,
            evaluator=lambda ctx: GateResult(gate_id="gate_10", passed=True),
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="gate_conditional",
            ordinal=30,
            evaluator=lambda ctx: GateResult(gate_id="gate_conditional", passed=True),
            applies=lambda ctx: ctx.diff_only_verification_allowed is True,
        )
    )

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

    # Context without diff_only
    ctx1 = GateContext(contract=contract, diff_only_verification_allowed=False)
    ordered1 = catalog.ordered_for(ctx1)
    assert [g.gate_id for g in ordered1] == ["gate_10", "gate_20"]

    # Context with diff_only
    ctx2 = GateContext(contract=contract, diff_only_verification_allowed=True)
    ordered2 = catalog.ordered_for(ctx2)
    assert [g.gate_id for g in ordered2] == ["gate_10", "gate_20", "gate_conditional"]
