"""Static content analysis for devexecutor tool steps.

Called before any Python-targeting mutation step is dispatched so that
syntactically broken or structurally invalid code is rejected fast with
a deterministic error code rather than written to disk and discovered
later via test failures or import errors.
"""

from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path
from typing import Any

from backend.services.devexecutor_mutation_tool_taxonomy_service import (
    CONTENT_MUTATION_TOOLS,
)
from backend.services.devexecutor_search_replace_blocks_service import (
    SearchReplaceBlockError,
    parse_search_replace_blocks,
)

# Ordered list of arg-key names that may carry the code content.
# The first non-empty string wins.
_CONTENT_FIELDS = ("content", "new_content", "new_code", "replacement")
_FULL_FUNCTION_REQUIRED_TOOLS: frozenset[str] = frozenset({"replace_in_function", "replace_function"})
_BODY_SCOPED_TOOLS: frozenset[str] = frozenset(
    {
        "insert_at_anchor",
        "insert_after_symbol",
        "insert_before_return",
    }
)
_REPLACEMENT_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "# ...",
    "... existing function body",
    "... (existing function body",
    "rest of existing function body",
    "<updated body>",
    "omitted",
    "unchanged code omitted",
)

_MODULE_DEPENDENCY_NAMES: dict[str, tuple[str, ...]] = {
    "Depends": ("fastapi",),
    "Request": ("fastapi",),
    "require_permission": ("backend.dependencies.codeintel_indexing_service_dependencies",),
    "project_id_header": ("backend.dependencies.codeintel_indexing_service_dependencies",),
    "validate_tenant_access": ("backend.dependencies.codeintel_indexing_service_dependencies",),
    "resolve_project_context": ("backend.dependencies.codeintel_indexing_service_dependencies",),
    "enforce_no_tenant_header": ("backend.dependencies.codeintel_indexing_service_dependencies",),
}


def _has_function_signature(content: str) -> bool:
    """Check that content starts with a def or async def line after dedent."""
    stripped = textwrap.dedent(str(content or "")).strip()
    return stripped.startswith("def ") or stripped.startswith("async def ")


def _replacement_content_failure(content: str) -> tuple[str, dict[str, Any]] | None:
    lowered = str(content or "").lower()
    placeholder_markers = [marker for marker in _REPLACEMENT_PLACEHOLDER_MARKERS if marker in lowered]
    if placeholder_markers:
        return (
            "PLANNER_INVALID_REPLACEMENT_CONTENT",
            {
                "reason": "replacement body contains placeholder/elided content",
                "placeholder_markers": placeholder_markers,
            },
        )
    try:
        tree = ast.parse(textwrap.dedent(str(content or "")).strip())
    except SyntaxError:
        return None
    function_nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(function_nodes) != 1:
        return None
    function_node = function_nodes[0]
    if len(function_node.body) == 1:
        only_stmt = function_node.body[0]
        if isinstance(only_stmt, ast.Expr):
            value = only_stmt.value
            if isinstance(value, ast.Constant) and value.value is Ellipsis:
                return (
                    "PLANNER_INVALID_REPLACEMENT_CONTENT",
                    {
                        "reason": "replacement body contains ellipsis placeholder",
                        "placeholder_markers": ["..."],
                    },
                )
    return None


def _prepare_for_parse(*, content: str, tool: str) -> tuple[str, bool]:
    """Wrap body-scoped fragments so indented function-body content parses as Python."""
    if tool not in _BODY_SCOPED_TOOLS:
        return content, False
    lines = [line for line in str(content or "").splitlines() if line.strip()]
    if not lines or not lines[0][:1].isspace():
        return content, False
    wrapped = "def _dummy():\n" + "\n".join(
        line if line.startswith("    ") else f"    {line}" for line in str(content or "").splitlines()
    )
    return wrapped, True


def _read_existing_python_source(*, workspace_root: str | Path | None, relative_path: str) -> str:
    if not workspace_root:
        return ""
    try:
        target = (Path(str(workspace_root)) / relative_path).resolve()
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _module_defined_names(source: str) -> set[str]:
    if not str(source or "").strip():
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = str(alias.asname or alias.name.split(".")[0]).strip()
                if bound:
                    defined.add(bound)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = str(alias.asname or alias.name).strip()
                if bound:
                    defined.add(bound)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(str(node.name))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    defined.add(str(target.id))
    return defined


