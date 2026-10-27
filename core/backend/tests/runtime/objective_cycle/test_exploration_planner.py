from backend.aix_core.runtime.planner.execution_context import ExecutionContext
from backend.aix_core.runtime.planner.exploration_planner import (
    EXPLORATION_REQUIRED,
    build_exploration_plan,
    gate_execution_planning,
)


def test_login_debug_plan_explores_auth_login_middleware_and_config() -> None:
    context = ExecutionContext(objective_id="obj-login", execution_root=".")

    plan = build_exploration_plan(
        objective="Why is login failing?",
        task_family="DEBUG",
        context=context,
    )

    assert plan.steps
    assert len(plan.steps) <= plan.max_steps
    assert all(step.category == "EXECUTABLE" and step.phase == "exploration" for step in plan.steps)
    queries = [step.arguments["query"] for step in plan.steps if step.action == "filesystem.search"]
    assert queries == ["auth", "login", "middleware", "config"]
    assert any(step.action == "filesystem.read_file" for step in plan.steps)
    assert any(step.action == "repository.resolve_symbol" for step in plan.steps)
    assert all(
        dependency < step.step_id
        for step in plan.steps
        for dependency in step.depends_on
    )
    assert "understanding_complete" in plan.stop_condition
    assert any("failure hypothesis" in criterion for criterion in plan.understanding_criteria)


def test_execution_plan_gate_blocks_debug_until_understanding_complete() -> None:
    context = ExecutionContext(objective_id="obj-login")

    blocked = gate_execution_planning(task_family="DEBUG", context=context)
    context.mark_understanding_complete()
    allowed = gate_execution_planning(task_family="DEBUG", context=context)

    assert blocked.allowed is False
    assert blocked.reason_code == EXPLORATION_REQUIRED
    assert allowed.allowed is True


def test_clarification_is_a_valid_terminal_gate_instead_of_mutation_plan() -> None:
    context = ExecutionContext(objective_id="obj-edit")
    plan = build_exploration_plan(
        objective="fix the client",
        task_family="EDIT",
        context=context,
    )
    gate = gate_execution_planning(
        task_family="EDIT",
        context=context,
        clarification_required=plan.clarification_required,
    )

    assert plan.steps == ()
    assert plan.clarification_required is True
    assert plan.reason_code == "INTENT_UNCLEAR"
    assert gate.allowed is True
    assert "clarification" in gate.detail


def test_underspecified_create_directory_asks_for_a_name() -> None:
    context = ExecutionContext(objective_id="obj-mkdir")
    plan = build_exploration_plan(
        objective="Create a directory at just any path and any name",
        task_family="CREATE",
        context=context,
    )
    assert plan.steps == ()
    assert plan.clarification_required is True
    assert plan.reason_code == "INTENT_UNCLEAR"
    assert "directory" in (plan.clarification_question or "").casefold()


def test_understanding_complete_stops_exploration() -> None:
    context = ExecutionContext(objective_id="obj-review", understanding_complete=True)

    plan = build_exploration_plan(
        objective="Review this service for authentication errors",
        task_family="REVIEW",
        context=context,
    )

    assert plan.steps == ()
    assert plan.understanding_complete is True
    assert plan.stop_condition == "understanding_complete"


def test_bound_read_gets_only_minimal_exists_stat_read_explore() -> None:
    context = ExecutionContext(objective_id="obj-read")

    plan = build_exploration_plan(
        objective="Read the bound configuration file",
        task_family="READ",
        context=context,
        target_path="config/settings.py",
    )

    assert [step.action for step in plan.steps] == [
        "filesystem.exists",
        "filesystem.stat",
        "filesystem.read_file",
    ]


def test_planner_does_not_reissue_identical_recorded_search() -> None:
    context = ExecutionContext(objective_id="obj-login", repositories_explored=["."])
    context.record_tool_success(
        "filesystem.search",
        arguments={"query": "auth", "scope": "."},
        result={"hits": []},
        tool_call_id="call-auth",
    )

    plan = build_exploration_plan(
        objective="Why is login failing?",
        task_family="DEBUG",
        context=context,
    )

    queries = [step.arguments["query"] for step in plan.steps if step.action == "filesystem.search"]
    assert "auth" not in queries
    assert queries[:3] == ["login", "middleware", "config"]


def test_planner_honors_hard_step_cap() -> None:
    context = ExecutionContext(objective_id="obj-login")

    plan = build_exploration_plan(
        objective="Why is login failing?",
        task_family="DEBUG",
        context=context,
        max_steps=3,
    )

    assert len(plan.steps) == 3


def test_empty_registered_root_is_vacuous_for_greenfield_create(tmp_path) -> None:
    """Greenfield CREATE against an empty root: exploration is vacuous.

    Live lane: an empty cycle workspace can never yield files_read, so the
    exploration gate would block CREATE forever. Any file (even dotfiles)
    defeats vacuity; nonexistent roots never qualify.
    """
    from backend.aix_core.runtime.objective.durable_api_backend import (
        _repository_root_is_empty,
    )

    assert _repository_root_is_empty(tmp_path) is True
    (tmp_path / "sub").mkdir()
    assert _repository_root_is_empty(tmp_path) is True
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    assert _repository_root_is_empty(tmp_path) is False
    assert _repository_root_is_empty(tmp_path / "nope") is False


def test_repo_root_target_still_explores_before_edit() -> None:
    """A repository-root path is not a bound mutation file; explore first."""
    context = ExecutionContext(objective_id="obj-authority", execution_root=".")
    plan = build_exploration_plan(
        objective="Classify every mounted public router and persist an endpoint authority matrix.",
        task_family="EDIT",
        context=context,
        target_path=".",
    )
    assert plan.clarification_required is False
    assert plan.steps
    assert any(step.action == "filesystem.list_directory" for step in plan.steps)
    queries = [step.arguments["query"] for step in plan.steps if step.action == "filesystem.search"]
    assert "Explore" not in queries
    assert "live" not in queries
    assert any(str(q).lower() == "router" for q in queries)

