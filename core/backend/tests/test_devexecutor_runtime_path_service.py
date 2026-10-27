from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.services.devexecutor_runtime_path_service import resolve_runtime_workspace_mapping


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
    return str(proc.stdout or "").strip()


def test_resolve_runtime_workspace_mapping_uses_configured_root_when_workspace_is_nested(tmp_path: Path) -> None:
    workspace_root = tmp_path / "services" / "hyeve-codeintel-service" / "backend"
    workspace_root.mkdir(parents=True)

    mapping = resolve_runtime_workspace_mapping(
        workspace_root=workspace_root,
        runtime={
            "host_workspace_root": str(tmp_path),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    assert mapping["mount_source"] == str(tmp_path)
    assert mapping["container_workspace_root"] == "/workspace/hyeve/services/hyeve-codeintel-service/backend"


def test_resolve_runtime_workspace_mapping_reanchors_container_workspace_to_host_root(tmp_path: Path) -> None:
    host_root = tmp_path / "repos" / "hyeve"
    host_root.mkdir(parents=True)

    mapping = resolve_runtime_workspace_mapping(
        workspace_root="/workspace/hyeve/projects/AIX/hyeve-aix-service",
        runtime={
            "host_workspace_root": str(host_root),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    assert mapping["host_workspace_root"] == str(host_root)
    assert mapping["mount_source"] == str((host_root / "projects" / "AIX" / "hyeve-aix-service").resolve())
    assert mapping["container_workspace_root"] == "/workspace/hyeve/projects/AIX/hyeve-aix-service"


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_resolve_runtime_workspace_mapping_prefers_git_root_for_proposal_subdir(tmp_path: Path) -> None:
    source_repo = tmp_path / "source"
    source_service = source_repo / "projects" / "AIX" / "hyeve-aix-service"
    source_service.mkdir(parents=True)
    _git(source_repo, "init", "-b", "main")
    _git(source_repo, "config", "user.email", "test@example.com")
    _git(source_repo, "config", "user.name", "Test User")
    (source_service / "demo.py").write_text("print('ok')\n", encoding="utf-8")
    _git(source_repo, "add", ".")
    _git(source_repo, "commit", "-m", "init")

    proposal_checkout = tmp_path / "proposals" / "task-2018"
    _git(tmp_path, "clone", "--no-hardlinks", str(source_repo), str(proposal_checkout))
    proposal_workspace = proposal_checkout / "projects" / "AIX" / "hyeve-aix-service"

    mapping = resolve_runtime_workspace_mapping(
        workspace_root=proposal_workspace,
        runtime={
            "host_workspace_root": str(source_repo),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    assert mapping["host_workspace_root"] == str(proposal_checkout.resolve())
    assert mapping["mount_source"] == str(proposal_checkout.resolve())
    assert mapping["container_workspace_root"] == "/workspace/hyeve/projects/AIX/hyeve-aix-service"
