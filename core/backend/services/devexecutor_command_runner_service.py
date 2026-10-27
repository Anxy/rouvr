from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any

from backend.models.command_allowlist_models import AllowlistRuleCurrent, validate_command_allowlist_profile
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
from backend.services.devexecutor_readonly_tools_service import _safe_resolve
from backend.services.devexecutor_runtime_path_service import (
    map_host_path_to_container,
    resolve_runtime_workspace_mapping,
)
from backend.services.repo_profile_resolver_service import resolve_effective_repo_profile

PolicyHook = Callable[[str, dict[str, Any]], None]


class CommandPolicyError(PermissionError):
    pass


class CommandRuntimeError(RuntimeError):
    pass


def _emit(hook: PolicyHook | None, event: str, payload: dict[str, Any]) -> None:
    if hook is None:
        return
    hook(event, payload)


def _normalize_subprocess_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _merge_env_overrides(env_overrides: dict[str, Any] | None) -> dict[str, str]:
    merged = dict(os.environ)
    for key, value in dict(env_overrides or {}).items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        merged[normalized_key] = str(value)
    return merged


def _contains_shell_escape(command: list[str]) -> bool:
    shell_bins = {"sh", "bash", "cmd", "powershell", "pwsh"}
    first = Path(command[0]).stem.lower()
    if first not in shell_bins:
        return any(token in {";", "&&", "||", "|", ">", "<"} for token in command)
    markers = (";", "&&", "||", "|", "`", "$(", ">", "<")
    return any(any(marker in token for marker in markers) for token in command[1:])


def _validate_rules(allow_rules: list[dict[str, Any]]) -> list[AllowlistRuleCurrent]:
    payload = {
        "schema_version": "command_allowlist.current",
        "profile_id": "runtime",
        "rules": allow_rules,
    }
    profile = validate_command_allowlist_profile(payload)
    return list(profile.rules)


def _resolve_rules(
    *,
    allow_prefixes: list[list[str]] | None,
    allow_rules: list[dict[str, Any]] | None,
    repo_key: str | None,
    explicit_profile_id: str | None,
    profiles_dir: str | Path | None,
    profile_overlay: dict[str, Any] | None,
) -> tuple[list[AllowlistRuleCurrent], dict[str, Any]]:
    if allow_rules is not None:
        return _validate_rules(allow_rules), {"policy_source": "allow_rules"}
    if allow_prefixes is not None:
        rules = [{"mode": "prefix", "argv": list(prefix)} for prefix in allow_prefixes]
        return _validate_rules(rules), {"policy_source": "allow_prefixes"}
    if repo_key and profiles_dir:
        profile, diag = resolve_effective_repo_profile(
            repo_key=repo_key,
            explicit_profile_id=explicit_profile_id,
            profiles_dir=profiles_dir,
            overlay=profile_overlay,
        )
        rules = [{"mode": "prefix", "argv": list(prefix)} for prefix in profile.command_policy.allow_prefixes]
        return _validate_rules(rules), {
            "policy_source": "repo_profile_resolver",
            "repo_profile_id": profile.profile_id,
            "resolver_diagnostics": diag,
        }
    raise ValueError("ALLOWLIST_POLICY_REQUIRED provide allow_rules/allow_prefixes or repo resolver inputs")


def _match_rule(command: list[str], rules: list[AllowlistRuleCurrent]) -> AllowlistRuleCurrent | None:
    def token_eq(actual: str, expected: str, index: int) -> bool:
        if actual == expected:
            return True
        if index == 0 and "\\" not in expected and "/" not in expected:
            return Path(actual).stem.lower() == Path(expected).stem.lower()
        return False

    def prefix_match(actual: list[str], expected: list[str]) -> bool:
        if len(actual) < len(expected):
            return False
        return all(token_eq(actual[i], expected[i], i) for i in range(len(expected)))

    def exact_match(actual: list[str], expected: list[str]) -> bool:
        if len(actual) != len(expected):
            return False
        return all(token_eq(actual[i], expected[i], i) for i in range(len(expected)))

    for rule in rules:
        if rule.mode == "prefix":
            if prefix_match(command, rule.argv):
                return rule
        elif rule.mode == "exact":
            if exact_match(command, rule.argv):
                return rule
    return None


