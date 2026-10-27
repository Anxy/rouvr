from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.services.devexecutor_proposal_workspace_service import ProposalWorkspaceManager


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or f"git {' '.join(args)} failed")
    return (proc.stdout or "").strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_proposal_workspace_create_and_capture_review_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(tmp_path / "proposals", branch_prefix="aix/proposals")
    context = manager.create_workspace(project_id="p1", job_id="job1", task_number=77, source_root=repo)

    assert context.workspace_root.exists()
    assert (context.workspace_root / ".git").exists()
    assert context.repo_root == repo.resolve()
    assert context.artifact_root.exists()
    assert context.artifact_root.parent.name == "_artifacts"
    meta = json.loads((context.workspace_root / ".devexecutor_proposal_workspace.json").read_text(encoding="utf-8"))
    assert meta["branch_name"].startswith("aix/proposals/task-77-")
    assert meta["base_branch"] == "main"
    assert meta["repo_root"] == str(repo.resolve())
    assert meta["artifact_root"] == str(context.artifact_root)
    exclude_path = Path(_git(context.checkout_root, "rev-parse", "--git-path", "info/exclude"))
    if not exclude_path.is_absolute():
        exclude_path = (context.checkout_root / exclude_path).resolve()
    exclude = exclude_path.read_text(encoding="utf-8")
    assert ".devexecutor_proposal_workspace.json" in exclude
    assert ".devexecutor_proposal_artifacts.json" in exclude
    worktree_list = _git(repo, "worktree", "list", "--porcelain")
    assert context.checkout_root.as_posix() in worktree_list

    (context.workspace_root / "hello.txt").write_text("hello\nproposal\n", encoding="utf-8")
    artifacts = manager.capture_review_artifacts(context=context)

    assert artifacts["has_changes"] is True
    assert artifacts["artifact_root"] == str(context.artifact_root)
    assert artifacts["changed_files"] == ["hello.txt"]
    assert (context.artifact_root / "diff.patch").read_text(encoding="utf-8")
    assert json.loads((context.artifact_root / "changed_files.json").read_text(encoding="utf-8")) == ["hello.txt"]
    assert not (context.workspace_root / "diff.patch").exists()
    assert not (context.workspace_root / "changed_files.json").exists()

    manager.teardown_workspace(context=context, retain=False, drop_branch=True)
    assert not context.workspace_root.exists()
    assert not context.artifact_root.exists()
    assert context.branch_name not in _git(repo, "branch", "--list")
    assert context.checkout_root.as_posix() not in _git(repo, "worktree", "list", "--porcelain")


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_proposal_workspace_preserves_service_subpath(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    service = repo / "services" / "hyeve-codeintel-service"
    tests_dir = service / "tests"
    tests_dir.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (tests_dir / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(tmp_path / "proposals", branch_prefix="aix/proposals")
    context = manager.create_workspace(project_id="p1", job_id="job1", task_number=88, source_root=service)

    assert context.source_root == service.resolve()
    assert context.checkout_root.exists()
    assert context.workspace_root == (context.checkout_root / "services" / "hyeve-codeintel-service").resolve()
    assert context.artifact_root.exists()
    assert (context.workspace_root / "tests" / "test_sample.py").exists()
    meta = json.loads((context.workspace_root / ".devexecutor_proposal_workspace.json").read_text(encoding="utf-8"))
    checkout_meta = json.loads(
        (context.checkout_root / ".devexecutor_proposal_workspace.json").read_text(encoding="utf-8")
    )
    assert meta["repo_root"] == str(repo.resolve())
    assert meta["source_root"] == str(service.resolve())
    assert meta["checkout_root"] == str(context.checkout_root)
    assert meta["workspace_root"] == str(context.workspace_root)
    assert meta["artifact_root"] == str(context.artifact_root)
    assert checkout_meta == meta
    exclude_path = Path(_git(context.checkout_root, "rev-parse", "--git-path", "info/exclude"))
    if not exclude_path.is_absolute():
        exclude_path = (context.checkout_root / exclude_path).resolve()
    exclude = exclude_path.read_text(encoding="utf-8")
    assert ".devexecutor_proposal_workspace.json" in exclude

    manager.teardown_workspace(context=context, retain=False, drop_branch=True)
    assert not context.checkout_root.exists()
    assert not context.artifact_root.exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_proposal_workspace_can_partition_by_tenant(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(tmp_path / "proposals", branch_prefix="aix/proposals")
    tenant_a = manager.create_workspace(
        project_id="p1",
        tenant_id="tenant-a",
        job_id="job-a",
        task_number=101,
        source_root=repo,
    )
    tenant_b = manager.create_workspace(
        project_id="p1",
        tenant_id="tenant-b",
        job_id="job-b",
        task_number=102,
        source_root=repo,
    )

    assert tenant_a.checkout_root.parts[-2] == "tenant-a"
    assert tenant_b.checkout_root.parts[-2] == "tenant-b"
    assert tenant_a.artifact_root.parts[-2] == "tenant-a"
    assert tenant_b.artifact_root.parts[-2] == "tenant-b"
    assert tenant_a.checkout_root != tenant_b.checkout_root
    assert tenant_a.artifact_root != tenant_b.artifact_root
    assert tenant_a.workspace_root != tenant_b.workspace_root
    meta_a = json.loads((tenant_a.workspace_root / ".devexecutor_proposal_workspace.json").read_text(encoding="utf-8"))
    meta_b = json.loads((tenant_b.workspace_root / ".devexecutor_proposal_workspace.json").read_text(encoding="utf-8"))
    assert meta_a["tenant_id"] == "tenant-a"
    assert meta_b["tenant_id"] == "tenant-b"
    assert "tenant-b" not in str(meta_a)
    assert "tenant-a" not in str(meta_b)

    manager.teardown_workspace(context=tenant_a, retain=False, drop_branch=True)
    manager.teardown_workspace(context=tenant_b, retain=False, drop_branch=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_capture_review_artifacts_canonicalizes_service_relative_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    service = repo / "services" / "hyeve-codeintel-service"
    backend_dir = service / "backend"
    backend_dir.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    target = backend_dir / "service.py"
    target.write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(tmp_path / "proposals", branch_prefix="aix/proposals")
    context = manager.create_workspace(project_id="p1", job_id="job1", task_number=89, source_root=service)
    (context.workspace_root / "backend" / "service.py").write_text("print('v2')\n", encoding="utf-8")

    artifacts = manager.capture_review_artifacts(context=context)

    assert artifacts["changed_files"] == ["backend/service.py"]
    assert artifacts["artifact_root"] == str(context.artifact_root)
    assert artifacts["workspace_root"] == str(context.checkout_root)
    assert artifacts["source_workspace_root"] == str(context.workspace_root)
    assert json.loads((context.artifact_root / "changed_files.json").read_text(encoding="utf-8")) == [
        "backend/service.py"
    ]
    assert not (context.workspace_root / "changed_files.json").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_capture_review_artifacts_includes_repo_root_file_from_service_workspace(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    service = repo / "projects" / "AIX" / "hyeve-aix-service"
    backend_dir = service / "backend"
    backend_dir.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (backend_dir / "service.py").write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(tmp_path / "proposals", branch_prefix="aix/proposals")
    context = manager.create_workspace(project_id="p1", job_id="job1", task_number=93, source_root=service)
    docs_file = context.checkout_root / "projects" / "AIX" / "docs" / "live_lane_trials" / "proof.md"
    docs_file.parent.mkdir(parents=True)
    docs_file.write_text("AIX CLI lane live proof\n", encoding="utf-8")

    artifacts = manager.capture_review_artifacts(context=context)
    diff_text = (context.artifact_root / "diff.patch").read_text(encoding="utf-8")

    assert artifacts["changed_files"] == ["projects/AIX/docs/live_lane_trials/proof.md"]
    assert artifacts["workspace_root"] == str(context.checkout_root)
    assert artifacts["source_workspace_root"] == str(context.workspace_root)
    assert "projects/AIX/docs/live_lane_trials/proof.md" in diff_text
    assert "AIX CLI lane live proof" in diff_text
    assert json.loads((context.artifact_root / "changed_files.json").read_text(encoding="utf-8")) == [
        "projects/AIX/docs/live_lane_trials/proof.md"
    ]


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_capture_review_artifacts_includes_untracked_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(tmp_path / "proposals", branch_prefix="aix/proposals")
    context = manager.create_workspace(project_id="p1", job_id="job1", task_number=92, source_root=repo)
    (context.workspace_root / "hello.txt").write_text("hello\nproposal\n", encoding="utf-8")
    (context.workspace_root / "new_module.py").write_text("print('new')\n", encoding="utf-8")

    artifacts = manager.capture_review_artifacts(context=context)

    assert artifacts["changed_files"] == ["hello.txt", "new_module.py"]
    assert json.loads((context.artifact_root / "changed_files.json").read_text(encoding="utf-8")) == [
        "hello.txt",
        "new_module.py",
    ]
    diff_text = (context.artifact_root / "diff.patch").read_text(encoding="utf-8")
    assert "new_module.py" in diff_text
    assert "print('new')" in diff_text


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_teardown_workspace_retain_preserves_sidecar_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(tmp_path / "proposals", branch_prefix="aix/proposals")
    context = manager.create_workspace(project_id="p1", job_id="job1", task_number=90, source_root=repo)
    (context.workspace_root / "hello.txt").write_text("hello\nproposal\n", encoding="utf-8")
    manager.capture_review_artifacts(context=context)

    manager.teardown_workspace(context=context, retain=True, retention_reason="debug")

    assert context.workspace_root.exists()
    assert context.artifact_root.exists()
    meta = json.loads((context.workspace_root / ".devexecutor_proposal_workspace.json").read_text(encoding="utf-8"))
    assert meta["retained"] is True
    assert meta["retention_reason"] == "debug"


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_create_workspace_fails_closed_when_free_space_below_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.services.devexecutor_proposal_workspace_service import ProposalWorkspaceError

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "init")

    manager = ProposalWorkspaceManager(
        tmp_path / "proposals",
        branch_prefix="aix/proposals",
        min_free_bytes=10_000,
    )
    monkeypatch.setattr(
        "backend.services.devexecutor_proposal_workspace_service.shutil.disk_usage",
        lambda _path: shutil._ntuple_diskusage(total=20_000, used=15_000, free=5_000),
    )

    with pytest.raises(ProposalWorkspaceError, match="free space below threshold"):
        manager.create_workspace(project_id="p1", job_id="job1", task_number=91, source_root=repo)
