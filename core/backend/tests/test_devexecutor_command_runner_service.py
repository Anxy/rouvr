from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.devexecutor_command_runner_service import CommandPolicyError, CommandRuntimeError, run_command


def _remote_runtime_diag() -> dict[str, object]:
    return {
        "cli_available": True,
        "daemon_reachable": True,
        "socket_backed": False,
        "transport": "remote",
        "docker_host": "tcp://runtime.example:2376",
        "socket_exists": False,
    }


def test_run_command_allows_whitelisted_prefix(tmp_path: Path) -> None:
    events: list[str] = []

    def hook(evt: str, _payload: dict) -> None:
        events.append(evt)

    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        allow_prefixes=[[sys.executable], ["git"]],
        timeout_seconds=5.0,
        policy_hook=hook,
    )
    assert result["returncode"] == 0
    assert "ok" in result["stdout"]
    assert result["timed_out"] is False
    assert result["attempts"] == 1
    assert result["retry_count"] == 0
    assert result["terminal_failure_class"] is None
    assert result["policy_diagnostics"]["policy_source"] == "allow_prefixes"
    assert "resource_snapshot" in result
    assert "command_allowed" in events
    assert "command_completed" in events


def test_run_command_denies_non_whitelisted_command(tmp_path: Path) -> None:
    with pytest.raises(CommandPolicyError, match="COMMAND_DENIED"):
        run_command(
            workspace_root=tmp_path,
            command=["whoami"],
            allow_prefixes=[["git"]],
            timeout_seconds=2.0,
        )


def test_run_command_supports_exact_rule_mode(tmp_path: Path) -> None:
    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        allow_rules=[{"mode": "exact", "argv": [sys.executable, "-c", "print('ok')"]}],
        timeout_seconds=5.0,
    )
    assert result["returncode"] == 0
    assert result["policy_diagnostics"]["policy_source"] == "allow_rules"


def test_run_command_rejects_malformed_allowlist_policy(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_command(
            workspace_root=tmp_path,
            command=[sys.executable, "-c", "print('ok')"],
            allow_rules=[{"mode": "badmode", "argv": []}],
            timeout_seconds=1.0,
        )


def test_run_command_denies_shell_escape_tokens(tmp_path: Path) -> None:
    with pytest.raises(CommandPolicyError, match="SHELL_ESCAPE_DENIED"):
        run_command(
            workspace_root=tmp_path,
            command=["cmd", "/c", "echo", "ok", "&&", "whoami"],
            allow_prefixes=[["cmd"]],
            timeout_seconds=1.0,
        )


def test_run_command_timeout_behavior(tmp_path: Path) -> None:
    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "import time; time.sleep(1.2); print('done')"],
        allow_prefixes=[[sys.executable]],
        timeout_seconds=0.1,
    )
    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert result["terminal_failure_class"] == "timeout"


def test_run_command_can_resolve_policy_from_repo_profile(tmp_path: Path) -> None:
    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        repo_key="hyeve-core",
        explicit_profile_id="RP-HYEVE-PY-FASTAPI-CURRENT",
        profiles_dir="projects/AIX/hyeve-aix-service/backend/config/repo_profiles",
        timeout_seconds=5.0,
    )
    assert result["returncode"] == 0
    assert result["policy_diagnostics"]["policy_source"] == "repo_profile_resolver"


def test_run_command_timeout_retry_envelope(tmp_path: Path) -> None:
    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "import time; time.sleep(0.2)"],
        allow_prefixes=[[sys.executable]],
        timeout_seconds=0.02,
        max_retries=2,
        retry_on_timeout=True,
    )
    assert result["timed_out"] is True
    assert result["attempts"] == 3
    assert result["retry_count"] == 2
    assert result["retry_strategy"] == "timeout_retry"
    assert len(result["attempt_logs"]) == 3


def test_run_command_nonzero_retry_envelope(tmp_path: Path) -> None:
    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        allow_prefixes=[[sys.executable]],
        timeout_seconds=1.0,
        max_retries=2,
        retry_on_nonzero=True,
        retryable_returncodes=[3],
    )
    assert result["timed_out"] is False
    assert result["returncode"] == 3
    assert result["attempts"] == 3
    assert result["retry_count"] == 2
    assert result["retry_strategy"] == "nonzero_retry"
    assert result["terminal_failure_class"] == "nonzero_exit"


def test_run_command_runtime_container_required_blocks_host(tmp_path: Path) -> None:
    with pytest.raises(CommandRuntimeError, match="CONTAINER_RUNTIME_REQUIRED"):
        run_command(
            workspace_root=tmp_path,
            command=[sys.executable, "-c", "print('ok')"],
            allow_prefixes=[[sys.executable]],
            runtime={"mode": "host", "container_required": True},
        )


