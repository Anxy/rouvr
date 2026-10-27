"""Regression: original objective scope must survive admission → plan → lane selection."""
from __future__ import annotations

from backend.aix_core.runtime.executor.real_execute import looks_like_page_generation_objective
from backend.aix_core.runtime.objective.contracts import (
    ActionRequestV1,
    BudgetV1,
    PlanRevisionV1,
    StepContractV1,
)
from backend.aix_core.runtime.objective.obligations import derive_obligations
from backend.aix_core.runtime.objective.objective_scope import (
    classify_task_mode,
    is_demo_compatible_page_objective,
    render_plan_markdown,
    selected_execution_lane,
    validate_objective_scope_coverage,
)
from backend.aix_core.runtime.planner.plan_quality import evaluate_plan_quality


MIXED = (
    "Build a frontend page, backend API, database model and tests "
    "for a school attendance web application using PHP and MySQL."
)
BACKEND = "Implement Python API endpoint and tests for retrying failed jobs."
FRONTEND = "Create a static landing page with HTML/CSS."
DATABASE = "Add migration and update persistence layer for user profiles."
REPO = "Refactor service module and update tests."


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)


def _html_only_step(*, obligation_refs: tuple[str, ...] = ()) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Generate HTML/CSS page into proposed/index.html and proposed/styles.css",
        target_scope=("proposed",),
        expected_output_schema_ref="aix.step_contract.v1.html_css_proposal",
        acceptance_criteria=("proposed/index.html exists", "proposed/styles.css exists"),
        validation_commands=("test -f proposed/index.html",),
        assigned_role="executor",
        step_budget=_budget(),
        obligation_refs=obligation_refs,
        actions=(
            ActionRequestV1(capability="content", operation="generate_html_css", arguments={}),
        ),
        status="PENDING",
    )


def test_mixed_stack_mode_and_demo_incompatible() -> None:
    assert classify_task_mode(MIXED) == "FULL_STACK"
    assert is_demo_compatible_page_objective(MIXED) is False
    assert looks_like_page_generation_objective(MIXED) is False


def test_mixed_stack_obligations_survive_meta_dod() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective=MIXED,
        definition_of_done=("scoped change complete", "validation evidence attached"),
    )
    blob = " ".join(o.description.casefold() for o in obligations)
    assert "frontend" in blob
    assert "backend" in blob or "php" in blob
    assert "database" in blob or "mysql" in blob
    assert "test" in blob
    # Meta DoD remains additive.
    assert any("scoped change complete" in o.description.casefold() for o in obligations)


def test_mixed_stack_html_only_plan_rejected() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective=MIXED,
        definition_of_done=("scoped change complete", "validation evidence attached"),
    )
    # Even if all obligation ids are falsely stamped, semantic scope must fail.
    step = _html_only_step(obligation_refs=tuple(o.id for o in obligations if o.required))
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        steps=(step,),
        obligation_coverage=tuple(step.obligation_refs),
        status="DRAFT",
    )
    quality = evaluate_plan_quality(
        plan=plan,
        obligations=obligations,
        objective=MIXED,
        used_legacy_one_step=True,
    )
    assert quality.ok is False
    assert quality.code == "OBJECTIVE_SCOPE_NOT_COVERED"

    scope = validate_objective_scope_coverage(original_objective=MIXED, plan_steps=plan.steps)
    assert scope.ok is False
    assert any(m.kind in {"backend", "database", "api", "tests"} for m in scope.missing)

    lane = selected_execution_lane(
        original_objective=MIXED, plan_steps=plan.steps, demo_requested=True
    )
    assert lane != "objective_cycle.real_execute.demo.v1"


def test_backend_only_no_page_rewrite() -> None:
    assert classify_task_mode(BACKEND) == "BACKEND_TASK"
    assert looks_like_page_generation_objective(BACKEND) is False
    obligations = derive_obligations(objective_id="obj-1", objective=BACKEND, definition_of_done=())
    blob = " ".join(o.description.casefold() for o in obligations)
    assert "api" in blob or "python" in blob or "backend" in blob
    assert "test" in blob


def test_frontend_only_static_page_still_demo_compatible() -> None:
    assert classify_task_mode(FRONTEND) == "STATIC_PAGE"
    assert is_demo_compatible_page_objective(FRONTEND) is True
    assert looks_like_page_generation_objective(FRONTEND) is True


