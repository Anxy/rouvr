from __future__ import annotations

# Source of truth: mutation tool taxonomy shared across orchestration,
# validation, manifests, and projections.

PRIMARY_MUTATION_LANE = "edit_file -> precheck -> patch -> apply -> postcheck"

DEPRECATED_MUTATION_TOOLS: tuple[str, ...] = (
    "edit_symbol",
    "replace_in_function",
    "update_call_kwarg",
    "insert_after_symbol",
    "insert_before_return",
)

ORCHESTRATION_REMOVED_MUTATION_TOOLS: tuple[str, ...] = (
    "edit_symbol",
    "replace_in_function",
    "update_call_kwarg",
    "insert_after_symbol",
    "insert_before_return",
)
ORCHESTRATION_FALLBACK_MUTATION_TOOLS: tuple[str, ...] = ()

LIVE_ORCHESTRATION_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "replace_function",
        "extend_python_signature",
        "insert_at_anchor",
        "write_file",
        "apply_patch",
        "replace_range",
    }
)

ALL_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "replace_function",
        "extend_python_signature",
        "insert_at_anchor",
        "write_file",
        "apply_patch",
        "replace_range",
        *DEPRECATED_MUTATION_TOOLS,
    }
)

LIVE_CONTENT_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "replace_function",
        "extend_python_signature",
        "insert_at_anchor",
        "replace_range",
    }
)

LEGACY_CONTENT_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "insert_after_symbol",
        "insert_before_return",
        "edit_symbol",
        "replace_in_function",
    }
)

CONTENT_MUTATION_TOOLS: frozenset[str] = frozenset(LIVE_CONTENT_MUTATION_TOOLS | LEGACY_CONTENT_MUTATION_TOOLS)

PRIMARY_CODE_MUTATION_TOOLS: tuple[str, ...] = (
    "replace_function",
    "edit_file",
    "apply_patch",
    "write_file",
    "replace_range",
)


def is_code_mutation_tool(tool_name: str | None, *, include_deprecated: bool = False) -> bool:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return False
    if include_deprecated:
        return normalized in ALL_MUTATION_TOOLS
    return normalized in LIVE_ORCHESTRATION_MUTATION_TOOLS


def canonical_code_mutation_tool_names(*, include_deprecated: bool = False) -> tuple[str, ...]:
    if include_deprecated:
        extras = tuple(tool for tool in DEPRECATED_MUTATION_TOOLS if tool not in PRIMARY_CODE_MUTATION_TOOLS)
        return PRIMARY_CODE_MUTATION_TOOLS + extras
    return PRIMARY_CODE_MUTATION_TOOLS


def canonical_code_mutation_tool_phrase(*, include_deprecated: bool = False) -> str:
    names = canonical_code_mutation_tool_names(include_deprecated=include_deprecated)
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return ", ".join(names[:-1]) + f", or {names[-1]}"


__all__ = [
    "ALL_MUTATION_TOOLS",
    "PRIMARY_CODE_MUTATION_TOOLS",
    "canonical_code_mutation_tool_names",
    "canonical_code_mutation_tool_phrase",
    "CONTENT_MUTATION_TOOLS",
    "DEPRECATED_MUTATION_TOOLS",
    "LEGACY_CONTENT_MUTATION_TOOLS",
    "LIVE_CONTENT_MUTATION_TOOLS",
    "LIVE_ORCHESTRATION_MUTATION_TOOLS",
    "ORCHESTRATION_FALLBACK_MUTATION_TOOLS",
    "ORCHESTRATION_REMOVED_MUTATION_TOOLS",
    "PRIMARY_MUTATION_LANE",
    "is_code_mutation_tool",
]
