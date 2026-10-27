"""Tool capability manifest builder (AIFIRST-2).

Provides build_tool_manifest() — a machine-readable JSON manifest of all
devexecutor tools sourced from the tool contract registry.
"""

from __future__ import annotations

from typing import Any

from backend.services.devexecutor_mutation_tool_taxonomy_service import (
    DEPRECATED_MUTATION_TOOLS,
    ORCHESTRATION_FALLBACK_MUTATION_TOOLS,
    ORCHESTRATION_REMOVED_MUTATION_TOOLS,
    PRIMARY_MUTATION_LANE,
)
from backend.services.devexecutor_mutation_tools_service import orchestration_visible_deprecated_mutation_tools
from backend.services.devexecutor_tool_contract_registry_service import (
    ToolContract,
    get_tool_contracts,
)

# ---------------------------------------------------------------------------
# Static enrichment data — sequencing constraints and examples.
# This is the single place to update when tools change.
# ---------------------------------------------------------------------------

_TOOL_SEQUENCING: dict[str, dict[str, Any]] = {
    "list_dir": {
        "must_precede": [],
        "must_follow": [],
        "constraints": ["Use as discovery step before targeting specific files"],
    },
    "read_file": {
        "must_precede": [],
        "must_follow": [],
        "constraints": [
            "Use source='filesystem' after a mutation step to verify the written content",
            "For symbol mutations, prefer read_range to scope the read",
        ],
    },
    "read_range": {
        "must_precede": ["confirm_anchor"],
        "must_follow": [],
        "constraints": ["Must be called before any symbol-level mutation on the same file"],
    },
    "search": {
        "must_precede": [],
        "must_follow": [],
        "constraints": ["Use to locate symbols or patterns before reading or mutating"],
    },
    "get_status": {
        "must_precede": [],
        "must_follow": [],
        "constraints": ["Use at session start and after batch mutations to inspect workspace state"],
    },
    "write_file": {
        "must_precede": [],
        "must_follow": [],
        "constraints": [
            "Writes the entire file; any omitted content is deleted",
            "Auto-injects py_compile check after writing a .py file",
        ],
    },
    "edit_file": {
        "must_precede": [],
        "must_follow": ["read_file", "read_range"],
        "constraints": [
            "Preferred primary mutation primitive for localized edits",
            "Provide SEARCH/REPLACE blocks only; do not wrap them in prose or JSON",
            "Applies changes via git apply with 3-way retry rather than direct file rewrite",
        ],
    },
    "insert_at_anchor": {
        "must_precede": [],
        "must_follow": ["read_file", "read_range"],
        "constraints": [
            "Preferred additive primitive for imports, sibling blocks, and anchored insertions",
            "Anchor text must match uniquely; exact match is preferred and whitespace-tolerant matching is fallback only",
            "Applies changes via git apply with 3-way retry rather than direct file rewrite",
        ],
    },
    "apply_patch": {
        "must_precede": [],
        "must_follow": [],
        "constraints": [
            "Do not pass path/rel_path args — use patch_text only",
            "Patch target files must already exist in the workspace",
        ],
    },
    "replace_range": {
        "must_precede": ["read_range"],
        "must_follow": [],
        "constraints": ["Requires knowing exact line numbers from a prior read_range call"],
    },
    "confirm_anchor": {
        "must_precede": [],
        "must_follow": ["read_range", "read_file"],
        "constraints": [
            "Use to ground a symbol before a read-only inspection or to confirm planner understanding",
            "Do not treat confirm_anchor as a gateway to deprecated symbol mutation tools",
        ],
    },
    "cmd.run": {
        "must_precede": [],
        "must_follow": [],
        "constraints": [
            "Command must match an allow-list prefix configured for the workspace",
            "Use for pytest and other validation commands only",
        ],
    },
    "evidence.attach": {
        "must_precede": [],
        "must_follow": [],
        "constraints": [
            "Only emit if the evidence_ref is already known and real",
            "Do not invent placeholder or synthetic refs",
        ],
    },
}

