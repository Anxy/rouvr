"""C002 identity inventory, mapping contract, and collision/cross-tenant tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime_identity import (
    IDENTITY_CONTRACT_VERSION,
    LIFECYCLE_SEMANTICS,
    CanonicalIdentity,
    IdentityBinding,
    IdentityRegistry,
    LegacyIdentityRef,
    map_execution_kernel_problem,
    map_execution_kernel_work_item,
    map_objective_cycle_cycle,
    map_objective_cycle_objective,
    map_orchestrator_v2_run,
    map_workhub_task,
)


def test_identity_contract_version() -> None:
    assert IDENTITY_CONTRACT_VERSION == "aix.runtime_identity.v1"
    assert "replan" in LIFECYCLE_SEMANTICS
    assert "reunderstand" in LIFECYCLE_SEMANTICS


def test_objective_cycle_ids_are_canonical() -> None:
    registry = IdentityRegistry()
    obj = map_objective_cycle_objective(tenant_id="t1", objective_id="obj_abc")
    run = map_objective_cycle_cycle(tenant_id="t1", cycle_id="cyc_abc", objective_id="obj_abc")
    registry.register(obj)
    registry.register(run)
    assert obj.canonical.kind == "objective"
    assert obj.canonical.id == "obj_abc"
    assert run.canonical.kind == "run"
    assert run.canonical.parent_id == "obj_abc"


def test_workhub_project_task_correlates_not_duplicates_objective() -> None:
    registry = IdentityRegistry()
    obj = map_objective_cycle_objective(tenant_id="t1", objective_id="obj_1")
    registry.register(obj)
    wh = map_workhub_task(
        tenant_id="t1",
        project_id="proj",
        task_number=42,
        objective_id="obj_1",
    )
    bound = registry.register(wh)
    assert bound.canonical.id == "obj_1"
    assert bound.legacy.system == "workhub"
    assert bound.legacy.value == "proj:42"


def test_idempotent_reregister_same_binding() -> None:
    registry = IdentityRegistry()
    first = registry.register(map_objective_cycle_objective(tenant_id="t1", objective_id="obj_1"))
    second = registry.register(map_objective_cycle_objective(tenant_id="t1", objective_id="obj_1"))
    assert first.canonical.id == second.canonical.id
    assert len(registry.bindings) == 1


def test_collision_on_same_legacy_different_canonical() -> None:
    registry = IdentityRegistry()
    registry.register(
        map_workhub_task(tenant_id="t1", project_id="p", task_number=1, objective_id="obj_a")
    )
    with pytest.raises(ValueError, match="IDENTITY_COLLISION"):
        registry.register(
            map_workhub_task(tenant_id="t1", project_id="p", task_number=1, objective_id="obj_b")
        )


def test_cross_tenant_binding_forbidden() -> None:
    bad = IdentityBinding(
        legacy=LegacyIdentityRef(
            system="workhub",
            field="project_id:task_number",
            value="p:1",
            tenant_id="t2",
        ),
        canonical=CanonicalIdentity(kind="objective", id="obj_x", tenant_id="t1"),
        mapping_rule="bad",
    )
    with pytest.raises(ValueError, match="CROSS_TENANT_BINDING_FORBIDDEN"):
        IdentityRegistry().register(bad)


def test_cross_tenant_same_workhub_key_are_distinct() -> None:
    registry = IdentityRegistry()
    a = registry.register(
        map_workhub_task(tenant_id="t1", project_id="p", task_number=7, objective_id="obj_t1")
    )
    b = registry.register(
        map_workhub_task(tenant_id="t2", project_id="p", task_number=7, objective_id="obj_t2")
    )
    assert a.canonical.id != b.canonical.id
    assert a.canonical.tenant_id == "t1"
    assert b.canonical.tenant_id == "t2"


def test_orchestrator_v2_run_adapts_to_run_kind() -> None:
    binding = map_orchestrator_v2_run(
        tenant_id="t1",
        run_id="aixv2-deadbeef",
        task_id="task-1",
        objective_id="obj_1",
    )
    assert binding.canonical.kind == "run"
    assert binding.canonical.parent_id == "obj_1"
    assert binding.mapping_rule.startswith("orchestrator_v2")


def test_execution_kernel_identities_adapt() -> None:
    registry = IdentityRegistry()
    problem = map_execution_kernel_problem(
        tenant_id="t1", problem_id="prob_1", objective_id="obj_1"
    )
    wi = map_execution_kernel_work_item(
        tenant_id="t1", work_item_id="wi_1", problem_id="prob_1"
    )
    registry.register(problem)
    registry.register(wi)
    assert problem.canonical.kind == "objective"
    assert wi.canonical.kind == "step_contract"


def test_tenant_required() -> None:
    binding = map_objective_cycle_objective(tenant_id="   ", objective_id="obj_1")
    with pytest.raises(ValueError, match="TENANT_REQUIRED"):
        IdentityRegistry().register(binding)