def test_run_command_runtime_docker_wraps_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "wrapped\n"
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        allow_prefixes=[[sys.executable]],
        runtime={"mode": "docker", "image": "hyeve-aix-devexecutor-runtime:latest"},
    )
    assert result["returncode"] == 0
    assert result["runtime"]["mode"] == "docker"
    assert isinstance(result["outer_command"], list)
    assert result["outer_command"][0] == "docker"
    assert result["outer_command"][1:4] == ["run", "--rm", "--workdir"]
    assert result["outer_command"][-3] == "python"
    assert calls[0][0] == "docker"
    assert "hyeve-aix-devexecutor-runtime:latest" in calls[0]


def test_run_command_runtime_docker_applies_isolation_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "wrapped\n"
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        allow_prefixes=[[sys.executable]],
        runtime={
            "mode": "docker",
            "image": "hyeve-aix-devexecutor-runtime:latest",
            "network": "none",
            "cpus": "1.0",
            "memory": "1g",
            "pids_limit": 256,
            "read_only_rootfs": True,
            "tmpfs_size": "512m",
            "labels": {"hyeve.run.id": "devrun-123"},
        },
    )

    command = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "--rm"])
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


def test_run_command_runtime_docker_builds_missing_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: list[list[str]] = []
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return FakeCompleted(1, stderr="missing")
        if command[:4] == ["docker", "buildx", "build", "--load"]:
            return FakeCompleted(0, stdout="built")
        if command[:3] == ["docker", "image", "prune"]:
            return FakeCompleted(0, stdout="pruned")
        if command[:3] == ["docker", "run", "--rm"]:
            return FakeCompleted(0, stdout="wrapped\n")
        raise AssertionError(command)

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        allow_prefixes=[[sys.executable]],
        runtime={
            "mode": "docker",
            "image": "hyeve-aix-devexecutor-runtime:v2",
            "auto_build_image": True,
            "build_context": str(tmp_path),
            "dockerfile": str(dockerfile),
        },
    )

    assert result["returncode"] == 0
    assert any(cmd[:4] == ["docker", "buildx", "build", "--load"] for cmd in calls)
    assert any(cmd[:3] == ["docker", "image", "prune"] for cmd in calls)
    build_command = next(cmd for cmd in calls if cmd[:4] == ["docker", "buildx", "build", "--load"])
    assert "aix.managed=true" in build_command
    assert "aix.runtime=devexecutor" in build_command
    run_docker_command = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "--rm"])
    assert "aix.managed=true" in run_docker_command
    assert "aix.runtime=devexecutor" in run_docker_command


def test_run_command_runtime_docker_fails_when_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: {
            "cli_available": False,
            "daemon_reachable": False,
            "socket_backed": False,
            "transport": "unknown",
            "docker_host": "default",
            "socket_exists": False,
        },
    )
    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._docker_command_exists", lambda _bin: False
    )
    with pytest.raises(CommandRuntimeError, match=r"DOCKER_UNAVAILABLE .*which_docker="):
        run_command(
            workspace_root=tmp_path,
            command=[sys.executable, "-c", "print('ok')"],
            allow_prefixes=[[sys.executable]],
            runtime={"mode": "docker", "image": "hyeve-aix-devexecutor-runtime:latest"},
        )


def test_run_command_runtime_docker_rejects_socket_backed_break_glass_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: {
            "cli_available": True,
            "daemon_reachable": True,
            "socket_backed": True,
            "transport": "unix_socket",
            "docker_host": "default",
            "socket_exists": True,
        },
    )
    with pytest.raises(CommandRuntimeError, match="SOCKET_BACKED_RUNTIME_DISALLOWED"):
        run_command(
            workspace_root=tmp_path,
            command=[sys.executable, "-c", "print('ok')"],
            allow_prefixes=[[sys.executable]],
            runtime={"mode": "docker", "image": "hyeve-aix-devexecutor-runtime:latest"},
        )


def test_run_command_runtime_docker_fails_closed_when_isolated_runtime_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: {
            "cli_available": True,
            "daemon_reachable": False,
            "socket_backed": False,
            "transport": "remote",
            "docker_host": "tcp://runtime.example:2376",
            "socket_exists": False,
        },
    )
    with pytest.raises(CommandRuntimeError, match="ISOLATED_RUNTIME_UNAVAILABLE"):
        run_command(
            workspace_root=tmp_path,
            command=[sys.executable, "-c", "print('ok')"],
            allow_prefixes=[[sys.executable]],
            runtime={"mode": "docker", "image": "hyeve-aix-devexecutor-runtime:latest"},
        )


