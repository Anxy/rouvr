"""Canonical Tool Primitive Registry (AIX-W05-E01-T002).

Single authoritative registry defining schema, authorization rules, idempotency class,
and evidence requirements for each tool primitive (read, search, patch, command, test, git, http, db).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Callable, Any


PrimitiveCategory = Literal["READ", "SEARCH", "PATCH", "COMMAND", "TEST", "GIT", "HTTP", "DB"]
IdempotencyClass = Literal["PURE_READ", "MUTATING_IDEMPOTENT", "MUTATING_NON_IDEMPOTENT"]


@dataclass(frozen=True)
class ToolPrimitiveSpec:
    name: str
    category: PrimitiveCategory
    description: str
    idempotency_class: IdempotencyClass
    requires_approval: bool
    required_evidence: tuple[str, ...]
    allowed_roles: tuple[str, ...]


_REGISTRY: dict[str, ToolPrimitiveSpec] = {
    "list_directory": ToolPrimitiveSpec(
        name="list_directory",
        category="SEARCH",
        description="List relative directory entries within workspace root.",
        idempotency_class="PURE_READ",
        requires_approval=False,
        required_evidence=("file://tree.json",),
        allowed_roles=("planner", "executor", "verifier"),
    ),
    "read_file": ToolPrimitiveSpec(
        name="read_file",
        category="READ",
        description="Read UTF-8 file content within workspace root.",
        idempotency_class="PURE_READ",
        requires_approval=False,
        required_evidence=("file://read.log",),
        allowed_roles=("planner", "executor", "verifier"),
    ),
    "search_files": ToolPrimitiveSpec(
        name="search_files",
        category="SEARCH",
        description="Search workspace files using substring/regex patterns.",
        idempotency_class="PURE_READ",
        requires_approval=False,
        required_evidence=("file://search_hits.json",),
        allowed_roles=("planner", "executor", "verifier"),
    ),
    "apply_unified_diff": ToolPrimitiveSpec(
        name="apply_unified_diff",
        category="PATCH",
        description="Apply a unified diff patch to workspace files.",
        idempotency_class="MUTATING_IDEMPOTENT",
        requires_approval=False,
        required_evidence=("file://patch.diff", "file://diff_stats.json"),
        allowed_roles=("executor",),
    ),
    "create_file": ToolPrimitiveSpec(
        name="create_file",
        category="PATCH",
        description="Create a new file with specified content.",
        idempotency_class="MUTATING_IDEMPOTENT",
        requires_approval=False,
        required_evidence=("file://create.log",),
        allowed_roles=("executor",),
    ),
    "delete_file": ToolPrimitiveSpec(
        name="delete_file",
        category="PATCH",
        description="Delete a file from workspace root.",
        idempotency_class="MUTATING_IDEMPOTENT",
        requires_approval=True,
        required_evidence=("file://delete.log",),
        allowed_roles=("executor",),
    ),
    "run_command": ToolPrimitiveSpec(
        name="run_command",
        category="COMMAND",
        description="Execute a sandboxed shell command.",
        idempotency_class="MUTATING_NON_IDEMPOTENT",
        requires_approval=False,
        required_evidence=("file://cmd.stdout", "file://cmd.exitcode"),
        allowed_roles=("executor", "verifier"),
    ),
    "run_tests": ToolPrimitiveSpec(
        name="run_tests",
        category="TEST",
        description="Run test suite in sandboxed workspace.",
        idempotency_class="PURE_READ",
        requires_approval=False,
        required_evidence=("file://pytest.log", "file://test_report.json"),
        allowed_roles=("executor", "verifier"),
    ),
    "get_git_diff": ToolPrimitiveSpec(
        name="get_git_diff",
        category="GIT",
        description="Inspect git workspace diff stats.",
        idempotency_class="PURE_READ",
        requires_approval=False,
        required_evidence=("file://git.diff",),
        allowed_roles=("planner", "executor", "verifier"),
    ),
    "http_request": ToolPrimitiveSpec(
        name="http_request",
        category="HTTP",
        description="Make an HTTP API call to approved endpoints.",
        idempotency_class="MUTATING_NON_IDEMPOTENT",
        requires_approval=False,
        required_evidence=("file://http_trace.json",),
        allowed_roles=("executor",),
    ),
    "db_query": ToolPrimitiveSpec(
        name="db_query",
        category="DB",
        description="Execute a database query against tenant store.",
        idempotency_class="PURE_READ",
        requires_approval=False,
        required_evidence=("file://db_query.log",),
        allowed_roles=("executor", "verifier"),
    ),
}


class ToolPrimitiveRegistry:
    @staticmethod
    def get(name: str) -> ToolPrimitiveSpec:
        if name not in _REGISTRY:
            raise KeyError(f"unregistered tool primitive: {name!r}")
        return _REGISTRY[name]

    @staticmethod
    def list_all() -> tuple[ToolPrimitiveSpec, ...]:
        return tuple(_REGISTRY.values())

    @staticmethod
    def list_by_category(category: PrimitiveCategory) -> tuple[ToolPrimitiveSpec, ...]:
        return tuple(spec for spec in _REGISTRY.values() if spec.category == category)


__all__ = ["ToolPrimitiveSpec", "ToolPrimitiveRegistry", "PrimitiveCategory", "IdempotencyClass"]
