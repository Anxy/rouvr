from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.services.devexecutor_mutation_runtime_service import (
    MutationRuntimeError,
    _docker_command_env,
    execute_mutation_in_runtime,
)


def _remote_runtime_diag() -> dict[str, object]:
    return {
        "cli_available": True,
        "daemon_reachable": True,
        "socket_backed": False,
        "transport": "remote",
        "docker_host": "tcp://runtime.example:2376",
        "socket_exists": False,
    }


def test_execute_mutation_in_runtime_requires_container_for_docker_runtime(tmp_path: Path) -> None:
    with pytest.raises(MutationRuntimeError, match="CONTAINER_MUTATION_RUNTIME_REQUIRED"):
        execute_mutation_in_runtime(
            workspace_root=tmp_path,
            tool="write_file",
            args={"relative_path": "a.txt", "content": "x"},
            runtime={"mode": "host", "container_required": True},
        )


def test_execute_mutation_in_runtime_wraps_docker_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            payload = json.loads(kwargs["input"])
            assert payload["tool"] == "write_file"
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )
    assert result["operation"] == "write_file"
    assert calls[1][0] == "docker"
    assert "WORKSPACE_ROOT=/workspace" in calls[1]
    assert "WORKSPACE_PATH_PREFIX=/workspace" in calls[1]
    assert "/opt/devexecutor-runtime/mutate_workspace.py" in calls[1]


