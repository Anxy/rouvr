"""AIX Feature 12 (Packet 11) — Cost Governance & Accounting tests."""

import pytest

from backend.aix_core.runtime.governance.cost_accounting import CostLedger
from backend.aix_core.runtime.model_registry.registry import ModelRegistry
from pathlib import Path


def _profile(model_id: str):
    config = Path(__file__).resolve().parents[3] / "aix_core" / "runtime" / "model_registry" / "models.json"
    return ModelRegistry.from_config(config).get_profile(model_id)


def test_cost_accounting_records_usage_and_computes_cost() -> None:
    profile = _profile("deepseek/deepseek-v3.2")
    ledger = CostLedger()

    entry = ledger.record_usage(
        objective_id="obj-cost-1",
        cycle_id="cyc-cost-1",
        role="verifier",
        model=profile,
        tokens_in=1_000_000,
        tokens_out=1_000_000,
        gap_id="gap-102",
    )

    expected = round(profile.input_cost_per_M + profile.output_cost_per_M, 6)
    assert entry.cost_usd == expected
    assert ledger.get_objective_total_cost("obj-cost-1") == expected
    assert ledger.get_gap_repair_cost("gap-102") == expected
    assert ledger.get_role_cost("obj-cost-1", "verifier") == expected


def test_cost_ledger_headroom_and_budget_enforcement() -> None:
    profile = _profile("qwen/qwen3-coder-30b-a3b")
    ledger = CostLedger()

    ledger.record_usage(
        objective_id="obj-cost-2",
        cycle_id="cyc-cost-2",
        role="planner",
        model=profile,
        tokens_in=5_000_000,
        tokens_out=1_000_000,
    )

    has_headroom, spend, remaining = ledger.check_budget_headroom(
        objective_id="obj-cost-2",
        budget_limit_usd=1.0,
    )
    assert has_headroom is True
    assert spend > 0

    ledger.record_usage(
        objective_id="obj-cost-2",
        cycle_id="cyc-cost-2",
        role="executor",
        model=profile,
        tokens_in=50_000_000,
        tokens_out=10_000_000,
    )

    has_headroom_2, spend_2, _remaining_2 = ledger.check_budget_headroom(
        objective_id="obj-cost-2",
        budget_limit_usd=0.05,
    )
    assert has_headroom_2 is False
    assert spend_2 > 0.05


def test_autonomous_budget_blocks_repair_attempts() -> None:
    ledger = CostLedger(max_repair_attempts=3)
    verdict, reason = ledger.enforce_autonomous_budget(
        objective_id="obj-x",
        repair_attempt_count=3,
    )
    assert verdict == "AUTONOMOUS_BLOCKED"
    assert "repair attempts" in reason