def _missing_module_dependency_name(*, tree: ast.AST, module_source: str) -> tuple[str, tuple[str, ...]] | None:
    defined_names = _module_defined_names(module_source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name):
            continue
        name = str(node.id or "").strip()
        if name not in _MODULE_DEPENDENCY_NAMES:
            continue
        if isinstance(node.ctx, ast.Store):
            continue
        if name in defined_names:
            continue
        return name, _MODULE_DEPENDENCY_NAMES[name]
    return None


def validate_step_content(
    *,
    tool: str,
    args: dict[str, Any],
    workspace_root: str | Path | None = None,
) -> str | None:
    """Validate the code content of a mutation step.

    Returns an error-code string on failure, ``None`` on pass.

    Only inspects ``.py`` files; all other targets are silently skipped.

    Legacy/off-by-default mutation tools are still validated here so rollback
    paths and compatibility tests stay deterministic. This validator does not
    imply those tools are part of the default orchestration surface.
    """
    if tool not in CONTENT_MUTATION_TOOLS:
        return None

    if tool == "edit_file":
        has_structured_range = isinstance(args.get("search_range"), dict) or isinstance(
            args.get("edit_range_within_read"), dict
        )
        if has_structured_range and isinstance(args.get("replacement_text"), str):
            return None
        try:
            parse_search_replace_blocks(str(args.get("search_replace_blocks") or ""))
        except SearchReplaceBlockError as exc:
            return f"CONTENT_SEARCH_REPLACE_INVALID: {exc.code}: {exc.message}"
        return None

    relative_path = str(args.get("relative_path") or "").strip()
    if not relative_path.endswith(".py"):
        return None

    content: str = ""
    for field in _CONTENT_FIELDS:
        candidate = args.get(field)
        if isinstance(candidate, str) and candidate.strip():
            content = candidate
            break

    if not content:
        return None

    if tool == "edit_symbol":
        mode = str(args.get("mode") or "").strip().lower()
        if mode == "refactor_symbol":
            try:
                payload = json.loads(str(content or "").strip())
            except Exception as exc:
                return f"CONTENT_REFACTOR_SYMBOL_PAYLOAD_INVALID: invalid_json:{exc}"
            if not isinstance(payload, dict):
                return "CONTENT_REFACTOR_SYMBOL_PAYLOAD_INVALID: payload_must_be_object"

    # 1. Hyphen in import path — check BEFORE ast.parse because the hyphen makes
    #    the line unparseable (e.g. "from hyeve-aix-service.foo import bar" is also
    #    a SyntaxError, so we must detect it first with a simpler line scan).
    for line in content.splitlines():
        stripped = line.strip()
        if (stripped.startswith("import ") or stripped.startswith("from ")) and "-" in stripped:
            return f"CONTENT_INVALID_IMPORT_PATH: {stripped!r}"

    # 2. Syntax check
    if tool in _FULL_FUNCTION_REQUIRED_TOOLS and not _has_function_signature(content):
        return "MUTATION_INVALID_FUNCTION_REPLACEMENT: missing_function_signature"
    if tool in _FULL_FUNCTION_REQUIRED_TOOLS:
        replacement_failure = _replacement_content_failure(content)
        if replacement_failure is not None:
            code, details = replacement_failure
            return f"{code}: {json.dumps(details, ensure_ascii=True, sort_keys=True)}"
    parse_target, wrapped = _prepare_for_parse(content=content, tool=tool)
    try:
        tree = ast.parse(parse_target)
    except SyntaxError as exc:
        return f"CONTENT_SYNTAX_ERROR: {exc}"

    # 3. Local imports inside function bodies
    walk_root: ast.AST = tree
    dummy_node: ast.FunctionDef | None = None
    if wrapped:
        dummy_node = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and str(node.name or "").strip() == "_dummy"
            ),
            None,
        )
        if dummy_node is not None:
            walk_root = dummy_node
    for node in ast.walk(walk_root):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if wrapped and dummy_node is not None and node is dummy_node:
                continue
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    return f"CONTENT_LOCAL_IMPORT: import inside function '{node.name}'"

    module_source = _read_existing_python_source(workspace_root=workspace_root, relative_path=relative_path)
    missing_dependency = _missing_module_dependency_name(tree=tree, module_source=module_source)
    if missing_dependency is not None:
        name, module_paths = missing_dependency
        sources = ", ".join(module_paths)
        return f"CONTENT_MISSING_MODULE_IMPORT: name '{name}' requires module-scope import from {sources}"

    return None