_TOOL_EXAMPLES: dict[str, dict[str, Any]] = {
    "list_dir": {
        "valid": [{"args": {"relative_path": "."}, "expected_result": {"entries": ["src/", "tests/"]}}],
        "invalid": [],
    },
    "read_file": {
        "valid": [
            {
                "args": {"relative_path": "backend/services/foo.py"},
                "expected_result": {"content": "...", "source": "git_blob_head"},
            }
        ],
        "invalid": [
            {
                "args": {"relative_path": "nonexistent.py"},
                "error_code": "FILE_NOT_FOUND",
                "reason": "File does not exist",
            }
        ],
    },
    "read_range": {
        "valid": [
            {
                "args": {"relative_path": "backend/services/foo.py", "start_line": 10, "end_line": 25},
                "expected_result": {"content": "...", "start_line": 10},
            }
        ],
        "invalid": [
            {
                "args": {"relative_path": "backend/services/foo.py", "start_line": 0, "end_line": 5},
                "error_code": "INVALID_LINE_RANGE",
                "reason": "Line numbers are 1-based",
            }
        ],
    },
    "search": {
        "valid": [
            {
                "args": {"pattern": "def my_function", "relative_path": "backend/"},
                "expected_result": {"matches": [{"file": "backend/services/foo.py", "line": 42}]},
            }
        ],
        "invalid": [],
    },
    "get_status": {
        "valid": [{"args": {}, "expected_result": {"status": "clean", "changed_files": []}}],
        "invalid": [],
    },
    "write_file": {
        "valid": [
            {
                "args": {"relative_path": "backend/services/new_service.py", "content": "x = 1\n"},
                "expected_result": {"bytes_written": 6, "relative_path": "backend/services/new_service.py"},
            }
        ],
        "invalid": [
            {
                "args": {"relative_path": "/absolute/path.py", "content": "x = 1"},
                "error_code": "PATH_ESCAPE_DENIED",
                "reason": "Only workspace-relative paths allowed",
            }
        ],
    },
    "apply_patch": {
        "valid": [
            {
                "args": {"patch_text": "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"},
                "expected_result": {"applied": True},
            }
        ],
        "invalid": [
            {
                "args": {"patch_text": "...", "path": "foo.py"},
                "error_code": "INVALID_ARG",
                "reason": "Do not pass path arg to apply_patch",
            }
        ],
    },
    "edit_file": {
        "valid": [
            {
                "args": {
                    "relative_path": "backend/services/foo.py",
                    "search_replace_blocks": "<<<<<<< SEARCH\nreturn old_value\n=======\nreturn new_value\n>>>>>>> REPLACE\n",
                },
                "expected_result": {"operation": "edit_file", "changed_files": ["backend/services/foo.py"]},
            }
        ],
        "invalid": [
            {
                "args": {"relative_path": "backend/services/foo.py", "search_replace_blocks": "notes only"},
                "error_code": "CONTENT_SEARCH_REPLACE_INVALID",
                "reason": "edit_file only accepts SEARCH/REPLACE blocks",
            }
        ],
    },
    "insert_at_anchor": {
        "valid": [
            {
                "args": {
                    "relative_path": "backend/services/foo.py",
                    "anchor_text": "from pathlib import Path\n",
                    "new_content": "from typing import Any\n",
                    "position": "after",
                },
                "expected_result": {"operation": "insert_at_anchor", "changed_files": ["backend/services/foo.py"]},
            }
        ],
        "invalid": [
            {
                "args": {
                    "relative_path": "backend/services/foo.py",
                    "anchor_text": "missing anchor",
                    "new_content": "x = 1\n",
                },
                "error_code": "ANCHOR_NOT_FOUND",
                "reason": "insert_at_anchor requires a unique grounded anchor string",
            }
        ],
    },
    "replace_range": {
        "valid": [
            {
                "args": {
                    "relative_path": "backend/services/foo.py",
                    "start_line": 5,
                    "end_line": 7,
                    "replacement": "    x = 42\n",
                },
                "expected_result": {"lines_replaced": 3},
            }
        ],
        "invalid": [],
    },
    "confirm_anchor": {
        "valid": [
            {
                "args": {
                    "relative_path": "backend/services/foo.py",
                    "symbol_name": "MyClass.method",
                    "anchor_quote": "def method(self):",
                },
                "expected_result": {"matched": True, "reason_code": "MATCH"},
            }
        ],
        "invalid": [
            {
                "args": {
                    "relative_path": "foo.py",
                    "symbol_name": "MyClass.method",
                    "anchor_quote": "stale code from training data",
                },
                "error_code": "GROUNDING_MISMATCH",
                "reason": "anchor does not match current source",
            }
        ],
    },
    "cmd.run": {
        "valid": [
            {"args": {"command": "pytest tests/ -x -q"}, "expected_result": {"returncode": 0, "stdout": "1 passed"}}
        ],
        "invalid": [
            {"args": {"command": "rm -rf /"}, "error_code": "COMMAND_DENIED", "reason": "Command not in allow-list"}
        ],
    },
    "evidence.attach": {
        "valid": [
            {
                "args": {"evidence_ref": "local://devrun-abc123", "evidence_type": "devrun"},
                "expected_result": {"operation": "evidence.attach", "evidence_ref": "local://devrun-abc123"},
            }
        ],
        "invalid": [
            {
                "args": {"evidence_ref": "placeholder://unknown"},
                "error_code": "INVALID_REF",
                "reason": "Do not invent placeholder refs",
            }
        ],
    },
}

