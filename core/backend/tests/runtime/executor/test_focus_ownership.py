"""AIX-EDIT-002 regression: explicit requested source outranks adjacent tests.

Reproduces the EDIT-1 wrong-file focus (objective names ship.py, step scope
targets only tests/test_ship.py) at the deterministic guard level. Uses a
temporary fixture repo (existing source + tests). No contract-engine
dependency; runs in the default suite.
"""

from __future__ import annotations

from pathlib import Path

from backend.aix_core.runtime.executor.multi_step_execute import (
    _explicit_source_focus_mismatch,
    _is_test_path,
    _related_repo_test_modules,
    _test_only_mutations_miss_named_source,
    _validation_path_pool,
    _whole_file_line_counts,
)
from backend.aix_core.runtime.executor.read_file_execute import (
    extract_target_path,
    resolve_relative_path,
)


def _looks(p: str) -> bool:
    return "." in p.rsplit("/", 1)[-1]


def _norm(p: str) -> str:
    return p.strip().lstrip("/")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "editproj"
    (root / "tests").mkdir(parents=True)
    (root / "ship.py").write_text("def manifest_line():\n    return 'x'\n", encoding="utf-8")
    (root / "tests" / "test_ship.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return root


def test_edit1_shape_tests_only_scope_is_mismatch(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    detail = _explicit_source_focus_mismatch(
        objective_target="ship.py",
        allowed_files=("tests/test_ship.py",),
        repository_root=root,
        normalize_repo_relative_path=_norm,
        looks_like_a_file=_looks,
        resolve_relative_path=resolve_relative_path,
    )
    assert detail is not None
    assert "ship.py" in detail
    assert "tests/test_ship.py" in detail


def test_source_plus_tests_scope_is_allowed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _explicit_source_focus_mismatch(
        objective_target="ship.py",
        allowed_files=("ship.py", "tests/test_ship.py"),
        repository_root=root,
        normalize_repo_relative_path=_norm,
        looks_like_a_file=_looks,
        resolve_relative_path=resolve_relative_path,
    ) is None


def test_source_only_scope_is_allowed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _explicit_source_focus_mismatch(
        objective_target="ship.py",
        allowed_files=("ship.py",),
        repository_root=root,
        normalize_repo_relative_path=_norm,
        looks_like_a_file=_looks,
        resolve_relative_path=resolve_relative_path,
    ) is None


def test_no_explicit_target_never_mismatches(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _explicit_source_focus_mismatch(
        objective_target=None,
        allowed_files=("tests/test_ship.py",),
        repository_root=root,
        normalize_repo_relative_path=_norm,
        looks_like_a_file=_looks,
        resolve_relative_path=resolve_relative_path,
    ) is None


def test_nonexistent_target_never_mismatches(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _explicit_source_focus_mismatch(
        objective_target="ghost.py",
        allowed_files=("tests/test_ship.py",),
        repository_root=root,
        normalize_repo_relative_path=_norm,
        looks_like_a_file=_looks,
        resolve_relative_path=resolve_relative_path,
    ) is None


def test_existence_on_preexisting_file_needs_focused() -> None:
    from types import SimpleNamespace

    from backend.aix_core.runtime.executor.multi_step_execute import (
        _existence_validated_preexisting_file,
    )

    edit_discovery = SimpleNamespace(config_files={"tests/test_ship.py": "content"})
    assert _existence_validated_preexisting_file(
        ["test", "-f", "tests/test_ship.py"], edit_discovery
    ) is True
    create_discovery = SimpleNamespace(config_files={})
    assert _existence_validated_preexisting_file(
        ["test", "-f", "proposed/notes.md"], create_discovery
    ) is False
    assert _existence_validated_preexisting_file(None, edit_discovery) is False
    assert _existence_validated_preexisting_file(["test", "-f"], edit_discovery) is False


def test_test_path_heuristic() -> None:
    assert _is_test_path("tests/test_ship.py") is True
    assert _is_test_path("tests/helpers.py") is True
    assert _is_test_path("ship_test.py") is True
    assert _is_test_path("ship.py") is False
    assert _is_test_path("") is False


def test_focus_guard_allows_empty_scope(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _explicit_source_focus_mismatch(
        objective_target="ship.py",
        allowed_files=(),
        repository_root=root,
        normalize_repo_relative_path=_norm,
        looks_like_a_file=_looks,
        resolve_relative_path=resolve_relative_path,
    ) is None


EDIT1_OBJECTIVE = (
    "In the existing file ship.py in the cycle workspace, fix manifest_line "
    "so the weight is always formatted with exactly one decimal place "
    "(for example manifest_line('BX1', 12) must return 'BX1:12.0kg'). "
    "Do not change the '<BOX_ID>:<WEIGHT>kg' shape. Verify by running "
    "the project's pytest suite."
)


def _miss_kwargs(root: Path, objective: str, paths: list[str]) -> dict:
    return {
        "objective": objective,
        "applied_mutation_paths": paths,
        "repository_root": root,
        "normalize_repo_relative_path": _norm,
        "looks_like_a_file": _looks,
        "extract_target_path": extract_target_path,
        "resolve_relative_path": resolve_relative_path,
    }


def test_mutation_level_edit1_shape_tests_only_is_mismatch(tmp_path: Path) -> None:
    """Wrong-file edit + untouched named target must FAIL (never PASS)."""
    root = _repo(tmp_path)
    detail = _test_only_mutations_miss_named_source(
        **_miss_kwargs(root, EDIT1_OBJECTIVE, ["tests/test_ship.py"])
    )
    assert detail is not None
    assert "ship.py" in detail
    assert "tests/test_ship.py" in detail


def test_mutation_level_source_touched_is_allowed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _test_only_mutations_miss_named_source(
        **_miss_kwargs(root, EDIT1_OBJECTIVE, ["ship.py", "tests/test_ship.py"])
    ) is None


def test_mutation_level_source_only_is_allowed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _test_only_mutations_miss_named_source(
        **_miss_kwargs(root, EDIT1_OBJECTIVE, ["ship.py"])
    ) is None


def test_mutation_level_no_mutations_never_mismatches(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _test_only_mutations_miss_named_source(
        **_miss_kwargs(root, EDIT1_OBJECTIVE, [])
    ) is None


def test_mutation_level_test_adding_objective_is_allowed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _test_only_mutations_miss_named_source(
        **_miss_kwargs(
            root,
            "Add a regression test for manifest_line rounding in ship.py.",
            ["tests/test_ship.py"],
        )
    ) is None


def test_mutation_level_ghost_target_never_mismatches(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _test_only_mutations_miss_named_source(
        **_miss_kwargs(root, "Fix ghost.py formatting.", ["tests/test_ship.py"])
    ) is None


def test_related_repo_tests_grafts_stem_match(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _related_repo_test_modules(root, ["ship.py"]) == ["tests/test_ship.py"]
    assert _related_repo_test_modules(root, ["tests/test_ship.py"]) == [
        "tests/test_ship.py"
    ]
    assert _related_repo_test_modules(root, ["unrelated.py"]) == []
    assert _related_repo_test_modules(root, []) == []


def test_validation_pool_grafts_tests_when_discovery_has_only_source(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    root = _repo(tmp_path)
    discovery = SimpleNamespace(config_files={"ship.py": "content"}, test_files=[])
    pool = _validation_path_pool(
        mutated=["ship.py"], bound_multi=[], discovery=discovery, repository_root=root
    )
    assert "ship.py" in pool
    assert "tests/test_ship.py" in pool


def test_whole_file_counts_same_line_count_rename() -> None:
    """Live WIDEN-w3: whole-file fallback counted line deltas, so a rename
    (same line count) reported 0/0 and gates failed correct work."""
    added, removed = _whole_file_line_counts(
        "def berth_fee():\n    return 1\n",
        "def dock_fee():\n    return 1\n",
    )
    assert (added, removed) == (1, 1)


def test_whole_file_counts_add_remove_and_identical() -> None:
    assert _whole_file_line_counts("a\nb\n", "a\nb\nc\n") == (1, 0)
    assert _whole_file_line_counts("a\nb\nc\n", "a\n") == (0, 2)
    assert _whole_file_line_counts("same\n", "same\n") == (0, 0)
    assert _whole_file_line_counts("x\nx\n", "x\nx\nx\ny\n") == (2, 0)
