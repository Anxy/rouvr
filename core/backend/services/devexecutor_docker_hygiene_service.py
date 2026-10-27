from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

_MANAGED_LABELS = {
    "aix.managed": "true",
    "aix.runtime": "devexecutor",
}


def docker_command_env(docker_bin: str) -> dict[str, str]:
    env = dict(os.environ)
    docker_path = str(Path(docker_bin).resolve().parent) if str(docker_bin).strip() else ""
    if docker_path:
        existing = str(env.get("PATH") or "")
        parts = [part for part in existing.split(os.pathsep) if part]
        if docker_path not in parts:
            env["PATH"] = os.pathsep.join([docker_path, *parts]) if parts else docker_path
    return env


def managed_runtime_labels(*, family: str, runtime_kind: str) -> dict[str, str]:
    labels = dict(_MANAGED_LABELS)
    if str(family or "").strip():
        labels["hyeve.runtime.family"] = str(family).strip()
    if str(runtime_kind or "").strip():
        labels["hyeve.runtime.kind"] = str(runtime_kind).strip()
    return labels


def merge_runtime_labels(
    existing: dict[str, Any] | None,
    *,
    family: str,
    runtime_kind: str,
) -> dict[str, str]:
    merged = managed_runtime_labels(family=family, runtime_kind=runtime_kind)
    for key, value in dict(existing or {}).items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        merged[normalized_key] = str(value)
    return merged


def prune_managed_runtime_artifacts(*, docker_bin: str) -> None:
    env = docker_command_env(docker_bin)
    container_command = [
        docker_bin,
        "container",
        "prune",
        "-f",
        "--filter",
        "label=aix.managed=true",
        "--filter",
        "label=aix.runtime=devexecutor",
    ]
    image_command = [
        docker_bin,
        "image",
        "prune",
        "-a",
        "-f",
        "--filter",
        "label=aix.managed=true",
        "--filter",
        "label=aix.runtime=devexecutor",
    ]
    for command in (container_command, image_command):
        try:
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=15.0,
            )
        except Exception:
            continue


def docker_command_exists(docker_bin: str) -> bool:
    candidate = str(docker_bin or "").strip()
    if not candidate:
        return False
    path = Path(candidate)
    if path.is_absolute():
        return path.exists()
    return shutil.which(candidate) is not None


def resolve_docker_bin(docker_bin: str) -> str:
    requested = str(docker_bin or "").strip() or "docker"
    candidates = [requested]
    requested_path = Path(requested)
    if requested_path.name and requested_path.name != requested:
        candidates.append(requested_path.name)
    if "docker" not in candidates:
        candidates.append("docker")
    for candidate in candidates:
        if docker_command_exists(candidate):
            return candidate
    return requested


__all__ = [
    "docker_command_env",
    "managed_runtime_labels",
    "merge_runtime_labels",
    "prune_managed_runtime_artifacts",
    "resolve_docker_bin",
    "docker_command_exists",
]