def test_execute_mutation_in_runtime_maps_subdir_workspace_to_configured_container_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    workspace_root = tmp_path / "services" / "hyeve-codeintel-service" / "backend"
    workspace_root.mkdir(parents=True)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    execute_mutation_in_runtime(
        workspace_root=workspace_root,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "host_workspace_root": str(tmp_path),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    command = calls[1]
    assert "WORKSPACE_ROOT=/workspace/hyeve/services/hyeve-codeintel-service/backend" in command
    assert "WORKSPACE_PATH_PREFIX=/workspace/hyeve/services/hyeve-codeintel-service/backend" in command
    assert "/workspace/hyeve/services/hyeve-codeintel-service/backend" in command


def test_execute_mutation_in_runtime_reanchors_container_workspace_to_host_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    host_root = tmp_path / "repos" / "hyeve"
    mounted_workspace = host_root / "projects" / "AIX" / "hyeve-aix-service" / "backend"
    mounted_workspace.mkdir(parents=True)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    execute_mutation_in_runtime(
        workspace_root="/workspace/hyeve/projects/AIX/hyeve-aix-service/backend",
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "host_workspace_root": str(host_root),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    command = calls[1]
    mount_index = command.index("-v")
    assert command[mount_index + 1] == f"{mounted_workspace.resolve()}:/workspace/hyeve"
    assert "WORKSPACE_ROOT=/workspace/hyeve/projects/AIX/hyeve-aix-service/backend" in command


def test_execute_mutation_in_runtime_rejects_live_host_checkout_without_proposal_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_root = tmp_path / "repos" / "hyeve"
    workspace_root = host_root / "projects" / "AIX" / "hyeve-aix-service" / "backend"
    workspace_root.mkdir(parents=True)
    (host_root / ".git").mkdir(parents=True)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )

    with pytest.raises(
        MutationRuntimeError,
        match="HOST_CHECKOUT_MUTATION_RUNTIME_REQUIRES_MANAGED_PROPOSAL_WORKSPACE",
    ):
        execute_mutation_in_runtime(
            workspace_root=workspace_root,
            tool="write_file",
            args={"relative_path": "a.txt", "content": "x"},
            runtime={
                "mode": "docker",
                "container_required": True,
                "image": "runtime:latest",
                "host_workspace_root": str(host_root),
                "container_workspace_root": "/workspace/hyeve",
            },
        )


def test_execute_mutation_in_runtime_allows_managed_proposal_workspace_under_host_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    host_root = tmp_path / "repos" / "hyeve"
    workspace_root = host_root / "proposals" / "task-2549" / "backend"
    workspace_root.mkdir(parents=True)
    (host_root / ".git").mkdir(parents=True)
    (workspace_root / ".devexecutor_proposal_workspace.json").write_text(
        json.dumps({"checkout_root": str(host_root / "proposals" / "task-2549"), "repo_root": str(host_root)}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    execute_mutation_in_runtime(
        workspace_root=workspace_root,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "host_workspace_root": str(host_root),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    command = calls[1]
    assert "WORKSPACE_ROOT=/workspace/hyeve/proposals/task-2549/backend" in command


def test_execute_mutation_in_runtime_injects_workspace_base_commit_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "workspace_base_commit_sha": "abc123base",
        },
    )

    command = calls[1]
    assert "AIX_WORKSPACE_BASE_COMMIT_SHA=abc123base" in command


def test_execute_mutation_in_runtime_adds_linked_worktree_passthrough_mounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    checkout_root = tmp_path / "proposals" / "task-2190"
    workspace_root = checkout_root / "services" / "hyeve-workhub-service"
    repo_root = tmp_path / "repos" / "hyeve"
    workspace_root.mkdir(parents=True)
    (repo_root / ".git").mkdir(parents=True)
    (workspace_root / ".devexecutor_proposal_workspace.json").write_text(
        json.dumps(
            {
                "checkout_root": "/srv/rouvr/proposals/task-2190",
                "repo_root": "/srv/rouvr/repos/hyeve",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._linked_worktree_passthrough_mounts",
        lambda **_kwargs: [
            "/srv/rouvr/proposals/task-2190:/srv/rouvr/proposals/task-2190",
            "/srv/rouvr/repos/hyeve/.git:/srv/rouvr/repos/hyeve/.git",
        ],
    )
    execute_mutation_in_runtime(
        workspace_root=workspace_root,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )

    command = calls[1]
    assert "/srv/rouvr/proposals/task-2190:/srv/rouvr/proposals/task-2190" in command
    assert "/srv/rouvr/repos/hyeve/.git:/srv/rouvr/repos/hyeve/.git" in command


def test_execute_mutation_in_runtime_applies_isolation_limits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            payload = json.loads(kwargs["input"])
            assert payload["tool"] == "write_file"
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "network": "none",
            "cpus": "1.0",
            "memory": "1g",
            "pids_limit": 256,
            "read_only_rootfs": True,
            "tmpfs_size": "512m",
            "labels": {"hyeve.run.id": "devrun-123"},
        },
    )

    command = calls[1]
    assert "--read-only" in command
    assert "--tmpfs" in command
    assert "/tmp:rw,exec,nosuid,size=512m" in command
    assert "--network" in command
    assert "none" in command
    assert "--cpus" in command
    assert "1.0" in command
    assert "--memory" in command
    assert "1g" in command
    assert "--pids-limit" in command
    assert "256" in command
    assert "--label" in command
    assert "hyeve.run.id=devrun-123" in command


def test_execute_mutation_in_runtime_builds_missing_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(1, stderr="missing")
        if command[:4] == ["docker", "buildx", "build", "--load"]:
            return FakeCompleted(0, stdout="built")
        if command[:3] == ["docker", "image", "prune"]:
            return FakeCompleted(0, stdout="pruned")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "auto_build_image": True,
            "build_context": str(tmp_path),
            "dockerfile": str(dockerfile),
        },
    )
    assert any(cmd[:4] == ["docker", "buildx", "build", "--load"] for cmd in calls)
    assert any(cmd[:3] == ["docker", "image", "prune"] for cmd in calls)
    build_command = next(cmd for cmd in calls if cmd[:4] == ["docker", "buildx", "build", "--load"])
    assert "aix.managed=true" in build_command
    assert "aix.runtime=devexecutor" in build_command
    run_command = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "--rm"])
    assert "aix.managed=true" in run_command
    assert "aix.runtime=devexecutor" in run_command


def test_execute_mutation_in_runtime_rebuilds_when_context_signature_drifts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    helper = tmp_path / "mutate_workspace.py"
    helper.write_text("print('ok')\n", encoding="utf-8")

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(
                0,
                stdout=json.dumps(
                    {
                        "hyeve.runtime.family": "hyeve.devexecutor.mutation-runtime",
                        "hyeve.runtime.context_signature": "stale-signature",
                    }
                ),
            )
        if command[:4] == ["docker", "buildx", "build", "--load"]:
            return FakeCompleted(0, stdout="built")
        if command[:3] == ["docker", "image", "prune"]:
            return FakeCompleted(0, stdout="pruned")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "auto_build_image": True,
            "build_context": str(tmp_path),
            "dockerfile": str(dockerfile),
        },
    )

    build_commands = [cmd for cmd in calls if cmd[:4] == ["docker", "buildx", "build", "--load"]]
    assert len(build_commands) == 1
    assert "--label" in build_commands[0]
    assert any(str(item).startswith("hyeve.runtime.context_signature=") for item in build_commands[0])


