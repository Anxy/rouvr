"""Regression tests for AUTHORIZATION_REQUIRED handling.

Proves:
- valid mocked execution progresses beyond STEP_READY
- missing auth produces AUTHORIZATION_REQUIRED
- auth failure does not trigger repeated discovery
- dispatcher does not loop indefinitely
- failure report is persisted and returned
- execution can resume after auth becomes available
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1
from backend.aix_core.runtime.objective.contracts import StepContractV1, BudgetV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteError, RealExecuteResult

BACKEND_ROOT = Path(__file__).resolve().parents[4] / "backend"
OBJECTIVE = "Update the feature flag in backend/config.py"


def _admit_body() -> dict[str, Any]:
    return {
        "objective": OBJECTIVE,
        "repository_id": "hyeve-aix-service",
        "project_id": "test",
        "workhub_task_id": "auth-test-001",
        "task_number": 1,
        "task_class": "verification_only",
        "definition_of_done": ["Feature flag updated"],
    }


async def _mock_propose(**kwargs: Any) -> StepContractProposalV1:
    obligations = tuple(kwargs.get("obligations") or ())
    obligation_refs = tuple(
        str(getattr(o, "id", "") or "")
        for o in obligations
        if str(getattr(o, "id", "") or "").strip()
    )
    return StepContractProposalV1(
        contract=StepContractV1(
            cycle_id=kwargs.get("cycle_id", "cyc-test"),
            tenant_id=kwargs.get("tenant_id", "test"),
            step_index=0,
            purpose="Update feature flag",
            target_scope=("backend/config.py",),
            validation_commands=("echo ok",),
            expected_output_schema_ref="text",
            acceptance_criteria=("Feature flag updated",),
            assigned_role="executor",
            risk_tier="LOW",
            step_budget=BudgetV1(
                time_seconds=300, tokens=50000, cost_usd=1.0,
                retries=1, steps=1, concurrency=1,
            ),
            obligation_refs=obligation_refs,
            status="PENDING",
        ),
        reason="PLAN_ACCEPTED",
        reply="Plan created",
        evidence_requests=(),
        used_legacy_one_step=True,
    )


async def _mock_real_execute_success(**kwargs: Any) -> RealExecuteResult:
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace="test-workspace",
        evidence_refs=[],
        workhub_loaded=False,
        artifacts={},
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=1,
        target_files_modified=0,
    )


async def _mock_real_execute_auth_required(**kwargs: Any) -> RealExecuteResult:
    raise RealExecuteError(
        code="AUTHORIZATION_REQUIRED",
        detail="No valid auth token for Hyeve Integration LLM gateway",
    )


class _FakeSettings:
    # Planner ON + no_plan_bootstrap: mock propose covers DoD obligations, then
    # these tests stub execute to prove AUTHORIZATION_REQUIRED (not coverage).
    # Do not rely on conftest seeding keyed off process-global planner settings.
    INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED = True
    INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS = 30
    OBJECTIVE_CYCLE_REPOSITORY_ROOTS = json.dumps({
        "hyeve-aix-service": str(BACKEND_ROOT.parent).replace("\\", "/"),
    })
    INTELLIGENCE_LAYER_VERIFIER_ENABLED = False
    INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED = False


def _make_backend() -> DurableObjectiveCycleApiBackend:
    return DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        create_workhub_tasks=False,
        mark_workhub_done=False,
        settings=_FakeSettings(),
    )


pytestmark = pytest.mark.no_plan_bootstrap


_test_counter = 0


async def _admit_and_start(backend: DurableObjectiveCycleApiBackend) -> tuple[str, str]:
    """ADMIT + START only, return (obj_id, cycle_id). Does not approve."""
    global _test_counter
    _test_counter += 1
    pfx = f"auth-{_test_counter}"

    admit = await backend.command(
        operation="admit_objective",
        tenant_id="test", actor_id="test", resource_id=None,
        body=_admit_body(),
        idempotency_key=f"{pfx}-admit",
        expected_version=None,
    )
    assert admit["ok"] is True
    obj_id = admit["objective_id"]

    start = await backend.command(
        operation="start_cycle",
        tenant_id="test", actor_id="test", resource_id=obj_id,
        body={"candidate_sha": "abc1234fake"},
        idempotency_key=f"{pfx}-start",
        expected_version=None,
    )
    assert start["ok"] is True
    return obj_id, start["cycle_id"]


async def _approve_cycle(
    backend: DurableObjectiveCycleApiBackend, cycle_id: str, key_prefix: str = "auth"
) -> dict[str, Any]:
    global _test_counter
    return await backend.command(
        operation="validate_approval_reference",
        tenant_id="test", actor_id="test",
        resource_id=cycle_id,
        body={"approval_reference": "test-approval", "approved": True},
        idempotency_key=f"{key_prefix}-{_test_counter}-approve",
        expected_version=None,
    )


class TestAuthorizationRequired:

    @pytest.mark.asyncio
    async def test_valid_execution_progresses_beyond_step_ready(self):
        """With valid auth mock, cycle progresses past STEP_READY to completion."""
        backend = _make_backend()
        with (
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
                side_effect=_mock_propose,
            ),
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
                side_effect=_mock_real_execute_success,
            ) as m_exec,
        ):
            obj_id, cycle_id = await _admit_and_start(backend)
            approve = await _approve_cycle(backend, cycle_id, "valid")
            assert approve["ok"] is True, f"Approve failed: {approve}"

            for i in range(1, 11):
                dispatch = await backend.command(
                    operation="dispatch_ready_step",
                    tenant_id="test", actor_id="test",
                    resource_id=cycle_id, body={},
                    idempotency_key=f"valid-dispatch-{i}",
                    expected_version=None,
                )
                if not dispatch.get("dispatch_deferred") and not dispatch.get("dispatch_required"):
                    break
            else:
                pytest.fail("Dispatch loop did not terminate")

            assert m_exec.call_count >= 1
            cycle = backend._cycles[cycle_id]
            assert cycle.dispatch_state != "STEP_READY"

    @pytest.mark.asyncio
    async def test_auth_failure_produces_authorization_required(self):
        """Missing auth produces AUTHORIZATION_REQUIRED and sets WAITING_FOR_AUTH."""
        backend = _make_backend()
        with (
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
                side_effect=_mock_propose,
            ),
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
                side_effect=_mock_real_execute_auth_required,
            ),
        ):
            obj_id, cycle_id = await _admit_and_start(backend)
            # Approve will inline-dispatch which hits auth failure
            await _approve_cycle(backend, cycle_id, "authfail")

            cycle = backend._cycles[cycle_id]
            assert cycle.dispatch_state == "WAITING_FOR_AUTH", (
                f"Expected WAITING_FOR_AUTH, got {cycle.dispatch_state}"
            )
            assert cycle.terminal_reason == "AUTHORIZATION_REQUIRED"

    @pytest.mark.asyncio
    async def test_auth_failure_does_not_trigger_discovery(self):
        """AUTHORIZATION_REQUIRED must not re-run discovery/planning after failure."""
        backend = _make_backend()
        discovery_calls: list[str] = []
        original_propose = _mock_propose

        async def tracking_propose(**kwargs: Any) -> StepContractProposalV1:
            discovery_calls.append("propose")
            return await original_propose(**kwargs)

        with (
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
                side_effect=tracking_propose,
            ),
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
                side_effect=_mock_real_execute_auth_required,
            ),
        ):
            obj_id, cycle_id = await _admit_and_start(backend)
            proposals_before_approve = len(discovery_calls)
            await _approve_cycle(backend, cycle_id, "nodisc")

            # After auth failure, try dispatching again — should not re-plan
            proposals_after = len(discovery_calls)
            for i in range(1, 4):
                dispatch = await backend.command(
                    operation="dispatch_ready_step",
                    tenant_id="test", actor_id="test",
                    resource_id=cycle_id, body={},
                    idempotency_key=f"nodisc-extra-dispatch-{i}",
                    expected_version=None,
                )
                if not dispatch.get("dispatch_deferred"):
                    break

            proposals_final = len(discovery_calls)
            extra_proposals = proposals_final - proposals_after
            assert extra_proposals == 0, (
                f"Discovery/planning re-ran {extra_proposals} times after auth failure"
            )

    @pytest.mark.asyncio
    async def test_dispatcher_does_not_loop_indefinitely(self):
        """Auth failure stops the dispatch loop immediately, no repeated attempts."""
        backend = _make_backend()
        exec_count = 0

        async def counting_auth_fail(**kwargs: Any) -> RealExecuteResult:
            nonlocal exec_count
            exec_count += 1
            raise RealExecuteError(
                code="AUTHORIZATION_REQUIRED",
                detail="No valid auth token",
            )

        with (
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
                side_effect=_mock_propose,
            ),
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
                side_effect=counting_auth_fail,
            ),
        ):
            obj_id, cycle_id = await _admit_and_start(backend)
            await _approve_cycle(backend, cycle_id, "noloop")

        # run_real_execute called exactly once — no retry loop
        assert exec_count == 1, f"run_real_execute called {exec_count} times, expected 1"

    @pytest.mark.asyncio
    async def test_failure_report_persisted_and_queryable(self):
        """FailureReportV1 is persisted and returned via query API."""
        backend = _make_backend()
        with (
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
                side_effect=_mock_propose,
            ),
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
                side_effect=_mock_real_execute_auth_required,
            ),
        ):
            obj_id, cycle_id = await _admit_and_start(backend)
            await _approve_cycle(backend, cycle_id, "report")

        reports = backend._failure_reports.get(obj_id, [])
        assert len(reports) >= 1, "No failure report persisted"
        report = reports[-1]
        assert report.failure_code == "AUTHORIZATION_REQUIRED"
        assert report.retryable is False
        assert report.upstream_dependency == "hyeve_integration_llm_gateway"
        assert report.retry_after_condition == "valid_authorization_token_available"

        query_result = await backend.query(
            operation="get_failure_reports",
            tenant_id="test",
            resource_id=obj_id,
            child_id=None,
            filters={},
        )
        assert query_result["ok"] is True
        assert query_result["count"] >= 1
        assert query_result["items"][0]["failure_code"] == "AUTHORIZATION_REQUIRED"

    @pytest.mark.asyncio
    async def test_resume_after_auth_restored(self):
        """After AUTHORIZATION_REQUIRED, resume with valid auth progresses execution."""
        backend = _make_backend()
        call_count = 0

        async def auth_then_succeed(**kwargs: Any) -> RealExecuteResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RealExecuteError(
                    code="AUTHORIZATION_REQUIRED",
                    detail="No valid auth token",
                )
            return RealExecuteResult(
                ok=True,
                code="REAL_EXECUTE_OK",
                workspace="test-workspace",
                evidence_refs=[],
                workhub_loaded=False,
                artifacts={},
                execution_status="COMPLETED",
                verification_status="PASSED",
                objective_status="COMPLETED",
                real_operations=1,
                target_files_modified=0,
            )

        with (
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
                side_effect=_mock_propose,
            ),
            patch(
                "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
                side_effect=auth_then_succeed,
            ),
        ):
            obj_id, cycle_id = await _admit_and_start(backend)
            # Approve triggers first dispatch which hits auth failure
            await _approve_cycle(backend, cycle_id, "resume")

            cycle = backend._cycles[cycle_id]
            assert cycle.dispatch_state == "WAITING_FOR_AUTH"

            # Resume with valid auth via the _control "resume" action
            resume = await backend.command(
                operation="resume_cycle",
                tenant_id="test", actor_id="test",
                resource_id=cycle_id,
                body={
                    "caller_authorization": "valid-token-xyz",
                    "reason": "auth_restored",
                },
                idempotency_key="resume-auth-restore",
                expected_version=None,
            )
            # The resume itself may have dispatched successfully
            if call_count >= 2:
                return  # success — resume already completed execution

            # Dispatch with auth if resume didn't inline-dispatch
            for i in range(1, 11):
                dispatch = await backend.command(
                    operation="dispatch_ready_step",
                    tenant_id="test", actor_id="test",
                    resource_id=cycle_id, body={
                        "caller_authorization": "valid-token-xyz",
                    },
                    idempotency_key=f"resume-dispatch-{i}",
                    expected_version=None,
                )
                if not dispatch.get("dispatch_deferred") and not dispatch.get("dispatch_required"):
                    break

        assert call_count >= 2, f"Expected at least 2 calls (1 fail + 1 success), got {call_count}"