_ARG_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "list_dir": {
        "relative_path": {
            "type": "string",
            "required": True,
            "description": "Workspace-relative directory path to list",
        },
    },
    "read_file": {
        "relative_path": {"type": "string", "required": True, "description": "Workspace-relative file path to read"},
        "source": {
            "type": "string",
            "required": False,
            "enum": ["filesystem", "git_blob_head"],
            "description": "filesystem reads post-mutation disk state; git_blob_head (default) reads git HEAD blob",
        },
        "max_bytes": {"type": "integer", "required": False, "description": "Maximum bytes to return (default 65536)"},
    },
    "read_range": {
        "relative_path": {"type": "string", "required": True, "description": "Workspace-relative file path"},
        "start_line": {"type": "integer", "required": True, "description": "First line to read (1-based)"},
        "end_line": {"type": "integer", "required": True, "description": "Last line to read (1-based, inclusive)"},
    },
    "search": {
        "pattern": {"type": "string", "required": True, "description": "Text pattern or regex to search for"},
        "relative_path": {
            "type": "string",
            "required": False,
            "description": "Workspace-relative path to scope the search",
        },
    },
    "get_status": {},
    "write_file": {
        "relative_path": {"type": "string", "required": True, "description": "Workspace-relative path to write"},
        "content": {
            "type": "string",
            "required": True,
            "description": "Full file content to write (replaces entire file)",
        },
    },
    "edit_file": {
        "relative_path": {
            "type": "string",
            "required": True,
            "description": "Workspace-relative file path to mutate via SEARCH/REPLACE blocks",
        },
        "search_replace_blocks": {
            "type": "string",
            "required": True,
            "description": "Raw SEARCH/REPLACE block text applied through git apply with 3-way retry",
        },
    },
    "insert_at_anchor": {
        "relative_path": {
            "type": "string",
            "required": True,
            "description": "Workspace-relative file path to mutate via anchor insertion",
        },
        "anchor_text": {
            "type": "string",
            "required": True,
            "description": "Unique line-aligned anchor text to insert around",
        },
        "new_content": {
            "type": "string",
            "required": True,
            "description": "Text to insert before or after the matched anchor",
        },
        "position": {
            "type": "string",
            "required": False,
            "enum": ["before", "after"],
            "description": "Insert position relative to the anchor; defaults to after",
        },
        "indent_style": {
            "type": "string",
            "required": False,
            "enum": ["match_anchor", "preserve"],
            "description": "Whether to infer indentation from the anchor line; defaults to match_anchor",
        },
    },
    "apply_patch": {
        "patch_text": {
            "type": "string",
            "required": True,
            "description": "Unified diff patch text (do not include path args)",
        },
        "fallback_replace": {
            "type": "object",
            "required": False,
            "description": "Fallback range replacement if patch fails",
        },
    },
    "replace_range": {
        "relative_path": {"type": "string", "required": True, "description": "Workspace-relative file path"},
        "start_line": {"type": "integer", "required": True, "description": "First line to replace (1-based)"},
        "end_line": {"type": "integer", "required": True, "description": "Last line to replace (1-based, inclusive)"},
        "replacement": {"type": "string", "required": True, "description": "Text to replace the specified line range"},
    },
    "confirm_anchor": {
        "relative_path": {"type": "string", "required": True, "description": "Workspace-relative file path"},
        "symbol_name": {"type": "string", "required": True, "description": "Qualified symbol name"},
        "anchor_quote": {
            "type": "string",
            "required": True,
            "description": "Verbatim quote from the current source to confirm grounding",
        },
    },
    "cmd.run": {
        "command": {
            "type": "string|array",
            "required": True,
            "description": "Shell command string or argv list; must match allow-list prefix",
        },
        "timeout_seconds": {"type": "number", "required": False, "description": "Max execution time in seconds"},
    },
    "evidence.attach": {
        "evidence_ref": {"type": "string", "required": True, "description": "Evidence artifact reference URI"},
        "evidence_type": {
            "type": "string",
            "required": False,
            "enum": ["devrun", "review_validation", "artifact"],
            "description": "Type of evidence",
        },
    },
}

