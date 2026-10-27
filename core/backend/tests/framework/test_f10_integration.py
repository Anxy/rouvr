"""F10 integration — compiler + multi-agent flag OFF/ON paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.aix_core.framework.compiler.runtime_bridge import (
    compile_from_work_and_plan,
    compiler_enabled,
    ensure_work_from_canonical,
    multi_agent_enabled,
)
from backend.aix_core.framework.compiler.stages.assign import AssignStage
from backend.aix_core.framework.compiler.stages.graph import GraphStage
from backend.aix_core.framework.multi_agent.protocol import MultiAgentProtocol
from backend.aix_core.framework.work.adapters import WorkDomainAdapter
from backend.aix_core.framework.work.models import WorkContract, WorkGraph, WorkUnit
from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveEnvelopeV1, PlanRevisionV1, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot, settings


def _envelope(oid: str = "obj_f10") -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        id=oid,
        tenant_id="t1",
        actor_ref="human-1",
        project_id="p1",
        workhub_task_ref="wh-1",
        task_class="verification_only",
        definition_of_done="Report status and edit helper",
        acceptance_criteria=("status reported", "helper updated"),
        repository_scope=("aix",),
        budget_ceiling=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=3, concurrency=1
        ),
        idempotency_key="idem-f10",
    )


def _plan_two_steps(oid: str = "obj_f10") -> PlanRevisionV1:
    budget = BudgetV1(
        time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
    )
    s1 = StepContractV1(
        id="step_a",
        cycle_id="cyc_f10",
        tenant_id="t1",
        step_index=0,
        purpose="Prepare helper",
        target_scope=("helper.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("helper ready",),
        validation_commands=("python -m py_compile helper.py",),
        assigned_role="planner",
        step_budget=budget,
        obligation_refs=(),
        status="PENDING",
    )
    s2 = StepContractV1(
        id="step_b",
        cycle_id="cyc_f10",
        tenant_id="t1",
        step_index=1,
        purpose="Implement helper",
        target_scope=("helper.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("helper works",),
        validation_commands=("python -m py_compile helper.py",),
        assigned_role="executor",
        step_budget=budget,
        obligation_refs=(),
        depends_on=("step_a",),
        status="PENDING",
    )
    return PlanRevisionV1(
        plan_id="plan_f10",
        objective_id=oid,
        cycle_id="cyc_f10",
        revision=1,
        status="ACCEPTED",
        steps=(s1, s2),
    )


def test_flags_default_off() -> None:
    assert compiler_enabled(settings) is False
    assert multi_agent_enabled(settings) is False


def test_p1_p2_work_to_workgraph_from_admitted_plan() -> None:
    env = _envelope()
    plan = _plan_two_steps()
    work = ensure_work_from_canonical(envelope=env, plan=plan)
    assert work.definition is not None
    result = compile_from_work_and_plan(work=work, plan=plan, bind_models=False)
    assert result.execution_plan.plan_id == plan.plan_id
    assert result.work_definition.work_id == work.work_id
    assert len(result.work_graph.units) == 2
    assert result.work_graph.execution_order == ("step_a", "step_b")
    assert result.work_graph.dependency_edges() == (("step_b", "step_a"),)
    u0 = result.work_graph.units[0]
    assert u0.verification_requirements
    assert u0.recovery_metadata.get("risk_tier")


def test_p3_capability_and_agent_assignment() -> None:
    plan = _plan_two_steps()
    graph = GraphStage.from_plan(plan)
    with patch(
        "backend.aix_core.framework.compiler.stages.assign.AgentBinding.for_role"
    ) as bind:
        route = MagicMock()
        route.disposition = "ALLOW"
        route.route = MagicMock(provider_ref="openrouter", model_ref="m1")
        bind.return_value = MagicMock(
            model_ref="m1",
            route_decision=route,
        )
        assigned = AssignStage.run(graph, bind_models=True)
    assert len(assigned.assignments) == 2
    a = assigned.assignments["step_b"]
    assert a.assigned_role == "executor_coder"
    assert a.is_governed is True
    assert a.resolved_capabilities or a.required_capabilities
    assert a.route_disposition == "ALLOW"


def test_p4_two_agent_handoff_context_evidence_continuity() -> None:
    env = _envelope()
    plan = _plan_two_steps()
    work = WorkDomainAdapter.work_from_envelope(env, None, plan=plan)
    result = compile_from_work_and_plan(work=work, plan=plan, bind_models=False)
    protocol = MultiAgentProtocol()
    session = protocol.open_session(result.assigned)
    units = {u.unit_id: u for u in result.assigned.graph.units}
    u_a, u_b = units["step_a"], units["step_b"]
    protocol.prepare_unit_capsule(session, u_a, evidence_refs=("ev://a",))
    protocol.authorize_handoff(
        session, from_role="planner", to_role="coder", unit=u_b
    )
    protocol.record_observation(
        session,
        "step_a",
        status="SUCCESS",
        evidence_payload={"diff": "--- a\n+++ b\n"},
        touched_files=("helper.py",),
    )
    transferred = protocol.transfer_context(
        session, from_unit_id="step_a", to_unit_id="step_b"
    )
    assert transferred["evidence_payload"]["diff"]
    assert transferred["evidence_hash"]
    protocol.prepare_unit_capsule(
        session,
        u_b,
        context_data=transferred,
        evidence_refs=("ev://a", "ev://b"),
    )
    assert "step_b" in session.capsules
    assert session.capsules["step_b"].context_data.get("from_unit_id") == "step_a"


def test_verification_failure_and_recovery_metadata() -> None:
    plan = _plan_two_steps()
    graph = GraphStage.from_plan(plan)
    unit = next(u for u in graph.units if u.unit_id == "step_b")
    assert "py_compile" in ";".join(unit.verification_requirements)
    assert unit.recovery_metadata.get("timeout_seconds")


def test_workhub_authority_still_enforced() -> None:
    from backend.aix_core.framework.integration.workhub.boundary import (
        WorkHubBoundary,
        WorkHubOperation,
    )

    d = WorkHubBoundary.evaluate_operation(
        WorkHubOperation.TASK_CLOSE,
        create_task_flag=False,
        mark_done_flag=False,
        lifecycle_writes_enabled=False,
    )
    assert d.allowed is False


def test_flag_off_parity_no_f10_artifacts() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(compiler=False, multi_agent=False),
    )
    env = _envelope("obj_off")
    backend._envelopes[env.id] = env  # noqa: SLF001
    plan = _plan_two_steps("obj_off")
    backend._admit_plan_revision(plan)  # noqa: SLF001
    assert "obj_off" not in backend._f10_compiles  # noqa: SLF001
    assert "obj_off" not in backend._f10_sessions  # noqa: SLF001


def test_flag_on_compiler_path() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(compiler=True, multi_agent=False),
    )
    env = _envelope("obj_on")
    backend._envelopes[env.id] = env  # noqa: SLF001
    plan = _plan_two_steps("obj_on")
    with patch(
        "backend.aix_core.framework.compiler.stages.assign.AgentBinding.for_role"
    ) as bind:
        route = MagicMock()
        route.disposition = "ALLOW"
        route.route = MagicMock(provider_ref="openrouter", model_ref="m1")
        bind.return_value = MagicMock(model_ref="m1", route_decision=route)
        backend._admit_plan_revision(plan)  # noqa: SLF001
    assert "obj_on" in backend._f10_compiles  # noqa: SLF001
    compiled = backend._f10_compiles["obj_on"]  # noqa: SLF001
    assert compiled.work_graph.execution_order == ("step_a", "step_b")
    assert "obj_on" not in backend._f10_sessions  # noqa: SLF001


def test_flag_on_multi_agent_session() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(compiler=True, multi_agent=True),
    )
    env = _envelope("obj_ma")
    backend._envelopes[env.id] = env  # noqa: SLF001
    plan = _plan_two_steps("obj_ma")
    with patch(
        "backend.aix_core.framework.compiler.stages.assign.AgentBinding.for_role"
    ) as bind:
        route = MagicMock()
        route.disposition = "ALLOW"
        route.route = MagicMock(provider_ref="openrouter", model_ref="m1")
        bind.return_value = MagicMock(model_ref="m1", route_decision=route)
        backend._admit_plan_revision(plan)  # noqa: SLF001
    assert "obj_ma" in backend._f10_sessions  # noqa: SLF001
    session = backend._f10_sessions["obj_ma"]["session"]  # noqa: SLF001
    assert session.assigned.graph.work_id == "obj_ma"


def test_no_second_executor_in_delegation() -> None:
    from backend.aix_core.framework.agent.models import Role
    from backend.aix_core.framework.multi_agent.delegation import DelegationManager

    unit = WorkUnit(
        unit_id="u1",
        work_id="w1",
        unit_index=0,
        purpose="x",
        target_scope=("a.py",),
        contract=WorkContract(unit_id="u1"),
    )
    mgr = DelegationManager()
    cap = mgr.prepare_capsule(unit, Role.EXECUTOR_CODER)
    with pytest.raises(NotImplementedError):
        mgr.execute_delegated_unit(cap)


def test_sole_canonical_command_unchanged() -> None:
    from backend.main import PUBLIC_ROUTER_AUTHORITY_MATRIX

    writers = [
        name
        for name, authority in PUBLIC_ROUTER_AUTHORITY_MATRIX["routers"].items()
        if authority == "CANONICAL_COMMAND"
    ]
    assert writers == ["objective_cycle_router"]
