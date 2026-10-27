"""Tests for Cursor #41–50 objective-cycle D/G modules."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.experimental.adapters.base import MoveTokenV1, StepAdapterRequestV1
from backend.aix_core.runtime.objective.experimental.adapters.devexecutor import DevExecutorStepAdapter
from backend.aix_core.runtime.objective.experimental.adapters.orchestrator_v2 import OrchestratorV2StepAdapter
from backend.aix_core.runtime.objective.experimental.adapters.planner_coder import PlannerCoderAiderStepAdapter
from backend.aix_core.runtime.governance.experimental.budget import BudgetLedgerError, DurableBudgetLedger
from backend.aix_core.runtime.objective.contracts import BudgetV1, UsageV1
from backend.aix_core.runtime.executor.experimental.executor_selection import (
    CycleExecutorSelectionPolicy,
    ExecutorSelectionRequestV1,
)
from backend.aix_core.runtime.objective.experimental.model_agent_eval import EvalCaseV1, ModelAgentEvaluator
from backend.aix_core.runtime.objective.experimental.model_lifecycle_cycle import CycleModelLifecycle, ModelRecordV1
from backend.aix_core.runtime.governance.experimental.prompt_registry import PromptRegistry, PromptRegistryError, PromptVersionV1
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter
from backend.aix_core.runtime.governance.experimental.token_efficiency import TokenEfficiencyEngine


def test_provider_routing_allows_integration_only() -> None:
    decision = GovernedProviderRouter().resolve(role="planner", model_ref="gpt-test")
    assert decision.disposition == "ALLOW"
    assert decision.route is not None
    assert decision.route.gateway == "hyeve_integration"


def test_provider_routing_denies_direct() -> None:
    decision = GovernedProviderRouter().resolve(
        role="executor",
        model_ref="gpt-test",
        route_kind="direct_provider",
    )
    assert decision.disposition == "DENY"
    assert "DIRECT_PROVIDER_ROUTE_FORBIDDEN" in decision.reason_codes


def test_prompt_registry_register_rollback() -> None:
    reg = PromptRegistry()
    v1 = reg.register(
        PromptVersionV1(
            prompt_id="planner.system",
            version="1",
            role="planner",
            body_hash="h1",
            body="plan v1",
            change_review_ref="review-1",
        )
    )
    reg.register(
        PromptVersionV1(
            prompt_id="planner.system",
            version="2",
            role="planner",
            body_hash="h2",
            body="plan v2",
            change_review_ref="review-2",
        )
    )
    assert reg.get_active("planner.system").version == "2"
    rolled = reg.rollback("planner.system", "1", review_ref="review-3")
    assert rolled.version == "1"
    assert reg.get_active("planner.system").body == "plan v1"
    assert v1.prompt_id == "planner.system"


def test_prompt_registry_requires_review() -> None:
    with pytest.raises(PromptRegistryError):
        PromptRegistry().register(
            PromptVersionV1(
                prompt_id="x",
                version="1",
                role="planner",
                body_hash="h",
                body="b",
            )
        )


def test_model_lifecycle_pins_and_blocks_silent_fallback() -> None:
    life = CycleModelLifecycle()
    life.upsert(ModelRecordV1("m1", "v1", health="healthy", rollback_version_ref="v0"))
    pin = life.pin_for_attempt(attempt_id="a1", model_ref="m1", requested_fallback_ref="m2")
    assert pin.allowed is False
    assert "SILENT_FALLBACK_FORBIDDEN" in pin.reason_codes
    ok = life.pin_for_attempt(attempt_id="a2", model_ref="m1")
    assert ok.allowed is True
    rb = life.rollback_pin("a2")
    assert rb.version_ref == "v0"


def test_budget_ledger_protects_verification_reserve() -> None:
    # Isolated per-test temp dir via `tempfile.mkdtemp()`, not a fixed repo-relative
    # path shared across runs and not pytest's own shared `tmp_path` basetemp -- a
    # shared, git-tracked path (previously `.tmp_budget_test/budget.json` under the
    # repo root) produced intermittent `os.replace` `PermissionError`s under Windows
    # file-lock contention (AV/indexer) on repeated writes to the same real path, and
    # this environment's shared pytest `tmp_path` basetemp (`%TEMP%\pytest-of-*`) has
    # the same intermittent-lock problem. A private `tempfile.mkdtemp()` directory
    # avoids both collision surfaces.
    budget_path = Path(tempfile.mkdtemp(prefix="aix-budget-test-")) / "budget.json"
    ceiling = BudgetV1(time_seconds=100, tokens=1000, cost_usd=10.0, retries=3, steps=5, concurrency=1)
    reserve = BudgetV1(time_seconds=10, tokens=200, cost_usd=2.0, retries=0, steps=1, concurrency=1)
    ledger = DurableBudgetLedger(path=budget_path, ceiling=ceiling, verification_reserve=reserve)
    ledger.reserve(
        reservation_id="r1",
        cycle_id="c1",
        attempt_id="a1",
        purpose="step",
        need=BudgetV1(time_seconds=50, tokens=700, cost_usd=5.0, retries=1, steps=1, concurrency=1),
    )
    with pytest.raises(BudgetLedgerError, match="budget_exhausted"):
        ledger.reserve(
            reservation_id="r2",
            cycle_id="c1",
            attempt_id="a2",
            purpose="step",
            need=BudgetV1(time_seconds=50, tokens=200, cost_usd=4.0, retries=1, steps=1, concurrency=1),
        )
    ledger.reserve(
        reservation_id="rv",
        cycle_id="c1",
        attempt_id="av",
        purpose="verification",
        need=BudgetV1(time_seconds=5, tokens=100, cost_usd=1.0, retries=0, steps=1, concurrency=1),
    )
    ledger.consume("r1", usage=UsageV1(tokens_in=10, tokens_out=10))
    # reload durability
    reloaded = DurableBudgetLedger(path=budget_path, ceiling=ceiling, verification_reserve=reserve)
    assert reloaded.snapshot()["reservations"]["r1"]["status"] == "CONSUMED"


def test_token_efficiency_dedupes_and_holds_verification_reserve() -> None:
    plan = TokenEfficiencyEngine().plan(
        blocks=["A", "A", "B", "C", "D", "E"],
        role="planner",
        verification_token_reserve=100,
    )
    assert plan.deduped_block_count < 6
    assert plan.verification_token_reserve == 100
    assert "VERIFICATION_RESERVE_HELD" in plan.reason_codes


def test_model_agent_eval_abuse_and_determinism() -> None:
    ev = ModelAgentEvaluator()
    abuse = ev.run_case(
        EvalCaseV1("c1", "abuse", "in", forbidden_substrings=("auto_accept",)),
        output="direct_provider boom",
        route_kind="direct",
    )
    assert abuse.disposition == "FAIL"
    from hashlib import sha256

    body = "stable"
    digest = sha256(body.encode()).hexdigest()
    det = ev.run_case(EvalCaseV1("c2", "determinism", "in", expected_hash=digest), output=body)
    assert det.disposition == "PASS"


def test_executor_selection_rejects_self_choice() -> None:
    policy = CycleExecutorSelectionPolicy()
    denied = policy.select(
        ExecutorSelectionRequestV1(
            step_id="s1",
            attempt_id="a1",
            role="executor",
            task_class="docs_only_change",
            executor_self_nomination="devexecutor",
        )
    )
    assert denied.disposition == "DENIED"
    ok = policy.select(
        ExecutorSelectionRequestV1(
            step_id="s1",
            attempt_id="a1",
            role="executor",
            task_class="single_file_helper",
        )
    )
    assert ok.disposition == "SELECTED"
    assert ok.executor_id == "planner_coder_aider"


def _token(executor_id: str, attempt_id: str = "a1") -> MoveTokenV1:
    return MoveTokenV1(
        token_id=f"tok-{executor_id}-{attempt_id}",
        step_id="s1",
        attempt_id=attempt_id,
        executor_id=executor_id,
        expires_at="2099-01-01T00:00:00Z",
    )


def test_orchestrator_v2_adapter_one_token_one_attempt() -> None:
    adapter = OrchestratorV2StepAdapter()
    req = StepAdapterRequestV1(
        cycle_id="c1",
        step_id="s1",
        attempt_id="a1",
        move_token=_token("orchestrator_v2"),
        workspace_root="D:/tmp",
    )
    first = adapter.execute(req)
    assert first.disposition == "SUCCEEDED"
    second = adapter.execute(req)
    assert second.disposition == "DENIED"
    assert "MOVE_TOKEN_REUSED" in second.reason_codes


def test_planner_coder_adapter_binds_lane() -> None:
    result = PlannerCoderAiderStepAdapter().execute(
        StepAdapterRequestV1(
            cycle_id="c1",
            step_id="s1",
            attempt_id="a2",
            move_token=_token("planner_coder_aider", "a2"),
            workspace_root="D:/tmp",
            dry_run=True,
        )
    )
    assert result.disposition == "SUCCEEDED"
    assert "PLANNER_CODER_AIDER_STEP_BOUND" in result.reason_codes


def test_devexecutor_adapter_manifest_lease_cancel() -> None:
    adapter = DevExecutorStepAdapter()
    bad = adapter.execute(
        StepAdapterRequestV1(
            cycle_id="c1",
            step_id="s1",
            attempt_id="a3",
            move_token=_token("devexecutor", "a3"),
            workspace_root="D:/tmp",
        ),
        tools_requested=("shell_exec",),
    )
    assert bad.disposition == "FAILED"
    ok = adapter.execute(
        StepAdapterRequestV1(
            cycle_id="c1",
            step_id="s1",
            attempt_id="a4",
            move_token=_token("devexecutor", "a4"),
            workspace_root="D:/tmp",
            timeout_seconds=30,
        ),
        tools_requested=("read_file", "git_diff"),
    )
    assert ok.disposition == "SUCCEEDED"
    cancelled = adapter.execute(
        StepAdapterRequestV1(
            cycle_id="c1",
            step_id="s1",
            attempt_id="a5",
            move_token=_token("devexecutor", "a5"),
            workspace_root="D:/tmp",
        ),
        cancel=True,
    )
    assert cancelled.disposition == "FAILED"
    assert "CANCELLED_BEFORE_EXECUTION" in cancelled.reason_codes