# Canonical multi-step sequences
_CANONICAL_SEQUENCES: list[dict[str, Any]] = [
    {
        "name": "discovery_then_mutate",
        "description": "Discover workspace structure before performing targeted mutation",
        "steps": ["list_dir", "search", "read_range", "edit_file"],
    },
    {
        "name": "discovery_then_insert",
        "description": "Discover, ground, then insert content adjacent to a unique anchor",
        "steps": ["list_dir", "search", "read_range", "insert_at_anchor"],
    },
    {
        "name": "write_and_verify",
        "description": "Write a new file then verify syntax",
        "steps": ["write_file", "cmd.run"],
    },
    {
        "name": "mutate_then_test",
        "description": "Mutate source, verify compilation, run test suite",
        "steps": ["edit_file", "cmd.run"],
    },
]

_MANIFEST_VERSION = "1.0"


def _manifest_visible_tool_names() -> set[str]:
    visible = {tool.name for tool in get_tool_contracts()}
    visible -= set(DEPRECATED_MUTATION_TOOLS)
    visible |= orchestration_visible_deprecated_mutation_tools()
    visible -= set(ORCHESTRATION_REMOVED_MUTATION_TOOLS)
    return visible


def _category_for(tool: ToolContract) -> str:
    if tool.name in {"cmd.run"}:
        return "system"
    if tool.name in {"evidence.attach"}:
        return "evidence"
    if tool.name in {"confirm_anchor"}:
        return "grounding"
    if tool.mutating:
        return "mutate"
    return "read"


def build_tool_manifest() -> dict[str, Any]:
    """Build the full tool capability manifest from the contract registry."""
    visible_tool_names = _manifest_visible_tool_names()
    tool_entries: list[dict[str, Any]] = []
    for tc in get_tool_contracts():
        if tc.name not in visible_tool_names:
            continue
        tool_entries.append(
            {
                "name": tc.name,
                "category": _category_for(tc),
                "description": tc.description,
                "mutating": tc.mutating,
                "args": _ARG_SCHEMAS.get(tc.name, {}),
                "sequencing": _TOOL_SEQUENCING.get(tc.name, {"must_precede": [], "must_follow": [], "constraints": []}),
                "examples": _TOOL_EXAMPLES.get(tc.name, {"valid": [], "invalid": []}),
            }
        )
    manifest = {
        "version": _MANIFEST_VERSION,
        "tool_count": len(tool_entries),
        "sequences": [
            sequence
            for sequence in _CANONICAL_SEQUENCES
            if all(step in visible_tool_names for step in sequence.get("steps", []))
        ],
        "primary_mutation_lane": PRIMARY_MUTATION_LANE,
        "deprecated_tools_hidden_by_default": sorted(set(ORCHESTRATION_FALLBACK_MUTATION_TOOLS) - visible_tool_names),
    }
    manifest["tools"] = tool_entries
    return manifest
