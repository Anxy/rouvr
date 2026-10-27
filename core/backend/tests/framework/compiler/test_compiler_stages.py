"""Tests for F10-P1 Work Compiler Stages 1-6."""

from __future__ import annotations

from datetime import UTC, datetime
import pytest

from backend.aix_core.framework.compiler.pipeline import CompilerPipeline
from backend.aix_core.framework.compiler.stages.bind import BindStage
from backend.aix_core.framework.compiler.stages.define import DefineStage
from backend.aix_core.framework.compiler.stages.intake import IntakeStage
from backend.aix_core.framework.compiler.stages.plan import PlanStage
from backend.aix_core.framework.compiler.stages.structure import StructureStage
from backend.aix_core.framework.compiler.stages.validate import ValidateStage, VerifiedPlan
from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveEnvelopeV1


@pytest.fixture
def sample_envelope() -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        id="obj_test_100",
        tenant_id="tenant-1",
        actor_ref="actor-1",
        project_id="proj-1",
        workhub_task_ref="task-100",
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
        idempotency_key="idemp-100",
        correlation_id="corr-100",
        created_at=datetime.now(UTC).isoformat(),
    )


def test_intake_and_define_stages(sample_envelope: ObjectiveEnvelopeV1):
    intake = IntakeStage.run(sample_envelope)
    assert intake.objective_id == "obj_test_100"
    assert len(intake.intake_hash) == 64

    outcome = DefineStage.run(
        intake,
        repository_id="aix-repo",
    )
    assert outcome.outcome_contract.objective_id == "obj_test_100"
    assert outcome.outcome_contract.contract_hash != ""


def test_structure_and_plan_stages(sample_envelope: ObjectiveEnvelopeV1):
    intake = IntakeStage.run(sample_envelope)
    outcome = DefineStage.run(
        intake,
        repository_id="aix-repo",
    )

    structure = StructureStage.run(outcome)
    assert structure.objective_id == "obj_test_100"
    assert len(structure.product_structure.views) > 0

    raw_plan, traces = PlanStage.run(
        structure,
        outcome,
        create_roots=("proposed/",),
    )
    assert raw_plan.objective_id == "obj_test_100"
    assert raw_plan.target_count > 0
    assert len(traces) > 0


def test_compiler_pipeline_e2e_compile(sample_envelope: ObjectiveEnvelopeV1):
    verified_plan = CompilerPipeline.compile_objective(
        sample_envelope,
        repository_id="aix-repo",
        create_roots=("proposed/",),
    )

    assert isinstance(verified_plan, VerifiedPlan)
    assert verified_plan.flow_coherence_verified is True
    assert verified_plan.coverage_alignment_verified is True
    assert len(verified_plan.plan_revision.steps) > 0
    assert verified_plan.plan_revision.status == "ACCEPTED"
