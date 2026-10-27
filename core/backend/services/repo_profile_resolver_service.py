from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from backend.models.repo_profile_models import RepoProfileCurrent, validate_repo_profile

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO_PROFILES_DIR = BACKEND_ROOT / "config" / "repo_profiles"


def _infer_repo_root(module_path: Path) -> Path:
    resolved = module_path.resolve()
    for candidate in resolved.parents:
        if (candidate / "projects").exists() and (candidate / "services").exists():
            return candidate
        if (candidate / "service" / "backend").exists() and (candidate / "libs").exists():
            return candidate
        if candidate.name == "service" and (candidate / "backend").exists():
            return candidate.parent
    return BACKEND_ROOT


REPO_ROOT = _infer_repo_root(Path(__file__))


class RepoProfileNotFoundError(LookupError):
    pass


class RepoProfileOverlayError(PermissionError):
    pass


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _set_nested(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = target
    for part in parts[:-1]:
        existing = cur.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cur[part] = existing
        cur = existing
    cur[parts[-1]] = value


def _flatten_overlay(overlay: dict[str, Any], *, prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for key, value in overlay.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.extend(_flatten_overlay(value, prefix=path))
        else:
            items.append((path, value))
    return items


def _normalize_profiles_dir(profiles_dir: str | Path) -> Path:
    root = Path(profiles_dir)
    if root.is_absolute():
        return root
    candidates = [(Path.cwd() / root).resolve(), (REPO_ROOT / root).resolve()]
    candidates.extend((ancestor / root).resolve() for ancestor in BACKEND_ROOT.parents)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[1]


def load_repo_profiles(*, profiles_dir: str | Path) -> dict[str, RepoProfileCurrent]:
    root = _normalize_profiles_dir(profiles_dir)
    if not root.exists():
        raise FileNotFoundError(str(root))
    items: dict[str, RepoProfileCurrent] = {}
    for file_path in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        profile = validate_repo_profile(payload)
        items[profile.profile_id] = profile
    return items


def _resolve_inheritance_chain(
    profile_id: str, profiles: dict[str, RepoProfileCurrent], seen: set[str] | None = None
) -> RepoProfileCurrent:
    chain_seen = set(seen or set())
    if profile_id in chain_seen:
        raise ValueError(f"REPO_PROFILE_INHERITANCE_CYCLE profile_id={profile_id}")
    chain_seen.add(profile_id)
    profile = profiles.get(profile_id)
    if profile is None:
        raise RepoProfileNotFoundError(f"REPO_PROFILE_NOT_FOUND profile_id={profile_id}")
    if not profile.inherits_profile_id:
        return profile

    parent = _resolve_inheritance_chain(profile.inherits_profile_id, profiles, chain_seen)
    merged_dict = _deep_merge(parent.model_dump(mode="python"), profile.model_dump(mode="python"))
    merged_dict["profile_id"] = profile.profile_id
    merged_dict["inherits_profile_id"] = profile.inherits_profile_id
    return RepoProfileCurrent.model_validate(merged_dict)


def resolve_repo_profile(
    *,
    repo_key: str,
    explicit_profile_id: str | None = None,
    profiles: dict[str, RepoProfileCurrent] | None = None,
    profiles_dir: str | Path | None = None,
) -> tuple[RepoProfileCurrent, dict[str, Any]]:
    available = dict(profiles or {})
    if not available:
        if profiles_dir is None:
            raise ValueError("profiles or profiles_dir is required")
        available = load_repo_profiles(profiles_dir=profiles_dir)

    source = "repo_pattern_match"
    if explicit_profile_id:
        source = "explicit_profile_id"
        profile = available.get(explicit_profile_id)
        if profile is None:
            raise RepoProfileNotFoundError(f"REPO_PROFILE_NOT_FOUND profile_id={explicit_profile_id}")
        resolved = _resolve_inheritance_chain(explicit_profile_id, available)
        return resolved, {
            "source": source,
            "explicit_profile_id": explicit_profile_id,
            "repo_key": repo_key,
            "matched_profile_ids": [explicit_profile_id],
        }

    matched_ids: list[str] = []
    match_rank: dict[str, tuple[int, int]] = {}
    repo_key_lower = repo_key.lower()
    for pid, profile in available.items():
        patterns = [p for p in profile.repo_patterns if p]
        for pattern in patterns:
            if fnmatch.fnmatchcase(repo_key_lower, pattern.lower()):
                matched_ids.append(pid)
                wildcards = pattern.count("*") + pattern.count("?")
                specificity = len(pattern.replace("*", "").replace("?", ""))
                candidate_rank = (-specificity, wildcards)
                existing = match_rank.get(pid)
                if existing is None or candidate_rank < existing:
                    match_rank[pid] = candidate_rank
                break
    if not matched_ids:
        raise RepoProfileNotFoundError(f"REPO_PROFILE_NOT_FOUND repo_key={repo_key}")

    winner = sorted(set(matched_ids), key=lambda pid: (match_rank.get(pid, (0, 99)), pid))[0]
    resolved = _resolve_inheritance_chain(winner, available)
    return resolved, {
        "source": source,
        "explicit_profile_id": None,
        "repo_key": repo_key,
        "matched_profile_ids": sorted(matched_ids),
        "selected_profile_id": winner,
    }


def apply_repo_profile_overlay(
    *,
    resolved_profile: RepoProfileCurrent,
    overlay: dict[str, Any] | None,
) -> tuple[RepoProfileCurrent, dict[str, Any]]:
    overlay_dict = dict(overlay or {})
    if not overlay_dict:
        return resolved_profile, {"overlay_applied": False, "paths": []}

    allowed = set(resolved_profile.overlay_policy.allowed_override_paths)
    flattened = _flatten_overlay(overlay_dict)
    for path, _value in flattened:
        if path not in allowed:
            raise RepoProfileOverlayError(f"UNSAFE_REPO_PROFILE_OVERRIDE path={path} allowed={sorted(allowed)}")

    payload = resolved_profile.model_dump(mode="python")
    for path, value in flattened:
        _set_nested(payload, path, value)

    resolved = RepoProfileCurrent.model_validate(payload)
    return resolved, {"overlay_applied": True, "paths": [path for path, _ in flattened]}


def resolve_effective_repo_profile(
    *,
    repo_key: str,
    explicit_profile_id: str | None = None,
    profiles: dict[str, RepoProfileCurrent] | None = None,
    profiles_dir: str | Path | None = None,
    overlay: dict[str, Any] | None = None,
) -> tuple[RepoProfileCurrent, dict[str, Any]]:
    base_profile, base_diag = resolve_repo_profile(
        repo_key=repo_key,
        explicit_profile_id=explicit_profile_id,
        profiles=profiles,
        profiles_dir=profiles_dir,
    )
    effective_profile, overlay_diag = apply_repo_profile_overlay(resolved_profile=base_profile, overlay=overlay)
    return effective_profile, {"resolver": base_diag, "overlay": overlay_diag}
