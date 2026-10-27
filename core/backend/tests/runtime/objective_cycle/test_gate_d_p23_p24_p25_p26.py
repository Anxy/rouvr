"""GATE-D through P26: Production wiring proof + evaluation + learning + self-improvement + capability discovery.

GATE-D proves P19-P22 are wired into the objective lifecycle (not standalone).
P23 establishes independent evaluation/benchmarking.
P24 learns from verified P23 outcomes.
P25 implements governed self-improvement lane.
P26 implements governed capability discovery.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.evaluation.evaluation_engine import (
    BaselineComparison,
    BaselineV1,
    BenchmarkCorpus,
    EvaluationCaseV1,
    EvaluationEngine,
    EvaluationMetricV1,
    EvaluationResultV1,
    EvaluationRunV1,
)
from backend.aix_core.runtime.learning.learning_store import LearningEntryV1, LearningStore
from backend.aix_core.runtime.improvement.self_improvement_lane import (
    SelfImprovementCandidateV1,
    SelfImprovementLane,
)
from backend.aix_core.runtime.discovery.capability_discovery_lane import (
    CapabilityDiscoveryLane,
    DiscoveredCapabilityV1,
)


# ═══════════════════════════════════════════════════════════════════════════════
# GATE-D: P19-P22 wired into objective lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class TestGateDLifecycleIntegration:
    """Prove the full lifecycle chain through production wiring."""

    def _make_integration(self):
        from backend.aix_core.runtime.objective.lifecycle_integration import (
            LifecycleContext, ObjectiveLifecycleIntegration,
        )
        from backend.aix_core.runtime.scheduling.work_scheduler import (
            WorkScheduler, ScheduleTriggerV1,
        )
        from backend.aix_core.runtime.observability.control_room import ControlRoom
        from backend.aix_core.runtime.governance.budget_ledger import BudgetLedger
        from backend.aix_core.runtime.reliability.recovery_manager import RecoveryManager

        scheduler = WorkScheduler()
        control_room = ControlRoom()
        budget_ledger = BudgetLedger()
        recovery_mgr = RecoveryManager()

        integration = ObjectiveLifecycleIntegration(
            scheduler=scheduler,
            control_room=control_room,
            budget_ledger=budget_ledger,
            recovery_manager=recovery_mgr,
        )
        return integration, scheduler, control_room, budget_ledger, recovery_mgr

    def test_full_lifecycle_chain(self):
        """scheduled → admit → budget → visibility → pause → checkpoint
        → interruption → recovery → resume → execution → verification → completion"""
        from backend.aix_core.runtime.objective.lifecycle_integration import (
            LifecycleContext, ObjectiveLifecycleIntegration,
        )
        from backend.aix_core.runtime.scheduling.work_scheduler import ScheduleTriggerV1
        from backend.aix_core.runtime.governance.budget_ledger import BudgetLimitV1
        from backend.aix_core.runtime.reliability.recovery_manager import RecoveryState

        integration, scheduler, control_room, budget_ledger, recovery_mgr = self._make_integration()
        ctx = LifecycleContext(
            objective_id="obj-gate-d", tenant_id="t-gate",
            cycle_id="cyc-1", step_id="s-1", attempt_id="a-1",
        )

        # 1. Schedule
        work = integration.schedule_objective(ctx, trigger=ScheduleTriggerV1(
            kind="once", at="2026-08-19T12:00:00Z",
        ), description="GATE-D test objective")
        assert work.work_id == "sched-obj-gate-d"

        # 2. Admit (register in control room)
        integration.register_objective(ctx, title="Fix authentication bug", steps_total=3)
        obj = control_room.get_objective("obj-gate-d")
        assert obj is not None
        assert obj.state == "admitted"

        # 3. Budget reservation
        result = integration.reserve_budget(ctx, limit=BudgetLimitV1(
            scope="objective", scope_id="obj-gate-d",
            max_tokens=50000, max_cost_usd=5.0,
        ))
        assert result.allowed is True

        # 4. Control room visibility
        assert control_room.get_objective("obj-gate-d") is not None

        # 5. Budget consumption
        consume_result = integration.consume_budget(ctx, tokens=10000, cost_usd=1.0, tool_calls=5)
        assert consume_result.allowed is True

        # 6. Update state to executing
        integration.update_objective_state(ctx, state="executing", current_step="s-1")
        assert control_room.get_objective("obj-gate-d").state == "executing"

        # 7. Checkpoint before mutation
        cp = integration.checkpoint(ctx, state_snapshot={"files_modified": ["auth.py"]},
                                     completed_actions=["act-1"])
        assert cp.objective_id == "obj-gate-d"

        # 8. Pause
        assert integration.pause_objective(ctx, reason="review needed", issued_by="admin") is True
        assert control_room.get_objective("obj-gate-d").state == "paused"

        # 9. Simulate interruption
        state = integration.simulate_interruption(ctx)
        assert state == RecoveryState.RECOVERING

        # 10. Recovery + reconciliation
        final_state, recon = integration.recover_and_reconcile(ctx)
        assert final_state == RecoveryState.RECOVERED
        assert recon.issues_resolved >= 1

        # 11. Resume
        assert integration.resume_objective(ctx, issued_by="admin") is True
        assert control_room.get_objective("obj-gate-d").state == "executing"

        # 12. Continue execution — more budget consumed
        consume2 = integration.consume_budget(ctx, tokens=20000, cost_usd=2.0)
        assert consume2.allowed is True

        # 13. Complete
        integration.complete_objective(ctx, success=True)
        assert control_room.get_objective("obj-gate-d").state == "completed"

        # 14. Timeline has full event chain
        timeline = control_room.get_timeline(objective_id="obj-gate-d")
        event_types = [e.event_type for e in timeline]
        assert "objective.scheduled" in event_types
        assert "objective.admitted" in event_types
        assert "budget.reserved" in event_types
        assert "checkpoint.saved" in event_types
        assert "recovery.started" in event_types
        assert "recovery.completed" in event_types
        assert "objective.completed" in event_types

    def test_budget_exhaustion_blocks_execution(self):
        from backend.aix_core.runtime.objective.lifecycle_integration import (
            LifecycleContext, ObjectiveLifecycleIntegration,
        )
        from backend.aix_core.runtime.governance.budget_ledger import BudgetLimitV1

        integration, *_ = self._make_integration()
        ctx = LifecycleContext(objective_id="obj-budget", tenant_id="t1")

        integration.register_objective(ctx, title="Budget test")
        integration.reserve_budget(ctx, limit=BudgetLimitV1(
            scope="objective", scope_id="obj-budget", max_tokens=100,
        ))
        result = integration.consume_budget(ctx, tokens=100)
        assert result.allowed is False
        assert result.exhausted_dimension == "tokens"

    def test_compensation_on_failure(self):
        from backend.aix_core.runtime.objective.lifecycle_integration import (
            LifecycleContext, ObjectiveLifecycleIntegration,
        )

        integration, *_ = self._make_integration()
        ctx = LifecycleContext(objective_id="obj-comp", tenant_id="t1")

        integration.register_compensation(
            ctx, action_id="a-1", capability="filesystem", operation="patch",
            rollback_args={"path": "auth.py", "original_content": "..."},
        )
        integration.register_compensation(
            ctx, action_id="a-2", capability="git", operation="checkout",
            rollback_args={"branch": "main"},
        )
        comps = integration.recovery_manager.execute_compensations("obj-comp")
        assert len(comps) == 2
        assert comps[0].original_action_id == "a-2"  # reverse order

    def test_backend_has_lifecycle(self):
        """Prove DurableObjectiveCycleApiBackend has _lifecycle wired."""
        from backend.aix_core.runtime.objective.durable_api_backend import (
            DurableObjectiveCycleApiBackend,
        )
        backend = DurableObjectiveCycleApiBackend(mode="inprocess")
        assert hasattr(backend, "_lifecycle")
        assert isinstance(
            backend._lifecycle,
            __import__(
                "backend.aix_core.runtime.objective.lifecycle_integration",
                fromlist=["ObjectiveLifecycleIntegration"],
            ).ObjectiveLifecycleIntegration,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# P23: Evaluation & Benchmarking (production module: evaluation_engine.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestP23Evaluation:
    def test_benchmark_corpus(self):
        corpus = BenchmarkCorpus()
        corpus.set_version("1.0.0")
        corpus.add_case(EvaluationCaseV1(
            case_id="eval-001", objective="Fix null pointer in auth.py",
            expected_outcome="NullPointerError resolved", category="bugfix",
            tags=("python", "auth"),
        ))
        corpus.add_case(EvaluationCaseV1(
            case_id="eval-002", objective="Add user registration endpoint",
            expected_outcome="POST /register returns 201", category="feature",
        ))
        assert len(corpus.list_cases()) == 2
        assert len(corpus.list_cases(category="bugfix")) == 1
        assert corpus.version == "1.0.0"

    def test_evaluation_run_and_metrics(self):
        engine = EvaluationEngine()
        run = EvaluationRunV1(
            run_id="run-001", version="1.0.0",
            timestamp="2026-08-19T12:00:00Z",
            results=(
                EvaluationResultV1(
                    case_id="eval-001", verdict="PASS",
                    metrics=(
                        EvaluationMetricV1("objective_success", 1.0),
                        EvaluationMetricV1("tokens_used", 5000, "tokens", higher_is_better=False),
                        EvaluationMetricV1("latency_ms", 3200, "ms", higher_is_better=False),
                    ),
                    tokens_used=5000, cost_usd=0.15,
                ),
                EvaluationResultV1(
                    case_id="eval-002", verdict="FAIL",
                    metrics=(
                        EvaluationMetricV1("objective_success", 0.0),
                        EvaluationMetricV1("false_completion", 1.0, higher_is_better=False),
                    ),
                    false_completion=True, human_interventions=1,
                ),
            ),
        )
        engine.record_run(run)
        agg = engine.aggregate_metrics(run)
        assert agg["objective_success"] == 0.5
        assert agg["false_completion"] == 1.0

    def test_baseline_comparison_detects_improvement(self):
        engine = EvaluationEngine()
        baseline = BaselineV1(
            baseline_id="base-001", version="0.9.0",
            created_at="2026-08-18T00:00:00Z",
            metrics={"objective_success": 0.5, "latency_ms": 4000},
        )
        engine.store_baseline(baseline)

        run = EvaluationRunV1(
            run_id="run-002", version="1.0.0",
            timestamp="2026-08-19T12:00:00Z",
            results=(
                EvaluationResultV1(case_id="e1", verdict="PASS", metrics=(
                    EvaluationMetricV1("objective_success", 0.8),
                    EvaluationMetricV1("latency_ms", 3000),
                )),
            ),
        )
        comparison = engine.compare_to_baseline(run, baseline)
        assert "objective_success" in comparison.improved
        assert comparison.is_improvement is False  # latency went down (lower=worse by default)

    def test_baseline_comparison_detects_regression(self):
        engine = EvaluationEngine()
        baseline = BaselineV1(
            baseline_id="base-002", version="0.9.0",
            created_at="2026-08-18T00:00:00Z",
            metrics={"objective_success": 0.9},
        )
        run = EvaluationRunV1(
            run_id="run-003", version="1.0.0",
            timestamp="2026-08-19T12:00:00Z",
            results=(
                EvaluationResultV1(case_id="e1", verdict="FAIL", metrics=(
                    EvaluationMetricV1("objective_success", 0.3),
                )),
            ),
        )
        comparison = engine.compare_to_baseline(run, baseline)
        assert "objective_success" in comparison.regressed
        assert comparison.is_improvement is False

    def test_no_change_without_baseline(self):
        engine = EvaluationEngine()
        assert engine.get_latest_baseline() is None

    def test_false_completion_tracking(self):
        result = EvaluationResultV1(
            case_id="e-fc", verdict="PASS",
            false_completion=True, policy_violations=2,
        )
        assert result.false_completion is True
        assert result.policy_violations == 2


# ═══════════════════════════════════════════════════════════════════════════════
# P24: Learning & Optimization
# ═══════════════════════════════════════════════════════════════════════════════

class TestP24Learning:
    def test_learning_requires_provenance(self):
        store = LearningStore()
        with pytest.raises(ValueError, match="provenance"):
            store.record(LearningEntryV1(
                entry_id="l-1", category="planner_strategy",
                insight="prefer smaller steps", provenance_run_id="",
            ))

    def test_learning_from_verified_outcome(self):
        engine = EvaluationEngine()
        run = EvaluationRunV1(
            run_id="run-learn", version="1.0.0",
            timestamp="2026-08-19T12:00:00Z",
            results=(
                EvaluationResultV1(case_id="e1", verdict="PASS", metrics=(
                    EvaluationMetricV1("objective_success", 1.0),
                )),
            ),
        )
        engine.record_run(run)

        store = LearningStore()
        entry = store.record(LearningEntryV1(
            entry_id="l-1", category="planner_strategy",
            insight="smaller steps improve success rate",
            provenance_run_id="run-learn",
            provenance_case_ids=("e1",),
            confidence=0.8,
        ))
        assert store.get("l-1") is not None
        assert entry.provenance_run_id == "run-learn"

    def test_learning_rollback(self):
        store = LearningStore()
        store.record(LearningEntryV1(
            entry_id="l-2", category="model_routing",
            insight="use cheaper model for trivial tasks",
            provenance_run_id="run-1", confidence=0.6,
        ))
        assert store.rollback("l-2") is True
        assert store.get("l-2") is None
        assert len(store.query()) == 0

    def test_learning_supersession(self):
        store = LearningStore()
        old = LearningEntryV1(
            entry_id="l-old", category="repair_choice",
            insight="retry first", provenance_run_id="run-1",
        )
        store.record(old)
        new = LearningEntryV1(
            entry_id="l-new", category="repair_choice",
            insight="replan first if retry already failed",
            provenance_run_id="run-2", confidence=0.9,
        )
        store.supersede("l-old", new)
        active = store.query(category="repair_choice")
        assert len(active) == 1
        assert active[0].entry_id == "l-new"

    def test_learning_cannot_modify_policy(self):
        """Learning entries have no mechanism to modify authorization policy."""
        entry = LearningEntryV1(
            entry_id="l-policy", category="planner_strategy",
            insight="test", provenance_run_id="run-1",
        )
        assert not hasattr(entry, "policy_rule")
        assert not hasattr(entry, "capability_grant")


# ═══════════════════════════════════════════════════════════════════════════════
# P25: Governed Self-Improvement
# ═══════════════════════════════════════════════════════════════════════════════

class TestP25SelfImprovement:
    def _engine_with_runs(self):
        engine = EvaluationEngine()
        baseline = EvaluationRunV1(
            run_id="baseline-run", version="1.0.0",
            timestamp="2026-08-19T10:00:00Z",
            results=(
                EvaluationResultV1(case_id="e1", verdict="PASS", metrics=(
                    EvaluationMetricV1("objective_success", 0.7),
                )),
            ),
        )
        candidate = EvaluationRunV1(
            run_id="candidate-run", version="1.1.0",
            timestamp="2026-08-19T11:00:00Z",
            results=(
                EvaluationResultV1(case_id="e1", verdict="PASS", metrics=(
                    EvaluationMetricV1("objective_success", 0.9),
                )),
            ),
        )
        engine.record_run(baseline)
        engine.record_run(candidate)
        return engine

    def test_full_improvement_pipeline(self):
        engine = self._engine_with_runs()
        lane = SelfImprovementLane(engine)

        c = lane.create_candidate("improve planner decomposition", baseline_run_id="baseline-run")
        assert c.stage == "workspace_created"

        lane.record_tests(c.candidate_id, passed=True)
        lane.record_evaluation(c.candidate_id, eval_run_id="candidate-run")
        comparison = lane.compare_to_baseline(c.candidate_id)
        assert comparison is not None
        assert "objective_success" in comparison.improved

        assert lane.security_check(c.candidate_id, modified_files=["planner.py", "decomposition.py"]) is True
        result = lane.request_approval(c.candidate_id)
        assert result.stage == "pending_approval"
        assert lane.approve(c.candidate_id, approved_by="admin") is True
        assert lane.promote(c.candidate_id) is True
        assert lane.get(c.candidate_id).stage == "promoted"

    def test_cannot_weaken_governance(self):
        engine = self._engine_with_runs()
        lane = SelfImprovementLane(engine)

        c = lane.create_candidate("modify policy", baseline_run_id="baseline-run")
        lane.record_tests(c.candidate_id, passed=True)

        # Modifying governance modules fails security
        assert lane.security_check(c.candidate_id, modified_files=["policy_gateway.py"]) is False
        result = lane.request_approval(c.candidate_id)
        assert result.stage == "rejected"
        assert "governance" in result.rejected_reason.lower() or "security" in result.rejected_reason.lower()

    def test_cannot_self_promote_without_approval(self):
        engine = self._engine_with_runs()
        lane = SelfImprovementLane(engine)

        c = lane.create_candidate("test", baseline_run_id="baseline-run")
        lane.record_tests(c.candidate_id, passed=True)
        lane.security_check(c.candidate_id, modified_files=[])
        # Skip approval — try to promote directly
        assert lane.promote(c.candidate_id) is False

    def test_cannot_promote_without_tests(self):
        engine = self._engine_with_runs()
        lane = SelfImprovementLane(engine)

        c = lane.create_candidate("test", baseline_run_id="baseline-run")
        lane.record_tests(c.candidate_id, passed=False)
        lane.security_check(c.candidate_id, modified_files=[])
        result = lane.request_approval(c.candidate_id)
        assert result.stage == "rejected"

    def test_requires_baseline(self):
        engine = EvaluationEngine()
        lane = SelfImprovementLane(engine)
        with pytest.raises(ValueError, match="baseline"):
            lane.create_candidate("improve", baseline_run_id="nonexistent")

    def test_rollback(self):
        engine = self._engine_with_runs()
        lane = SelfImprovementLane(engine)

        c = lane.create_candidate("test rollback", baseline_run_id="baseline-run")
        lane.record_tests(c.candidate_id, passed=True)
        lane.security_check(c.candidate_id, modified_files=[])
        lane.request_approval(c.candidate_id)
        lane.approve(c.candidate_id, approved_by="admin")
        lane.promote(c.candidate_id)
        assert lane.rollback(c.candidate_id) is True
        assert lane.get(c.candidate_id).stage == "rolled_back"


# ═══════════════════════════════════════════════════════════════════════════════
# P26: Governed Capability Discovery
# ═══════════════════════════════════════════════════════════════════════════════

class TestP26CapabilityDiscovery:
    def test_full_discovery_pipeline(self):
        lane = CapabilityDiscoveryLane()

        d = lane.identify_need(
            "need Jira integration for ticket updates",
            capability_id="jira.update_ticket",
            adapter_type="http_api",
        )
        assert d.stage == "need_identified"
        assert not lane.is_available("jira.update_ticket")

        lane.assess_trust(d.discovery_id, trust_level="verified")
        lane.sandbox_test(d.discovery_id, passed=True, details={"latency_ms": 200})
        lane.assign_policy(d.discovery_id)
        result = lane.request_activation(d.discovery_id)
        assert result.stage == "pending_approval"

        assert lane.approve_activation(d.discovery_id, approved_by="admin") is True
        assert lane.activate(d.discovery_id) is True
        assert lane.is_available("jira.update_ticket") is True

    def test_discovery_does_not_imply_authorization(self):
        lane = CapabilityDiscoveryLane()
        d = lane.identify_need("need S3", capability_id="aws.s3", adapter_type="sdk")
        assert not lane.is_available("aws.s3")

        lane.assess_trust(d.discovery_id, trust_level="sandbox")
        assert not lane.is_available("aws.s3")

        lane.sandbox_test(d.discovery_id, passed=True)
        assert not lane.is_available("aws.s3")  # still not available

    def test_cannot_activate_without_policy(self):
        lane = CapabilityDiscoveryLane()
        d = lane.identify_need("need X", capability_id="x.y", adapter_type="cli")
        lane.assess_trust(d.discovery_id, trust_level="sandbox")
        lane.sandbox_test(d.discovery_id, passed=True)
        result = lane.request_activation(d.discovery_id)
        assert result.stage == "rejected"
        assert "no policy" in result.rejected_reason

    def test_cannot_activate_without_approval(self):
        lane = CapabilityDiscoveryLane()
        d = lane.identify_need("need Y", capability_id="y.z", adapter_type="mcp")
        lane.assess_trust(d.discovery_id, trust_level="verified")
        lane.sandbox_test(d.discovery_id, passed=True)
        lane.assign_policy(d.discovery_id)
        lane.request_activation(d.discovery_id)
        assert lane.activate(d.discovery_id) is False  # not approved

    def test_failed_sandbox_blocks_activation(self):
        lane = CapabilityDiscoveryLane()
        d = lane.identify_need("need Z", capability_id="z.w", adapter_type="http_api")
        lane.assess_trust(d.discovery_id, trust_level="sandbox")
        lane.sandbox_test(d.discovery_id, passed=False)
        assert d.stage == "rejected"
        with pytest.raises(ValueError, match="rejected"):
            lane.assign_policy(d.discovery_id)

    def test_unavailable_until_activated(self):
        lane = CapabilityDiscoveryLane()
        d = lane.identify_need("need DB", capability_id="database.query", adapter_type="sdk")
        lane.assess_trust(d.discovery_id, trust_level="trusted")
        lane.sandbox_test(d.discovery_id, passed=True)
        lane.assign_policy(d.discovery_id)
        lane.request_activation(d.discovery_id)
        lane.approve_activation(d.discovery_id, approved_by="admin")
        assert not lane.is_available("database.query")  # approved but not activated yet
        lane.activate(d.discovery_id)
        assert lane.is_available("database.query")
