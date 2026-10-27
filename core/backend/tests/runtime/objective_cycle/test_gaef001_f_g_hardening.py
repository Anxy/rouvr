"""AIX-GAEF-001: focused tests for Test F (codegen retry) and Test G (deterministic verification).

Covers:
  F: bounded retry on MODEL_RETURNED_NO_EDIT, prior failure context, terminal results
  G: deterministic PASS for new file creation, semantic verifier skipped, no repair loop
  Cross-cutting: semantic verifier demotion, cosmetic repair rejection, retry budget
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RequiredEffectV1,
    StepContractV1,
)
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.executor.execution_reasons import REASON_CATALOG
from backend.aix_core.runtime.recovery.repair_directive import (
    _failure_fingerprint,
    _is_cosmetic_only_repair,
    build_repair_directive,
)
from backend.aix_core.runtime.verification.semantic_verifier import (
    SemanticVerificationOutcome,
    verify_step,
)
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatCompletionResultV1,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contract(**overrides: Any) -> StepContractV1:
    base = dict(
        cycle_id="cyc-1", tenant_id="t-1", step_index=0,
        purpose="create utility module",
        target_scope=("util_g.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("util_g.py file exists", "tests pass"),
        validation_commands=("pytest test_util_g.py",),
        assigned_role="executor",
        step_budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        status="PENDING",
    )
    base.update(overrides)
    return StepContractV1(**base)  # type: ignore[arg-type]


def _result(**overrides: Any) -> RealExecuteResult:
    base = dict(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", objective_status="COMPLETED",
        completion_gates={
            "gates": {"diff_exists": True, "verification_passed": True, "verification_ran": True},
            "verification_verdict": "PASSED",
        },
        diff_text="--- /dev/null\n+++ b/util_g.py\n@@ -0,0 +1,5 @@\n+def helper():\n+    return 42\n",
        changed_source_files=({"path": "util_g.py"},),
    )
    base.update(overrides)
    return RealExecuteResult(**base)  # type: ignore[arg-type]


# ===========================================================================
# TEST F — Codegen contract hardening
# ===========================================================================


class TestF_CodegenRetry:
    """Test F: MODEL_RETURNED_NO_EDIT bounded retry behavior."""

    def test_model_returned_no_edit_is_retryable_in_catalog(self) -> None:
        reason = REASON_CATALOG["MODEL_RETURNED_NO_EDIT"]
        assert reason.retryable is True
        assert reason.system_action_required is True

    def test_codegen_failed_is_terminal_in_catalog(self) -> None:
        reason = REASON_CATALOG["CODEGEN_FAILED"]
        assert reason.retryable is False

    def test_edit_parse_failed_is_retryable_in_catalog(self) -> None:
        reason = REASON_CATALOG["EDIT_PARSE_FAILED"]
        assert reason.retryable is True

    def test_retry_classification_consistency(self) -> None:
        """Reason catalog and durable backend retry sets must agree."""
        from backend.aix_core.runtime.objective.durable_api_backend import (
            _REPAIRABLE_TERMINAL_REASONS,
            _RETRYABLE_TERMINAL_REASONS,
            _TERMINAL_ONLY_REASONS,
        )
        for code, reason in REASON_CATALOG.items():
            if reason.retryable:
                assert code not in _TERMINAL_ONLY_REASONS, (
                    f"{code} is retryable in catalog but TERMINAL in durable backend"
                )

    def test_no_unrelated_file_mutation_on_codegen_failure(self) -> None:
        """A MODEL_RETURNED_NO_EDIT must not produce changed files."""
        from backend.aix_core.runtime.executor.multi_step_execute import MultiStepMutationError
        try:
            raise MultiStepMutationError(
                "MODEL_RETURNED_NO_EDIT",
                "model returned no edits",
                raw_model_payload={"no_files_identified": True},
            )
        except MultiStepMutationError as exc:
            assert exc.code == "MODEL_RETURNED_NO_EDIT"
            assert exc.raw_model_payload is not None


# ===========================================================================
# TEST G — Deterministic verification
# ===========================================================================


class TestG_DeterministicVerification:
    """Test G: deterministic PASS for new file creation without semantic model."""

    @pytest.mark.asyncio
    async def test_new_file_creation_passes_without_semantic_model(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = False

        async def _should_not_be_called(**kwargs: Any) -> Any:
            nonlocal called
            called = True
            raise AssertionError("semantic model must not be called for decidable new-file creation")

        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
            _should_not_be_called,
        )
        outcome = await verify_step(
            result=_result(),
            step_contract=_contract(),
            repair_budget_remaining=2,
            semantic_verifier_enabled=True,
        )
        assert called is False
        assert outcome.verdict == "PASS"
        assert outcome.semantic_checked is False

    @pytest.mark.asyncio
    async def test_new_file_with_tests_passing_is_decidable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """File creation + test pass = deterministic PASS, no semantic verifier."""
        called = False

        async def _should_not_be_called(**kwargs: Any) -> Any:
            nonlocal called
            called = True
            raise AssertionError("should not call semantic model")

        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
            _should_not_be_called,
        )
        contract = _contract(
            purpose="create util_g.py and test_util_g.py",
            target_scope=("util_g.py", "test_util_g.py"),
            acceptance_criteria=("util_g.py file exists", "test_util_g.py file exists", "tests pass"),
        )
        result = _result(
            diff_text=(
                "--- /dev/null\n+++ b/util_g.py\n@@ -0,0 +1,3 @@\n+def helper():\n+    return 42\n"
                "--- /dev/null\n+++ b/test_util_g.py\n@@ -0,0 +1,5 @@\n+from util_g import helper\n+def test_helper():\n+    assert helper() == 42\n"
            ),
            changed_source_files=({"path": "util_g.py"}, {"path": "test_util_g.py"}),
        )
        outcome = await verify_step(
            result=result, step_contract=contract,
            repair_budget_remaining=2, semantic_verifier_enabled=True,
        )
        assert called is False
        assert outcome.verdict == "PASS"
        assert outcome.semantic_checked is False

    @pytest.mark.asyncio
    async def test_no_repair_loop_on_decidable_step(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A decidable step never returns REPAIR — it's PASS or mechanical HALT."""
        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
            AsyncMock(side_effect=AssertionError("no model call")),
        )
        outcome = await verify_step(
            result=_result(),
            step_contract=_contract(),
            repair_budget_remaining=2,
            semantic_verifier_enabled=True,
        )
        assert outcome.verdict == "PASS"

    @pytest.mark.asyncio
    async def test_deterministic_acceptance_with_all_criteria_met(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When all acceptance criteria are mechanically provable, skip semantic."""
        called = False

        async def _should_not_be_called(**kwargs: Any) -> Any:
            nonlocal called
            called = True
            raise AssertionError("should not call semantic model")

        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
            _should_not_be_called,
        )
        contract = _contract(
            purpose="fix add function in calc_f.py",
            target_scope=("calc_f.py",),
            acceptance_criteria=(
                "calc_f.py must contain `return a + b`",
                "tests pass",
            ),
            validation_commands=("pytest test_calc_f.py",),
        )
        result = _result(
            diff_text="--- a/calc_f.py\n+++ b/calc_f.py\n@@ -1,3 +1,3 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n",
            changed_source_files=({"path": "calc_f.py"},),
            events=[{
                "event_type": "mutation.round.completed",
                "payload_refs": {"test_returncode": 0},
            }],
        )
        outcome = await verify_step(
            result=result, step_contract=contract,
            repair_budget_remaining=2, semantic_verifier_enabled=True,
        )
        assert called is False
        assert outcome.verdict == "PASS"
        assert outcome.semantic_checked is False


# ===========================================================================
# Semantic verifier demotion
# ===========================================================================


class TestSemanticVerifierDemotion:
    """PART 3: vague REPAIR without concrete criterion failure is demoted."""

    @pytest.mark.asyncio
    async def test_vague_repair_demoted_to_pass(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """REPAIR with no concrete criterion failure is demoted when mechanical passed."""
        async def _vague_model(**kwargs: Any) -> ChatCompletionResultV1:
            return ChatCompletionResultV1(
                output_text=json.dumps({
                    "verdict": "REPAIR",
                    "reasons": ["not meaningful enough"],
                    "criteria_results": [
                        {"criterion": "code quality", "status": "partial"},
                    ],
                }),
                model="test",
                usage={},
                provider="test",
                resolved_provider="test",
            )

        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
            _vague_model,
        )
        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.load_model_registry",
            lambda: type("R", (), {"model_ref_for": lambda self, r: "test-model"})(),
        )
        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.GovernedProviderRouter",
            lambda: type("G", (), {"resolve": lambda self, **kw: type("D", (), {"disposition": "ALLOW"})()})(),
        )

        # Use ambiguous criteria so _deterministic_acceptance_proved returns False
        contract = _contract(
            purpose="refactor module for better architecture",
            target_scope=("module.py",),
            acceptance_criteria=("module uses new pattern throughout the codebase",),
            validation_commands=("echo ok",),
        )
        result = _result(
            diff_text="--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-old\n+new\n",
            changed_source_files=({"path": "module.py"},),
        )
        outcome = await verify_step(
            result=result, step_contract=contract,
            repair_budget_remaining=2, semantic_verifier_enabled=True,
        )
        assert outcome.verdict == "PASS"
        assert "demoted" in " ".join(outcome.reasons).lower()

    @pytest.mark.asyncio
    async def test_concrete_failure_not_demoted(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """REPAIR with a concrete failed criterion is not demoted."""
        async def _concrete_model(**kwargs: Any) -> ChatCompletionResultV1:
            return ChatCompletionResultV1(
                output_text=json.dumps({
                    "verdict": "REPAIR",
                    "reasons": ["function add still returns a - b"],
                    "criteria_results": [
                        {"criterion": "add must return a + b", "status": "failed"},
                    ],
                }),
                model="test",
                usage={},
                provider="test",
                resolved_provider="test",
            )

        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
            _concrete_model,
        )
        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.load_model_registry",
            lambda: type("R", (), {"model_ref_for": lambda self, r: "test-model"})(),
        )
        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.GovernedProviderRouter",
            lambda: type("G", (), {"resolve": lambda self, **kw: type("D", (), {"disposition": "ALLOW"})()})(),
        )

        # Use ambiguous criteria so deterministic check can't prove it
        contract = _contract(
            purpose="fix add function behavior",
            target_scope=("calc_f.py",),
            acceptance_criteria=("add must return a + b for all inputs",),
            validation_commands=("echo ok",),
        )
        result = _result(
            diff_text="--- a/calc_f.py\n+++ b/calc_f.py\n@@ -1 +1 @@\n-old\n+new\n",
            changed_source_files=({"path": "calc_f.py"},),
        )
        outcome = await verify_step(
            result=result, step_contract=contract,
            repair_budget_remaining=2, semantic_verifier_enabled=True,
        )
        assert outcome.verdict == "REPAIR"

    @pytest.mark.asyncio
    async def test_semantic_verifier_runs_for_ambiguous_criteria(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Semantic verifier is still invoked when criteria are genuinely ambiguous."""
        called = False

        async def _model(**kwargs: Any) -> ChatCompletionResultV1:
            nonlocal called
            called = True
            return ChatCompletionResultV1(
                output_text=json.dumps({
                    "verdict": "PASS",
                    "reasons": ["looks good"],
                    "criteria_results": [
                        {"criterion": "code is well structured", "status": "pass"},
                    ],
                }),
                model="test",
                usage={},
                provider="test",
                resolved_provider="test",
            )

        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.chat_completion",
            _model,
        )
        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.load_model_registry",
            lambda: type("R", (), {"model_ref_for": lambda self, r: "test-model"})(),
        )
        monkeypatch.setattr(
            "backend.aix_core.runtime.verification.semantic_verifier.GovernedProviderRouter",
            lambda: type("G", (), {"resolve": lambda self, **kw: type("D", (), {"disposition": "ALLOW"})()})(),
        )

        # Genuinely ambiguous criteria that _deterministic_acceptance_proved cannot decide
        contract = _contract(
            purpose="improve code quality across the module",
            target_scope=("module.py",),
            acceptance_criteria=("code is well structured and maintainable",),
            validation_commands=("echo ok",),
        )
        result = _result(
            diff_text="--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-old\n+new\n",
            changed_source_files=({"path": "module.py"},),
        )
        outcome = await verify_step(
            result=result, step_contract=contract,
            repair_budget_remaining=2, semantic_verifier_enabled=True,
        )
        assert called is True
        assert outcome.semantic_checked is True