def run_command(
    *,
    workspace_root: str | Path | None = None,
    command: list[str],
    allow_prefixes: list[list[str]] | None = None,
    allow_rules: list[dict[str, Any]] | None = None,
    repo_key: str | None = None,
    explicit_profile_id: str | None = None,
    profiles_dir: str | Path | None = None,
    profile_overlay: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
    max_retries: int = 0,
    retry_on_timeout: bool = True,
    retry_on_nonzero: bool = False,
    retryable_returncodes: list[int] | None = None,
    policy_hook: PolicyHook | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = _safe_resolve(workspace_root, ".")
    if not command:
        raise ValueError("Empty command is not allowed.")
    if _contains_shell_escape(command):
        _emit(policy_hook, "command_denied", {"command": command, "reason": "SHELL_ESCAPE_DENIED"})
        raise CommandPolicyError(f"SHELL_ESCAPE_DENIED command={command}")

    rules, policy_diag = _resolve_rules(
        allow_prefixes=allow_prefixes,
        allow_rules=allow_rules,
        repo_key=repo_key,
        explicit_profile_id=explicit_profile_id,
        profiles_dir=profiles_dir,
        profile_overlay=profile_overlay,
    )
    matched_rule = _match_rule(command, rules)
    if matched_rule is None:
        _emit(
            policy_hook,
            "command_denied",
            {"command": command, "allow_rules": [r.model_dump(mode="python") for r in rules], **policy_diag},
        )
        raise CommandPolicyError(f"COMMAND_DENIED command={command} policy_source={policy_diag.get('policy_source')}")

    _emit(
        policy_hook,
        "command_allowed",
        {
            "command": command,
            "timeout_seconds": timeout_seconds,
            "matched_rule": matched_rule.model_dump(mode="python"),
            **policy_diag,
        },
    )
    retries_allowed = max(0, int(max_retries))
    retry_set = {int(code) for code in list(retryable_returncodes or [])}
    attempts_total = retries_allowed + 1
    attempt_logs: list[dict[str, Any]] = []
    last_timeout_stdout = ""
    last_timeout_stderr = ""
    resource_snapshot = _resource_snapshot()
    runtime_payload = dict(runtime or {})
    mode = str(runtime_payload.get("mode") or "host").strip().lower()
    if mode not in {"host", "docker"}:
        raise CommandRuntimeError(f"Unsupported runtime mode: {mode}")
    if bool(runtime_payload.get("container_required")) and mode != "docker":
        raise CommandRuntimeError("CONTAINER_RUNTIME_REQUIRED mode=docker")

    outer_command: list[str] | None = None
    outer_cwd = str(root)
    process_env = _merge_env_overrides(runtime_payload.get("env_overrides"))
    if mode == "docker":
        outer_command, outer_cwd = _build_docker_command(
            workspace_root=root,
            raw_workspace_root=workspace_root,
            command=command,
            runtime=runtime_payload,
        )

    for attempt_index in range(attempts_total):
        started = monotonic()
        try:
            proc = subprocess.run(
                outer_command or command,
                cwd=outer_cwd,
                env=process_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = int((monotonic() - started) * 1000)
            last_timeout_stdout = _normalize_subprocess_text(exc.stdout)
            last_timeout_stderr = _normalize_subprocess_text(exc.stderr)
            attempt_logs.append(
                {
                    "attempt": attempt_index + 1,
                    "timed_out": True,
                    "returncode": None,
                    "elapsed_ms": elapsed_ms,
                    "resource_snapshot": resource_snapshot,
                }
            )
            _emit(
                policy_hook,
                "command_timeout",
                {"command": command, "timeout_seconds": timeout_seconds, "attempt": attempt_index + 1},
            )
            if retry_on_timeout and attempt_index < retries_allowed:
                _retry_sleep(attempt_index=attempt_index)
                continue
            return {
                "command": command,
                "cwd": str(root),
                "timeout_seconds": timeout_seconds,
                "timed_out": True,
                "returncode": None,
                "stdout": last_timeout_stdout,
                "stderr": last_timeout_stderr,
                "attempts": attempt_index + 1,
                "retry_count": attempt_index,
                "retry_strategy": "timeout_retry" if retries_allowed > 0 else "none",
                "terminal_failure_class": "timeout",
                "attempt_logs": attempt_logs,
                "policy_diagnostics": policy_diag,
                "resource_snapshot": resource_snapshot,
                "runtime": {"mode": mode, "container_required": bool(runtime_payload.get("container_required"))},
                "outer_command": outer_command,
            }

        elapsed_ms = int((monotonic() - started) * 1000)
        returncode = int(proc.returncode)
        attempt_logs.append(
            {
                "attempt": attempt_index + 1,
                "timed_out": False,
                "returncode": returncode,
                "elapsed_ms": elapsed_ms,
                "resource_snapshot": resource_snapshot,
            }
        )
        if returncode != 0 and retry_on_nonzero and attempt_index < retries_allowed:
            should_retry = (len(retry_set) == 0) or (returncode in retry_set)
            if should_retry:
                _retry_sleep(attempt_index=attempt_index)
                _emit(
                    policy_hook,
                    "command_retry_nonzero",
                    {"command": command, "attempt": attempt_index + 1, "returncode": returncode},
                )
                continue

        result = {
            "command": command,
            "cwd": str(root),
            "timeout_seconds": timeout_seconds,
            "timed_out": False,
            "returncode": returncode,
            "stdout": proc.stdout or "",
            # Preserve actionable diagnostics for non-zero exits even when tools emit failures on stdout.
            "stderr": (proc.stderr or proc.stdout or "") if returncode != 0 else (proc.stderr or ""),
            "attempts": attempt_index + 1,
            "retry_count": attempt_index,
            "retry_strategy": "nonzero_retry" if retry_on_nonzero and retries_allowed > 0 else "none",
            "terminal_failure_class": ("nonzero_exit" if returncode != 0 else None),
            "attempt_logs": attempt_logs,
            "policy_diagnostics": {**policy_diag, "matched_rule": matched_rule.model_dump(mode="python")},
            "resource_snapshot": resource_snapshot,
            "runtime": {"mode": mode, "container_required": bool(runtime_payload.get("container_required"))},
            "outer_command": outer_command,
        }
        _emit(
            policy_hook,
            "command_completed",
            {"command": command, "returncode": returncode, "attempt": attempt_index + 1},
        )
        return result

    return {
        "command": command,
        "cwd": str(root),
        "timeout_seconds": timeout_seconds,
        "timed_out": False,
        "returncode": 1,
        "stdout": "",
        "stderr": "unexpected retry loop exhaustion",
        "attempts": attempts_total,
        "retry_count": retries_allowed,
        "retry_strategy": "unknown",
        "terminal_failure_class": "internal_error",
        "attempt_logs": attempt_logs,
        "policy_diagnostics": {**policy_diag, "matched_rule": matched_rule.model_dump(mode="python")},
        "resource_snapshot": resource_snapshot,
        "runtime": {"mode": mode, "container_required": bool(runtime_payload.get("container_required"))},
        "outer_command": outer_command,
    }


def _build_docker_command(
    *,
    workspace_root: Path,
    raw_workspace_root: str | Path | None,
    command: list[str],
    runtime: dict[str, Any],
) -> tuple[list[str], str]:
    requested_docker_bin = str(runtime.get("docker_bin") or "docker").strip() or "docker"
    docker_bin = _resolve_docker_bin(requested_docker_bin)
    image = str(runtime.get("image") or "").strip()
    if not image:
        raise CommandRuntimeError("DOCKER_IMAGE_REQUIRED for runtime.mode=docker")
    diagnostics = _runtime_transport_diagnostics(
        requested_docker_bin=requested_docker_bin, resolved_docker_bin=docker_bin
    )
    if not diagnostics["cli_available"]:
        raise CommandRuntimeError(_docker_unavailable_message(requested_docker_bin, docker_bin))
    if diagnostics["socket_backed"] and not bool(runtime.get("allow_socket_runtime")):
        raise CommandRuntimeError(
            _socket_backed_runtime_disallowed_message(requested_docker_bin, docker_bin, diagnostics)
        )
    if not diagnostics["daemon_reachable"]:
        raise CommandRuntimeError(_isolated_runtime_unavailable_message(requested_docker_bin, docker_bin, diagnostics))
    _ensure_image_available(runtime=runtime, docker_bin=docker_bin, image=image)

    mapping = resolve_runtime_workspace_mapping(
        workspace_root=raw_workspace_root if raw_workspace_root is not None else workspace_root,
        runtime=runtime,
    )
    mount_spec = f"{mapping['mount_source']}:{mapping['container_mount_root']}"
    args: list[str] = [
        docker_bin,
        "run",
        "--rm",
        "--workdir",
        mapping["container_workspace_root"],
        "-v",
        mount_spec,
    ]
    if bool(runtime.get("read_only_rootfs")):
        args.append("--read-only")
        tmpfs_size = str(runtime.get("tmpfs_size") or "").strip()
        tmpfs_value = "/tmp"
        if tmpfs_size:
            tmpfs_value = f"/tmp:rw,exec,nosuid,size={tmpfs_size}"
        args.extend(["--tmpfs", tmpfs_value])
    network = str(runtime.get("network") or "").strip()
    if network:
        args.extend(["--network", network])
    cpus = str(runtime.get("cpus") or "").strip()
    if cpus:
        args.extend(["--cpus", cpus])
    memory = str(runtime.get("memory") or "").strip()
    if memory:
        args.extend(["--memory", memory])
    pids_limit = runtime.get("pids_limit")
    if pids_limit is not None and str(pids_limit).strip():
        args.extend(["--pids-limit", str(pids_limit)])
    labels = merge_runtime_labels(
        runtime.get("labels") if isinstance(runtime.get("labels"), dict) else None,
        family=str(runtime.get("image_label_family") or "").strip() or "hyeve.devexecutor.command-runtime",
        runtime_kind="command",
    )
    if labels:
        for key, value in labels.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            args.extend(["--label", f"{normalized_key}={value}"])
    env_overrides = runtime.get("env_overrides")
    if isinstance(env_overrides, dict):
        for key, value in env_overrides.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            args.extend(["-e", f"{normalized_key}={value}"])

    extra_args_raw = runtime.get("extra_args")
    if isinstance(extra_args_raw, list):
        args.extend([str(item) for item in extra_args_raw if str(item).strip()])

    args.append(image)
    args.extend(
        _normalize_command_for_container(
            command=command,
            workspace_root=workspace_root,
            container_workspace_root=mapping["container_workspace_root"],
        )
    )
    return args, str(workspace_root)


def _ensure_image_available(*, runtime: dict[str, Any], docker_bin: str, image: str) -> None:
    build_context = str(runtime.get("build_context") or "").strip()
    dockerfile = str(runtime.get("dockerfile") or "").strip()
    image_labels = _expected_runtime_image_labels(runtime=runtime)
    inspect = subprocess.run(
        [docker_bin, "image", "inspect", image, "--format", "{{json .Config.Labels}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_docker_command_env(docker_bin),
    )
    if inspect.returncode == 0 and (
        not image_labels or _image_labels_match(inspect.stdout, expected_labels=image_labels)
    ):
        return
    if not bool(runtime.get("auto_build_image")):
        return
    if not build_context or not dockerfile:
        raise CommandRuntimeError(f"DOCKER_IMAGE_MISSING image={image}")
    context_path = Path(build_context).resolve()
    dockerfile_path = Path(dockerfile).resolve()
    if not context_path.exists():
        raise CommandRuntimeError(f"DOCKER_BUILD_CONTEXT_MISSING path={context_path}")
    if not dockerfile_path.exists():
        raise CommandRuntimeError(f"DOCKERFILE_MISSING path={dockerfile_path}")
    build_command = [docker_bin, "buildx", "build", "--load", "-t", image]
    for key, value in image_labels.items():
        build_command.extend(["--label", f"{key}={value}"])
    build_command.extend(["-f", str(dockerfile_path), str(context_path)])
    build = subprocess.run(
        build_command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_docker_command_env(docker_bin),
    )
    if build.returncode != 0:
        detail = (
            (build.stderr or "").strip()
            or (build.stdout or "").strip()
            or f"docker buildx build exited {build.returncode}"
        )
        raise CommandRuntimeError(f"DOCKER_BUILD_FAILED image={image} detail={detail}")
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
    family = str(runtime.get("image_label_family") or "").strip() or "hyeve.devexecutor.command-runtime"
    signature = _compute_build_context_signature(build_context=build_context, dockerfile=dockerfile)
    labels = merge_runtime_labels({}, family=family, runtime_kind="command")
    labels["hyeve.runtime.context_signature"] = signature
    return labels


def _image_labels_match(raw_labels: str, *, expected_labels: dict[str, str]) -> bool:
    try:
        parsed = json.loads(raw_labels or "{}")
    except Exception:
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
    subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_docker_command_env(docker_bin),
    )


def _normalize_command_for_container(
    *,
    command: list[str],
    workspace_root: Path,
    container_workspace_root: str,
) -> list[str]:
    if not command:
        return []

    normalized = list(command)
    normalized[0] = _normalize_executable_for_container(normalized[0])
    for idx in range(1, len(normalized)):
        normalized[idx] = _normalize_arg_for_container(
            normalized[idx],
            workspace_root=workspace_root,
            container_workspace_root=container_workspace_root,
        )
    return normalized


def _normalize_executable_for_container(token: str) -> str:
    stripped = str(token).strip()
    if not stripped:
        return stripped
    path = Path(stripped)
    if path.is_absolute() or (":" in stripped and ("\\" in stripped or "/" in stripped)):
        stem = path.stem.lower()
        if stem.startswith("python"):
            return "python"
        if stem.startswith("pip"):
            return "pip"
        return path.stem or path.name
    return stripped


def _normalize_arg_for_container(token: str, *, workspace_root: Path, container_workspace_root: str) -> str:
    raw = str(token)
    if not raw:
        return raw
    if raw.startswith("-"):
        return raw
    if not any(sep in raw for sep in ("\\", "/", ":")):
        return raw
    return map_host_path_to_container(
        host_path=raw,
        workspace_root=workspace_root,
        container_workspace_root=container_workspace_root,
    )


def _docker_available(docker_bin: str) -> bool:
    try:
        probe = subprocess.run(
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
        probe = subprocess.run(
            [docker_bin, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            env=_docker_command_env(docker_bin),
        )
        return int(probe.returncode) == 0
    except Exception:
        return False


def _docker_buildx_available(docker_bin: str) -> bool:
    try:
        probe = subprocess.run(
            [docker_bin, "buildx", "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            env=_docker_command_env(docker_bin),
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


def _docker_command_env(docker_bin: str) -> dict[str, str]:
    return _shared_docker_command_env(docker_bin)


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
        "SOCKET_BACKED_RUNTIME_DISALLOWED "
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
        "ISOLATED_RUNTIME_UNAVAILABLE "
        f"requested={requested} "
        f"docker_bin={resolved} "
        f"transport={diagnostics.get('transport')} "
        f"docker_host={diagnostics.get('docker_host')} "
        f"cli_available={str(bool(diagnostics.get('cli_available'))).lower()} "
        f"daemon_reachable={str(bool(diagnostics.get('daemon_reachable'))).lower()}"
    )


def _resource_snapshot() -> dict[str, Any]:
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        return {
            "psutil_available": True,
            "cpu_percent": float(psutil.cpu_percent(interval=0.0)),
            "memory_percent": float(vm.percent),
            "memory_available_bytes": int(vm.available),
        }
    except Exception:
        return {"psutil_available": False}


def _retry_sleep(*, attempt_index: int) -> None:
    # tenacity-backed jittered backoff when available; deterministic fallback otherwise.
    try:
        from tenacity.wait import wait_random_exponential  # type: ignore

        wait_fn = wait_random_exponential(multiplier=0.15, max=2.0)
        delay = float(wait_fn(retry_state=None))  # type: ignore[arg-type]
        time.sleep(max(delay, 0.01))
    except Exception:
        time.sleep(min(0.15 * (2**attempt_index), 1.0))


def cleanup_managed_command_runtime_artifacts(*, runtime: dict[str, Any] | None) -> None:
    payload = dict(runtime or {})
    if str(payload.get("mode") or "host").strip().lower() != "docker":
        return
    docker_bin = _resolve_docker_bin(str(payload.get("docker_bin") or "docker").strip() or "docker")
    prune_managed_runtime_artifacts(docker_bin=docker_bin)


__all__ = ["CommandPolicyError", "CommandRuntimeError", "run_command", "cleanup_managed_command_runtime_artifacts"]
