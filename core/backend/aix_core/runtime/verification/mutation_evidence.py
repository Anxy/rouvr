"""Generic mutation evidence: prove filesystem changes without relying on git alone.

Gitignored / untracked files never appear in ``git diff``. Callers pass the patches
that were actually validated and applied; this module merges them with any git stats
into a single evidence payload for completion gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppliedMutation:
    path: str
    unified_diff: str
    lines_added: int = 0
    lines_removed: int = 0
    after_content: str | None = None


@dataclass
class MutationEvidence:
    diff_text: str
    diff_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    applied_paths: list[str] = field(default_factory=list)
    sources: tuple[str, ...] = ()


def merge_mutation_evidence(
    *,
    git_diff_result: dict[str, Any] | None,
    applied: list[AppliedMutation],
) -> MutationEvidence:
    """Combine git diff (when present) with applied-patch evidence for ignored files."""
    git = git_diff_result or {}
    stats: dict[str, dict[str, int]] = {
        str(path): dict(values) for path, values in dict(git.get("stats") or {}).items()
    }
    git_keys = {p.replace("\\", "/") for p in stats}
    synth_parts: list[str] = []
    applied_paths: list[str] = []
    sources: list[str] = []

    if str(git.get("diff") or "").strip():
        sources.append("git_diff")

    for item in applied:
        norm = str(item.path).replace("\\", "/")
        applied_paths.append(item.path)
        if norm not in git_keys:
            stats[item.path] = {
                "additions": int(item.lines_added or 0),
                "deletions": int(item.lines_removed or 0),
            }
            if str(item.unified_diff or "").strip():
                synth_parts.append(str(item.unified_diff).rstrip() + "\n")
            sources.append("applied_patch")

    existing = str(git.get("diff") or "").rstrip()
    if synth_parts:
        joined = "\n".join(synth_parts)
        diff_text = f"{existing}\n{joined}".strip() + "\n" if existing else joined
    else:
        diff_text = (existing + "\n") if existing else ""

    return MutationEvidence(
        diff_text=diff_text,
        diff_stats=stats,
        applied_paths=list(dict.fromkeys(applied_paths)),
        sources=tuple(dict.fromkeys(sources)),
    )


def assert_content_contains(path: str, content: str, *, needles: list[str]) -> list[str]:
    """Return failing needles that are missing from ``content`` (empty list = pass)."""
    failures: list[str] = []
    for needle in needles:
        if needle and needle not in content:
            failures.append(f"{path}: missing {needle!r}")
    return failures


__all__ = [
    "AppliedMutation",
    "MutationEvidence",
    "merge_mutation_evidence",
    "assert_content_contains",
]
