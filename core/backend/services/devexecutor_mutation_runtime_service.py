from __future__ import annotations

import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from backend.services.devexecutor_docker_hygiene_service import (
    docker_command_env as _shared_docker_command_env,
)
from backend.services.devexecutor_docker_hygiene_service import (
    merge_runtime_labels,
    prune_managed_runtime_artifacts,
)
from backend.services.devexecutor_docker_hygiene_service import (
    resolve_docker_bin as _shared_resolve_docker_bin,
)
from backend.services.devexecutor_runtime_path_service import resolve_runtime_workspace_mapping


class MutationRuntimeError(RuntimeError):
    pass


CONTAINER_WORKSPACE_ROOT = "/workspace"
CONTAINER_WORKSPACE_PATH_PREFIX = "/workspace"
CONTAINER_HELPER_ROOT = "/opt/devexecutor-runtime"
CONTAINER_HELPER_PATH = f"{CONTAINER_HELPER_ROOT}/mutate_workspace.py"
CONTAINER_WORKSPACE_BASE_COMMIT_ENV = "AIX_WORKSPACE_BASE_COMMIT_SHA"

# AIX autonomous lane changes must run through scripts/run_aix_lane_gauntlet.sh before deploy.


class MutationRuntimeGateway(Protocol):
    def execute(
        self,
        *,
        workspace_root: str | Path,
        tool: str,
        args: dict[str, Any],
        runtime: dict[str, Any] | None,
    ) -> dict[str, Any]:
        ...


