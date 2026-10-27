"""Tests for F10-P3 Capability & Agent Role Assignment (Stage 8)."""

from __future__ import annotations

from datetime import UTC, datetime
import pytest

from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.compiler.pipeline import CompilerPipeline
from backend.aix_core.framework.compiler.stages.assign import AssignedWorkGraph, AssignStage
from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveEnvelopeV1


@pytest.fixture
def sample_envelope() -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        id="obj_assign_100",
        tenant_id="tenant-1",
        actor_ref="actor-1",
        project_id="proj-1",
        workhub_task_ref="task-assign-100",
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
        idempotency_key="idemp-assign-100",
        correlation_id="corr-assign-100",
        created_at=datetime.now(UTC).isoformat(),
    )


def test_assigned_work_graph_governance(sample_envelope: ObjectiveEnvelopeV1):
    assigned = CompilerPipeline.compile_assigned_work_graph(
        sample_envelope,
        default_role=Role.EXECUTOR_CODER,
        repository_id="aix-repo",
        create_roots=("proposed/",),
    )

    assert isinstance(assigned, AssignedWorkGraph)
    assert len(assigned.assignments) == len(assigned.graph.units)

    for unit in assigned.graph.units:
        assign = assigned.assignments[unit.unit_id]
        assert assign.unit_id == unit.unit_id
        assert assign.assigned_role == Role.EXECUTOR_CODER.value
        assert assign.is_governed is True
        assert len(assign.skills) > 0
        assert "filesystem" in assign.required_capabilities
