"""AIX-EDIT planner-focus regressions: explicitly named source targets ground first.

Reproduces the EDIT-1-shape grounding failure (objective names ship.py, yet
only tests/test_ship.py got grounded, so the contract scoped tests and the
run edited the wrong file). Generic target grounding only: explicit source
target > admission hints > discovered files > adjacent tests. Tests may join
validation/context but must not replace an explicitly named implementation
target unless the objective asks to modify tests.
"""

from __future__ import annotations

from pathlib import Path

from backend.aix_core.runtime.executor.read_file_execute import (
    is_test_like_path,
    objective_asserts_existing_file,
    split_named_objective_files,
)
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
)
from backend.aix_core.runtime.planner.execution_context import SearchRecord


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "editproj"
    (root / "tests").mkdir(parents=True)
    (root / "ship.py").write_text("def manifest_line():\n    return 'x'\n", encoding="utf-8")
    (root / "berth.py").write_text("def berth_fee():\n    return 0\n", encoding="utf-8")
    (root / "tests" / "test_ship.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return root


def test_target_line_names_source_first(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    existing, missing = split_named_objective_files("Target: ship.py", root)
    assert existing == ("ship.py",)
    assert missing == ()


def test_prose_existing_file_names_source(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    existing, missing = split_named_objective_files(
        "In the existing file ship.py in the cycle workspace, fix manifest_line "
        "so the weight is always formatted with exactly one decimal place. "
        "Verify by running the project's pytest suite.",
        root,
    )
    assert existing == ("ship.py",)
    assert missing == ()
    assert objective_asserts_existing_file(
        "In the existing file ship.py, fix manifest_line."
    ) is True


def test_source_outranks_adjacent_tests_when_both_named(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    existing, _missing = split_named_objective_files(
        "Fix manifest_line in ship.py; see tests/test_ship.py for expectations.",
        root,
    )
    assert "ship.py" in existing
    assert "tests/test_ship.py" in existing
    assert existing.index("ship.py") < existing.index("tests/test_ship.py")


def test_explicit_test_edit_objective_grounds_tests(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    existing, missing = split_named_objective_files(
        "Update tests/test_ship.py to cover manifest_line rounding.",
        root,
    )
    assert "tests/test_ship.py" in existing
    assert missing == ()
    assert is_test_like_path("tests/test_ship.py") is True
    assert is_test_like_path("ship.py") is False


def test_missing_source_target_signals_clarify(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    existing, missing = split_named_objective_files(
        "In the existing file ghost.py, fix formatting.",
        root,
    )
    assert existing == ()
    assert missing == ("ghost.py",)
    assert objective_asserts_existing_file("In the existing file ghost.py, fix it.") is True
    # Create-shaped asks must not route to clarify: absence is expected.
    assert objective_asserts_existing_file("Create a file named notes.md.") is False


def test_multiple_named_sources_all_preserved(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    existing, _missing = split_named_objective_files(
        "Refactor ship.py and berth.py to share a common fee helper.",
        root,
    )
    assert set(existing) == {"ship.py", "berth.py"}
    # Merge contract (durable_api_backend fulfill path): explicit targets
    # first, grounded evidence kept, admission hints kept — never narrowed.
    files_read = ("tests/test_ship.py",)
    allowed = ("ship.py", "berth.py", "tests/test_ship.py")
    named_first = [p for p in existing if p in set(files_read) | set(allowed)]
    merged = tuple(dict.fromkeys([*named_first, *files_read, *allowed]))
    assert merged[:2] == tuple(existing)
    assert set(merged) == {"ship.py", "berth.py", "tests/test_ship.py"}


def _search(paths: list[str]) -> SearchRecord:
    return SearchRecord(
        query="manifest_line",
        scope=".",
        fingerprint="fp",
        tool_call_id="t1",
        hit_summary={"paths": paths},
    )


def _selector_step() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(arguments={"selector": "first_source_hit"})


def _ctx(searches: list[SearchRecord]) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(searches_executed=searches)


_RESOLVE = DurableObjectiveCycleApiBackend._resolve_exploration_step_path


def test_hit_selector_prefers_named_source_over_hit_order() -> None:
    ctx = _ctx([_search(["tests/test_ship.py", "ship.py"])])
    assert _RESOLVE(None, _selector_step(), ctx, ("ship.py",)) == "ship.py"
    ctx = _ctx([_search(["ship.py", "tests/test_ship.py"])])
    assert _RESOLVE(None, _selector_step(), ctx, ("ship.py",)) == "ship.py"


def test_hit_selector_prefers_implementation_over_tests() -> None:
    ctx = _ctx([_search(["tests/test_ship.py", "ship.py"])])
    assert _RESOLVE(None, _selector_step(), ctx, ()) == "ship.py"


def test_hit_selector_honors_explicit_test_target() -> None:
    ctx = _ctx([_search(["ship.py", "tests/test_ship.py"])])
    assert (
        _RESOLVE(None, _selector_step(), ctx, ("tests/test_ship.py",))
        == "tests/test_ship.py"
    )


def test_hit_selector_never_invents_paths() -> None:
    ctx = _ctx([_search(["tests/test_ship.py"])])
    assert _RESOLVE(None, _selector_step(), ctx, ("ship.py",)) == "tests/test_ship.py"
    assert _RESOLVE(None, _selector_step(), ctx, ()) == "tests/test_ship.py"
    assert _RESOLVE(None, _selector_step(), _ctx([]), ("ship.py",)) is None
    assert _RESOLVE(None, _selector_step(), _ctx([_search([])]), ()) is None


def test_hit_selector_explicit_path_wins() -> None:
    from types import SimpleNamespace

    step = SimpleNamespace(arguments={"path": "ship.py", "selector": "first_source_hit"})
    ctx = _ctx([_search(["tests/test_ship.py"])])
    assert _RESOLVE(None, step, ctx, ()) == "ship.py"
