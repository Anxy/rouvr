from __future__ import annotations

from pathlib import Path

from backend.services.aix_target_file_resolution_service import resolve_target_file


def test_resolve_target_file_handles_service_prefixed_repo_relative_path(tmp_path: Path) -> None:
    service_root = tmp_path / "services" / "hyeve-codeintel-service"
    target = service_root / "backend" / "services" / "codeintel_query_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")

    result = resolve_target_file(
        workspace_root=service_root,
        input_path="services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
    )

    assert result.exists is True
    assert result.normalized_repo_relative_path == "backend/services/codeintel_query_service.py"
    assert result.resolved_absolute_path == str(target)


def test_resolve_target_file_normalizes_separators(tmp_path: Path) -> None:
    service_root = tmp_path / "services" / "hyeve-codeintel-service"
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")

    result = resolve_target_file(
        workspace_root=service_root,
        input_path="services\\hyeve-codeintel-service\\backend\\service.py",
    )

    assert result.exists is True
    assert result.normalized_repo_relative_path == "backend/service.py"


def test_resolve_target_file_reports_missing_target_explicitly(tmp_path: Path) -> None:
    service_root = tmp_path / "services" / "hyeve-codeintel-service"
    service_root.mkdir(parents=True, exist_ok=True)

    result = resolve_target_file(
        workspace_root=service_root,
        input_path="services/hyeve-codeintel-service/backend/missing.py",
    )

    assert result.exists is False
    assert result.failure_reason == "target_not_found"
    assert result.normalized_repo_relative_path == "backend/missing.py"
    assert str(service_root / "backend" / "missing.py") in result.attempted_absolute_paths


def test_resolve_target_file_accepts_project_root_directory_from_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "hyeve"
    target = repo_root / "projects" / "AIX" / "hyeve-aix-service" / "backend" / "services"
    target.mkdir(parents=True)
    (repo_root / ".git").mkdir()

    result = resolve_target_file(
        workspace_root=repo_root,
        input_path="projects/AIX/hyeve-aix-service/backend/services",
        metadata={"repo_key": "hyeve-aix-service"},
    )

    assert result.exists is True
    assert result.normalized_repo_relative_path == "projects/AIX/hyeve-aix-service/backend/services"
    assert result.resolved_absolute_path == str(target)
    assert result.chosen_root_kind == "workspace_root"


def test_resolve_target_file_accepts_service_root_directory_path(tmp_path: Path) -> None:
    service_root = tmp_path / "hyeve" / "projects" / "AIX" / "hyeve-aix-service"
    target = service_root / "backend" / "tests"
    target.mkdir(parents=True)

    result = resolve_target_file(
        workspace_root=service_root,
        input_path="backend/tests",
        metadata={"repo_key": "hyeve-aix-service"},
    )

    assert result.exists is True
    assert result.normalized_repo_relative_path == "backend/tests"
    assert result.resolved_absolute_path == str(target)


def test_resolve_target_file_missing_project_path_reports_attempted_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "hyeve"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()

    result = resolve_target_file(
        workspace_root=repo_root,
        input_path="projects/AIX/hyeve-aix-service/backend/missing.py",
        metadata={"repo_key": "hyeve-aix-service"},
    )

    assert result.exists is False
    assert result.failure_reason == "target_not_found"
    assert (
        str(repo_root / "projects" / "AIX" / "hyeve-aix-service" / "backend" / "missing.py")
        in result.attempted_absolute_paths
    )
    assert str(repo_root / "backend" / "missing.py") in result.attempted_absolute_paths
