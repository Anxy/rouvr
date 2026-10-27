"""Pure file/symbol metadata for CodeIntel v2 citations (Agent 4, Wave 4).

Advisory-only lane: this module records caller-supplied metadata exactly as
given. It performs no filesystem reads, no directory enumeration, and no guessing
of any kind — every field is explicit input, and instances are immutable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _check_line_range(value: object) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = value
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and not isinstance(start, bool)
            and not isinstance(end, bool)
            and 1 <= start <= end
        ):
            return (start, end)
    raise ValueError(
        "line_range must be a (start, end) pair of ints with 1 <= start <= end"
    )


def _check_text(value: object, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class FileMeta:
    """Explicit metadata for one file revision.

    Pure record: no filesystem access happens here, and no field is ever
    derived from another. ``path`` is stored exactly as supplied.
    """

    path: str
    line_range: tuple[int, int]
    snapshot_id: str
    commit_sha: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _check_text(self.path, "path"))
        object.__setattr__(self, "line_range", _check_line_range(self.line_range))
        object.__setattr__(self, "snapshot_id", _check_text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "commit_sha", _check_text(self.commit_sha, "commit_sha"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line_range": [self.line_range[0], self.line_range[1]],
            "snapshot_id": self.snapshot_id,
            "commit_sha": self.commit_sha,
        }


@dataclass(frozen=True, slots=True)
class SymbolMeta:
    """Explicit metadata for one named symbol. Pure record, no lookup."""

    name: str
    kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _check_text(self.name, "name"))
        object.__setattr__(self, "kind", _check_text(self.kind, "kind"))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind}


__all__ = ["FileMeta", "SymbolMeta"]
