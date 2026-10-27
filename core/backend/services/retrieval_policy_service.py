"""Retrieval policy guardrails for AIX CodeIntel-first behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RetrievalPolicyResult:
    requested_mode: str
    effective_mode: str
    direct_filesystem_requested: bool
    direct_filesystem_allowed: bool
    denied_reason: str | None = None
    override_reason: str | None = None
    requested_paths: list[str] = field(default_factory=list)


def _normalize_path(value: str) -> str:
    try:
        return str(Path(value).as_posix().rstrip("/"))
    except Exception:
        return value.rstrip("/")


def _codeintel_posture() -> str:
    """Wave 0 authority freeze: read AIX_CODEINTEL without hard-importing settings at module load."""
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return str(getattr(_settings, "AIX_CODEINTEL", "off") or "off").strip().lower()
    except Exception:
        return "off"


def evaluate_retrieval_policy(
    *,
    requested_mode: str,
    override_reason: str | None,
    requested_paths: list[str] | None,
    exception_paths: list[str] | None,
) -> RetrievalPolicyResult:
    if _codeintel_posture() == "off":
        # Wave 0: CodeIntel/Haystack retrieval disabled — force direct filesystem.
        return RetrievalPolicyResult(
            requested_mode=requested_mode,
            effective_mode="direct_filesystem",
            direct_filesystem_requested=True,
            direct_filesystem_allowed=True,
            denied_reason="WAVE0_CODEINTEL_DISABLED",
            requested_paths=[_normalize_path(item) for item in (requested_paths or []) if str(item).strip()],
        )
    direct_requested = requested_mode == "direct_filesystem"
    normalized_paths = [_normalize_path(item) for item in (requested_paths or []) if str(item).strip()]
    allowed_prefixes = [_normalize_path(item) for item in (exception_paths or []) if str(item).strip()]
    reason = (override_reason or "").strip()

    if not direct_requested:
        return RetrievalPolicyResult(
            requested_mode=requested_mode,
            effective_mode="codeintel_first",
            direct_filesystem_requested=False,
            direct_filesystem_allowed=False,
            requested_paths=normalized_paths,
        )

    if not reason:
        return RetrievalPolicyResult(
            requested_mode=requested_mode,
            effective_mode="codeintel_first",
            direct_filesystem_requested=True,
            direct_filesystem_allowed=False,
            denied_reason="OVERRIDE_REASON_REQUIRED",
            requested_paths=normalized_paths,
        )

    if not normalized_paths:
        return RetrievalPolicyResult(
            requested_mode=requested_mode,
            effective_mode="codeintel_first",
            direct_filesystem_requested=True,
            direct_filesystem_allowed=False,
            denied_reason="OVERRIDE_PATHS_REQUIRED",
            override_reason=reason,
            requested_paths=normalized_paths,
        )

    is_allowed = all(any(path.startswith(prefix) for prefix in allowed_prefixes) for path in normalized_paths)
    if not is_allowed:
        return RetrievalPolicyResult(
            requested_mode=requested_mode,
            effective_mode="codeintel_first",
            direct_filesystem_requested=True,
            direct_filesystem_allowed=False,
            denied_reason="PATH_NOT_IN_EXCEPTION_LIST",
            override_reason=reason,
            requested_paths=normalized_paths,
        )

    # Guardrail allows the request, but execution remains CodeIntel-first in MVP3.
    return RetrievalPolicyResult(
        requested_mode=requested_mode,
        effective_mode="codeintel_first",
        direct_filesystem_requested=True,
        direct_filesystem_allowed=True,
        override_reason=reason,
        requested_paths=normalized_paths,
    )
