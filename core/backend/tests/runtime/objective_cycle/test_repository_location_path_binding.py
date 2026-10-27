"""AIX-REAL-EXECUTION-002: bare-path -> workspace reverse lookup."""

from __future__ import annotations

import json

from backend.aix_core.intelligence.repository.repository_location import (
    rebind_repository_for_objective,
    resolve_repository_for_path,
)


class _FakeSettings:
    def __init__(self, roots: dict[str, str]) -> None:
        self.OBJECTIVE_CYCLE_REPOSITORY_ROOTS = json.dumps(roots)


def test_unambiguous_match_returns_the_repository_id(tmp_path) -> None:
    root = tmp_path / "hyeve"
    root.mkdir()
    target = root / "services" / "hyeve-integration-service" / ".env"
    target.parent.mkdir(parents=True)
    target.write_text("SMTP_HOST=x\n", encoding="utf-8")

    settings = _FakeSettings({"hyeve": str(root)})
    assert resolve_repository_for_path(str(target), settings=settings) == "hyeve"


def test_no_registered_root_contains_the_path_returns_none(tmp_path) -> None:
    root = tmp_path / "other"
    root.mkdir()
    settings = _FakeSettings({"other": str(root)})
    assert resolve_repository_for_path(str(tmp_path / "unrelated" / "file.txt"), settings=settings) is None


def test_empty_registry_returns_none(tmp_path) -> None:
    settings = _FakeSettings({})
    assert resolve_repository_for_path(str(tmp_path / "file.txt"), settings=settings) is None


def test_prefix_collision_is_not_a_false_match(tmp_path) -> None:
    """`C:\\repo` must never match a path under `C:\\repository` (or vice versa) just
    because one string is a prefix of the other -- only a real path-segment ancestor
    counts."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repository_root = tmp_path / "repository"
    (repository_root / "file.txt").parent.mkdir(parents=True)
    (repository_root / "file.txt").write_text("x", encoding="utf-8")

    settings = _FakeSettings({"repo": str(repo_root)})
    assert resolve_repository_for_path(str(repository_root / "file.txt"), settings=settings) is None


def test_nested_match_picks_the_most_specific_root(tmp_path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "a" / "nested_root_b"
    root_b.mkdir(parents=True)
    target = root_b / "file.txt"
    target.write_text("x", encoding="utf-8")

    settings = _FakeSettings({"root-a": str(root_a), "root-b": str(root_b)})
    assert resolve_repository_for_path(str(target), settings=settings) == "root-b"


def test_parent_of_registered_roots_maps_to_hyeve_aix_service(tmp_path) -> None:
    """D:\\v2.1\\AIX-style umbrella path must not be admitted as repository_id."""
    umbrella = tmp_path / "AIX"
    service = umbrella / "hyeve-aix-service"
    backend = service / "backend"
    backend.mkdir(parents=True)
    settings = _FakeSettings(
        {
            "hyeve-aix-service": str(service),
            "aix-backend": str(backend),
        }
    )
    assert resolve_repository_for_path(str(umbrella), settings=settings) == "hyeve-aix-service"


def test_exact_registered_umbrella_root_matches(tmp_path) -> None:
    umbrella = tmp_path / "AIX"
    umbrella.mkdir()
    settings = _FakeSettings({"aix": str(umbrella)})
    assert resolve_repository_for_path(str(umbrella), settings=settings) == "aix"


def test_rebind_keeps_parent_repo_and_relativizes_nested_absolute_path(tmp_path) -> None:
    parent = tmp_path / "hyeve-aix-service"
    child = parent / "backend"
    child.mkdir(parents=True)
    settings = _FakeSettings(
        {"hyeve-aix-service": str(parent), "aix-backend": str(child)}
    )
    repo, rel = rebind_repository_for_objective(
        "hyeve-aix-service",
        f"summary of {child}",
        target_path=str(child),
        settings=settings,
    )
    assert repo == "hyeve-aix-service"
    assert rel.replace("\\", "/") == "backend"


def test_rebind_does_not_expand_child_bind_to_parent_repo(tmp_path) -> None:
    parent = tmp_path / "hyeve-aix-service"
    child = parent / "backend"
    (parent / "README.md").parent.mkdir(parents=True, exist_ok=True)
    child.mkdir(parents=True, exist_ok=True)
    (parent / "README.md").write_text("x", encoding="utf-8")
    settings = _FakeSettings(
        {"hyeve-aix-service": str(parent), "aix-backend": str(child)}
    )
    repo, rel = rebind_repository_for_objective(
        "aix-backend",
        f"read {parent / 'README.md'}",
        target_path=str(parent / "README.md"),
        settings=settings,
    )
    assert repo is None
    assert "README.md" in str(rel)
