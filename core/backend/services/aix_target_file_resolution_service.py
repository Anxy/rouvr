from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TargetFileResolutionResult:
    input_path: str
    normalized_repo_relative_path: str
    resolved_absolute_path: str | None
    exists: bool
    repo_root_used: str | None
    failure_reason: str | None
    chosen_root_kind: str | None = None
    attempted_absolute_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_path": self.input_path,
            "normalized_repo_relative_path": self.normalized_repo_relative_path,
            "resolved_absolute_path": self.resolved_absolute_path,
            "exists": self.exists,
            "repo_root_used": self.repo_root_used,
            "failure_reason": self.failure_reason,
            "chosen_root_kind": self.chosen_root_kind,
            "attempted_absolute_paths": list(self.attempted_absolute_paths),
        }


def resolve_target_file(
    *,
    workspace_root: str | Path,
    input_path: str,
    metadata: dict[str, Any] | None = None,
    instruction_contract: dict[str, Any] | None = None,
    allow_ambient_fallback: bool = False,
) -> TargetFileResolutionResult:
    normalized_input = _normalize_path(input_path)
    if not normalized_input:
        return TargetFileResolutionResult(
            input_path="",
            normalized_repo_relative_path="",
            resolved_absolute_path=None,
            exists=False,
            repo_root_used=None,
            failure_reason="input_path_required",
        )
    workspace_text = str(workspace_root or "").strip()
    if not workspace_text:
        return TargetFileResolutionResult(
            input_path=normalized_input,
            normalized_repo_relative_path=normalized_input,
            resolved_absolute_path=None,
            exists=False,
            repo_root_used=None,
            failure_reason="workspace_root_required",
        )
    try:
        service_root = Path(workspace_text).expanduser().resolve()
    except Exception:
        service_root = Path(workspace_text).expanduser()
    aliases = _candidate_path_aliases(
        candidate_path=normalized_input,
        metadata=metadata,
        instruction_contract=instruction_contract,
    )
    if not aliases:
        aliases = [normalized_input]
    canonical_alias = min(aliases, key=lambda item: (len(item), item))
    candidate_roots: list[tuple[str, Path]] = [("workspace_root", service_root)]
    repo_root = _discover_repo_root_from_service_root(service_root)
    if repo_root is not None and repo_root != service_root:
        candidate_roots.append(("repo_root", repo_root))
    if allow_ambient_fallback and repo_root is None:
        ambient_repo_root = _discover_repo_root_from_service_root(Path(__file__).resolve())
        if ambient_repo_root is not None and ambient_repo_root not in {root for _, root in candidate_roots}:
            candidate_roots.append(("ambient_repo_root", ambient_repo_root))

    matches: list[tuple[int, int, str, Path, str]] = []
    attempted_paths: list[str] = []
    seen_attempts: set[str] = set()
    for root_index, (root_kind, root_path) in enumerate(candidate_roots):
        for alias_index, alias in enumerate(aliases):
            candidate = _resolve_under_root(root_path=root_path, alias=alias)
            if candidate is None:
                continue
            candidate_text = str(candidate)
            if candidate_text not in seen_attempts:
                seen_attempts.add(candidate_text)
                attempted_paths.append(candidate_text)
            if not candidate.exists() or not (candidate.is_file() or candidate.is_dir()):
                continue
            matches.append((root_index, alias_index, root_kind, candidate, alias))

    if matches:
        matches.sort(key=lambda item: (item[0], item[1], len(item[4]), item[4]))
        _, _, root_kind, candidate_path, alias = matches[0]
        repo_root_used = next((str(root) for kind, root in candidate_roots if kind == root_kind), str(service_root))
        return TargetFileResolutionResult(
            input_path=normalized_input,
            normalized_repo_relative_path=alias,
            resolved_absolute_path=str(candidate_path),
            exists=True,
            repo_root_used=repo_root_used.replace("\\", "/"),
            failure_reason=None,
            chosen_root_kind=root_kind,
            attempted_absolute_paths=tuple(attempted_paths),
        )

    return TargetFileResolutionResult(
        input_path=normalized_input,
        normalized_repo_relative_path=canonical_alias,
        resolved_absolute_path=None,
        exists=False,
        repo_root_used=str(candidate_roots[-1][1]).replace("\\", "/") if candidate_roots else None,
        failure_reason="target_not_found",
        chosen_root_kind=candidate_roots[-1][0] if candidate_roots else None,
        attempted_absolute_paths=tuple(attempted_paths),
    )


def _resolve_under_root(*, root_path: Path, alias: str) -> Path | None:
    try:
        candidate = (root_path / alias).resolve()
    except Exception:
        return None
    try:
        candidate.relative_to(root_path)
    except ValueError:
        return None
    return candidate


def _normalize_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _discover_repo_root_from_service_root(service_root: Path) -> Path | None:
    try:
        resolved_service_root = service_root.resolve()
    except Exception:
        resolved_service_root = service_root
    for candidate in (resolved_service_root, *resolved_service_root.parents):
        try:
            if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
                return candidate
        except Exception:
            continue
    return None


def _candidate_path_aliases(
    *,
    candidate_path: str,
    metadata: dict[str, Any] | None = None,
    instruction_contract: dict[str, Any] | None = None,
) -> list[str]:
    normalized = _normalize_path(candidate_path)
    if not normalized:
        return []
    aliases: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        alias = _normalize_path(value)
        if alias and alias not in seen:
            seen.add(alias)
            aliases.append(alias)

    _add(normalized)
    metadata_map = dict(metadata or {})
    repo_key = str(metadata_map.get("repo_key") or "").strip().strip("/\\")
    service_root = (
        str(metadata_map.get("service_root") or (instruction_contract or {}).get("service_root") or "")
        .strip()
        .strip("/\\")
    )

    if repo_key:
        for prefix in (f"services/{repo_key}/", f"projects/{repo_key}/", f"projects/AIX/{repo_key}/", f"AIX/{repo_key}/"):
            if normalized.startswith(prefix):
                _add(normalized[len(prefix) :])
            else:
                _add(f"{prefix}{normalized}")

    if service_root:
        service_prefix = f"{service_root}/"
        if normalized.startswith(service_prefix):
            _add(normalized[len(service_prefix) :])
        else:
            _add(f"{service_root}/{normalized}")

    parts = normalized.split("/")
    for marker in ("backend", "tests", "frontend", "app", "sql", "scripts"):
        if marker in parts:
            _add("/".join(parts[parts.index(marker) :]))
    return aliases


__all__ = ["TargetFileResolutionResult", "resolve_target_file"]
