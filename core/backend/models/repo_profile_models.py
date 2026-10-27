from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

REPO_PROFILE_SCHEMA_VERSION_Current = "repo_profile.current"
SUPPORTED_REPO_PROFILE_VERSIONS = {REPO_PROFILE_SCHEMA_VERSION_Current}


class RepoProfileCommandPolicyCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_prefixes: list[list[str]] = Field(default_factory=list)
    default_timeout_seconds: float = Field(30.0, gt=0.0, le=300.0)
    max_timeout_seconds: float = Field(120.0, gt=0.0, le=900.0)


class RepoProfileDefaultCommandsCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_command: list[str] = Field(default_factory=list)
    lint_command: list[str] = Field(default_factory=list)
    typecheck_command: list[str] = Field(default_factory=list)


class RepoProfileDriftGuardsCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_files_changed: int = Field(25, ge=1, le=500)
    max_diff_bytes: int = Field(500_000, ge=1)
    blocked_path_prefixes: list[str] = Field(default_factory=list)


class RepoProfileOverlayPolicyCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_override_paths: list[str] = Field(
        default_factory=lambda: [
            "command_policy.allow_prefixes",
            "command_policy.default_timeout_seconds",
            "drift_guards.max_files_changed",
            "drift_guards.max_diff_bytes",
            "drift_guards.blocked_path_prefixes",
        ]
    )


class RepoProfileCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["repo_profile.current"]
    profile_id: str = Field(..., min_length=1)
    version: int = Field(1, ge=1)
    repo_patterns: list[str] = Field(default_factory=list)
    inherits_profile_id: str | None = Field(default=None, min_length=1)
    command_policy: RepoProfileCommandPolicyCurrent = Field(default_factory=RepoProfileCommandPolicyCurrent)
    default_commands: RepoProfileDefaultCommandsCurrent = Field(default_factory=RepoProfileDefaultCommandsCurrent)
    drift_guards: RepoProfileDriftGuardsCurrent = Field(default_factory=RepoProfileDriftGuardsCurrent)
    overlay_policy: RepoProfileOverlayPolicyCurrent = Field(default_factory=RepoProfileOverlayPolicyCurrent)
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_repo_profile_schema_current() -> dict[str, Any]:
    return RepoProfileCurrent.model_json_schema()


def validate_repo_profile(payload: dict[str, Any]) -> RepoProfileCurrent:
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version not in SUPPORTED_REPO_PROFILE_VERSIONS:
        raise ValueError(
            f"Unsupported repo profile schema_version '{schema_version}'. "
            f"Supported: {sorted(SUPPORTED_REPO_PROFILE_VERSIONS)}"
        )
    return RepoProfileCurrent.model_validate(payload)