def test_execute_mutation_in_runtime_reuses_image_when_context_signature_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    helper = tmp_path / "mutate_workspace.py"
    helper.write_text("print('ok')\n", encoding="utf-8")

    from backend.services.devexecutor_mutation_runtime_service import _expected_runtime_image_labels

    expected_labels = _expected_runtime_image_labels(
        runtime={
            "build_context": str(tmp_path),
            "dockerfile": str(dockerfile),
            "image_label_family": "hyeve.devexecutor.mutation-runtime",
        }
    )

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout=json.dumps(expected_labels))
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "auto_build_image": True,
            "build_context": str(tmp_path),
            "dockerfile": str(dockerfile),
        },
    )

    assert not any(cmd[:4] == ["docker", "buildx", "build", "--load"] for cmd in calls)


def test_execute_mutation_in_runtime_falls_back_from_absolute_docker_bin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout="[]")
        if command[:3] == ["docker", "run", "--rm"]:
            payload = json.loads(kwargs["input"])
            assert payload["tool"] == "write_file"
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._docker_command_exists",
        lambda candidate: candidate == "docker",
    )
    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "docker_bin": "/usr/bin/docker",
        },
    )

    assert result["operation"] == "write_file"
    assert calls[0][0] == "docker"


def test_execute_mutation_in_runtime_uses_build_context_as_helper_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")

    from backend.services.devexecutor_mutation_runtime_service import _expected_runtime_image_labels

    expected_labels = _expected_runtime_image_labels(
        runtime={
            "build_context": str(tmp_path),
            "dockerfile": str(dockerfile),
            "image_label_family": "hyeve.devexecutor.mutation-runtime",
        }
    )

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command: list[str], **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(0, stdout=json.dumps(expected_labels))
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout=json.dumps({"operation": "write_file", "changed_files": ["a.txt"]}))
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    execute_mutation_in_runtime(
        workspace_root=tmp_path,
        tool="write_file",
        args={"relative_path": "a.txt", "content": "x"},
        runtime={
            "mode": "docker",
            "container_required": True,
            "image": "runtime:latest",
            "build_context": str(tmp_path),
            "dockerfile": str(dockerfile),
        },
    )

    assert f"{tmp_path.resolve()}:/opt/devexecutor-runtime:ro" in calls[1]


def test_cleanup_managed_mutation_runtime_artifacts_prunes_only_managed_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)

    from backend.services.devexecutor_mutation_runtime_service import cleanup_managed_mutation_runtime_artifacts

    cleanup_managed_mutation_runtime_artifacts(runtime={"mode": "docker", "docker_bin": "docker"})

    assert calls[0][:3] == ["docker", "container", "prune"]
    assert "label=aix.managed=true" in calls[0]
    assert "label=aix.runtime=devexecutor" in calls[0]
    assert calls[1][:3] == ["docker", "image", "prune"]
    assert "-a" in calls[1]
    assert "label=aix.managed=true" in calls[1]
    assert "label=aix.runtime=devexecutor" in calls[1]


def test_execute_mutation_in_runtime_rejects_socket_backed_break_glass_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: {
            "cli_available": True,
            "daemon_reachable": True,
            "socket_backed": True,
            "transport": "unix_socket",
            "docker_host": "default",
            "socket_exists": True,
        },
    )
    with pytest.raises(MutationRuntimeError, match="SOCKET_BACKED_MUTATION_RUNTIME_DISALLOWED"):
        execute_mutation_in_runtime(
            workspace_root=tmp_path,
            tool="write_file",
            args={"relative_path": "a.txt", "content": "x"},
            runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
        )


def test_execute_mutation_in_runtime_fails_closed_when_isolated_runtime_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_runtime_service._runtime_transport_diagnostics",
        lambda **_kwargs: {
            "cli_available": True,
            "daemon_reachable": False,
            "socket_backed": False,
            "transport": "remote",
            "docker_host": "tcp://runtime.example:2376",
            "socket_exists": False,
        },
    )
    with pytest.raises(MutationRuntimeError, match="ISOLATED_MUTATION_RUNTIME_UNAVAILABLE"):
        execute_mutation_in_runtime(
            workspace_root=tmp_path,
            tool="write_file",
            args={"relative_path": "a.txt", "content": "x"},
            runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
        )


def test_docker_command_env_prepends_binary_directory() -> None:
    docker_bin = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    env = _docker_command_env(docker_bin)
    path_parts = str(env["PATH"]).split(";")
    assert r"C:\Program Files\Docker\Docker\resources\bin" in path_parts
