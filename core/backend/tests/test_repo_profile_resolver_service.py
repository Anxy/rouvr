from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.repo_profile_resolver_service import (
    RepoProfileNotFoundError,
    RepoProfileOverlayError,
    _infer_repo_root,
    apply_repo_profile_overlay,
    load_repo_profiles,
    resolve_effective_repo_profile,
    resolve_repo_profile,
)


def _profiles_dir() -> Path:
    return Path("projects/AIX/hyeve-aix-service/backend/config/repo_profiles")


def test_load_repo_profiles_reads_fixtures() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    assert "RP-BASE-PY-CURRENT" in profiles
    assert "RP-HYEVE-AUTH-PY-FASTAPI-CURRENT" in profiles
    assert "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT" in profiles
    assert "RP-HYEVE-PY-FASTAPI-CURRENT" in profiles


def test_load_repo_profiles_relative_path_is_not_cwd_sensitive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    assert "RP-BASE-PY-CURRENT" in profiles
    assert "RP-HYEVE-PY-FASTAPI-CURRENT" in profiles


def test_infer_repo_root_tolerates_shallow_runtime_image_path(tmp_path: Path) -> None:
    module_path = tmp_path / "app" / "service" / "backend" / "services" / "repo_profile_resolver_service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "app" / "service" / "libs").mkdir(parents=True)
    inferred = _infer_repo_root(module_path)
    assert inferred == (tmp_path / "app")


def test_resolve_repo_profile_by_explicit_id_is_deterministic() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    profile_a, diag_a = resolve_repo_profile(
        repo_key="hyeve-core",
        explicit_profile_id="RP-HYEVE-PY-FASTAPI-CURRENT",
        profiles=profiles,
    )
    profile_b, diag_b = resolve_repo_profile(
        repo_key="hyeve-core",
        explicit_profile_id="RP-HYEVE-PY-FASTAPI-CURRENT",
        profiles=profiles,
    )
    assert profile_a.model_dump() == profile_b.model_dump()
    assert diag_a == diag_b


def test_resolve_repo_profile_by_pattern_match() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    profile, diag = resolve_repo_profile(repo_key="aix-service", profiles=profiles)
    assert profile.profile_id == "RP-HYEVE-PY-FASTAPI-CURRENT"
    assert diag["source"] == "repo_pattern_match"


def test_resolve_repo_profile_prefers_codeintel_specific_profile() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    profile, diag = resolve_repo_profile(repo_key="hyeve-codeintel-service", profiles=profiles)
    assert profile.profile_id == "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT"
    assert diag["source"] == "repo_pattern_match"


def test_resolve_repo_profile_prefers_auth_specific_profile() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    profile, diag = resolve_repo_profile(repo_key="hyeve-auth-service", profiles=profiles)
    assert profile.profile_id == "RP-HYEVE-AUTH-PY-FASTAPI-CURRENT"
    assert diag["source"] == "repo_pattern_match"


def test_resolve_repo_profile_missing_raises_typed_error() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    with pytest.raises(RepoProfileNotFoundError, match="REPO_PROFILE_NOT_FOUND"):
        resolve_repo_profile(repo_key="unknown-repo", explicit_profile_id="RP-MISSING", profiles=profiles)


def test_apply_repo_profile_overlay_allows_whitelisted_paths() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    profile, _diag = resolve_repo_profile(
        repo_key="hyeve-core",
        explicit_profile_id="RP-HYEVE-PY-FASTAPI-CURRENT",
        profiles=profiles,
    )
    overlaid, diag = apply_repo_profile_overlay(
        resolved_profile=profile,
        overlay={"drift_guards": {"max_files_changed": 12}},
    )
    assert overlaid.drift_guards.max_files_changed == 12
    assert diag["overlay_applied"] is True


def test_apply_repo_profile_overlay_rejects_unsafe_paths() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    profile, _diag = resolve_repo_profile(
        repo_key="hyeve-core",
        explicit_profile_id="RP-HYEVE-PY-FASTAPI-CURRENT",
        profiles=profiles,
    )
    with pytest.raises(RepoProfileOverlayError, match="UNSAFE_REPO_PROFILE_OVERRIDE"):
        apply_repo_profile_overlay(
            resolved_profile=profile,
            overlay={"default_commands": {"test_command": ["rm", "-rf", "/"]}},
        )


def test_resolve_effective_repo_profile_includes_overlay_diagnostics() -> None:
    profiles = load_repo_profiles(profiles_dir=_profiles_dir())
    profile, diag = resolve_effective_repo_profile(
        repo_key="hyeve-core",
        explicit_profile_id="RP-HYEVE-PY-FASTAPI-CURRENT",
        profiles=profiles,
        overlay={"command_policy": {"default_timeout_seconds": 10.0}},
    )
    assert profile.command_policy.default_timeout_seconds == 10.0
    assert diag["overlay"]["overlay_applied"] is True