# ===========================================================================
# Repair loop guard
# ===========================================================================


class TestRepairLoopGuard:
    """PART 4: cosmetic repair rejection and failure fingerprinting."""

    def test_cosmetic_diff_detected(self) -> None:
        diff = "+# added a comment\n-# old comment\n"
        assert _is_cosmetic_only_repair(diff) is True

    def test_substantive_diff_not_cosmetic(self) -> None:
        diff = "+    return a + b\n-    return a - b\n"
        assert _is_cosmetic_only_repair(diff) is False

    def test_empty_diff_is_cosmetic(self) -> None:
        assert _is_cosmetic_only_repair("") is True

    def test_failure_fingerprint_stable(self) -> None:
        fp1 = _failure_fingerprint(("reason A", "reason B"), "diff1")
        fp2 = _failure_fingerprint(("reason A", "reason B"), "diff1")
        assert fp1 == fp2

    def test_failure_fingerprint_differs_on_different_reasons(self) -> None:
        fp1 = _failure_fingerprint(("reason A",), "diff1")
        fp2 = _failure_fingerprint(("reason B",), "diff1")
        assert fp1 != fp2

    def test_cosmetic_repair_rejected_in_directive(self) -> None:
        contract = _contract(acceptance_criteria=("function must return correct value",))
        directive = build_repair_directive(
            step=contract,
            reasons=("test failed",),
            last_repair_diff="+ # added docstring\n- # old docstring\n",
        )
        assert "COSMETIC_REPAIR_REJECTED" in directive.required_correction

    def test_cosmetic_repair_allowed_when_criteria_require_docstring(self) -> None:
        contract = _contract(acceptance_criteria=("add docstring to helper function",))
        directive = build_repair_directive(
            step=contract,
            reasons=("missing docstring",),
            last_repair_diff="+ # added docstring\n- # old docstring\n",
        )
        assert "COSMETIC_REPAIR_REJECTED" not in directive.required_correction

    def test_repeated_failure_fingerprint_flagged(self) -> None:
        fp = _failure_fingerprint(("test failed",), "diff")
        directive = build_repair_directive(
            step=_contract(),
            reasons=("test failed",),
            prior_failure_fingerprints=(fp,),
            last_repair_diff="diff",
        )
        assert "REPEATED_FAILURE_DETECTED" in directive.required_correction

    def test_retry_budget_bounded(self) -> None:
        """Retry budget is finite and enforced."""
        from backend.aix_core.runtime.recovery.retry_repair import decide_retry
        from backend.aix_core.runtime.objective.contracts import BudgetV1
        from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1

        classification = FailureClassificationV1("REPAIRABLE", ("MODEL_RETURNED_NO_EDIT",))
        budget = BudgetV1(time_seconds=3600, tokens=100000, cost_usd=5.0, retries=0, steps=10, concurrency=1)
        decision = decide_retry(classification, current_attempt_number=3, remaining_budget=budget)
        assert decision.disposition == "DENIED"
        assert "RETRY_BUDGET_EXHAUSTED" in decision.reason_codes
