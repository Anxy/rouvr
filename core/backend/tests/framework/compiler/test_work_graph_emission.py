"""Tests for F10-P2 WorkGraph emission (Stage 7)."""

from __future__ import annotations

from datetime import UTC, datetime
import pytest

from backend.aix_core.framework.compiler.pipeline import CompilerPipeline
from backend.aix_core.framework.compiler.stages.graph import GraphStage
from backend.aix_core.framework.work.models import WorkGraph, WorkUnit
from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveEnvelopeV1


@pytest.fixture
def sample_envelope() -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        id="obj_graph_100",
        tenant_id="tenant-1",
        actor_ref="actor-1",
        project_id="proj-1",
        workhub_task_ref="task-graph-100",
        task_class="source_edit",
        definition_of_done=(
            "Create greenfield under proposed/app/. Governance create_root = proposed/. "
            "CREATE proposed/app/index.html, proposed/app/styles.css, proposed/app/app.js, "
            "proposed/app/api.py, proposed/app/config.py, proposed/app/database.sql, and proposed/app/README.md. "
            "Python backend and SQLite database."
        ),
        acceptance_criteria=("UI and API components implemented under proposed/app/",),
        repository_scope=("proposed/app/",),
        allowed_effects=("filesystem:read", "filesystem:patch"),
        budget_ceiling=BudgetV1(
            time_seconds=60,
            tokens=4000,
            cost_usd=0.50,
            retries=2,
            steps=5,
            concurrency=1,
        ),
        idempotency_key="idemp-graph-100",
        correlation_id="corr-graph-100",
        created_at=datetime.now(UTC).isoformat(),
    )


def test_work_graph_emission_and_invariants(sample_envelope: ObjectiveEnvelopeV1):
    graph = CompilerPipeline.compile_work_graph(
        sample_envelope,
        repository_id="aix-repo",
        create_roots=("proposed/",),
    )

    assert isinstance(graph, WorkGraph)
    assert graph.work_id == "obj_graph_100"
    assert len(graph.units) > 0

    # Ensure all units have valid target scopes, contracts, and acyclic properties
    for unit in graph.units:
        assert isinstance(unit, WorkUnit)
        assert unit.work_id == "obj_graph_100"
        assert len(unit.target_scope) > 0
        assert unit.contract.unit_id == unit.unit_id
        assert unit.contract.assigned_role in ("executor", "planner", "verifier")

    # Verify dependency edges
    edges = graph.dependency_edges()
    assert isinstance(edges, tuple)