def test_run_command_runtime_docker_falls_back_from_absolute_docker_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "wrapped\n"
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._docker_command_exists",
        lambda candidate: candidate == "docker",
    )
    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        allow_prefixes=[[sys.executable]],
        runtime={"mode": "docker", "image": "hyeve-aix-devexecutor-runtime:latest", "docker_bin": "/usr/bin/docker"},
    )

    assert result["returncode"] == 0
    assert calls[0][0] == "docker"


def test_run_command_runtime_docker_remaps_workspace_absolute_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    script_path = tmp_path / "scripts" / "demo.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_command(
        workspace_root=tmp_path,
        command=[sys.executable, str(script_path)],
        allow_prefixes=[[sys.executable]],
        runtime={"mode": "docker", "image": "hyeve-aix-devexecutor-runtime:latest"},
    )
    command = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "--rm"])
    assert "/workspace/scripts/demo.py" in command


def test_run_command_runtime_docker_maps_subdir_workspace_to_configured_container_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    workspace_root = tmp_path / "services" / "hyeve-codeintel-service" / "backend"
    workspace_root.mkdir(parents=True)
    script_path = workspace_root / "demo.py"
    script_path.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_command(
        workspace_root=workspace_root,
        command=[sys.executable, str(script_path)],
        allow_prefixes=[[sys.executable]],
        runtime={
            "mode": "docker",
            "image": "hyeve-aix-devexecutor-runtime:latest",
            "host_workspace_root": str(tmp_path),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    command = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "--rm"])
    assert "/workspace/hyeve/services/hyeve-codeintel-service/backend" in command
    assert "/workspace/hyeve/services/hyeve-codeintel-service/backend/demo.py" in command


def test_run_command_runtime_docker_reanchors_container_workspace_to_host_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    host_root = tmp_path / "repos" / "hyeve"
    workspace_root = host_root / "projects" / "AIX" / "hyeve-aix-service" / "backend"
    workspace_root.mkdir(parents=True)
    script_path = workspace_root / "demo.py"
    script_path.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_command(
        workspace_root="/workspace/hyeve/projects/AIX/hyeve-aix-service/backend",
        command=[sys.executable, "/workspace/hyeve/projects/AIX/hyeve-aix-service/backend/demo.py"],
        allow_prefixes=[[sys.executable]],
        runtime={
            "mode": "docker",
            "image": "hyeve-aix-devexecutor-runtime:latest",
            "host_workspace_root": str(host_root),
            "container_workspace_root": "/workspace/hyeve",
        },
    )

    command = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "--rm"])
    mount_index = command.index("-v")
    assert command[mount_index + 1] == f"{workspace_root.resolve()}:/workspace/hyeve"
    assert "/workspace/hyeve/projects/AIX/hyeve-aix-service/backend/demo.py" in command


def test_run_command_applies_env_overrides_in_host_mode(tmp_path: Path) -> None:
    result = run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "import os; print(os.getenv('DATABASE_URL', 'missing'))"],
        allow_prefixes=[[sys.executable]],
        runtime={"mode": "host", "env_overrides": {"DATABASE_URL": "postgres://example/db"}},
        timeout_seconds=5.0,
    )
    assert result["returncode"] == 0
    assert "postgres://example/db" in result["stdout"]


def test_run_command_runtime_docker_passes_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    monkeypatch.setattr(
        "backend.services.devexecutor_command_runner_service._runtime_transport_diagnostics",
        lambda **_kwargs: _remote_runtime_diag(),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_command(
        workspace_root=tmp_path,
        command=[sys.executable, "-c", "print('ok')"],
        allow_prefixes=[[sys.executable]],
        runtime={
            "mode": "docker",
            "image": "hyeve-aix-devexecutor-runtime:latest",
            "env_overrides": {"DATABASE_URL": "postgres://example/db"},
        },
    )
    command = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "--rm"])
    assert "-e" in command
    assert "DATABASE_URL=postgres://example/db" in command


def test_cleanup_managed_command_runtime_artifacts_prunes_only_managed_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(list(command))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)

    from backend.services.devexecutor_command_runner_service import cleanup_managed_command_runtime_artifacts

    cleanup_managed_command_runtime_artifacts(runtime={"mode": "docker", "docker_bin": "docker"})

    assert calls[0][:3] == ["docker", "container", "prune"]
    assert "label=aix.managed=true" in calls[0]
    assert "label=aix.runtime=devexecutor" in calls[0]
    assert calls[1][:3] == ["docker", "image", "prune"]
    assert "-a" in calls[1]
    assert "label=aix.managed=true" in calls[1]
    assert "label=aix.runtime=devexecutor" in calls[1]
