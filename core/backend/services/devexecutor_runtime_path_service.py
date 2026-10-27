from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_CONTAINER_WORKSPACE_ROOT = "/workspace"


def resolve_runtime_workspace_mapping(
    *,
    workspace_root: str | Path,
    runtime: dict[str, Any] | None,
) -> dict[str, str]:
    workspace_text = str(workspace_root or "").strip()
    workspace_path = Path(workspace_root).resolve()
    payload = dict(runtime or {})

    host_root_raw = str(payload.get("host_workspace_root") or "").strip()
    container_mount_root = _normalize_container_root(
        str(payload.get("container_workspace_root") or DEFAULT_CONTAINER_WORKSPACE_ROOT)
    )
    candidate_roots: list[Path] = []
    if host_root_raw:
        candidate_roots.append(Path(host_root_raw).resolve())
    git_root = _find_git_repo_root(workspace_path)
    if git_root is not None and git_root not in candidate_roots:
        candidate_roots.append(git_root)
    if workspace_path not in candidate_roots:
        candidate_roots.append(workspace_path)

    host_root = workspace_path
    mount_source = workspace_path
    container_workspace_root = container_mount_root
    container_relative = _container_relative_workspace(
        workspace_root_text=workspace_text,
        container_mount_root=container_mount_root,
    )
    anchored_from_container_root = container_relative is not None and bool(host_root_raw)
    if anchored_from_container_root:
        relative = container_relative or ""
        host_root = Path(host_root_raw).resolve()
        mount_source = host_root if not relative else (host_root / relative).resolve()
        container_workspace_root = _join_container_path(container_mount_root, relative)

    if not anchored_from_container_root:
        for candidate_root in candidate_roots:
            try:
                relative = workspace_path.relative_to(candidate_root).as_posix().strip("/")
            except ValueError:
                continue
            host_root = candidate_root
            mount_source = candidate_root
            container_workspace_root = _join_container_path(container_mount_root, relative)
            break

    return {
        "workspace_root": str(workspace_path),
        "host_workspace_root": str(host_root),
        "mount_source": str(mount_source),
        "container_mount_root": container_mount_root,
        "container_workspace_root": container_workspace_root,
    }


def map_host_path_to_container(
    *,
    host_path: str,
    workspace_root: Path,
    container_workspace_root: str,
) -> str:
    raw = str(host_path or "")
    if not raw or raw.startswith("-"):
        return raw
    if not any(sep in raw for sep in ("\\", "/", ":")):
        return raw
    try:
        path = Path(raw)
        if not path.is_absolute():
            return raw
        resolved = path.resolve(strict=False)
        rel = resolved.relative_to(workspace_root).as_posix().strip("/")
        return _join_container_path(container_workspace_root, rel)
    except Exception:
        return raw


def _normalize_container_root(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return DEFAULT_CONTAINER_WORKSPACE_ROOT
    if not text.startswith("/"):
        text = f"/{text}"
    return text.rstrip("/") or DEFAULT_CONTAINER_WORKSPACE_ROOT


def _join_container_path(root: str, suffix: str) -> str:
    normalized_root = _normalize_container_root(root)
    normalized_suffix = str(suffix or "").strip().replace("\\", "/").strip("/")
    if not normalized_suffix:
        return normalized_root
    return str(PurePosixPath(normalized_root) / normalized_suffix)


def _find_git_repo_root(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _container_relative_workspace(*, workspace_root_text: str, container_mount_root: str) -> str | None:
    workspace_text = str(workspace_root_text or "").strip().replace("\\", "/").rstrip("/")
    normalized_mount_root = _normalize_container_root(container_mount_root)
    if workspace_text == normalized_mount_root:
        return ""
    prefix = f"{normalized_mount_root}/"
    if workspace_text.startswith(prefix):
        return workspace_text[len(prefix) :].strip("/")
    return None


__all__ = [
    "DEFAULT_CONTAINER_WORKSPACE_ROOT",
    "map_host_path_to_container",
    "resolve_runtime_workspace_mapping",
]
