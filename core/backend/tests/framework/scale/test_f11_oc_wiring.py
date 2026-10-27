"""F11 integration — flag OFF/ON wiring into DurableObjectiveCycleApiBackend."""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveEnvelopeV1, PlanRevisionV1, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot


def _envelope(oid: str = "obj_f11") -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        id=oid,
        tenant_id="t1",
        actor_ref="human-1",
        project_id="p1",
        workhub_task_ref="wh-1",
        task_class="verification_only",
        definition_of_done="scale test",
        acceptance_criteria=("ok",),
        repository_scope=("aix",),
        budget_ceiling=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=3, concurrency=1
        ),
        idempotency_key="idem-f11",
    )


def _plan(oid: str = "obj_f11") -> PlanRevisionV1:
    budget = BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=3, concurrency=1
    )
    s1 = StepContractV1(
        id="s1",
        cycle_id="cyc",
        tenant_id="t1",
        step_index=0,
        purpose="one",
        target_scope=("a.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("ok",),
        validation_commands=("true",),
        assigned_role="executor",
        step_budget=budget,
        status="PENDING",
    )
    s2 = StepContractV1(
        id="s2",
        cycle_id="cyc",
        tenant_id="t1",
        step_index=1,
        purpose="two",
        depends_on=("s1",),
        target_scope=("b.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("ok",),
        validation_commands=("true",),
        assigned_role="executor",
        step_budget=budget,
        status="PENDING",
    )
    return PlanRevisionV1(
        plan_id="plan_f11",
        objective_id=oid,
        cycle_id="cyc",
        revision=1,
        status="ACCEPTED",
        steps=(s1, s2),
    )


def test_flag_off_no_scale_session():
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(scale=False),
    )
    env = _envelope("obj_off")
    backend._envelopes[env.id] = env  # noqa: SLF001
    backend._admit_plan_revision(_plan("obj_off"))  # noqa: SLF001
    assert "obj_off" not in backend._scale_sessions  # noqa: SLF001


def test_flag_on_projects_scale_session():
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(scale=True),
    )
    env = _envelope("obj_on")
    backend._envelopes[env.id] = env  # noqa: SLF001
    plan = _plan("obj_on")
    backend._admit_plan_revision(plan)  # noqa: SLF001
    assert "obj_on" in backend._scale_sessions  # noqa: SLF001
    session = backend._scale_sessions["obj_on"]  # noqa: SLF001
    assert session.store.get_snapshot(session.graph.graph_id) is not None
    assert len(session.store.list_units(session.graph.graph_id)) == 2

    # Hibernate / resume through OC API
    rec = backend.suspend_scale_cycle(
        objective_id="obj_on", cycle_id="cyc", simulated_elapsed_hours=24.0
    )
    assert rec.status == "SUSPENDED"
    resumed, capsule = backend.resume_scale_cycle(objective_id="obj_on", cycle_id="cyc")
    assert resumed.status == "RESUMED"
    assert capsule is not None
