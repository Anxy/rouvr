"""P30: Agent failure feedback loop — structured reports from every pipeline agent."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.aix_core.runtime.objective.failure_report import (
    AgentFeedbackCollector,
    build_failure_report,
    summarize_failure_chain,
)
from backend.aix_core.runtime.objective.contracts import FailureReportV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1
from backend.aix_core.runtime.objective.contracts import StepContractV1, BudgetV1


class TestFailureReportModule:
    def test_build_failure_report_has_agent_context(self):
        report = build_failure_report(
            agent_role="planner",
            agent_component="evaluate_plan_quality",
            objective_id="obj_test",
            failure_code="OBLIGATION_COVERAGE_INCOMPLETE",
            failure_detail="missing obligation refs",
            suggested_recovery="Auto-assign obligation refs for single-step plans.",
        )
        assert report.agent_role == "planner"
        assert report.agent_component == "evaluate_plan_quality"
        assert report.objective_id == "obj_test"
        assert report.failure_code == "OBLIGATION_COVERAGE_INCOMPLETE"

    def test_collector_records_and_summarizes_chain(self):
        collector = AgentFeedbackCollector()
        r1 = build_failure_report(
            agent_role="planner",
            agent_component="propose_step_contract",
            objective_id="obj_1",
            failure_code="NEEDS_MORE_EVIDENCE",
            failure_detail="no files read",
            evidence_missing=("repository_files",),
        )
        r2 = build_failure_report(
            agent_role="obligation_coverage",
            agent_component="validate_obligation_coverage",
            objective_id="obj_1",
            failure_code="OBLIGATION_COVERAGE_INCOMPLETE",
            failure_detail="step missing obligation refs",
            suggested_recovery="Attach obligation_refs to single-step plan.",
        )
        collector.record(r1)
        collector.record(r2)

        chain = collector.failure_chain("obj_1")
        assert len(chain) == 2
        assert chain[0]["agent"] == "planner"
        assert chain[1]["agent"] == "obligation_coverage"

        summary = summarize_failure_chain(collector.get_reports("obj_1"))
        assert "Pipeline blocked after 2 failure(s)" in summary
        assert "OBLIGATION_COVERAGE_INCOMPLETE" in summary

    def test_failure_report_v1_contract_accepts_new_fields(self):
        report = FailureReportV1(
            agent_id="planner:propose_step_contract",
            agent_role="planner",
            agent_component="propose_step_contract",
            objective_id="obj_x",
            phase="EXECUTING",
            attempted_action="propose_step_contract",
            failure_code="PLANNER_MODEL_ERROR",
            severity="BLOCKING",
        )
        assert report.agent_role == "planner"
        assert report.severity == "BLOCKING"


class TestFailureReportBackendIntegration:

    def _make_backend(self):
        class FakeSettings:
            INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED = True
            INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS = 30
            OBJECTIVE_CYCLE_REPOSITORY_ROOTS = "{}"
            INTELLIGENCE_LAYER_VERIFIER_ENABLED = False
            INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED = False

        return DurableObjectiveCycleApiBackend(
            mode="inprocess",
            real_execute_enabled=True,
            require_workhub_load=False,
            create_workhub_tasks=False,
            mark_workhub_done=False,
            settings=FakeSettings(),
        )

    @pytest.mark.asyncio
    async def test_blocked_payload_includes_failure_chain(self):
        backend = self._make_backend()
        admit = await backend.command(
            operation="admit_objective",
            tenant_id="test",
            actor_id="test",
            resource_id=None,
            body={
                "objective": "status check",
                "repository_id": "hyeve-aix-service",
                "project_id": "test",
                "workhub_task_id": "fr-001",
                "task_number": 1,
                "task_class": "verification_only",
                "definition_of_done": ["Report status"],
            },
            idempotency_key="fr-admit",
            expected_version=None,
        )
        obj_id = admit["objective_id"]
        start = await backend.command(
            operation="start_cycle",
            tenant_id="test",
            actor_id="test",
            resource_id=obj_id,
            body={"candidate_sha": "abc1234"},
            idempotency_key="fr-start",
            expected_version=None,
        )
        cycle = backend._cycles[start["cycle_id"]]

        result = await backend._blocked_exploration_payload(
            cycle=cycle,
            code="PLAN_INCOMPLETE",
            detail="missing obligation coverage",
            agent_role="planner",
            agent_component="step_contract_errors_gate",
            suggested_recovery="Attach obligation_refs to plan steps.",
            evidence_missing=("obligation_ref_1",),
        )
        assert result["ok"] is False
        assert "failure_chain" in result
        assert "failure_summary" in result
        assert result["failure_chain"][-1]["agent"] == "planner"
        assert result["failure_chain"][-1]["code"] == "PLAN_INCOMPLETE"
        assert "PLAN_INCOMPLETE" in result["failure_summary"]

    @pytest.mark.asyncio
    async def test_get_failure_reports_api(self):
        backend = self._make_backend()
        admit = await backend.command(
            operation="admit_objective",
            tenant_id="test",
            actor_id="test",
            resource_id=None,
            body={
                "objective": "status check",
                "repository_id": "hyeve-aix-service",
                "project_id": "test",
                "workhub_task_id": "fr-api-001",
                "task_number": 1,
                "task_class": "verification_only",
                "definition_of_done": ["Report status"],
            },
            idempotency_key="fr-api-admit",
            expected_version=None,
        )
        obj_id = admit["objective_id"]
        report = build_failure_report(
            agent_role="executor",
            agent_component="run_real_execute",
            objective_id=obj_id,
            failure_code="AUTHORIZATION_REQUIRED",
            failure_detail="no token",
            suggested_recovery="Login via CLI",
            upstream_dependency="hyeve_integration_llm_gateway",
        )
        backend._feedback_collector.record(report)
        backend._failure_reports.setdefault(obj_id, []).append(report)

        result = await backend.query(
            operation="get_failure_reports",
            tenant_id="test",
            resource_id=obj_id,
            child_id=None,
            filters={},
        )
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["items"][0]["agent_role"] == "executor"
        assert "AUTHORIZATION_REQUIRED" in result["summary"]
        assert result["feedback"]["blocking_count"] == 1
