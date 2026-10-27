"""Regression: focused pytest inference when objective asks for tests without a root manifest."""

from backend.aix_core.runtime.executor.multi_step_execute import (
    _infer_focused_pytest_from_paths,
)


def test_infer_focused_pytest_from_bound_test_modules() -> None:
    cmd = _infer_focused_pytest_from_paths(
        objective=(
            "Find the cause, add a regression test if needed, fix it, and run the "
            "focused feature-flag tests until they pass."
        ),
        paths=[
            "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py",
            "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py",
            "backend/services/feature_flags_service.py",
        ],
    )
    assert cmd is not None
    assert cmd[-1].endswith("test_feature_flag.py")
    assert "pytest" in cmd


def test_infer_focused_pytest_prefers_mutated_directory() -> None:
    cmd = _infer_focused_pytest_from_paths(
        objective="Add regression coverage for is_feature_enabled.",
        paths=[
            "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py",
            "backend/tests/test_devexecutor_mutate_workspace_helper.py",
            "backend/tests/aix_core/objective_cycle/live_fixtures/test_mutation_helpers.py",
        ],
        prefer_near_paths=[
            "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py",
        ],
    )
    assert cmd is not None
    joined = " ".join(cmd)
    assert "test_feature_flag.py" in joined
    assert "test_devexecutor_mutate_workspace_helper.py" not in joined
    assert "test_mutation_helpers.py" not in joined


def test_infer_focused_pytest_skips_when_objective_has_no_test_ask() -> None:
    cmd = _infer_focused_pytest_from_paths(
        objective="Rename the helper for clarity.",
        paths=[
            "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py",
        ],
    )
    assert cmd is None
