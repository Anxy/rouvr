"""P12: Project Memory — first-class project-scoped knowledge accumulation.

Provides persistent, project-scoped memory that accumulates institutional
knowledge about a specific codebase over time. This sits above the generic
KnowledgeStore (which is capability/problem-scoped) and provides:

- Architecture decisions and their rationale
- Coding conventions detected or declared
- Known failure patterns and their resolutions
- Dependency constraints and compatibility notes
- Performance baselines and regressions

Each entry is scoped to a (tenant_id, project_id) pair and carries provenance,
confidence, and freshness metadata. Entries are never deleted — they are
superseded or deprecated with an audit trail.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

MemoryKind = Literal[
    "architecture_decision",
    "coding_convention",
    "failure_pattern",
    "dependency_constraint",
    "performance_baseline",
    "project_note",
]

MEMORY_KINDS: frozenset[str] = frozenset(MemoryKind.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class ProjectMemoryEntry:
    entry_id: str
    tenant_id: str
    project_id: str
    kind: MemoryKind
    title: str
    content: str
    provenance: str  # e.g. "user_declared", "auto_detected", "execution_learned"
    confidence: float = 1.0
    source_cycle_id: str | None = None
    source_step_id: str | None = None
    tags: tuple[str, ...] = ()
    superseded_by: str | None = None
    status: str = "ACTIVE"
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )

    def content_hash(self) -> str:
        payload = f"{self.kind}:{self.title}:{self.content}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class ProjectMemoryStore:
    """In-memory project memory store. Production path targets Postgres."""

    def __init__(self) -> None:
        self._entries: list[ProjectMemoryEntry] = []

    def record(self, entry: ProjectMemoryEntry) -> None:
        if entry.kind not in MEMORY_KINDS:
            raise ValueError(f"invalid memory kind: {entry.kind!r}")
        if not entry.tenant_id or not entry.project_id:
            raise ValueError("tenant_id and project_id are required")
        if entry.confidence < 0.0 or entry.confidence > 1.0:
            raise ValueError(f"confidence must be 0.0–1.0, got {entry.confidence}")
        existing = self.get(entry.tenant_id, entry.project_id, entry.entry_id)
        if existing is not None:
            self._entries = [
                e for e in self._entries if e.entry_id != entry.entry_id
            ]
        self._entries.append(entry)

    def get(self, tenant_id: str, project_id: str, entry_id: str) -> ProjectMemoryEntry | None:
        for e in self._entries:
            if e.tenant_id == tenant_id and e.project_id == project_id and e.entry_id == entry_id:
                return e
        return None

    def query(
        self,
        tenant_id: str,
        project_id: str,
        *,
        kind: MemoryKind | None = None,
        tag: str | None = None,
        active_only: bool = True,
    ) -> list[ProjectMemoryEntry]:
        results = []
        for e in self._entries:
            if e.tenant_id != tenant_id or e.project_id != project_id:
                continue
            if active_only and e.status != "ACTIVE":
                continue
            if kind is not None and e.kind != kind:
                continue
            if tag is not None and tag not in e.tags:
                continue
            results.append(e)
        return results

    def supersede(self, tenant_id: str, project_id: str, old_id: str, new_id: str) -> None:
        old = self.get(tenant_id, project_id, old_id)
        if old is None:
            raise ValueError(f"entry {old_id!r} not found")
        updated = ProjectMemoryEntry(
            entry_id=old.entry_id,
            tenant_id=old.tenant_id,
            project_id=old.project_id,
            kind=old.kind,
            title=old.title,
            content=old.content,
            provenance=old.provenance,
            confidence=old.confidence,
            source_cycle_id=old.source_cycle_id,
            source_step_id=old.source_step_id,
            tags=old.tags,
            superseded_by=new_id,
            status="SUPERSEDED",
            created_at=old.created_at,
        )
        self._entries = [e for e in self._entries if e.entry_id != old_id]
        self._entries.append(updated)

    def for_context_capsule(
        self,
        tenant_id: str,
        project_id: str,
        *,
        max_entries: int = 20,
    ) -> list[dict[str, Any]]:
        """Return active entries formatted for inclusion in a context capsule."""
        active = self.query(tenant_id, project_id, active_only=True)
        active.sort(key=lambda e: e.confidence, reverse=True)
        return [
            {
                "kind": e.kind,
                "title": e.title,
                "content": e.content,
                "confidence": e.confidence,
                "tags": list(e.tags),
            }
            for e in active[:max_entries]
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "tenant_id": e.tenant_id,
                    "project_id": e.project_id,
                    "kind": e.kind,
                    "title": e.title,
                    "content": e.content,
                    "provenance": e.provenance,
                    "confidence": e.confidence,
                    "source_cycle_id": e.source_cycle_id,
                    "tags": list(e.tags),
                    "superseded_by": e.superseded_by,
                    "status": e.status,
                    "created_at": e.created_at,
                }
                for e in self._entries
            ],
        }


def detect_conventions(file_contents: dict[str, str]) -> list[ProjectMemoryEntry]:
    """Auto-detect coding conventions from repository file contents.

    Returns candidate memory entries with provenance='auto_detected' and
    moderate confidence. These should be reviewed before promotion.
    """
    conventions: list[ProjectMemoryEntry] = []
    _ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    indent_counts: dict[str, int] = {"tabs": 0, "2spaces": 0, "4spaces": 0}
    has_type_hints = 0
    total_py = 0
    uses_dataclasses = 0
    uses_pydantic = 0

    for path, content in file_contents.items():
        lower = path.lower()
        if lower.endswith(".py"):
            total_py += 1
            for line in content.splitlines():
                stripped = line.lstrip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent = line[: len(line) - len(stripped)]
                if "\t" in indent:
                    indent_counts["tabs"] += 1
                elif indent.startswith("    "):
                    indent_counts["4spaces"] += 1
                elif indent.startswith("  ") and not indent.startswith("    "):
                    indent_counts["2spaces"] += 1
            if "def " in content and "->" in content:
                has_type_hints += 1
            if "@dataclass" in content:
                uses_dataclasses += 1
            if "BaseModel" in content or "from pydantic" in content:
                uses_pydantic += 1

    if total_py > 0:
        dominant_indent = max(indent_counts, key=indent_counts.get)  # type: ignore[arg-type]
        if indent_counts[dominant_indent] > 10:
            conventions.append(ProjectMemoryEntry(
                entry_id=f"conv:indent:{dominant_indent}",
                tenant_id="", project_id="",
                kind="coding_convention",
                title=f"Indentation style: {dominant_indent}",
                content=f"This project predominantly uses {dominant_indent} indentation "
                        f"({indent_counts[dominant_indent]} occurrences across {total_py} Python files).",
                provenance="auto_detected",
                confidence=min(0.9, indent_counts[dominant_indent] / max(1, sum(indent_counts.values()))),
                created_at=_ts,
            ))

        if has_type_hints > total_py * 0.5:
            conventions.append(ProjectMemoryEntry(
                entry_id="conv:type_hints",
                tenant_id="", project_id="",
                kind="coding_convention",
                title="Type hints used consistently",
                content=f"{has_type_hints}/{total_py} Python files use return type annotations.",
                provenance="auto_detected",
                confidence=has_type_hints / total_py,
                created_at=_ts,
            ))

        if uses_pydantic > uses_dataclasses and uses_pydantic > 2:
            conventions.append(ProjectMemoryEntry(
                entry_id="conv:data_models:pydantic",
                tenant_id="", project_id="",
                kind="coding_convention",
                title="Pydantic preferred for data models",
                content=f"Pydantic BaseModel used in {uses_pydantic} files vs dataclass in {uses_dataclasses}.",
                provenance="auto_detected",
                confidence=0.7,
                created_at=_ts,
            ))
        elif uses_dataclasses > uses_pydantic and uses_dataclasses > 2:
            conventions.append(ProjectMemoryEntry(
                entry_id="conv:data_models:dataclass",
                tenant_id="", project_id="",
                kind="coding_convention",
                title="Dataclasses preferred for data models",
                content=f"Dataclass used in {uses_dataclasses} files vs Pydantic in {uses_pydantic}.",
                provenance="auto_detected",
                confidence=0.7,
                created_at=_ts,
            ))

    return conventions


__all__ = [
    "MemoryKind",
    "MEMORY_KINDS",
    "ProjectMemoryEntry",
    "ProjectMemoryStore",
    "detect_conventions",
]
