"""F1-P1: Work domain schema tests (F1-AC-01)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.aix_core.framework.work.models import (
    WorkContract,
    WorkDefinition,
    WorkGraph,
    WorkType,
    WorkUnit,
    UnitStatus,
)


def _contract(unit_id: str = "u1") -> WorkContract:
    return WorkContract(unit_id=unit_id)


def test_work_type_rejects_overlapping_effects() -> None:
    with pytest.raises(ValidationError):
        WorkType(
            task_class="source_edit",
            allowed_effects=("read_source",),
            forbidden_effects=("read_source",),
        )


def test_work_definition_hash_stable() -> None:
    d1 = WorkDefinition(
        work_id="w1",
        definition_of_done="done",
        acceptance_criteria=("a",),
    )
    h1 = d1.compute_hash()
    d2 = d1.model_copy(update={"definition_hash": h1})
    assert d2.compute_hash() == h1
    assert d1.compute_hash() == h1


def test_work_unit_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError):
        WorkUnit(
            unit_id="u1",
            work_id="w1",
            unit_index=0,
            purpose="step",
            depends_on=("u1",),
            target_scope=("a.py",),
            contract=_contract("u1"),
        )


def test_work_graph_rejects_cycle() -> None:
    u1 = WorkUnit(
        unit_id="u1",
        work_id="w1",
        unit_index=0,
        purpose="a",
        depends_on=("u2",),
        target_scope=("a.py",),
        contract=_contract("u1"),
        status=UnitStatus.PENDING,
    )
    u2 = WorkUnit(
        unit_id="u2",
        work_id="w1",
        unit_index=1,
        purpose="b",
        depends_on=("u1",),
        target_scope=("b.py",),
        contract=_contract("u2"),
    )
    with pytest.raises(ValidationError, match="Cycle"):
        WorkGraph(work_id="w1", units=(u1, u2))


def test_work_graph_accepts_dag() -> None:
    u1 = WorkUnit(
        unit_id="u1",
        work_id="w1",
        unit_index=0,
        purpose="a",
        target_scope=("a.py",),
        contract=_contract("u1"),
    )
    u2 = WorkUnit(
        unit_id="u2",
        work_id="w1",
        unit_index=1,
        purpose="b",
        depends_on=("u1",),
        target_scope=("b.py",),
        contract=_contract("u2"),
    )
    graph = WorkGraph(work_id="w1", units=(u1, u2))
    assert graph.dependency_edges() == (("u2", "u1"),)
    assert graph.model_config.get("frozen") is True