class DockerMutationRuntimeGateway:
    def execute(
        self,
        *,
        workspace_root: str | Path,
        tool: str,
        args: dict[str, Any],
        runtime: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _execute_mutation_in_runtime_legacy(
            workspace_root=workspace_root,
            tool=tool,
            args=args,
            runtime=runtime,
        )


def build_mutation_runtime_gateway(*, runtime: dict[str, Any] | None = None) -> MutationRuntimeGateway:
    override = (runtime or {}).get("gateway") if isinstance(runtime, dict) else None
    if override is not None and hasattr(override, "execute"):
        return override
    return DockerMutationRuntimeGateway()


def execute_mutation_in_runtime(
    *,
    workspace_root: str | Path,
    tool: str,
    args: dict[str, Any],
    runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    gateway = build_mutation_runtime_gateway(runtime=runtime)
    return gateway.execute(
        workspace_root=workspace_root,
        tool=tool,
        args=args,
        runtime=runtime,
    )


def _execute_mutation_in_runtime_legacy(
    *,
    workspace_root: str | Path,
    tool: str,
    args: dict[str, Any],
    runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(runtime or {})
    mode = str(payload.get("mode") or "host").strip().lower()
    if mode not in {"host", "docker"}:
        raise MutationRuntimeError(f"Unsupported mutation runtime mode: {mode}")
    if bool(payload.get("container_required")) and mode != "docker":
        raise MutationRuntimeError("CONTAINER_MUTATION_RUNTIME_REQUIRED mode=docker")
    if mode == "host":
        raise MutationRuntimeError("HOST_MUTATION_RUNTIME_DISABLED")

    requested_docker_bin = str(payload.get("docker_bin") or "docker").strip() or "docker"
    docker_bin = _resolve_docker_bin(requested_docker_bin)
    image = str(payload.get("image") or "").strip()
    if not image:
        raise MutationRuntimeError("MUTATION_DOCKER_IMAGE_REQUIRED")
    diagnostics = _runtime_transport_diagnostics(
        requested_docker_bin=requested_docker_bin, resolved_docker_bin=docker_bin
    )
    if not diagnostics["cli_available"]:
        raise MutationRuntimeError(_docker_unavailable_message(requested_docker_bin, docker_bin))
    if diagnostics["socket_backed"] and not bool(payload.get("allow_socket_runtime")):
        raise MutationRuntimeError(
            _socket_backed_runtime_disallowed_message(requested_docker_bin, docker_bin, diagnostics)
        )
    if not diagnostics["daemon_reachable"]:
        raise MutationRuntimeError(_isolated_runtime_unavailable_message(requested_docker_bin, docker_bin, diagnostics))

    mapping = resolve_runtime_workspace_mapping(workspace_root=workspace_root, runtime=payload)
    _enforce_managed_proposal_workspace_runtime_isolation(
        workspace_root=workspace_root,
        runtime=payload,
        mapping=mapping,
    )
    _ensure_image_available(runtime=payload, docker_bin=docker_bin, image=image)
    mount_spec = f"{mapping['mount_source']}:{mapping['container_mount_root']}"
    passthrough_mounts = _linked_worktree_passthrough_mounts(workspace_root=workspace_root)
    helper_root_value = (
        str(payload.get("helper_root") or "").strip()
        or str(payload.get("build_context") or "").strip()
        or str(Path(__file__).resolve().parent.parent / "docker" / "devexecutor-runtime")
    )
    helper_root = Path(helper_root_value).resolve()
    helper_mount = f"{helper_root}:{CONTAINER_HELPER_ROOT}:ro"
    command: list[str] = [
        docker_bin,
        "run",
        "--rm",
        "-i",
        "-e",
        f"WORKSPACE_ROOT={mapping['container_workspace_root']}",
        "-e",
        f"WORKSPACE_PATH_PREFIX={mapping['container_workspace_root']}",
        "--workdir",
        mapping["container_workspace_root"],
        "-v",
        mount_spec,
        "-v",
        helper_mount,
    ]
    for extra_mount in passthrough_mounts:
        command.extend(["-v", extra_mount])
    workspace_base_commit_sha = str(payload.get("workspace_base_commit_sha") or "").strip()
    if workspace_base_commit_sha:
        command.extend(["-e", f"{CONTAINER_WORKSPACE_BASE_COMMIT_ENV}={workspace_base_commit_sha}"])
    if bool(payload.get("read_only_rootfs")):
        command.append("--read-only")
        tmpfs_size = str(payload.get("tmpfs_size") or "").strip()
        tmpfs_value = "/tmp"
        if tmpfs_size:
            tmpfs_value = f"/tmp:rw,exec,nosuid,size={tmpfs_size}"
        command.extend(["--tmpfs", tmpfs_value])
    network = str(payload.get("network") or "").strip()
    if network:
        command.extend(["--network", network])
    cpus = str(payload.get("cpus") or "").strip()
    if cpus:
        command.extend(["--cpus", cpus])
    memory = str(payload.get("memory") or "").strip()
    if memory:
        command.extend(["--memory", memory])
    pids_limit = payload.get("pids_limit")
    if pids_limit is not None and str(pids_limit).strip():
        command.extend(["--pids-limit", str(pids_limit)])
    labels = merge_runtime_labels(
        payload.get("labels") if isinstance(payload.get("labels"), dict) else None,
        family=str(payload.get("image_label_family") or "").strip() or "hyeve.devexecutor.mutation-runtime",
        runtime_kind="mutation",
    )
    if labels:
        for key, value in labels.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            command.extend(["--label", f"{normalized_key}={value}"])
    extra_args = payload.get("extra_args")
    if isinstance(extra_args, list):
        command.extend([str(item) for item in extra_args if str(item).strip()])
    command.extend([image, "python", CONTAINER_HELPER_PATH])

    proc = _run_docker_command(
        command,
        input=json.dumps({"tool": tool, "args": dict(args or {})}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"docker exited {proc.returncode}"
        raise MutationRuntimeError(f"MUTATION_RUNTIME_FAILED tool={tool} detail={detail}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MutationRuntimeError(
            f"MUTATION_RUNTIME_INVALID_JSON tool={tool} stdout={(proc.stdout or '').strip()}"
        ) from exc


def _enforce_managed_proposal_workspace_runtime_isolation(
    *,
    workspace_root: str | Path,
    runtime: dict[str, Any],
    mapping: dict[str, str],
) -> None:
    configured_host_root = str(runtime.get("host_workspace_root") or "").strip()
    if not configured_host_root:
        return
    try:
        host_root = Path(configured_host_root).resolve()
        workspace_path = Path(workspace_root).resolve()
        mount_source = Path(str(mapping.get("mount_source") or "")).resolve()
    except OSError:
        return
    if not (host_root / ".git").exists():
        return
    if mount_source != host_root:
        return
    try:
        workspace_path.relative_to(host_root)
    except ValueError:
        return
    if _is_managed_proposal_workspace(workspace_path):
        return
    raise MutationRuntimeError(
        "HOST_CHECKOUT_MUTATION_RUNTIME_REQUIRES_MANAGED_PROPOSAL_WORKSPACE "
        f"workspace_root={workspace_path} host_workspace_root={host_root}"
    )


def _linked_worktree_passthrough_mounts(*, workspace_root: str | Path) -> list[str]:
    root = Path(workspace_root).resolve()
    metadata_path = root / ".devexecutor_proposal_workspace.json"
    if not metadata_path.exists():
        return []
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []
    checkout_root = Path(str(payload.get("checkout_root") or "")).resolve()
    repo_root = Path(str(payload.get("repo_root") or "")).resolve()
    if not checkout_root.is_absolute() or not repo_root.is_absolute():
        return []
    if not str(checkout_root).startswith("/") or not str(repo_root).startswith("/"):
        return []
    repo_git_root = repo_root / ".git"
    mounts: list[str] = []
    if checkout_root.exists():
        mounts.append(f"{checkout_root}:{checkout_root}")
    if repo_git_root.exists():
        mounts.append(f"{repo_git_root}:{repo_git_root}")
    return mounts


def _is_managed_proposal_workspace(workspace_root: Path) -> bool:
    metadata_path = workspace_root / ".devexecutor_proposal_workspace.json"
    if not metadata_path.exists():
        return False
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return isinstance(payload, dict)


def _ensure_image_available(*, runtime: dict[str, Any], docker_bin: str, image: str) -> None:
    build_context = str(runtime.get("build_context") or "").strip()
    dockerfile = str(runtime.get("dockerfile") or "").strip()
    image_labels = _expected_runtime_image_labels(runtime=runtime)
    inspect = _run_docker_command(
        [docker_bin, "image", "inspect", image, "--format", "{{json .Config.Labels}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if inspect.returncode == 0 and (
        not image_labels or _image_labels_match(inspect.stdout, expected_labels=image_labels)
    ):
        return
    if not bool(runtime.get("auto_build_image")):
        raise MutationRuntimeError(f"MUTATION_DOCKER_IMAGE_MISSING image={image}")
    if not build_context or not dockerfile:
        raise MutationRuntimeError(f"MUTATION_DOCKER_IMAGE_MISSING image={image}")
    context_path = Path(build_context).resolve()
    dockerfile_path = Path(dockerfile).resolve()
    if not context_path.exists():
        raise MutationRuntimeError(f"MUTATION_DOCKER_BUILD_CONTEXT_MISSING path={context_path}")
    if not dockerfile_path.exists():
        raise MutationRuntimeError(f"MUTATION_DOCKERFILE_MISSING path={dockerfile_path}")
    build_command = [docker_bin, "buildx", "build", "--load", "-t", image]
    for key, value in image_labels.items():
        build_command.extend(["--label", f"{key}={value}"])
    build_command.extend(["-f", str(dockerfile_path), str(context_path)])
    build = _run_docker_command(
        build_command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if build.returncode != 0:
        detail = (
            (build.stderr or "").strip()
            or (build.stdout or "").strip()
            or f"docker buildx build exited {build.returncode}"
        )
        raise MutationRuntimeError(f"MUTATION_DOCKER_BUILD_FAILED image={image} detail={detail}")
    if bool(runtime.get("prune_dangling_images", True)):
        _prune_dangling_runtime_images(
            docker_bin=docker_bin,
            family=str(runtime.get("image_label_family") or "").strip(),
        )


def _expected_runtime_image_labels(*, runtime: dict[str, Any]) -> dict[str, str]:
    build_context = str(runtime.get("build_context") or "").strip()
    dockerfile = str(runtime.get("dockerfile") or "").strip()
    if not build_context or not dockerfile:
        return {}
    family = str(runtime.get("image_label_family") or "").strip() or "hyeve.devexecutor.mutation-runtime"
    signature = _compute_build_context_signature(
        build_context=build_context,
        dockerfile=dockerfile,
    )
    labels = merge_runtime_labels({}, family=family, runtime_kind="mutation")
    labels["hyeve.runtime.context_signature"] = signature
    return labels


def _image_labels_match(raw_labels: str, *, expected_labels: dict[str, str]) -> bool:
    try:
        parsed = json.loads(raw_labels or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    for key, value in expected_labels.items():
        if str(parsed.get(key) or "") != value:
            return False
    return True


def _compute_build_context_signature(*, build_context: str, dockerfile: str) -> str:
    context_path = Path(build_context).resolve()
    dockerfile_path = Path(dockerfile).resolve()
    digest = sha256()
    digest.update(
        str(
            dockerfile_path.relative_to(context_path)
            if dockerfile_path.is_relative_to(context_path)
            else dockerfile_path.name
        ).encode("utf-8")
    )
    digest.update(b"\0")
    for path in sorted(_iter_context_files(context_path=context_path, dockerfile_path=dockerfile_path)):
        rel = path.relative_to(context_path).as_posix() if path.is_relative_to(context_path) else path.name
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _iter_context_files(*, context_path: Path, dockerfile_path: Path) -> list[Path]:
    files: list[Path] = []
    if dockerfile_path.exists():
        files.append(dockerfile_path)
    if not context_path.exists():
        return files
    skip_names = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    for path in context_path.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts.intersection(skip_names):
            continue
        files.append(path)
    deduped: dict[str, Path] = {}
    for path in files:
        deduped[str(path.resolve())] = path
    return list(deduped.values())


def _prune_dangling_runtime_images(*, docker_bin: str, family: str) -> None:
    command = [docker_bin, "image", "prune", "-f"]
    if family:
        command.extend(["--filter", f"label=hyeve.runtime.family={family}"])
    _run_docker_command(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _docker_available(docker_bin: str) -> bool:
    try:
        probe = _run_docker_command(
            [docker_bin, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
        )
        return int(probe.returncode) == 0
    except Exception:
        return False


def _docker_daemon_available(docker_bin: str) -> bool:
    try:
        probe = _run_docker_command(
            [docker_bin, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
        )
        return int(probe.returncode) == 0
    except Exception:
        return False


def _docker_buildx_available(docker_bin: str) -> bool:
    try:
        probe = _run_docker_command(
            [docker_bin, "buildx", "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
        )
        return int(probe.returncode) == 0
    except Exception:
        return False


def _docker_bin_candidates(docker_bin: str) -> list[str]:
    requested = str(docker_bin or "").strip() or "docker"
    candidates = [requested]
    requested_path = Path(requested)
    if requested_path.name and requested_path.name != requested:
        candidates.append(requested_path.name)
    if "docker" not in candidates:
        candidates.append("docker")
    deduped: list[str] = []
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _docker_command_exists(docker_bin: str) -> bool:
    candidate = str(docker_bin or "").strip()
    if not candidate:
        return False
    path = Path(candidate)
    if path.is_absolute():
        return path.exists()
    return shutil.which(candidate) is not None


def _resolve_docker_bin(docker_bin: str) -> str:
    return _shared_resolve_docker_bin(docker_bin)


def _docker_unavailable_message(requested_docker_bin: str, resolved_docker_bin: str) -> str:
    requested = str(requested_docker_bin or "").strip() or "docker"
    resolved = str(resolved_docker_bin or "").strip() or "docker"
    which_docker = shutil.which("docker") or "missing"
    socket_exists = Path("/var/run/docker.sock").exists()
    requested_exists = _docker_command_exists(requested)
    resolved_exists = _docker_command_exists(resolved)
    return (
        "DOCKER_UNAVAILABLE "
        f"requested={requested} "
        f"docker_bin={resolved} "
        f"requested_exists={str(requested_exists).lower()} "
        f"resolved_exists={str(resolved_exists).lower()} "
        f"which_docker={which_docker} "
        f"socket_exists={str(socket_exists).lower()}"
    )


def _runtime_transport_diagnostics(*, requested_docker_bin: str, resolved_docker_bin: str) -> dict[str, Any]:
    docker_host = str(os.getenv("DOCKER_HOST") or "").strip()
    socket_exists = Path("/var/run/docker.sock").exists()
    uses_unix_socket = docker_host.startswith("unix://") or (not docker_host and socket_exists)
    return {
        "requested": str(requested_docker_bin or "").strip() or "docker",
        "resolved": str(resolved_docker_bin or "").strip() or "docker",
        "requested_exists": _docker_command_exists(requested_docker_bin),
        "resolved_exists": _docker_command_exists(resolved_docker_bin),
        "which_docker": shutil.which("docker") or "missing",
        "docker_host": docker_host or "default",
        "socket_exists": socket_exists,
        "socket_backed": uses_unix_socket,
        "transport": ("unix_socket" if uses_unix_socket else ("remote" if docker_host else "unknown")),
        "cli_available": _docker_available(resolved_docker_bin),
        "daemon_reachable": _docker_daemon_available(resolved_docker_bin),
        "buildx_available": _docker_buildx_available(resolved_docker_bin),
    }


def _socket_backed_runtime_disallowed_message(
    requested_docker_bin: str, resolved_docker_bin: str, diagnostics: dict[str, Any]
) -> str:
    requested = str(requested_docker_bin or "").strip() or "docker"
    resolved = str(resolved_docker_bin or "").strip() or "docker"
    return (
        "SOCKET_BACKED_MUTATION_RUNTIME_DISALLOWED "
        f"requested={requested} "
        f"docker_bin={resolved} "
        f"transport={diagnostics.get('transport')} "
        f"docker_host={diagnostics.get('docker_host')} "
        f"socket_exists={str(bool(diagnostics.get('socket_exists'))).lower()} "
        "set allow_socket_runtime=true only for explicit break-glass use"
    )


def _isolated_runtime_unavailable_message(
    requested_docker_bin: str, resolved_docker_bin: str, diagnostics: dict[str, Any]
) -> str:
    requested = str(requested_docker_bin or "").strip() or "docker"
    resolved = str(resolved_docker_bin or "").strip() or "docker"
    return (
        "ISOLATED_MUTATION_RUNTIME_UNAVAILABLE "
        f"requested={requested} "
        f"docker_bin={resolved} "
        f"transport={diagnostics.get('transport')} "
        f"docker_host={diagnostics.get('docker_host')} "
        f"cli_available={str(bool(diagnostics.get('cli_available'))).lower()} "
        f"daemon_reachable={str(bool(diagnostics.get('daemon_reachable'))).lower()}"
    )


def _docker_command_env(docker_bin: str) -> dict[str, str]:
    return _shared_docker_command_env(docker_bin)


def _run_docker_command(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    docker_bin = str(command[0] if command else "docker")
    if "env" not in kwargs:
        kwargs["env"] = _docker_command_env(docker_bin)
    return subprocess.run(command, **kwargs)


def cleanup_managed_mutation_runtime_artifacts(*, runtime: dict[str, Any] | None) -> None:
    payload = dict(runtime or {})
    if str(payload.get("mode") or "host").strip().lower() != "docker":
        return
    docker_bin = _resolve_docker_bin(str(payload.get("docker_bin") or "docker").strip() or "docker")
    prune_managed_runtime_artifacts(docker_bin=docker_bin)


__all__ = ["MutationRuntimeError", "execute_mutation_in_runtime", "cleanup_managed_mutation_runtime_artifacts"]
