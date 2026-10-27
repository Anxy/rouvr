from inspect import signature

from backend.aix_core.runtime.executor.real_execute import (
    _run_patch_propose_execute,
    prior_read_paths_from_context,
)


def test_prior_read_paths_from_context_dedupes_and_keeps_order() -> None:
    ctx = {
        "files_read": [
            {"path": "backend/main.py"},
            {"path": r"backend\main.py"},
            {"path": "backend/routers/objective_cycle_router.py"},
        ]
    }
    assert prior_read_paths_from_context(ctx) == (
        "backend/main.py",
        "backend/routers/objective_cycle_router.py",
    )


def test_prior_read_paths_empty_without_exploration_context() -> None:
    assert prior_read_paths_from_context(None) == ()
    assert prior_read_paths_from_context({}) == ()


def test_edit_branch_accepts_exploration_context() -> None:
    assert "execution_context" in signature(_run_patch_propose_execute).parameters
