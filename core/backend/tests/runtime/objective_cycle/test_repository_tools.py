"""Repository tool layer: path confinement, real file/git/command operations."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.aix_core.intelligence.repository.repository_tools import (
    ApprovalRequiredError,
    RepositoryToolbox,
    RepositoryToolError,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "config.py").write_text("SMTP_HOST = 'x'\nSMTP_PORT = 587\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_list_directory_read_search_status_are_real(repo: Path) -> None:
    events: list[tuple[str, dict]] = []
    box = RepositoryToolbox(repository_root=repo, on_event=lambda t, r: events.append((t, r)))

    listing = box.list_directory(".")
    assert any(e["name"] == "config.py" for e in listing["entries"])

    read = box.read_file("config.py")
    assert "SMTP_HOST" in read["content"]

    hits = box.search_files("SMTP_HOST")
    assert hits["hits"]

    status = box.get_git_status()
    assert status["is_git_repo"] is True

    event_types = [e[0] for e in events]
    assert "tool.list_directory" in event_types
    assert "tool.file_read" in event_types
    assert "tool.search" in event_types
    assert "tool.git_status" in event_types


def test_read_file_missing_raises(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)
    with pytest.raises(FileNotFoundError):
        box.read_file("does_not_exist.py")


@pytest.mark.parametrize(
    "relative_path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "a/../../outside.txt",
    ],
)
def test_path_traversal_is_rejected(repo: Path, relative_path: str) -> None:
    box = RepositoryToolbox(repository_root=repo)
    with pytest.raises(RepositoryToolError) as exc_info:
        box.read_file(relative_path)
    assert exc_info.value.code == "PATH_ESCAPES_WORKSPACE_ROOT"


def test_symlink_escape_is_rejected(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    secret = outside / "secret.txt"
    secret.write_text("top secret\n", encoding="utf-8")
    link = repo / "escape_link"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")
    box = RepositoryToolbox(repository_root=repo)
    with pytest.raises(RepositoryToolError) as exc_info:
        box.read_file("escape_link")
    assert exc_info.value.code == "PATH_ESCAPES_WORKSPACE_ROOT"


def test_create_file_rejects_existing_target(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)
    box.create_file("new_file.txt", "hello\n")
    assert (repo / "new_file.txt").read_text(encoding="utf-8") == "hello\n"
    with pytest.raises(RepositoryToolError) as exc_info:
        box.create_file("new_file.txt", "again\n")
    assert exc_info.value.code == "FILE_ALREADY_EXISTS"


def test_delete_file_requires_explicit_approval(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)
    box.create_file("scratch.txt", "x\n")

    with pytest.raises(ApprovalRequiredError) as exc_info:
        box.delete_file("scratch.txt")
    assert exc_info.value.relative_path == "scratch.txt"
    assert (repo / "scratch.txt").exists()

    result = box.delete_file("scratch.txt", approved=True)
    assert result["deleted"] is True
    assert not (repo / "scratch.txt").exists()


def test_apply_patch_and_get_git_diff_are_real(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)
    diff = (
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -1,2 +1,3 @@\n"
        " SMTP_HOST = 'x'\n"
        " SMTP_PORT = 587\n"
        "+SMTP_PASSWORD = ''\n"
    )
    outcome = box.apply_patch(diff)
    assert outcome["status"] == "applied"
    assert "config.py" in outcome["changed_files"]

    on_disk = (repo / "config.py").read_text(encoding="utf-8")
    assert "SMTP_PASSWORD" in on_disk

    diff_result = box.get_git_diff()
    assert "SMTP_PASSWORD" in diff_result["diff"]
    assert diff_result["stats"]["config.py"]["additions"] == 1
    assert diff_result["stats"]["config.py"]["deletions"] == 0


def test_apply_patch_rejects_empty_patch(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)
    with pytest.raises(RepositoryToolError) as exc_info:
        box.apply_patch("   ")
    assert exc_info.value.code == "EMPTY_PATCH"


def test_run_command_enforces_allowlist(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)

    result = box.run_command(["python", "--version"], allowlist=("python --version",))
    assert result["returncode"] == 0

    with pytest.raises(RepositoryToolError) as exc_info:
        box.run_command(["curl", "evil.example.com"], allowlist=("python --version",))
    assert exc_info.value.code == "COMMAND_DENIED"


def test_run_command_rejects_shell_metacharacters(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)
    with pytest.raises(RepositoryToolError) as exc_info:
        box.run_command(["python", "-c", "print('x')"], allowlist=("python -c print('x')",))
    assert exc_info.value.code == "COMMAND_DENIED"


def test_run_tests_only_allows_the_exact_test_command(repo: Path) -> None:
    box = RepositoryToolbox(repository_root=repo)
    result = box.run_tests(["python", "--version"])
    assert result["returncode"] == 0


def test_repository_root_must_exist(tmp_path: Path) -> None:
    with pytest.raises(RepositoryToolError) as exc_info:
        RepositoryToolbox(repository_root=tmp_path / "does-not-exist")
    assert exc_info.value.code == "REPOSITORY_ROOT_MISSING"
