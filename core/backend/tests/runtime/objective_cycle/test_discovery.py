"""Repository discovery: real inspection, not a hardcoded "Ready" status."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.discovery import run_repository_discovery
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def fake_integration_service(tmp_path: Path) -> Path:
    (tmp_path / "backend" / "hyeve_integration_service").mkdir(parents=True)
    (tmp_path / "backend" / "services").mkdir(parents=True)
    (tmp_path / "backend" / "hyeve_integration_service" / "config.py").write_text(
        "class Settings:\n    SMTP_HOST: str = ''\n    SMTP_PORT: int = 587\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "backend" / "services" / "message_adapters.py").write_text(
        "def _smtp_configured():\n    return False\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / ".env.example").write_text("SMTP_HOST=\nSMTP_PORT=587\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'hyeve-integration-service'\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


def test_discovery_reads_real_manifest_config_and_env(fake_integration_service: Path) -> None:
    events: list[tuple[str, dict]] = []
    box = RepositoryToolbox(repository_root=fake_integration_service, on_event=lambda t, r: events.append((t, r)))
    report = run_repository_discovery(toolbox=box, objective="Add SMTP_PASSWORD support to the mail configuration")

    assert "pyproject.toml" in report.manifests
    assert "hyeve-integration-service" in report.manifests["pyproject.toml"]

    assert "backend/hyeve_integration_service/config.py" in report.config_files
    assert "SMTP_HOST" in report.config_files["backend/hyeve_integration_service/config.py"]

    assert report.env_example_path == ".env.example"
    assert report.env_example is not None and "SMTP_HOST" in report.env_example

    assert "smtp" in report.keywords
    assert any(hit["keyword"] == "smtp" for hit in report.relevant_module_hits)

    assert "test_smoke.py" in report.test_files
    assert report.files_read > 0

    # Real events, not a hardcoded "Ready" — bracketed start/end plus per-call tool events.
    event_types = [e[0] for e in events]
    assert event_types[0] == "discovery.started"
    assert event_types[-1] == "discovery.completed"
    assert "tool.file_read" in event_types
    assert "tool.list_directory" in event_types
    assert "tool.search" in event_types


def test_discovery_finds_nested_config_files(fake_integration_service: Path) -> None:
    box = RepositoryToolbox(repository_root=fake_integration_service)
    report = run_repository_discovery(toolbox=box, objective="anything")
    # config.py lives two levels deep (backend/hyeve_integration_service/) — must not
    # only look at the top-level of each discovered src root.
    assert any(path.endswith("config.py") for path in report.config_files)


def test_discovery_never_reads_real_dotenv_only_dotenv_example(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SMTP_PASSWORD=super-secret-real-value\n", encoding="utf-8", newline="\n")
    (tmp_path / ".env.example").write_text("SMTP_PASSWORD=\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    box = RepositoryToolbox(repository_root=tmp_path)
    report = run_repository_discovery(toolbox=box, objective="add smtp support")
    assert report.env_example_path == ".env.example"
    assert report.env_example is not None
    assert "super-secret-real-value" not in report.env_example


def test_discovery_on_empty_repo_finds_nothing_but_does_not_crash(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("nothing here\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    box = RepositoryToolbox(repository_root=tmp_path)
    report = run_repository_discovery(toolbox=box, objective="add smtp support")
    assert report.manifests == {}
    assert report.config_files == {}
    assert report.env_example is None