def test_database_task_not_demo() -> None:
    assert looks_like_page_generation_objective(DATABASE) is False
    obligations = derive_obligations(objective_id="obj-1", objective=DATABASE, definition_of_done=())
    blob = " ".join(o.description.casefold() for o in obligations)
    assert "migration" in blob or "database" in blob or "persistence" in blob


def test_general_repo_task_no_tech_narrowing() -> None:
    assert classify_task_mode(REPO) == "GENERAL_REPO_TASK"
    assert looks_like_page_generation_objective(REPO) is False


def test_false_obligation_coverage_caught_by_scope() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective=MIXED,
        definition_of_done=(),
    )
    step = _html_only_step(obligation_refs=tuple(o.id for o in obligations))
    result = validate_objective_scope_coverage(original_objective=MIXED, plan_steps=(step,))
    assert result.ok is False
    assert result.code == "OBJECTIVE_SCOPE_NOT_COVERED"
    assert result.missing
    assert result.original_objective == MIXED


ATTENDANCE = (
    "Can you create an attendance registration system with a frontend in HTML, "
    "a backend in PHP, and a database in MySQL? Please provide only the schema, "
    "as I will create it myself."
)


def test_attendance_preserves_fe_be_db_obligations() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective=ATTENDANCE,
        definition_of_done=("scoped change complete", "validation evidence attached"),
    )
    descriptions = [o.description for o in obligations]
    assert "Frontend / UI surface" not in descriptions
    assert "Backend / server runtime" not in descriptions
    assert "Database / persistence" not in descriptions


def test_attendance_schema_only_prose_plan_rejected() -> None:
    """Facet labels are not binding WHAT; semantic scope still sees FE/BE in the user text."""
    obligations = derive_obligations(
        objective_id="obj-1",
        objective=ATTENDANCE,
        definition_of_done=("scoped change complete", "validation evidence attached"),
    )
    meta = [o for o in obligations if o.source == "definition_of_done"]
    schema_step = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose=(
            "Define schema.sql for attendance registration needed by an HTML frontend "
            "and PHP backend without modifying application code."
        ),
        target_scope=("schema.sql",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=(
            "schema.sql only; does not add HTML or PHP; supports HTML frontend and PHP backend.",
        ),
        validation_commands=("grep -Eiq 'CREATE TABLE' schema.sql",),
        prohibited_actions=(
            "Do not modify files other than schema.sql.",
            "Do not add HTML, PHP, or application runtime code.",
        ),
        assigned_role="executor",
        step_budget=_budget(),
        obligation_refs=(),
        status="PENDING",
    )
    validate_step = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=1,
        purpose="Validate schema change and attach evidence.",
        target_scope=("schema.sql",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("validation evidence attached",),
        validation_commands=("git diff --check -- schema.sql",),
        depends_on=(schema_step.id,),
        assigned_role="executor",
        step_budget=_budget(),
        obligation_refs=tuple(o.id for o in meta),
        status="PENDING",
    )
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        steps=(schema_step, validate_step),
        status="DRAFT",
    )
    scope = validate_objective_scope_coverage(original_objective=ATTENDANCE, plan_steps=plan.steps)
    assert scope.ok is False
    assert scope.code == "OBJECTIVE_SCOPE_NOT_COVERED"
    missing_kinds = {m.kind for m in scope.missing}
    assert "frontend" in missing_kinds
    assert "backend" in missing_kinds
    assert "database" not in missing_kinds

    quality = evaluate_plan_quality(
        plan=plan,
        obligations=obligations,
        objective=ATTENDANCE,
    )
    assert quality.ok is False


def test_plan_md_derived_from_structured_plan() -> None:
    obligations = derive_obligations(objective_id="obj-1", objective=FRONTEND, definition_of_done=())
    step = _html_only_step(obligation_refs=tuple(o.id for o in obligations[:1]))
    plan = PlanRevisionV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        revision=1,
        steps=(step,),
        status="ACCEPTED",
    )
    md = render_plan_markdown(
        original_objective=FRONTEND,
        understanding_summary="static page",
        obligations=obligations,
        constraints=("do not mutate repository source",),
        plan=plan,
        execution_lane="objective_cycle.real_execute.demo.v1",
    )
    assert "## Original Objective" in md
    assert FRONTEND in md
    assert "## Planned Steps" in md
    assert "proposed" in md
    assert "objective_cycle.real_execute.demo.v1" in md
