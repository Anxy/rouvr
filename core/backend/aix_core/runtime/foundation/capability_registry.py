"""AIX-GAEF-001: one authoritative catalog of executable capabilities.

Distinct from EX4 `executor/capability_registry.py` (tenant-versioned kernel
metadata) and from `capability_dispatch.py` (mutation *strategy* picker).

Planner/controller names `(capability, operation)`. Python looks up this catalog.
Unknown names fail with CAPABILITY_NOT_AVAILABLE — never a silent edit fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class OperationSpec:
    name: str
    arguments: tuple[str, ...]
    optional_arguments: tuple[str, ...] = ()
    mutates: bool = False
    risk: RiskLevel = "low"
    approval_required: bool = False
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    operations: tuple[OperationSpec, ...]
    description: str = ""


FILESYSTEM = CapabilitySpec(
    name="filesystem",
    description="Confined repository filesystem operations.",
    operations=(
        OperationSpec("read", ("path",), description="Read one file.", aliases=("read_file", "inspect")),
        OperationSpec("inspect", (), optional_arguments=("path",), description="List a directory.", aliases=("list_directory", "list")),
        OperationSpec("search", ("query",), optional_arguments=("path",), description="Search file contents.", aliases=("search_files",)),
        OperationSpec("exists", ("path",), description="Path exists check."),
        OperationSpec("stat", ("path",), description="Path metadata."),
        OperationSpec(
            "create",
            ("path",),
            optional_arguments=("content",),
            mutates=True,
            risk="medium",
            description="Create a new file. Will not overwrite.",
        ),
        OperationSpec(
            "patch",
            (),
            optional_arguments=("path", "paths"),
            mutates=True,
            risk="medium",
            description="Surgical edit of existing files via the proven patch applicator.",
        ),
        OperationSpec(
            "mkdir",
            ("path",),
            optional_arguments=("marker",),
            mutates=True,
            risk="low",
            description="Create a directory. Default marker is .gitkeep.",
            aliases=("create_directory",),
        ),
        OperationSpec(
            "rename",
            ("source", "destination"),
            mutates=True,
            risk="medium",
            description="Rename or move a confined path. Refuses overwrite.",
            aliases=("rename_path", "move"),
        ),
        OperationSpec(
            "copy",
            ("source", "destination"),
            mutates=True,
            risk="medium",
            description="Copy a confined path. Refuses overwrite.",
        ),
        OperationSpec(
            "delete",
            ("path",),
            mutates=True,
            risk="high",
            approval_required=True,
            description="Delete a confined path. Requires explicit approval on the action.",
        ),
    ),
)

REPOSITORY = CapabilitySpec(
    name="repository",
    description="Repository-scoped intelligence that does not mutate.",
    operations=(
        OperationSpec("resolve_symbol", ("symbol_name",), optional_arguments=("target_file",)),
    ),
)

TESTS = CapabilitySpec(
    name="tests",
    description="Allowlisted test/lint/coverage process execution.",
    operations=(
        OperationSpec("run", (), optional_arguments=("command", "path", "filter"),
                      mutates=False, risk="medium",
                      description="Run test suite or specific tests."),
        OperationSpec("lint", (), optional_arguments=("command", "path", "fix"),
                      mutates=False, risk="low",
                      description="Run linter/formatter checks."),
        OperationSpec("coverage", (), optional_arguments=("command", "path"),
                      mutates=False, risk="low",
                      description="Run test coverage analysis."),
    ),
)

SEARCH = CapabilitySpec(
    name="search",
    description="Alias capability for filesystem.search.",
    operations=(
        OperationSpec("code", ("query",), optional_arguments=("path",), aliases=("run",)),
    ),
)

PROCESS = CapabilitySpec(
    name="process",
    description="Sandboxed process execution within the repository workspace.",
    operations=(
        OperationSpec(
            "run", ("command",),
            optional_arguments=("cwd", "timeout_seconds", "env"),
            mutates=False, risk="high",
            description="Run a shell command in a sandbox with bounded timeout. Read-only by default.",
        ),
        OperationSpec(
            "run_mutating", ("command",),
            optional_arguments=("cwd", "timeout_seconds", "env"),
            mutates=True, risk="high", approval_required=True,
            description="Run a shell command that may mutate the workspace. Requires approval.",
        ),
    ),
)

GIT = CapabilitySpec(
    name="git",
    description="Git operations scoped to the repository workspace.",
    operations=(
        OperationSpec("status", (), description="Show working tree status."),
        OperationSpec("diff", (), optional_arguments=("ref", "staged"), description="Show diff."),
        OperationSpec("log", (), optional_arguments=("count", "ref"), description="Show commit log."),
        OperationSpec("branch", (), optional_arguments=("name",), description="List or show current branch."),
        OperationSpec(
            "stage", ("paths",),
            mutates=True, risk="low",
            description="Stage files for commit.",
        ),
        OperationSpec(
            "commit", ("message",),
            optional_arguments=("author",),
            mutates=True, risk="medium",
            description="Create a commit from staged changes.",
        ),
        OperationSpec(
            "checkout", ("ref",),
            mutates=True, risk="medium",
            description="Checkout a branch or ref.",
        ),
        OperationSpec(
            "create_branch", ("name",),
            optional_arguments=("start_point",),
            mutates=True, risk="low",
            description="Create a new branch.",
        ),
    ),
)

NETWORK = CapabilitySpec(
    name="network",
    description="Controlled network operations.",
    operations=(
        OperationSpec(
            "http_get", ("url",),
            optional_arguments=("headers", "timeout_seconds"),
            description="HTTP GET request with bounded timeout.",
        ),
        OperationSpec(
            "http_post", ("url",),
            optional_arguments=("body", "headers", "timeout_seconds"),
            mutates=True, risk="high", approval_required=True,
            description="HTTP POST request. Requires approval as it may cause side effects.",
        ),
    ),
)

CI = CapabilitySpec(
    name="ci",
    description="CI/CD pipeline operations.",
    operations=(
        OperationSpec("status", (), optional_arguments=("pipeline_id",),
                      description="Get CI pipeline status."),
        OperationSpec("trigger", ("pipeline",),
                      optional_arguments=("branch", "parameters"),
                      mutates=True, risk="high", approval_required=True,
                      description="Trigger a CI pipeline run. Requires approval."),
        OperationSpec("logs", ("run_id",),
                      description="Fetch CI run logs."),
        OperationSpec("artifacts", ("run_id",),
                      optional_arguments=("artifact_name",),
                      description="Fetch CI build artifacts."),
    ),
)

DEPLOYMENT = CapabilitySpec(
    name="deployment",
    description="Deployment operations with environment-aware policy.",
    operations=(
        OperationSpec("status", (), optional_arguments=("environment", "service"),
                      description="Check deployment status."),
        OperationSpec("deploy", ("environment", "artifact"),
                      optional_arguments=("version", "strategy"),
                      mutates=True, risk="high", approval_required=True,
                      description="Deploy to an environment. Requires approval."),
        OperationSpec("rollback", ("environment",),
                      optional_arguments=("to_version",),
                      mutates=True, risk="high", approval_required=True,
                      description="Rollback a deployment. Requires approval."),
    ),
)

CONTENT = CapabilitySpec(
    name="content",
    description="Cycle-workspace content generation (proposals, not repository mutations).",
    operations=(
        OperationSpec(
            "generate_html_css",
            (),
            optional_arguments=("objective", "output_dir"),
            mutates=True,
            risk="low",
            description=(
                "Generate HTML/CSS page proposal into the cycle workspace proposed/ "
                "directory via the HTML/CSS LLM codegen module. Does not mutate repository source."
            ),
            aliases=("generate_page", "html_css_page"),
        ),
    ),
)

CAPABILITIES: tuple[CapabilitySpec, ...] = (
    FILESYSTEM, REPOSITORY, TESTS, SEARCH, PROCESS, GIT, NETWORK, CI, DEPLOYMENT, CONTENT,
)

_OPS: dict[tuple[str, str], OperationSpec] = {}
_ALIASES: dict[str, tuple[str, str]] = {}

for _cap in CAPABILITIES:
    for _op in _cap.operations:
        _OPS[(_cap.name, _op.name)] = _op
        _ALIASES[f"{_cap.name}.{_op.name}"] = (_cap.name, _op.name)
        for _alias in _op.aliases:
            _OPS[(_cap.name, _alias)] = _op
            _ALIASES[f"{_cap.name}.{_alias}"] = (_cap.name, _op.name)

# Provenance PlanTarget vocabulary (create_capability / effect language) must not be
# stored as ActionRequest capability names. Map common LLM slips onto the catalog.
# CREATE_FILE.write → filesystem.patch so greenfield bodies still use the codegen
# pipeline (filesystem.create alone would "fulfill" the step with empty stubs).
_ALIASES["CREATE_FILE.write"] = ("filesystem", "patch")
_ALIASES["CREATE_FILE.patch"] = ("filesystem", "patch")
_ALIASES["CREATE_FILE.create"] = ("filesystem", "create")
_ALIASES["CREATE_DIRECTORY.mkdir"] = ("filesystem", "mkdir")
_ALIASES["CREATE_DIRECTORY.create"] = ("filesystem", "mkdir")
_ALIASES["CREATE_DIRECTORY.write"] = ("filesystem", "mkdir")
_ALIASES["EDIT_FILE.write"] = ("filesystem", "patch")
_ALIASES["EDIT_FILE.patch"] = ("filesystem", "patch")
_ALIASES["EDIT_SYMBOL.write"] = ("filesystem", "patch")
_ALIASES["EDIT_SYMBOL.patch"] = ("filesystem", "patch")
# content.generate is not a catalog op (only content.generate_html_css). For multi-file
# greenfield (PHP/SQL/etc.) route to filesystem.patch, not the HTML/CSS-only lane.
_ALIASES["content.generate"] = ("filesystem", "patch")
_ALIASES["content.write"] = ("filesystem", "patch")
_ALIASES["content.create"] = ("filesystem", "patch")
_ALIASES["content.generate_files"] = ("filesystem", "patch")
_ALIASES["content.codegen"] = ("filesystem", "patch")


def resolve_capability(capability: str, operation: str) -> tuple[str, str, OperationSpec] | None:
    key = (str(capability or "").strip(), str(operation or "").strip())
    spec = _OPS.get(key)
    if spec is not None:
        return key[0], spec.name, spec
    combined = f"{key[0]}.{key[1]}" if key[1] else key[0]
    alias = _ALIASES.get(combined)
    if alias is None and "." in combined:
        alias = _ALIASES.get(combined)
    if alias is not None:
        spec = _OPS.get(alias)
        if spec is not None:
            return alias[0], spec.name, spec
    if "." in key[0] and not key[1]:
        cap, op = key[0].split(".", 1)
        return resolve_capability(cap, op)
    return None


def _heuristic_catalog_target(capability: str, operation: str) -> tuple[str, str] | None:
    """Map invented planner verbs onto the catalog once — no per-hallucination aliases.

    Models invent names like ``filesystem.create_complete_files`` or ``content.generate``.
    Hard-failing those as CAPABILITY_NOT_AVAILABLE blocks greenfield forever; coerce
    mutation-shaped invents to ``filesystem.patch`` (codegen pipeline) and directory
    invents to ``filesystem.mkdir``.
    """
    cap = str(capability or "").strip().lower()
    op = str(operation or "").strip().lower()
    blob = f"{cap}.{op}" if op else cap
    tokens = {part for part in blob.replace("-", "_").replace("/", ".").split(".") if part}
    # Flatten without keeping the bare capability head "filesystem" glued to ops —
    # substring "file" must not match inside "filesystem".
    op_tokens = {part for part in op.replace("-", "_").split("_") if part}
    op_flat = "_".join(sorted(op_tokens))
    all_flat = "_".join(sorted(tokens))

    def _in_op(*needles: str) -> bool:
        return any(n in op_tokens or n in op_flat for n in needles)

    def _in_all(*needles: str) -> bool:
        return any(n in tokens or n in all_flat for n in needles)

    # Directory first (create_directory / mkdir_folder) before generic create→patch.
    if (_in_op("mkdir", "directory", "folder") or _in_all("mkdir", "create_directory")) and not _in_op(
        "file", "files", "html", "css", "php", "sql"
    ):
        return ("filesystem", "mkdir")
    if cap in {"create_directory", "mkdir"} or op in {"mkdir", "create_directory"}:
        return ("filesystem", "mkdir")

    # HTML/CSS-only demo lane — keep only when explicitly html/css page shaped.
    if _in_all("generate_html_css", "html_css", "html_css_page") or (
        _in_all("html", "css") and _in_all("generate", "page") and not _in_all("php", "sql", "py", "ts")
    ):
        return ("content", "generate_html_css")

    # Read / inspect / search family (operation-shaped only — don't treat "filesystem" as read).
    if _in_op("read", "inspect", "list", "search", "exists", "stat", "resolve_symbol"):
        if _in_op("search"):
            return ("filesystem", "search")
        if _in_op("inspect", "list"):
            return ("filesystem", "inspect")
        if _in_op("resolve_symbol", "symbol"):
            return ("repository", "resolve_symbol")
        if _in_op("exists"):
            return ("filesystem", "exists")
        if _in_op("stat"):
            return ("filesystem", "stat")
        return ("filesystem", "read")

    # Mutation / create / write / generate invents → patch (not empty filesystem.create).
    # Match invent tokens on the operation side, plus known provenance capability heads.
    mutation_needles = (
        "create",
        "write",
        "generate",
        "patch",
        "edit",
        "codegen",
        "mutate",
        "apply",
        "update",
        "implement",
        "build",
        "produce",
        "literal",
        "refactor",
        "replace",
        "complete",
        "sources",
    )
    if _in_op(*mutation_needles) or _in_op("file", "files"):
        return ("filesystem", "patch")
    if cap in {
        "create_file",
        "edit_file",
        "edit_symbol",
        "content",
        "repo",
        "code",
        "source",
        "sources",
    }:
        return ("filesystem", "patch")
    # Dotted invent on unknown heads: foo.create_complete_files
    if any(n in all_flat for n in mutation_needles) and cap not in {
        "filesystem",
        "repository",
        "tests",
        "search",
        "process",
        "git",
        "network",
        "ci",
        "deployment",
    }:
        return ("filesystem", "patch")

    return None


def resolve_or_coerce_capability(
    capability: str, operation: str
) -> tuple[str, str, OperationSpec] | None:
    """Resolve a catalog op, or coerce invented planner names to a safe registered op."""
    resolved = resolve_capability(capability, operation)
    if resolved is not None:
        return resolved
    target = _heuristic_catalog_target(capability, operation)
    if target is None:
        return None
    return resolve_capability(target[0], target[1])


def all_registered_capability_ids() -> frozenset[str]:
    return frozenset(_ALIASES.keys())


def catalog_for_planner() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for cap in CAPABILITIES:
        for op in cap.operations:
            out.append(
                {
                    "capability": cap.name,
                    "operation": op.name,
                    "arguments": list(op.arguments),
                    "optional_arguments": list(op.optional_arguments),
                    "mutates": op.mutates,
                    "risk": op.risk,
                    "approval_required": op.approval_required,
                    "description": op.description,
                }
            )
    return out


def is_information_operation(capability: str, operation: str) -> bool:
    resolved = resolve_or_coerce_capability(capability, operation)
    if resolved is None:
        return False
    return not resolved[2].mutates


__all__ = [
    "OperationSpec",
    "CapabilitySpec",
    "CAPABILITIES",
    "resolve_capability",
    "resolve_or_coerce_capability",
    "all_registered_capability_ids",
    "catalog_for_planner",
    "is_information_operation",
]
