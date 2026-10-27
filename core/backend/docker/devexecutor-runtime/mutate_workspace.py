from __future__ import annotations

import ast
import builtins
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, cast

import libcst as cst

try:
    import tree_sitter_python
    from tree_sitter import Language, Node, Parser, Tree
except ImportError:  # pragma: no cover - container image is expected to provide tree-sitter.
    Language = None
    Node = Any
    Parser = None
    Tree = Any
    tree_sitter_python = None

WORKSPACE_BASE_COMMIT_ENV = "AIX_WORKSPACE_BASE_COMMIT_SHA"
SEARCH_MARKER = "<<<<<<< SEARCH"
DIVIDER_MARKER = "======="
REPLACE_MARKER = ">>>>>>> REPLACE"
_PYTHON_TS_LANGUAGE: Language | None = (
    Language(tree_sitter_python.language()) if Language and tree_sitter_python else None
)
_PYTHON_TS_PARSER: Parser | None = Parser(_PYTHON_TS_LANGUAGE) if _PYTHON_TS_LANGUAGE and Parser else None


def _workspace_root() -> Path:
    return Path(os.environ.get("WORKSPACE_ROOT", "/workspace")).resolve()


def _workspace_path_prefix() -> str:
    prefix = str(os.environ.get("WORKSPACE_PATH_PREFIX", "/workspace") or "/workspace").strip()
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/") or "/workspace"


def _normalize_relative_path(raw_path: str) -> str:
    candidate = str(raw_path or "").strip().replace("\\", "/")
    if not candidate:
        return candidate
    prefix = _workspace_path_prefix()
    if candidate == prefix:
        return "."
    if candidate.startswith(f"{prefix}/"):
        return candidate[len(prefix) + 1 :]
    return candidate.lstrip("/")


def _safe_resolve(workspace_root: str | Path, relative_path: str) -> Path:
    root = Path(workspace_root).resolve()
    normalized = _normalize_relative_path(relative_path)
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"workspace escape denied: {relative_path}") from exc
    return candidate


def _extract_changed_files(patch_text: str) -> list[str]:
    changed_files: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            changed_files.append(line.replace("+++ b/", "", 1).strip())
    return changed_files


def _estimate_diff_bytes(patch_text: str) -> int:
    total = 0
    for line in patch_text.splitlines():
        if line.startswith(("+++", "---", "@@", "diff --git")):
            continue
        if line.startswith("+") or line.startswith("-"):
            total += len(line.encode("utf-8"))
    return total


def _classify_patch_failure(stderr: str, stdout: str = "") -> tuple[str, str, bool]:
    err = f"{stderr}\n{stdout}".strip().lower()
    if "not a git repository" in err:
        return ("failed", "not_git_repo", False)
    if "corrupt patch" in err or "malformed patch" in err:
        return ("failed", "malformed_patch", False)
    if "does not exist in index" in err:
        return ("failed", "path_missing_in_index", False)
    if "already exists in working directory" in err:
        return ("failed", "path_conflict", False)
    if "patch failed" in err or "does not apply" in err:
        return ("conflict", "hunk_conflict", True)
    return ("failed", "apply_failed", False)


def _write_file(*, workspace_root: str, relative_path: str, content: str) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    old_bytes = target.read_bytes() if target.exists() else b""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
            newline="",
        ) as handle:
            handle.write(content)
            tmp_path = Path(handle.name)
        os.replace(str(tmp_path), str(target))
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    new_bytes = target.read_bytes()
    return {
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "operation": "write_file",
        "bytes_before": len(old_bytes),
        "bytes_after": len(new_bytes),
        "changed_bytes_estimate": abs(len(new_bytes) - len(old_bytes)),
        "atomic_write": True,
        "changed_files": [relative_path.replace("\\", "/")],
        "formatter": {"enabled": False, "executed": [], "errors": [], "reason": "container_runtime_no_formatters"},
    }


def _replace_range(
    *,
    workspace_root: str,
    relative_path: str,
    start_line: int,
    end_line: int,
    replacement: str,
) -> dict[str, Any]:
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    target = _safe_resolve(workspace_root, relative_path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if end_line > len(lines):
        raise ValueError(f"end_line out of range: {end_line} > {len(lines)}")
    replacement_lines = replacement.splitlines(keepends=True)
    if replacement and not replacement.endswith(("\n", "\r")) and replacement_lines:
        replacement_lines[-1] = replacement_lines[-1] + "\n"
    new_content = "".join(lines[: start_line - 1] + replacement_lines + lines[end_line:])
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
            newline="",
        ) as handle:
            handle.write(new_content)
            tmp_path = Path(handle.name)
        os.replace(str(tmp_path), str(target))
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return {
        "operation": "replace_range",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "start_line": start_line,
        "end_line": end_line,
        "replacement_line_count": len(replacement_lines),
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
    }


def _resolve_python_symbol_range(*, file_path: Path, symbol_name: str) -> tuple[int, int, int, str, str]:
    source = file_path.read_text(encoding="utf-8")
    if _PYTHON_TS_PARSER is not None:
        resolved = _resolve_python_symbol_range_tree_sitter(file_path=file_path, symbol_name=symbol_name, source=source)
        if resolved is not None:
            return resolved
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(file_path))
    requested_class: str | None = None
    requested_symbol = str(symbol_name or "").strip()
    fallback_symbol = ""
    if "." in requested_symbol:
        requested_class, requested_symbol = requested_symbol.rsplit(".", 1)
        requested_class = requested_class.strip() or None
        requested_symbol = requested_symbol.strip()
        if requested_class and "." in requested_class:
            requested_class = None
        fallback_symbol = _module_qualified_function_fallback(file_path=file_path, symbol_name=symbol_name)
        if fallback_symbol:
            requested_class = None
            requested_symbol = fallback_symbol
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name == requested_symbol and (requested_class is None or class_name == requested_class):
                        def_line = int(child.lineno)
                        def_col = len(source_lines[def_line - 1]) - len(source_lines[def_line - 1].lstrip(" "))
                        return (
                            def_line,
                            int(getattr(child, "end_lineno", child.lineno)),
                            def_col,
                            f"{class_name}.{child.name}",
                            "\n".join(
                                source_lines[int(child.lineno) - 1 : int(getattr(child, "end_lineno", child.lineno))]
                            ),
                        )
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and requested_class is None
            and node.name == requested_symbol
        ):
            def_line = int(node.lineno)
            def_col = len(source_lines[def_line - 1]) - len(source_lines[def_line - 1].lstrip(" "))
            return (
                def_line,
                int(getattr(node, "end_lineno", node.lineno)),
                def_col,
                node.name,
                "\n".join(source_lines[int(node.lineno) - 1 : int(getattr(node, "end_lineno", node.lineno))]),
            )
    raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={symbol_name}")


def _resolve_python_symbol_range_tree_sitter(
    *,
    file_path: Path,
    symbol_name: str,
    source: str,
) -> tuple[int, int, int, str, str] | None:
    if _PYTHON_TS_PARSER is None:
        return None
    source_bytes = source.encode("utf-8")
    tree = _PYTHON_TS_PARSER.parse(source_bytes)
    requested_class: str | None = None
    requested_symbol = str(symbol_name or "").strip()
    fallback_symbol = ""
    if "." in requested_symbol:
        requested_class, requested_symbol = requested_symbol.rsplit(".", 1)
        requested_class = requested_class.strip() or None
        requested_symbol = requested_symbol.strip()
        if requested_class and "." in requested_class:
            requested_class = None
        fallback_symbol = _module_qualified_function_fallback(file_path=file_path, symbol_name=symbol_name)
        if fallback_symbol:
            requested_class = None
            requested_symbol = fallback_symbol

    def walk_container(node: Node, *, qualifier: str) -> tuple[int, int, int, str, str] | None:
        for child in node.named_children:
            resolved = walk_definition(child, qualifier=qualifier, decorator_nodes=(), wrapped_node=None)
            if resolved is not None:
                return resolved
        return None

    def walk_definition(
        node: Node,
        *,
        qualifier: str,
        decorator_nodes: tuple[Node, ...],
        wrapped_node: Node | None,
    ) -> tuple[int, int, int, str, str] | None:
        if node.type == "decorated_definition":
            named_children = list(node.named_children)
            decorators = tuple(child for child in named_children if child.type == "decorator")
            inner = next(
                (
                    child
                    for child in reversed(named_children)
                    if child.type in {"function_definition", "class_definition"}
                ),
                None,
            )
            if inner is None:
                return None
            return walk_definition(inner, qualifier=qualifier, decorator_nodes=decorators, wrapped_node=node)

        if node.type == "class_definition":
            name_node = _tree_sitter_child_of_type(node, "identifier")
            if name_node is None:
                return None
            class_symbol_name = _node_text(source_bytes=source_bytes, node=name_node)
            current_qualifier = f"{qualifier}.{class_symbol_name}".strip(".")
            block = _tree_sitter_child_of_type(node, "block")
            if block is not None:
                return walk_container(block, qualifier=current_qualifier)
            return None

        if node.type != "function_definition":
            return None

        name_node = _tree_sitter_child_of_type(node, "identifier")
        if name_node is None:
            return None
        function_name = _node_text(source_bytes=source_bytes, node=name_node)
        enclosing_class_name: str | None = qualifier.split(".")[-1] if qualifier else None
        if function_name == requested_symbol and (requested_class is None or enclosing_class_name == requested_class):
            owner = wrapped_node or node
            qualified_name = f"{qualifier}.{function_name}".strip(".")
            def_col = int(node.start_point.column)
            start_line = int(owner.start_point.row) + 1
            end_line = int(owner.end_point.row) + 1
            symbol_source = source_bytes[owner.start_byte : owner.end_byte].decode("utf-8", errors="replace").rstrip()
            return start_line, end_line, def_col, qualified_name, symbol_source

        block = _tree_sitter_child_of_type(node, "block")
        if block is not None:
            return walk_container(block, qualifier=f"{qualifier}.{function_name}".strip("."))
        return None

    return walk_container(tree.root_node, qualifier="")


def _tree_sitter_child_of_type(node: Node, node_type: str) -> Node | None:
    for child in node.named_children:
        if child.type == node_type:
            return child
    return None


def _node_text(*, source_bytes: bytes, node: Node) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _build_grounded_symbol_payload(*, file_path: Path, qualified_name: str, symbol_source: str) -> dict[str, Any]:
    git_commit_sha, head_file_blob_sha = _git_grounding_for_path(file_path=file_path)
    injected_commit_sha = _normalized_expected_hash(os.environ.get(WORKSPACE_BASE_COMMIT_ENV))
    payload = {
        "file_path": str(file_path.resolve()),
        "qualified_name": qualified_name,
        "symbol_source": symbol_source,
        "symbol_source_hash": f"sha256:{hashlib.sha256(symbol_source.encode('utf-8')).hexdigest()}",
        "file_blob_sha": f"sha256:{hashlib.sha256(file_path.read_bytes()).hexdigest()}",
        "commit_sha": injected_commit_sha or git_commit_sha,
        "head_file_blob_sha": head_file_blob_sha,
    }
    if injected_commit_sha:
        # The proposal workspace already records the authoritative base commit.
        # Keep the git-walk value only as a diagnostic because worktree gitdir
        # pointers can break inside bind-mounted sandboxes.
        payload["workspace_base_commit_sha"] = injected_commit_sha
        payload["diagnostic_git_commit_sha"] = git_commit_sha
    return payload


def _parse_search_replace_blocks(block_text: str) -> list[dict[str, Any]]:
    text = str(block_text or "")
    lines = text.splitlines(keepends=True)
    index = 0
    blocks: list[dict[str, Any]] = []
    outside_chunks: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line.strip() == SEARCH_MARKER:
            index += 1
            search_lines: list[str] = []
            while index < len(lines) and lines[index].strip() != DIVIDER_MARKER:
                search_lines.append(lines[index])
                index += 1
            if index >= len(lines):
                raise ValueError("SEARCH_REPLACE_MISSING_DIVIDER: SEARCH/REPLACE block is missing ======= divider")
            index += 1
            replace_lines: list[str] = []
            while index < len(lines) and lines[index].strip() != REPLACE_MARKER:
                replace_lines.append(lines[index])
                index += 1
            if index >= len(lines):
                raise ValueError(
                    "SEARCH_REPLACE_MISSING_END: SEARCH/REPLACE block is missing >>>>>>> REPLACE terminator"
                )
            index += 1
            block_index = len(blocks) + 1
            search = "".join(search_lines)
            replace = "".join(replace_lines)
            if not search:
                raise ValueError(
                    f"SEARCH_REPLACE_EMPTY_SEARCH: SEARCH/REPLACE block #{block_index} has empty SEARCH text"
                )
            blocks.append({"index": block_index, "search": search, "replace": replace})
            continue
        outside_chunks.append(line)
        index += 1
    if not blocks:
        raise ValueError("SEARCH_REPLACE_NO_BLOCKS: No SEARCH/REPLACE blocks were found in edit_file input")
    if "".join(outside_chunks).strip():
        raise ValueError("SEARCH_REPLACE_UNEXPECTED_TEXT: SEARCH/REPLACE input contains unexpected text outside blocks")
    return blocks


def _find_line_aligned_matches(text: str, search: str) -> list[tuple[int, int]]:
    exact_matches = _find_candidate_matches(text=text, candidate=search)
    if exact_matches:
        return exact_matches
    if search.endswith("\n") and not search.endswith("\r\n"):
        trimmed_matches = _find_candidate_matches(text=text, candidate=search[:-1])
        if trimmed_matches:
            return trimmed_matches
    return _find_fuzzy_line_aligned_matches(text=text, search=search)


def _find_fuzzy_line_aligned_matches(*, text: str, search: str) -> list[tuple[int, int]]:
    search_lines = str(search or "").splitlines(keepends=True)
    if len(search_lines) < 2:
        return []
    text_lines = text.splitlines(keepends=True)
    if len(search_lines) > len(text_lines):
        return []
    offsets = _line_offsets(text_lines)
    normalized_search = _normalize_match_text("".join(search_lines))
    scored: list[tuple[float, int, int]] = []
    for start_index in range(0, len(text_lines) - len(search_lines) + 1):
        window = "".join(text_lines[start_index : start_index + len(search_lines)])
        similarity = difflib.SequenceMatcher(None, normalized_search, _normalize_match_text(window)).ratio()
        if similarity < 0.92:
            continue
        scored.append((similarity, offsets[start_index], offsets[start_index + len(search_lines)]))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0]
    ambiguous = [item for item in scored[1:] if (best[0] - item[0]) <= 0.01]
    if ambiguous:
        return [(item[1], item[2]) for item in [best, *ambiguous]]
    return [(best[1], best[2])]


def _line_offsets(lines: list[str]) -> list[int]:
    offsets = [0]
    total = 0
    for line in lines:
        total += len(line)
        offsets.append(total)
    return offsets


def _normalize_match_text(text: str) -> str:
    return "\n".join(line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n"))


def _find_candidate_matches(*, text: str, candidate: str) -> list[tuple[int, int]]:
    if not candidate:
        return []
    matches: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(candidate, start)
        if index < 0:
            break
        end = index + len(candidate)
        if _is_line_aligned(text=text, start=index, end=end):
            matches.append((index, end))
        start = index + 1
    return matches


def _is_line_aligned(*, text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1] != "\n":
        return False
    if end >= len(text):
        return True
    if end > start and text[end - 1] == "\n":
        return True
    if text[end] != "\n":
        return False
    return True


def _build_file_patch(*, relative_path: str, before_text: str, after_text: str) -> str:
    normalized_relative_path = relative_path.replace("\\", "/")
    before_lines = str(before_text or "").splitlines(keepends=True)
    after_lines = str(after_text or "").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{normalized_relative_path}",
            tofile=f"b/{normalized_relative_path}",
        )
    )


def _has_terminal_newline(text: str) -> bool:
    return not text or text.endswith(("\n", "\r"))


def _coerce_inserted_content(*, anchor_line: str, new_content: str, indent_style: str) -> str:
    content = str(new_content or "")
    if indent_style != "match_anchor":
        return content
    anchor_indent = anchor_line[: len(anchor_line) - len(anchor_line.lstrip(" \t"))]
    adjusted_lines: list[str] = []
    for line in content.splitlines(keepends=True):
        if line.strip() and not line[:1].isspace():
            adjusted_lines.append(f"{anchor_indent}{line}")
        else:
            adjusted_lines.append(line)
    return "".join(adjusted_lines)


def _edit_file(*, workspace_root: str, relative_path: str, search_replace_blocks: str) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))
    blocks = _parse_search_replace_blocks(search_replace_blocks)
    before = target.read_text(encoding="utf-8", errors="replace")
    after = before
    block_results: list[dict[str, int | bool]] = []
    for block in blocks:
        search = str(block.get("search") or "")
        replace = str(block.get("replace") or "")
        matches = _find_line_aligned_matches(after, search)
        if not matches:
            raise ValueError(
                "SEARCH_REPLACE_NOT_FOUND: "
                f"SEARCH text for block #{block['index']} was not matchable in the target file\n"
                f"Target file: {relative_path.replace('\\', '/')}\n"
                "Attempted SEARCH text:\n"
                f"{search}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"SEARCH_REPLACE_AMBIGUOUS: SEARCH text for block #{block['index']} matched multiple locations in the target file"
            )
        start, end = matches[0]
        after = after[:start] + replace + after[end:]
        block_results.append({"index": int(block["index"]), "match_count": len(matches), "no_op": search == replace})
    normalized_relative_path = relative_path.replace("\\", "/")
    if after == before:
        return {
            "operation": "edit_file",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "block_count": len(blocks),
            "blocks": block_results,
            "changed_files": [],
            "applied": False,
            "skipped": True,
            "code": "ALREADY_APPLIED",
            "atomic_write": True,
        }
    if not (_has_terminal_newline(before) and _has_terminal_newline(after)):
        write_result = _write_file(workspace_root=workspace_root, relative_path=normalized_relative_path, content=after)
        return {
            "operation": "edit_file",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "block_count": len(blocks),
            "blocks": block_results,
            "changed_files": list(write_result.get("changed_files") or [normalized_relative_path]),
            "applied": True,
            "skipped": False,
            "atomic_write": True,
            "adapter": "write_file_fallback",
            "adapter_chain": [
                {"name": "git_apply", "available": True},
                {"name": "write_file_fallback", "available": True},
            ],
            "fallback_reason": "missing_terminal_newline",
        }
    patch_text = _build_file_patch(relative_path=normalized_relative_path, before_text=before, after_text=after)
    patch_result = _apply_patch(workspace_root=workspace_root, patch_text=patch_text)
    return {
        "operation": "edit_file",
        "path": str(target),
        "relative_path": normalized_relative_path,
        "block_count": len(blocks),
        "blocks": block_results,
        "changed_files": list(patch_result.get("changed_files") or [normalized_relative_path]),
        "applied": True,
        "skipped": False,
        "atomic_write": True,
        "patch_attempt": patch_result.get("patch_attempt"),
        "patch_bytes": patch_result.get("patch_bytes"),
        "patch_sha1": patch_result.get("patch_sha1"),
        "approx_diff_bytes": patch_result.get("approx_diff_bytes"),
        "retry_count": patch_result.get("retry_count"),
        "retry_strategy": patch_result.get("retry_strategy"),
        "adapter": patch_result.get("adapter", "git_apply"),
        "adapter_chain": patch_result.get("adapter_chain") or [{"name": "git_apply", "available": True}],
    }


def _insert_at_anchor(
    *,
    workspace_root: str,
    relative_path: str,
    anchor_text: str,
    new_content: str,
    position: str = "after",
    indent_style: str = "match_anchor",
) -> dict[str, Any]:
    normalized_position = str(position or "after").strip().lower()
    if normalized_position not in {"before", "after"}:
        raise ValueError(f"INVALID_INSERT_POSITION: {position!r}")
    target = _safe_resolve(workspace_root, relative_path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))
    before = target.read_text(encoding="utf-8", errors="replace")
    matches = _find_line_aligned_matches(before, anchor_text)
    if not matches:
        raise ValueError("ANCHOR_NOT_FOUND: anchor_text was not matchable in the target file")
    if len(matches) > 1:
        raise ValueError("ANCHOR_AMBIGUOUS: anchor_text matched multiple locations in the target file")
    start, end = matches[0]
    anchor_line = anchor_text.splitlines()[0] if anchor_text.splitlines() else ""
    inserted = _coerce_inserted_content(anchor_line=anchor_line, new_content=new_content, indent_style=indent_style)
    insert_at = start if normalized_position == "before" else end
    after = before[:insert_at] + inserted + before[insert_at:]
    if after == before:
        return {
            "operation": "insert_at_anchor",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "changed_files": [],
            "applied": False,
            "skipped": True,
            "code": "ALREADY_APPLIED",
            "atomic_write": True,
            "position": normalized_position,
            "indent_style": indent_style,
        }
    if not (_has_terminal_newline(before) and _has_terminal_newline(after)):
        write_result = _write_file(
            workspace_root=workspace_root, relative_path=relative_path.replace("\\", "/"), content=after
        )
        return {
            "operation": "insert_at_anchor",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "changed_files": list(write_result.get("changed_files") or [relative_path.replace("\\", "/")]),
            "applied": True,
            "skipped": False,
            "atomic_write": True,
            "position": normalized_position,
            "indent_style": indent_style,
            "adapter": "write_file_fallback",
            "adapter_chain": [
                {"name": "git_apply", "available": True},
                {"name": "write_file_fallback", "available": True},
            ],
            "fallback_reason": "missing_terminal_newline",
        }
    patch_text = _build_file_patch(relative_path=relative_path.replace("\\", "/"), before_text=before, after_text=after)
    patch_result = _apply_patch(workspace_root=workspace_root, patch_text=patch_text)
    return {
        "operation": "insert_at_anchor",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "changed_files": list(patch_result.get("changed_files") or [relative_path.replace("\\", "/")]),
        "applied": True,
        "skipped": False,
        "atomic_write": True,
        "position": normalized_position,
        "indent_style": indent_style,
        "patch_attempt": patch_result.get("patch_attempt"),
        "patch_bytes": patch_result.get("patch_bytes"),
        "patch_sha1": patch_result.get("patch_sha1"),
        "approx_diff_bytes": patch_result.get("approx_diff_bytes"),
        "retry_count": patch_result.get("retry_count"),
        "retry_strategy": patch_result.get("retry_strategy"),
        "adapter": patch_result.get("adapter", "git_apply"),
        "adapter_chain": patch_result.get("adapter_chain") or [{"name": "git_apply", "available": True}],
    }


def _normalized_expected_hash(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _assert_symbol_grounding(
    *,
    file_path: Path,
    qualified_name: str,
    symbol_source: str,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    grounded_symbol = _build_grounded_symbol_payload(
        file_path=file_path,
        qualified_name=qualified_name,
        symbol_source=symbol_source,
    )
    expected_commit = _normalized_expected_hash(expected_base_commit_sha)
    expected_file = _normalized_expected_hash(expected_file_blob_sha)
    expected_symbol = _normalized_expected_hash(expected_symbol_source_hash)
    current_commit = _normalized_expected_hash(grounded_symbol.get("commit_sha"))
    current_file = _normalized_expected_hash(grounded_symbol.get("file_blob_sha"))
    current_symbol = _normalized_expected_hash(grounded_symbol.get("symbol_source_hash"))

    if expected_symbol and current_symbol != expected_symbol:
        raise ValueError(
            "SYMBOL_DIVERGED "
            f"qualified_name={qualified_name} "
            f"expected_symbol_source_hash={expected_symbol} "
            f"current_symbol_source_hash={current_symbol}"
        )
    if expected_file and current_file != expected_file:
        raise ValueError(
            "BASE_DIVERGED "
            f"qualified_name={qualified_name} "
            f"expected_file_blob_sha={expected_file} "
            f"current_file_blob_sha={current_file}"
        )
    if expected_commit and current_commit != expected_commit:
        raise ValueError(
            "BASE_DIVERGED "
            f"qualified_name={qualified_name} "
            f"expected_base_commit_sha={expected_commit} "
            f"current_base_commit_sha={current_commit}"
        )
    return grounded_symbol


def _git_grounding_for_path(*, file_path: Path) -> tuple[str | None, str | None]:
    repo_root = _find_git_repo_root(file_path)
    if repo_root is None:
        return None, None
    try:
        relative_path = file_path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return None, None
    commit_sha = _run_git(repo_root=repo_root, args=["rev-parse", "HEAD"])
    file_blob_sha = _run_git(repo_root=repo_root, args=["rev-parse", f"HEAD:{relative_path}"])
    return commit_sha, file_blob_sha


def _find_git_repo_root(file_path: Path) -> Path | None:
    current = file_path.resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _run_git(*, repo_root: Path, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-c", f"safe.directory={repo_root}", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _module_qualified_function_fallback(*, file_path: Path, symbol_name: str) -> str:
    normalized = str(symbol_name or "").strip()
    if normalized.count(".") < 1:
        return ""
    parts = [part.strip() for part in normalized.split(".") if part.strip()]
    if len(parts) < 2:
        return ""
    requested_module = ".".join(parts[:-1])
    requested_symbol = parts[-1]
    path_parts = [part.strip() for part in file_path.with_suffix("").parts if part.strip()]
    normalized_path = ".".join(part.replace("-", "_") for part in path_parts)
    if normalized_path == requested_module or normalized_path.endswith(f".{requested_module}"):
        return requested_symbol
    return ""


def _reindent_function_replacement(*, replacement: str, base_indent: int) -> str:
    dedented = textwrap.dedent(replacement).strip("\n")
    prefix = " " * max(0, int(base_indent))
    lines = dedented.splitlines()
    rendered = [(f"{prefix}{line}" if line.strip() else line) for line in lines]
    return "\n".join(rendered) + "\n"


def _function_header_text(source: str) -> str:
    for line in str(source or "").splitlines():
        if line.strip():
            return line.rstrip()
    return ""


def _replacement_preview_lines(replacement: str, *, limit: int = 20) -> list[str]:
    return [str(line) for line in str(replacement or "").splitlines()[:limit]]


def _single_top_level_function(
    *, candidate: str, qualified_name: str
) -> tuple[ast.Module, ast.FunctionDef | ast.AsyncFunctionDef]:
    try:
        tree = ast.parse(candidate)
    except SyntaxError as exc:
        message = str(exc)
        if "expected an indented block" in message:
            raise ValueError("MUTATION_FRAGMENT_REJECTED function body required") from exc
        raise ValueError(f"MUTATION_INVALID_FUNCTION_REPLACEMENT syntax={exc.msg}") from exc
    function_nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(function_nodes) != 1 or len(tree.body) != 1:
        raise ValueError("MUTATION_INVALID_FUNCTION_REPLACEMENT exactly_one_function_definition_required")
    func = function_nodes[0]
    expected_name = str(qualified_name or "").split(".")[-1].strip()
    if expected_name and func.name != expected_name:
        raise ValueError(
            f"MUTATION_INVALID_FUNCTION_SHAPE symbol_name_mismatch expected={expected_name} got={func.name}"
        )
    return tree, func


def _emit_replace_function_shape_debug(
    *,
    relative_path: str | None,
    qualified_name: str,
    original_source: str,
    replacement: str,
    original_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    replacement_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    failure_detail: str,
) -> None:
    payload = {
        "event": "replace_function_shape_validation",
        "relative_path": str(relative_path or "").strip().replace("\\", "/") or None,
        "resolved_symbol": str(qualified_name or "").strip() or None,
        "original_symbol_header": _function_header_text(original_source) or None,
        "replacement_header": _function_header_text(replacement) or None,
        "is_async_original": isinstance(original_func, ast.AsyncFunctionDef),
        "is_async_replacement": isinstance(replacement_func, ast.AsyncFunctionDef),
        "parsed_replacement_symbol_name": getattr(replacement_func, "name", None),
        "replacement_preview_lines": _replacement_preview_lines(replacement),
        "failure_detail": str(failure_detail or "").strip() or None,
    }
    sys.stderr.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")


def _validate_function_replacement(
    *, replacement: str, original_source: str, qualified_name: str, relative_path: str | None = None
) -> None:
    candidate = textwrap.dedent(str(replacement or "")).strip()
    if not candidate.startswith(("def ", "async def ")):
        raise ValueError("MUTATION_INVALID_FUNCTION_REPLACEMENT missing_function_signature")
    _tree, func = _single_top_level_function(candidate=candidate, qualified_name=qualified_name)
    if not func.body or all(
        isinstance(node, ast.Pass)
        or (
            isinstance(node, ast.Expr)
            and isinstance(getattr(node, "value", None), ast.Constant)
            and getattr(node.value, "value", None) is Ellipsis
        )
        for node in func.body
    ):
        raise ValueError("MUTATION_FRAGMENT_REJECTED function body required")
    original_tree = ast.parse(textwrap.dedent(str(original_source or "")).strip())
    original_func = next(
        (node for node in original_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if original_func is not None:
        if isinstance(original_func, ast.AsyncFunctionDef) != isinstance(func, ast.AsyncFunctionDef):
            _emit_replace_function_shape_debug(
                relative_path=relative_path,
                qualified_name=qualified_name,
                original_source=original_source,
                replacement=replacement,
                original_func=original_func,
                replacement_func=func,
                failure_detail="MUTATION_INVALID_FUNCTION_SHAPE async_mismatch",
            )
            raise ValueError("MUTATION_INVALID_FUNCTION_SHAPE async_mismatch")
        if "." in qualified_name:
            original_arg = original_func.args.args[0].arg if original_func.args.args else None
            replacement_arg = func.args.args[0].arg if func.args.args else None
            if original_arg != replacement_arg:
                raise ValueError("MUTATION_INVALID_FUNCTION_SHAPE method_receiver_mismatch")


def _callable_name(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _callable_name(node.value)
        return f"{base}.{node.attr.value}" if base else node.attr.value
    return ""


def _callee_matches(node: cst.BaseExpression, expected: str) -> bool:
    actual = _callable_name(node).strip()
    normalized_actual = actual.replace(" ", "")
    normalized_expected = str(expected or "").strip().replace(" ", "")
    if not normalized_actual or not normalized_expected:
        return False
    if normalized_actual == normalized_expected:
        return True
    return normalized_actual.split(".")[-1] == normalized_expected.split(".")[-1]


def _validate_update_call_kwarg(
    *,
    target: Path,
    symbol: str,
    callee: str,
    kwarg: str,
    new_value: str,
) -> list[dict[str, str]]:
    normalized_callee = str(callee or "").strip()
    normalized_kwarg = str(kwarg or "").strip()
    normalized_value = str(new_value or "").strip()
    failures: list[dict[str, str]] = []
    if not normalized_callee:
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "CALLEE_REQUIRED",
                "detail": "callee is required",
                "suggested_fix": "Provide the target call expression, for example self.list_entities.",
            }
        )
    if not normalized_kwarg:
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "KWARG_REQUIRED",
                "detail": "kwarg is required",
                "suggested_fix": "Provide the keyword argument name to update.",
            }
        )
    elif not normalized_kwarg.isidentifier():
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "INVALID_KWARG_NAME",
                "detail": f"kwarg={normalized_kwarg!r}",
                "suggested_fix": "Use a valid Python identifier for the kwarg name.",
            }
        )
    if not normalized_value:
        failures.append(
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "NEW_VALUE_REQUIRED",
                "detail": "new_value is required",
                "suggested_fix": "Provide the replacement Python expression for the kwarg value.",
            }
        )
    else:
        try:
            cst.parse_expression(normalized_value)
        except Exception as exc:
            failures.append(
                {
                    "tool": "update_call_kwarg",
                    "symbol": symbol,
                    "failure": "INVALID_NEW_VALUE_SYNTAX",
                    "detail": str(exc),
                    "suggested_fix": "Provide a valid Python expression for new_value.",
                }
            )
    if failures:
        return failures

    module = cst.parse_module(target.read_text(encoding="utf-8"))
    found_callee = False
    found_kwarg = False

    class _Visitor(cst.CSTVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self._inside_target = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
            self._class_stack.pop()

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            self._inside_target = current_name == symbol
            return True

        def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
            self._inside_target = False

        def visit_Call(self, node: cst.Call) -> bool:
            nonlocal found_callee, found_kwarg
            if not self._inside_target or found_kwarg:
                return True
            if not _callee_matches(node.func, normalized_callee):
                return True
            found_callee = True
            for arg in node.args:
                if arg.keyword and arg.keyword.value == normalized_kwarg:
                    found_kwarg = True
                    break
            return True

    module.visit(_Visitor())
    if not found_callee:
        return [
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "CALL_NOT_FOUND",
                "detail": f"callee={normalized_callee!r}",
                "suggested_fix": "Confirm the callee exists inside the grounded symbol body.",
            }
        ]
    if not found_kwarg:
        return [
            {
                "tool": "update_call_kwarg",
                "symbol": symbol,
                "failure": "CALL_KWARG_NOT_FOUND",
                "detail": f"callee={normalized_callee!r} kwarg={normalized_kwarg!r}",
                "suggested_fix": "Confirm the matching call already has the target keyword argument.",
            }
        ]
    return []


def _replace_lines_in_file(*, target: Path, start_line: int, end_line: int, replacement: str) -> None:
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line > len(lines):
        raise ValueError(f"end_line out of range: {end_line} > {len(lines)}")
    if start_line > len(lines) + 1:
        raise ValueError(f"start_line out of range: {start_line} > {len(lines) + 1}")
    replacement_lines = replacement.splitlines(keepends=True)
    if replacement and not replacement.endswith(("\n", "\r")) and replacement_lines:
        replacement_lines[-1] = replacement_lines[-1] + "\n"
    if end_line < start_line:
        new_lines = lines[: start_line - 1] + replacement_lines + lines[start_line - 1 :]
    else:
        new_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
            newline="",
        ) as handle:
            handle.write("".join(new_lines))
            tmp_path = Path(handle.name)
        os.replace(str(tmp_path), str(target))
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _replace_in_function(
    *,
    workspace_root: str,
    relative_path: str,
    function_name: str,
    replacement: str,
    dry_run: bool = False,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    start_line, end_line, def_col, qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=function_name
    )
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        qualified_name=qualified_name,
        symbol_source=original_source,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    _validate_function_replacement(
        replacement=replacement,
        original_source=original_source,
        qualified_name=qualified_name,
        relative_path=relative_path,
    )
    if dry_run:
        return {
            "operation": "replace_in_function",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "symbol_name": function_name,
            "dry_run": True,
            "dry_run_failures": [],
            "validated": True,
            "changed_files": [],
            "symbol_range": {
                "start_line": start_line,
                "end_line": end_line,
                "def_col": def_col,
                "qualified_name": qualified_name,
            },
            "grounded_symbol": grounded_symbol,
        }
    _replace_function_with_libcst(target=target, qualified_name=qualified_name, replacement=replacement)
    return {
        "operation": "replace_in_function",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "symbol_name": function_name,
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
        "symbol_range": {
            "start_line": start_line,
            "end_line": end_line,
            "def_col": def_col,
            "qualified_name": qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }


def _edit_symbol(
    *,
    workspace_root: str,
    relative_path: str,
    symbol: str,
    mode: str,
    new_signature: str | None = None,
    anchor_pattern: str | None = None,
    position: str | None = None,
    new_code: str | None = None,
    dry_run: bool = False,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    start_line, end_line, def_col, qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=symbol
    )
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        qualified_name=qualified_name,
        symbol_source=original_source,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _dry_run_edit_symbol(
        target=target,
        symbol=qualified_name,
        mode=mode,
        new_signature=str(new_signature or ""),
        anchor_pattern=str(anchor_pattern or ""),
        position=str(position or ""),
        new_code=str(new_code or ""),
    )
    if dry_run:
        return {
            "operation": "edit_symbol",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "symbol": qualified_name,
            "mode": str(mode or "").strip(),
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "grounded_symbol": grounded_symbol,
        }
    if failures:
        first = failures[0]
        raise ValueError(f"{first['failure']}: {first['detail']}")
    _edit_symbol_with_libcst(
        target=target,
        qualified_name=qualified_name,
        mode=str(mode or "").strip(),
        new_signature=str(new_signature or ""),
        anchor_pattern=str(anchor_pattern or ""),
        position=str(position or ""),
        new_code=str(new_code or ""),
    )
    return {
        "operation": "edit_symbol",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "symbol": qualified_name,
        "mode": str(mode or "").strip(),
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
        "dry_run": False,
        "dry_run_failures": [],
        "symbol_range": {
            "start_line": start_line,
            "end_line": end_line,
            "def_col": def_col,
            "qualified_name": qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }


def _mutation_failure_payload(
    *,
    tool: str,
    symbol: str,
    failure_code: str,
    detail: str,
    suggested_fix: str,
    mode: str | None = None,
) -> dict[str, str]:
    payload = {
        "tool": str(tool or "").strip(),
        "symbol": str(symbol or "").strip(),
        "failure": str(failure_code or "").strip(),
        "failure_code": str(failure_code or "").strip(),
        "detail": str(detail or "").strip(),
        "suggested_fix": str(suggested_fix or "").strip(),
    }
    if str(mode or "").strip():
        payload["mode"] = str(mode or "").strip()
    return payload


def _normalize_signature_parameter_kind(value: str | None) -> str:
    normalized = str(value or "").strip().lower() or "auto"
    if normalized == "optional":
        normalized = "keyword_only"
    elif normalized == "keyword":
        normalized = "keyword_only"
    if normalized not in {"auto", "keyword_only", "positional_or_keyword"}:
        raise ValueError(f"unsupported_parameter_kind:{normalized}")
    return normalized


def _parse_signature_extension_stub(
    *,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str,
) -> tuple[cst.Param, cst.ParamStar | cst.Param | cst.MaybeSentinel]:
    normalized_kind = _normalize_signature_parameter_kind(parameter_kind)
    if normalized_kind == "keyword_only":
        parsed = cst.parse_module(
            "def _signature_stub(\n"
            "    *,\n"
            f"    {parameter_name}: {parameter_type} = {default_value},\n"
            "):\n"
            "    pass\n"
        )
        func = parsed.body[0]
        assert isinstance(func, cst.FunctionDef)
        return func.params.kwonly_params[0], func.params.star_arg
    parsed = cst.parse_module(f"def _signature_stub({parameter_name}: {parameter_type} = {default_value}):\n    pass\n")
    func = parsed.body[0]
    assert isinstance(func, cst.FunctionDef)
    return func.params.params[0], cst.MaybeSentinel.DEFAULT


def _collect_cst_parameter_names(params: cst.Parameters) -> set[str]:
    names: set[str] = set()
    for collection in (params.posonly_params, params.params, params.kwonly_params):
        for param in collection:
            names.add(param.name.value)
    if isinstance(params.star_arg, cst.Param):
        names.add(params.star_arg.name.value)
    if params.star_kwarg is not None:
        names.add(params.star_kwarg.name.value)
    return names


def _validate_extend_python_signature(
    *,
    target: Path,
    symbol: str,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str,
) -> list[dict[str, str]]:
    normalized_name = str(parameter_name or "").strip()
    normalized_type = str(parameter_type or "").strip()
    normalized_default = str(default_value or "").strip()
    failures: list[dict[str, str]] = []
    try:
        normalized_kind = _normalize_signature_parameter_kind(parameter_kind)
    except ValueError as exc:
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="INVALID_PARAMETER_KIND",
                detail=str(exc),
                suggested_fix="Use parameter_kind='auto', 'keyword_only', or 'positional_or_keyword'.",
            )
        ]
    if not normalized_name:
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PARAMETER_NAME_REQUIRED",
                detail="parameter_name is required",
                suggested_fix="Provide the new optional parameter name.",
            )
        )
    elif not normalized_name.isidentifier():
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="INVALID_PARAMETER_NAME",
                detail=f"parameter_name={normalized_name!r}",
                suggested_fix="Use a valid Python identifier for parameter_name.",
            )
        )
    if not normalized_type:
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PARAMETER_TYPE_REQUIRED",
                detail="parameter_type is required",
                suggested_fix="Provide a non-empty Python annotation string for parameter_type.",
            )
        )
    if not normalized_default:
        failures.append(
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="DEFAULT_VALUE_REQUIRED",
                detail="default_value is required",
                suggested_fix="Provide a valid Python expression for default_value.",
            )
        )
    if failures:
        return failures
    try:
        param_node, _ = _parse_signature_extension_stub(
            parameter_name=normalized_name,
            parameter_type=normalized_type,
            default_value=normalized_default,
            parameter_kind=normalized_kind,
        )
    except Exception as exc:
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="INVALID_SIGNATURE_EXTENSION_SYNTAX",
                detail=str(exc),
                suggested_fix="Provide a valid parameter_type and default_value expression for the new optional parameter.",
            )
        ]
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    target_params: cst.Parameters | None = None

    class _Visitor(cst.CSTVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
            self._class_stack.pop()

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            nonlocal target_params
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            if current_name == symbol:
                target_params = node.params
            return True

    module.visit(_Visitor())
    if target_params is None:
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PYTHON_SYMBOL_NOT_FOUND",
                detail=f"symbol={symbol}",
                suggested_fix="Confirm the fully qualified symbol name with confirm_anchor before extending the signature.",
            )
        ]
    if param_node.name.value in _collect_cst_parameter_names(target_params):
        return [
            _mutation_failure_payload(
                tool="extend_python_signature",
                symbol=symbol,
                failure_code="PARAMETER_ALREADY_PRESENT",
                detail=f"parameter_name={param_node.name.value}",
                suggested_fix="Append only a new optional parameter that does not already exist on the target function.",
            )
        ]
    return []


def _extend_python_signature_with_libcst(
    *,
    target: Path,
    qualified_name: str,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str,
) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    normalized_kind = _normalize_signature_parameter_kind(parameter_kind)
    explicit_kind = "keyword_only" if normalized_kind == "keyword_only" else "positional_or_keyword"
    parsed_param, parsed_star_arg = _parse_signature_extension_stub(
        parameter_name=parameter_name,
        parameter_type=parameter_type,
        default_value=default_value,
        parameter_kind=explicit_kind,
    )

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self.replaced = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.CSTNode:
            self._class_stack.pop()
            return updated_node

        def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.CSTNode:
            current_name = ".".join([*self._class_stack, original_node.name.value]).strip(".")
            if current_name != qualified_name:
                return updated_node
            params = updated_node.params
            existing_names = _collect_cst_parameter_names(params)
            if parameter_name in existing_names:
                raise ValueError(f"PARAMETER_ALREADY_PRESENT parameter_name={parameter_name}")
            target_kind = normalized_kind
            if target_kind == "auto":
                target_kind = (
                    "keyword_only"
                    if params.kwonly_params
                    or params.star_kwarg is not None
                    or params.star_arg is not cst.MaybeSentinel.DEFAULT
                    else "positional_or_keyword"
                )
            if target_kind == "keyword_only":
                star_arg = params.star_arg
                if star_arg is cst.MaybeSentinel.DEFAULT:
                    star_arg = parsed_star_arg
                new_params = params.with_changes(
                    star_arg=star_arg,
                    kwonly_params=[*params.kwonly_params, parsed_param],
                )
            else:
                new_params = params.with_changes(params=[*params.params, parsed_param])
            self.replaced = True
            return updated_node.with_changes(params=new_params)

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={qualified_name}")
    target.write_text(updated.code, encoding="utf-8")


def _extend_python_signature(
    *,
    workspace_root: str,
    relative_path: str,
    symbol: str,
    parameter_name: str,
    parameter_type: str,
    default_value: str,
    parameter_kind: str = "auto",
    dry_run: bool = False,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    start_line, end_line, def_col, qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=symbol
    )
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        qualified_name=qualified_name,
        symbol_source=original_source,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _validate_extend_python_signature(
        target=target,
        symbol=qualified_name,
        parameter_name=parameter_name,
        parameter_type=parameter_type,
        default_value=default_value,
        parameter_kind=parameter_kind,
    )
    normalized_relative_path = relative_path.replace("\\", "/")
    normalized_kind = _normalize_signature_parameter_kind(parameter_kind)
    if dry_run:
        return {
            "operation": "extend_python_signature",
            "path": str(target),
            "relative_path": normalized_relative_path,
            "symbol": qualified_name,
            "parameter_name": str(parameter_name or "").strip(),
            "parameter_kind": normalized_kind,
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "grounded_symbol": grounded_symbol,
        }
    if failures:
        first = failures[0]
        raise ValueError(f"{first['failure']}: {first['detail']}")
    _extend_python_signature_with_libcst(
        target=target,
        qualified_name=qualified_name,
        parameter_name=parameter_name,
        parameter_type=parameter_type,
        default_value=default_value,
        parameter_kind=parameter_kind,
    )
    return {
        "operation": "extend_python_signature",
        "path": str(target),
        "relative_path": normalized_relative_path,
        "symbol": qualified_name,
        "parameter_name": str(parameter_name or "").strip(),
        "parameter_kind": normalized_kind,
        "changed_files": [normalized_relative_path],
        "atomic_write": True,
        "dry_run": False,
        "dry_run_failures": [],
        "symbol_range": {
            "start_line": start_line,
            "end_line": end_line,
            "def_col": def_col,
            "qualified_name": qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }


def _update_call_kwarg(
    *,
    workspace_root: str,
    relative_path: str,
    symbol: str,
    callee: str,
    kwarg: str,
    new_value: str,
    dry_run: bool = False,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    start_line, end_line, def_col, qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=symbol
    )
    grounded_symbol = _assert_symbol_grounding(
        file_path=target,
        qualified_name=qualified_name,
        symbol_source=original_source,
        expected_base_commit_sha=expected_base_commit_sha,
        expected_file_blob_sha=expected_file_blob_sha,
        expected_symbol_source_hash=expected_symbol_source_hash,
    )
    failures = _validate_update_call_kwarg(
        target=target,
        symbol=qualified_name,
        callee=callee,
        kwarg=kwarg,
        new_value=new_value,
    )
    if dry_run:
        return {
            "operation": "update_call_kwarg",
            "path": str(target),
            "relative_path": relative_path.replace("\\", "/"),
            "symbol": qualified_name,
            "callee": str(callee or "").strip(),
            "kwarg": str(kwarg or "").strip(),
            "dry_run": True,
            "dry_run_failures": failures,
            "validated": not failures,
            "changed_files": [],
            "grounded_symbol": grounded_symbol,
        }
    if failures:
        first = failures[0]
        raise ValueError(f"{first['failure']}: {first['detail']}")
    _update_call_kwarg_with_libcst(
        target=target,
        qualified_name=qualified_name,
        callee=str(callee or "").strip(),
        kwarg=str(kwarg or "").strip(),
        new_value=str(new_value or "").strip(),
    )
    return {
        "operation": "update_call_kwarg",
        "path": str(target),
        "relative_path": relative_path.replace("\\", "/"),
        "symbol": qualified_name,
        "callee": str(callee or "").strip(),
        "kwarg": str(kwarg or "").strip(),
        "changed_files": [relative_path.replace("\\", "/")],
        "atomic_write": True,
        "dry_run": False,
        "dry_run_failures": [],
        "symbol_range": {
            "start_line": start_line,
            "end_line": end_line,
            "def_col": def_col,
            "qualified_name": qualified_name,
        },
        "grounded_symbol": grounded_symbol,
    }


def _replace_function_with_libcst(*, target: Path, qualified_name: str, replacement: str) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    replacement_node = _parse_replacement_function_node(replacement)

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self.replaced = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(
            self,
            original_node: cst.ClassDef,
            updated_node: cst.ClassDef,
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            self._class_stack.pop()
            return updated_node

        def leave_FunctionDef(
            self,
            original_node: cst.FunctionDef,
            updated_node: cst.FunctionDef,
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            current_name = ".".join([*self._class_stack, original_node.name.value]).strip(".")
            if current_name != qualified_name:
                return updated_node
            self.replaced = True
            return replacement_node.with_changes(
                leading_lines=original_node.leading_lines,
                decorators=original_node.decorators,
            )

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={qualified_name}")
    target.write_text(updated.code, encoding="utf-8")


def _parse_replacement_function_node(replacement: str) -> cst.FunctionDef:
    parsed = cst.parse_module(textwrap.dedent(str(replacement or "")).strip() + "\n")
    for statement in parsed.body:
        if isinstance(statement, cst.FunctionDef):
            return statement
    raise ValueError("MUTATION_INVALID_FUNCTION_REPLACEMENT missing_function_node")


def _validate_signature_edit(*, target: Path, symbol: str, new_signature: str) -> list[dict[str, str]]:
    header = str(new_signature or "").strip()
    if not header:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "SIGNATURE_REQUIRED",
                "detail": "new_signature is required",
                "suggested_fix": "Provide a full function signature header.",
            }
        ]
    if not header.startswith(("def ", "async def ")):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "INVALID_SIGNATURE_FORMAT",
                "detail": "new_signature must begin with def or async def",
                "suggested_fix": "Provide the full signature line.",
            }
        ]
    signature_stub = header if header.endswith(":") else f"{header}:"
    try:
        tree = ast.parse(f"{signature_stub}\n    pass\n")
    except SyntaxError as exc:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "INVALID_SIGNATURE_SYNTAX",
                "detail": exc.msg,
                "suggested_fix": "Fix parameter ordering and syntax.",
            }
        ]
    func = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    expected_name = str(symbol).split(".")[-1]
    if func is None:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "INVALID_SIGNATURE_FORMAT",
                "detail": "signature did not parse into a function definition",
                "suggested_fix": "Provide exactly one function signature.",
            }
        ]
    if func.name != expected_name:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "signature",
                "failure": "SIGNATURE_NAME_MISMATCH",
                "detail": f"expected {expected_name!r}, got {func.name!r}",
                "suggested_fix": "Keep the existing function name.",
            }
        ]
    _start_line, _end_line, _def_col, qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=symbol
    )
    original_tree = ast.parse(textwrap.dedent(str(original_source or "").strip()))
    original_func = next(
        (node for node in original_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if original_func is not None:
        if isinstance(original_func, ast.AsyncFunctionDef) != isinstance(func, ast.AsyncFunctionDef):
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": qualified_name,
                    "mode": "signature",
                    "failure": "MUTATION_INVALID_FUNCTION_SHAPE",
                    "detail": "async_mismatch",
                    "suggested_fix": "Preserve async def when editing an async method signature.",
                }
            ]
        if "." in qualified_name:
            original_arg = original_func.args.args[0].arg if original_func.args.args else None
            replacement_arg = func.args.args[0].arg if func.args.args else None
            if original_arg == "self" and replacement_arg != "self":
                return [
                    {
                        "tool": "edit_symbol",
                        "symbol": qualified_name,
                        "mode": "signature",
                        "failure": "MUTATION_INVALID_FUNCTION_SHAPE",
                        "detail": "method_receiver_mismatch",
                        "suggested_fix": "Preserve the method receiver argument when editing a class method signature.",
                    }
                ]
    return []


def _parse_signature_function_node(signature_text: str) -> cst.FunctionDef:
    header = str(signature_text or "").strip()
    stub = header if header.endswith(":") else f"{header}:"
    parsed = cst.parse_module(f"{stub}\n    pass\n")
    for statement in parsed.body:
        if isinstance(statement, cst.FunctionDef):
            return statement
    raise ValueError("INVALID_SIGNATURE_FORMAT")


def _parse_insert_statements(new_code: str) -> list[cst.BaseStatement]:
    parsed = cst.parse_module(textwrap.dedent(str(new_code or "")).strip() + "\n")
    statements = cast(list[cst.BaseStatement], list(parsed.body))
    if not statements:
        raise ValueError("NEW_CODE_REQUIRED")
    return statements


def _function_body_contains_anchor(*, target: Path, qualified_name: str, anchor_pattern: str) -> bool:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    renderer = cst.Module(body=[])
    normalized_anchor = " ".join(str(anchor_pattern or "").split())
    found = False

    class _AnchorVisitor(cst.CSTVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
            self._class_stack.pop()

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            nonlocal found
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            if current_name != qualified_name:
                return True
            if isinstance(node.body, cst.IndentedBlock):
                for statement in node.body.body:
                    rendered = " ".join(renderer.code_for_node(statement).split())
                    if normalized_anchor and normalized_anchor in rendered:
                        found = True
                        break
            return True

    module.visit(_AnchorVisitor())
    return found


def _validate_body_insert_edit(
    *,
    target: Path,
    symbol: str,
    anchor_pattern: str,
    position: str,
    new_code: str,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    if position not in {"before", "after"}:
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "INVALID_POSITION",
                "detail": f"unsupported position={position!r}",
                "suggested_fix": "Use before or after.",
            }
        )
    if not anchor_pattern.strip():
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "ANCHOR_REQUIRED",
                "detail": "anchor_pattern is required",
                "suggested_fix": "Provide an exact body anchor.",
            }
        )
    if not str(new_code or "").strip():
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "NEW_CODE_REQUIRED",
                "detail": "new_code is required",
                "suggested_fix": "Provide valid Python statements.",
            }
        )
    if failures:
        return failures
    try:
        _parse_insert_statements(new_code)
    except Exception as exc:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "INVALID_NEW_CODE_SYNTAX",
                "detail": str(exc),
                "suggested_fix": "Provide valid Python statements only.",
            }
        ]
    if not _function_body_contains_anchor(target=target, qualified_name=symbol, anchor_pattern=anchor_pattern):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "ANCHOR_NOT_FOUND",
                "detail": f"anchor_pattern {anchor_pattern!r} was not found in the resolved symbol body",
                "suggested_fix": (
                    "body_insert with anchor_pattern is a legacy compatibility shim. "
                    "Do not retry with a different anchor. "
                    "Instead: use replace_in_function with args.function_name and the complete updated "
                    "args.replacement function body, or use write_file with the full updated file content. "
                    "Read the current function body first with read_range or read_file."
                ),
                "shim_event": "anchor_not_found_compat",
            }
        ]
    unresolved_names = _find_unresolved_insert_names(target=target, symbol=symbol, new_code=new_code)
    if unresolved_names:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "body_insert",
                "failure": "UNDEFINED_LOCAL_REFERENCE",
                "detail": ", ".join(unresolved_names),
                "suggested_fix": "Reference only existing parameters/locals, or define new locals within the inserted code block before using them.",
            }
        ]
    return []


def _find_unresolved_insert_names(*, target: Path, symbol: str, new_code: str) -> list[str]:
    _start_line, _end_line, _def_col, _qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=symbol
    )
    original_tree = ast.parse(textwrap.dedent(original_source))
    original_func = next(
        (node for node in original_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if original_func is None:
        return []

    inserted_tree = ast.parse(textwrap.dedent(str(new_code or "").strip()))
    builtins_allowed = set(dir(builtins)) | {"True", "False", "None"}

    def _collect_original_allowed(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        allowed: set[str] = {arg.arg for arg in func.args.args}
        allowed.update(arg.arg for arg in func.args.kwonlyargs)
        if func.args.vararg is not None:
            allowed.add(func.args.vararg.arg)
        if func.args.kwarg is not None:
            allowed.add(func.args.kwarg.arg)

        class _OriginalAssignedNameCollector(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    allowed.add(node.id)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                allowed.add(node.name)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                allowed.add(node.name)
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                allowed.add(node.name)
                self.generic_visit(node)

        _OriginalAssignedNameCollector().visit(func)
        return allowed

    class _StmtNameCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.loads: set[str] = set()
            self.stores: set[str] = set()

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                self.loads.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                self.stores.add(node.id)

        def visit_arg(self, node: ast.arg) -> None:
            self.stores.add(node.arg)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stores.add(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.stores.add(node.name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stores.add(node.name)

    allowed = _collect_original_allowed(original_func) | builtins_allowed
    unresolved: list[str] = []
    for stmt in inserted_tree.body:
        collector = _StmtNameCollector()
        collector.visit(stmt)
        for name in sorted(collector.loads):
            if name not in allowed and name not in unresolved:
                unresolved.append(name)
        allowed.update(collector.stores)
    return unresolved


def _normalize_edit_symbol_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "body":
        return "body_insert"
    return normalized


def _dry_run_edit_symbol(
    *,
    target: Path,
    symbol: str,
    mode: str,
    new_signature: str,
    anchor_pattern: str,
    position: str,
    new_code: str,
) -> list[dict[str, str]]:
    normalized_mode = _normalize_edit_symbol_mode(mode)
    if normalized_mode not in {"signature", "body_insert", "sql_blocks", "full"}:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": normalized_mode,
                "failure": "INVALID_MODE",
                "detail": f"unsupported mode={normalized_mode!r}",
                "suggested_fix": "Use signature, body_insert, sql_blocks, or full.",
            }
        ]
    if "." not in str(symbol or ""):
        return [
            {
                "tool": "edit_symbol",
                "symbol": str(symbol or ""),
                "mode": normalized_mode,
                "failure": "INVALID_SYMBOL_FORMAT",
                "detail": "symbol must be fully qualified",
                "suggested_fix": "Use ClassName.method_name.",
            }
        ]
    if normalized_mode == "signature":
        return _validate_signature_edit(target=target, symbol=symbol, new_signature=new_signature)
    if normalized_mode == "body_insert":
        return _validate_body_insert_edit(
            target=target,
            symbol=symbol,
            anchor_pattern=anchor_pattern,
            position=position,
            new_code=new_code,
        )
    if normalized_mode == "sql_blocks":
        return _validate_sql_block_edit(target=target, symbol=symbol, new_code=new_code)
    return _validate_full_symbol_edit(target=target, symbol=symbol, replacement=new_code)


def _parse_sql_block_payload(raw_value: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(str(raw_value or "").strip())
    except Exception as exc:
        raise ValueError(f"invalid_json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    top_level_conditions = payload.get("conditions")
    if top_level_conditions is not None:
        if not isinstance(top_level_conditions, list) or not top_level_conditions:
            raise ValueError("top-level conditions must be a non-empty list")
        top_level_parsed_conditions: list[dict[str, str]] = []
        for condition in top_level_conditions:
            if not isinstance(condition, dict):
                raise ValueError("each condition must be an object")
            column = condition.get("column")
            operator = condition.get("operator", "=")
            param_variable = condition.get("param_variable")
            if not isinstance(column, str) or not column.strip():
                raise ValueError("each condition column must be a non-empty string")
            if not isinstance(operator, str) or not operator.strip():
                raise ValueError("each condition operator must be a non-empty string")
            if not isinstance(param_variable, str) or not param_variable.strip():
                raise ValueError("each condition param_variable must be a non-empty string")
            top_level_parsed_conditions.append(
                {
                    "column": column.strip(),
                    "operator": operator.strip(),
                    "param_variable": param_variable.strip(),
                }
            )
        return [{"index": "all", "conditions": top_level_parsed_conditions}]
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("blocks array is required")
    parsed: list[dict[str, Any]] = []
    for item in blocks:
        if not isinstance(item, dict):
            raise ValueError("each block entry must be an object")
        index = item.get("index")
        sql = item.get("sql")
        predicate = item.get("predicate")
        argument = item.get("argument", "relation_type")
        conditions = item.get("conditions")
        if not isinstance(index, int) or index < 1:
            raise ValueError("each block index must be a positive integer")
        if isinstance(sql, str) and sql.strip():
            parsed.append({"index": index, "sql": sql})
            continue
        if conditions is not None:
            if not isinstance(conditions, list) or not conditions:
                raise ValueError("each block conditions must be a non-empty list")
            block_parsed_conditions: list[dict[str, str]] = []
            for condition in conditions:
                if not isinstance(condition, dict):
                    raise ValueError("each condition must be an object")
                column = condition.get("column")
                operator = condition.get("operator", "=")
                param_variable = condition.get("param_variable")
                if not isinstance(column, str) or not column.strip():
                    raise ValueError("each condition column must be a non-empty string")
                if not isinstance(operator, str) or not operator.strip():
                    raise ValueError("each condition operator must be a non-empty string")
                if not isinstance(param_variable, str) or not param_variable.strip():
                    raise ValueError("each condition param_variable must be a non-empty string")
                block_parsed_conditions.append(
                    {
                        "column": column.strip(),
                        "operator": operator.strip(),
                        "param_variable": param_variable.strip(),
                    }
                )
            parsed.append({"index": index, "conditions": block_parsed_conditions})
            continue
        if not isinstance(predicate, str) or not predicate.strip():
            raise ValueError("each block must provide either non-empty sql or non-empty predicate")
        if not isinstance(argument, str) or not argument.strip():
            raise ValueError("each block argument must be a non-empty string")
        parsed.append({"index": index, "predicate": predicate, "argument": argument})
    return parsed


def _renumber_asyncpg_placeholders(sql: str, *, insertion_index: int) -> str:
    def _replace(match: re.Match[str]) -> str:
        current = int(match.group(1))
        return f"${current + 1}" if current >= insertion_index else match.group(0)

    return re.sub(r"\$(\d+)", _replace, sql)


def _infer_sql_predicate_insertion_index(*, original_sql: str) -> int:
    limit_match = re.search(r"\bLIMIT\s+\$(\d+)", original_sql, flags=re.IGNORECASE)
    if limit_match:
        return int(limit_match.group(1))
    placeholders = [int(match) for match in re.findall(r"\$(\d+)", original_sql)]
    return (max(placeholders) + 1) if placeholders else 1


def _materialize_sql_conditions(
    *,
    conditions: list[dict[str, str]],
    insertion_index: int,
) -> tuple[list[str], list[str]]:
    predicates: list[str] = []
    arguments: list[str] = []
    next_index = insertion_index
    for condition in conditions:
        predicates.append(f"{condition['column']} {condition['operator']} ${next_index}")
        arguments.append(condition["param_variable"])
        next_index += 1
    return predicates, arguments


def _extract_sql_metadata(sql: str) -> dict[str, object]:
    normalized = " ".join(sql.split())
    table_names = re.findall(r"\b[a-z_]+\.[a-z_]+\b", normalized, flags=re.IGNORECASE)
    projection = _sql_projection(normalized)
    order_match = re.search(r"\bORDER\s+BY\b.*?(?=\bLIMIT\b|$)", normalized, flags=re.IGNORECASE)
    order_by = " ".join(order_match.group(0).split()) if order_match else None
    placeholders = [int(match) for match in re.findall(r"\$(\d+)", normalized)]
    return {
        "table_names": table_names,
        "projection": projection,
        "order_by": order_by,
        "placeholders": placeholders,
    }


def _validate_mutated_sql_block(
    *,
    original_sql: str,
    mutated_sql: str,
    symbol: str,
    expected_predicate_sql: str | list[str] | None = None,
    expected_max_param: int | None = None,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    metadata = _extract_sql_metadata(original_sql)
    mutated_metadata = _extract_sql_metadata(mutated_sql)
    table_names = [str(item) for item in cast(list[object], metadata["table_names"])]
    original_projection = str(metadata["projection"]) if metadata["projection"] is not None else None
    mutated_projection = str(mutated_metadata["projection"]) if mutated_metadata["projection"] is not None else None
    order_by = str(metadata["order_by"]) if metadata["order_by"] is not None else None
    actual_placeholders = [int(str(item)) for item in cast(list[object], mutated_metadata["placeholders"])]
    if "${" in mutated_sql:
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "invalid_template_placeholder",
                "suggested_fix": "Let the harness materialize asyncpg placeholders; do not emit template placeholders like ${}.",
            }
        )
    if "AND AND" in mutated_sql.upper():
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "duplicate_and_connector",
                "suggested_fix": "Return only the bare predicate; the harness owns connector insertion.",
            }
        )
    for table_name in table_names:
        if table_name not in mutated_sql:
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"missing_table_name:{table_name}",
                    "suggested_fix": "Preserve schema-qualified table names from the original SQL block.",
                }
            )
            break
    if original_projection and original_projection != mutated_projection:
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "projection_changed",
                "suggested_fix": "Preserve the existing SELECT column list exactly.",
            }
        )
    if order_by and order_by not in " ".join(mutated_sql.split()):
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "order_by_missing",
                "suggested_fix": "Preserve the existing ORDER BY clause verbatim.",
            }
        )
    if expected_predicate_sql:
        expected_predicates = (
            [expected_predicate_sql] if isinstance(expected_predicate_sql, str) else list(expected_predicate_sql)
        )
        for predicate_sql in expected_predicates:
            if predicate_sql not in mutated_sql:
                failures.append(
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "sql_blocks",
                        "failure": "SQL_PRESERVATION_VIOLATED",
                        "detail": f"predicate_missing:{predicate_sql}",
                        "suggested_fix": "Add only the requested predicate to the existing WHERE clause.",
                    }
                )
    if expected_max_param is not None:
        actual_max_param = max(actual_placeholders) if actual_placeholders else 0
        if actual_max_param != expected_max_param:
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"param_count_mismatch:expected${expected_max_param}:got${actual_max_param}",
                    "suggested_fix": "Preserve placeholder continuity and insert the new asyncpg parameter at the next available index.",
                }
            )
    return failures


def _inject_sql_predicate(*, original_sql: str, predicate: str, insertion_index: int) -> str:
    clean_predicate = str(predicate or "").strip()
    if not clean_predicate:
        raise ValueError("empty_predicate")
    if re.search(r"\bSELECT\b|\bFROM\b", clean_predicate, flags=re.IGNORECASE):
        raise ValueError("predicate_must_not_contain_select_or_from")
    clean_predicate = re.sub(r"^(AND|OR)\s+", "", clean_predicate, flags=re.IGNORECASE)
    predicate_sql = clean_predicate.replace("?", f"${insertion_index}")
    renumbered_sql = _renumber_asyncpg_placeholders(original_sql, insertion_index=insertion_index)
    where_match = re.search(
        r"(?is)\bWHERE\b(?P<body>.*?)(?P<suffix>\bORDER\s+BY\b.*|\bLIMIT\b.*|$)",
        renumbered_sql,
    )
    if not where_match:
        raise ValueError("missing_where_clause")
    body = where_match.group("body")
    trailing_ws = body[len(body.rstrip()) :]
    indent_matches = re.findall(r"\n([ \t]*)\S", body)
    connector_indent = indent_matches[-1] if indent_matches else " "
    spliced_body = body.rstrip() + f"\n{connector_indent}AND {predicate_sql}" + trailing_ws
    return renumbered_sql[: where_match.start("body")] + spliced_body + renumbered_sql[where_match.end("body") :]


def _validate_single_sql_block_replacement(
    *, original_sql: str, replacement_sql: str, symbol: str
) -> list[dict[str, str]]:
    if "$$" in replacement_sql:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "invalid_placeholder_style",
                "suggested_fix": "Use asyncpg positional placeholders like $1, $2; do not emit $$ placeholder syntax.",
            }
        ]
    for table_name in re.findall(r"\b[a-z_]+\.[a-z_]+\b", original_sql, flags=re.IGNORECASE):
        if table_name not in replacement_sql:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"missing_table_name:{table_name}",
                    "suggested_fix": "Preserve schema-qualified table names from the original SQL block.",
                }
            ]
    if (
        _sql_projection(original_sql)
        and _sql_projection(replacement_sql)
        and _sql_projection(original_sql) != _sql_projection(replacement_sql)
    ):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "projection_changed",
                "suggested_fix": "Preserve the existing SELECT column list; do not broaden to SELECT * or change the curated projection.",
            }
        ]
    missing_identifiers = sorted(_sql_where_identifiers(original_sql) - _sql_where_identifiers(replacement_sql))
    if missing_identifiers:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": f"where_identifiers_missing:{', '.join(missing_identifiers)}",
                "suggested_fix": "Preserve existing WHERE-clause identifiers and add new filter conditions without renaming existing columns.",
            }
        ]
    return []


def _validate_sql_block_predicate(*, predicate: str, argument: str, symbol: str) -> list[dict[str, str]]:
    if re.search(r"\bSELECT\b|\bFROM\b", str(predicate or ""), flags=re.IGNORECASE):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_PREDICATE",
                "detail": "predicate_contains_select_or_from",
                "suggested_fix": "Return only the additive WHERE-clause predicate, for example relation_type = ?.",
            }
        ]
    if "$$" in str(predicate or ""):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_PREDICATE",
                "detail": "invalid_placeholder_style",
                "suggested_fix": "Use ? as the placeholder marker in sql_blocks predicate payloads; the harness will assign asyncpg indexes.",
            }
        ]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", str(argument or "").strip()):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_ARGUMENT",
                "detail": "argument must be a simple identifier or dotted attribute path",
                "suggested_fix": "Use a simple argument like relation_type.",
            }
        ]
    return []


def _validate_sql_block_conditions(*, conditions: list[dict[str, str]], symbol: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    allowed_operators = {"=", "!=", "<>", ">", ">=", "<", "<="}
    for condition in conditions:
        column = str(condition.get("column") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        param_variable = str(condition.get("param_variable") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", column):
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "INVALID_SQL_CONDITION",
                    "detail": f"invalid_column:{column}",
                    "suggested_fix": "Use a simple SQL column identifier such as relation_type.",
                }
            )
        if operator not in allowed_operators:
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "INVALID_SQL_CONDITION",
                    "detail": f"invalid_operator:{operator}",
                    "suggested_fix": "Use a supported SQL operator such as =.",
                }
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", param_variable):
            failures.append(
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "sql_blocks",
                    "failure": "INVALID_SQL_ARGUMENT",
                    "detail": "param_variable must be a simple identifier or dotted attribute path",
                    "suggested_fix": "Use a simple argument like relation_type.",
                }
            )
    return failures


def _validate_sql_block_edit(*, target: Path, symbol: str, new_code: str) -> list[dict[str, str]]:
    try:
        blocks = _parse_sql_block_payload(new_code)
    except ValueError as exc:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "INVALID_SQL_BLOCK_PAYLOAD",
                "detail": str(exc),
                "suggested_fix": 'Provide JSON like {"blocks":[{"index":1,"sql":"SELECT ..."}]}.',
            }
        ]
    _start_line, _end_line, _def_col, qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=symbol
    )
    original_sql = _extract_sql_literals_from_function_source(original_source)
    if not original_sql:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_BLOCKS_NOT_FOUND",
                "detail": "no SQL blocks were extracted from the resolved symbol",
                "suggested_fix": "Use sql_blocks only for SQL-heavy methods with extracted SQL literals.",
            }
        ]
    failures: list[dict[str, str]] = []
    seen_indexes: set[int] = set()
    applies_to_all = False
    has_semantic_conditions = False
    for block in blocks:
        if block["index"] == "all":
            applies_to_all = True
            indexes = list(range(1, len(original_sql) + 1))
        else:
            indexes = [int(block["index"])]
        for index in indexes:
            if index in seen_indexes:
                failures.append(
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "sql_blocks",
                        "failure": "DUPLICATE_SQL_BLOCK_INDEX",
                        "detail": str(index),
                        "suggested_fix": "Provide each SQL block index at most once.",
                    }
                )
                continue
            seen_indexes.add(index)
            if index > len(original_sql):
                failures.append(
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "sql_blocks",
                        "failure": "SQL_BLOCK_INDEX_OUT_OF_RANGE",
                        "detail": str(index),
                        "suggested_fix": "Use only extracted SQL block indexes from the current symbol context.",
                    }
                )
                continue
            if "sql" in block:
                failures.extend(
                    _validate_single_sql_block_replacement(
                        original_sql=original_sql[index - 1], replacement_sql=str(block["sql"]), symbol=symbol
                    )
                )
            else:
                original_block = original_sql[index - 1]
                inferred_index = _infer_sql_predicate_insertion_index(original_sql=original_block)
                if "conditions" in block:
                    has_semantic_conditions = True
                    predicates_sql, _arguments = _materialize_sql_conditions(
                        conditions=list(block["conditions"]),
                        insertion_index=inferred_index,
                    )
                    failures.extend(
                        _validate_sql_block_conditions(
                            conditions=list(block["conditions"]),
                            symbol=symbol,
                        )
                    )
                    predicate_input = " AND ".join(predicates_sql)
                    expected_predicates: str | list[str] = predicates_sql
                else:
                    predicate_sql = str(block["predicate"]).strip().replace("?", f"${inferred_index}")
                    predicates_sql = [predicate_sql]
                    predicate_input = str(block["predicate"])
                    expected_predicates = predicate_sql
                    failures.extend(
                        _validate_sql_block_predicate(
                            predicate=str(block["predicate"]),
                            argument=str(block["argument"]),
                            symbol=symbol,
                        )
                    )
                try:
                    mutated_sql = _inject_sql_predicate(
                        original_sql=original_block,
                        predicate=predicate_input,
                        insertion_index=inferred_index,
                    )
                except ValueError as exc:
                    failures.append(
                        {
                            "tool": "edit_symbol",
                            "symbol": symbol,
                            "mode": "sql_blocks",
                            "failure": "INVALID_SQL_PREDICATE",
                            "detail": str(exc),
                            "suggested_fix": "Return only a bare additive WHERE predicate and let the harness own connector insertion.",
                        }
                    )
                    continue
                mutated_placeholders = cast(list[int], _extract_sql_metadata(mutated_sql)["placeholders"])
                failures.extend(
                    _validate_mutated_sql_block(
                        original_sql=original_block,
                        mutated_sql=mutated_sql,
                        symbol=symbol,
                        expected_predicate_sql=expected_predicates,
                        expected_max_param=(max(mutated_placeholders) if mutated_placeholders else None),
                    )
                )
    if has_semantic_conditions and not applies_to_all and len(seen_indexes) != len(original_sql):
        failures.append(
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "sql_blocks",
                "failure": "SQL_BLOCKS_INCOMPLETE",
                "detail": f"provided={len(seen_indexes)} extracted={len(original_sql)}",
                "suggested_fix": "Provide semantic SQL conditions for every extracted SQL block in the target symbol.",
            }
        )
    return failures


def _validate_full_symbol_edit(*, target: Path, symbol: str, replacement: str) -> list[dict[str, str]]:
    candidate = str(replacement or "").strip()
    if not candidate:
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "FULL_REPLACEMENT_REQUIRED",
                "detail": "new_code is required for mode='full'",
                "suggested_fix": "Provide the full updated function or method source in new_code.",
            }
        ]
    if not candidate.startswith(("def ", "async def ")):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "INVALID_FULL_REPLACEMENT_FORMAT",
                "detail": "replacement must begin with def or async def",
                "suggested_fix": "Provide the full replacement function source beginning with def or async def.",
            }
        ]
    _start_line, _end_line, _def_col, qualified_name, original_source = _resolve_python_symbol_range(
        file_path=target, symbol_name=symbol
    )
    try:
        _validate_function_replacement(
            replacement=candidate,
            original_source=original_source,
            qualified_name=qualified_name,
            relative_path=str(target),
        )
    except ValueError as exc:
        detail = str(exc).replace("MUTATION_INVALID_FUNCTION_REPLACEMENT ", "", 1).strip()
        failure = "MUTATION_INVALID_FUNCTION_REPLACEMENT"
        if detail.startswith("MUTATION_INVALID_FUNCTION_SHAPE"):
            failure = "MUTATION_INVALID_FUNCTION_SHAPE"
            detail = detail.replace("MUTATION_INVALID_FUNCTION_SHAPE", "", 1).strip() or "invalid_shape"
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": failure,
                "detail": detail or "invalid_replacement",
                "suggested_fix": "Preserve the original async/method shape and provide a complete valid replacement body.",
            }
        ]
    sql_failures = _validate_sql_preservation(
        original_source=original_source,
        replacement_source=textwrap.dedent(candidate),
        symbol=symbol,
    )
    if sql_failures:
        return sql_failures
    return []


def _extract_sql_literals_from_function_source(source: str) -> list[str]:
    try:
        tree = ast.parse(textwrap.dedent(str(source or "").strip()))
    except SyntaxError:
        return []
    sql_blocks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = str(node.value)
            normalized = " ".join(text.split())
            upper = normalized.upper()
            if "SELECT " in upper and " FROM " in upper:
                sql_blocks.append(normalized)
    return sql_blocks


def _render_simple_string_like(*, original_literal: str, new_value: str) -> cst.SimpleString:
    match = re.match(r"^(?P<prefix>[rRuUbBfF]*)(?P<quote>'''|\"\"\"|'|\")", original_literal)
    if not match:
        return cst.SimpleString(repr(new_value))
    prefix = match.group("prefix")
    quote = match.group("quote")
    if len(quote) == 3:
        rendered = new_value.replace("\\", "\\\\").replace(quote, "\\" + quote)
        return cst.SimpleString(f"{prefix}{quote}{rendered}{quote}")
    rendered = new_value.replace("\\", "\\\\").replace(quote, "\\" + quote).replace("\n", "\\n")
    return cst.SimpleString(f"{prefix}{quote}{rendered}{quote}")


def _sql_projection(sql: str) -> str | None:
    match = re.search(r"SELECT\s+(.*?)\s+FROM\s", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return " ".join(match.group(1).split())


def _sql_where_identifiers(sql: str) -> set[str]:
    match = re.search(r"\bWHERE\b(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return set()
    where_text = match.group(1)
    return {
        token
        for token in re.findall(r"\b[a-z_][a-z0-9_]*\b", where_text, flags=re.IGNORECASE)
        if token.lower() not in {"where", "and", "or", "not", "null", "is", "desc", "asc"}
    }


def _validate_sql_preservation(*, original_source: str, replacement_source: str, symbol: str) -> list[dict[str, str]]:
    original_sql = _extract_sql_literals_from_function_source(original_source)
    if not original_sql:
        return []
    replacement_sql = _extract_sql_literals_from_function_source(replacement_source)
    if len(replacement_sql) < len(original_sql):
        return [
            {
                "tool": "edit_symbol",
                "symbol": symbol,
                "mode": "full",
                "failure": "SQL_PRESERVATION_VIOLATED",
                "detail": "missing_sql_blocks",
                "suggested_fix": "Preserve the existing SQL blocks verbatim and only add the requested filter condition and parameter threading.",
            }
        ]
    for index, original_block in enumerate(original_sql):
        replacement_block = replacement_sql[index]
        if "$$" in replacement_block:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "full",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": "invalid_placeholder_style",
                    "suggested_fix": "Use asyncpg positional placeholders like $1, $2; do not emit $$ placeholder syntax.",
                }
            ]
        for table_name in re.findall(r"\b[a-z_]+\.[a-z_]+\b", original_block, flags=re.IGNORECASE):
            if table_name not in replacement_block:
                return [
                    {
                        "tool": "edit_symbol",
                        "symbol": symbol,
                        "mode": "full",
                        "failure": "SQL_PRESERVATION_VIOLATED",
                        "detail": f"missing_table_name:{table_name}",
                        "suggested_fix": "Preserve schema-qualified table names from the original SQL block.",
                    }
                ]
        original_projection = _sql_projection(original_block)
        replacement_projection = _sql_projection(replacement_block)
        if original_projection and replacement_projection and original_projection != replacement_projection:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "full",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": "projection_changed",
                    "suggested_fix": "Preserve the existing SELECT column list; do not broaden to SELECT * or change the curated projection.",
                }
            ]
        missing_identifiers = sorted(_sql_where_identifiers(original_block) - _sql_where_identifiers(replacement_block))
        if missing_identifiers:
            return [
                {
                    "tool": "edit_symbol",
                    "symbol": symbol,
                    "mode": "full",
                    "failure": "SQL_PRESERVATION_VIOLATED",
                    "detail": f"where_identifiers_missing:{', '.join(missing_identifiers)}",
                    "suggested_fix": "Preserve existing WHERE-clause identifiers and add new filter conditions without renaming existing columns.",
                }
            ]
    return []


def _edit_symbol_with_libcst(
    *,
    target: Path,
    qualified_name: str,
    mode: str,
    new_signature: str,
    anchor_pattern: str,
    position: str,
    new_code: str,
) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    renderer = cst.Module(body=[])
    normalized_mode = _normalize_edit_symbol_mode(mode)
    signature_node = _parse_signature_function_node(new_signature) if normalized_mode == "signature" else None
    replacement_node = _parse_replacement_function_node(new_code) if normalized_mode == "full" else None
    insert_nodes = _parse_insert_statements(new_code) if normalized_mode == "body_insert" else []
    sql_replacement_items = _parse_sql_block_payload(new_code) if normalized_mode == "sql_blocks" else []
    sql_replacement_map = (
        {int(item["index"]): dict(item) for item in sql_replacement_items if item.get("index") != "all"}
        if normalized_mode == "sql_blocks"
        else {}
    )
    sql_apply_all_payload = next(
        (dict(item) for item in sql_replacement_items if item.get("index") == "all"),
        None,
    )
    normalized_anchor = " ".join(str(anchor_pattern or "").split())

    def _insert_into_block(block: cst.IndentedBlock) -> tuple[cst.IndentedBlock, bool]:
        statements = list(block.body)
        for idx, statement in enumerate(statements):
            updated_statement, replaced = _insert_into_statement(statement)
            statements[idx] = updated_statement
            if replaced:
                return block.with_changes(body=statements), True
            rendered = " ".join(renderer.code_for_node(updated_statement).split())
            if normalized_anchor and normalized_anchor in rendered:
                insertion_index = idx if position == "before" else idx + 1
                new_body = [*statements[:insertion_index], *insert_nodes, *statements[insertion_index:]]
                return block.with_changes(body=new_body), True
        return block, False

    def _insert_into_else(else_node: cst.Else) -> tuple[cst.Else, bool]:
        if isinstance(else_node.body, cst.IndentedBlock):
            new_body, replaced = _insert_into_block(else_node.body)
            if replaced:
                return else_node.with_changes(body=new_body), True
        return else_node, False

    def _insert_into_statement(statement: cst.BaseStatement) -> tuple[cst.BaseStatement, bool]:
        if isinstance(statement, cst.If):
            if isinstance(statement.body, cst.IndentedBlock):
                new_body, replaced = _insert_into_block(statement.body)
                if replaced:
                    return statement.with_changes(body=new_body), True
            if isinstance(statement.orelse, cst.Else):
                new_else, replaced = _insert_into_else(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_else), True
            elif isinstance(statement.orelse, cst.If):
                new_elif, replaced = _insert_into_statement(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_elif), True
        elif isinstance(statement, cst.Try):
            if isinstance(statement.body, cst.IndentedBlock):
                new_body, replaced = _insert_into_block(statement.body)
                if replaced:
                    return statement.with_changes(body=new_body), True
            new_handlers: list[cst.ExceptHandler] = []
            for handler in statement.handlers:
                if isinstance(handler.body, cst.IndentedBlock):
                    new_body, replaced = _insert_into_block(handler.body)
                    if replaced:
                        return (
                            statement.with_changes(
                                handlers=(
                                    *new_handlers,
                                    handler.with_changes(body=new_body),
                                    *statement.handlers[len(new_handlers) + 1 :],
                                )
                            ),
                            True,
                        )
                new_handlers.append(handler)
            if isinstance(statement.orelse, cst.Else):
                new_else, replaced = _insert_into_else(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_else), True
            if statement.finalbody is not None and isinstance(statement.finalbody.body, cst.IndentedBlock):
                new_final, replaced = _insert_into_block(statement.finalbody.body)
                if replaced:
                    return statement.with_changes(finalbody=statement.finalbody.with_changes(body=new_final)), True
        elif isinstance(statement, (cst.For, cst.While)):
            if isinstance(statement.body, cst.IndentedBlock):
                new_body, replaced = _insert_into_block(statement.body)
                if replaced:
                    return statement.with_changes(body=new_body), True
            if isinstance(statement.orelse, cst.Else):
                new_else, replaced = _insert_into_else(statement.orelse)
                if replaced:
                    return statement.with_changes(orelse=new_else), True
        elif isinstance(statement, cst.With):
            if isinstance(statement.body, cst.IndentedBlock):
                new_body, replaced = _insert_into_block(statement.body)
                if replaced:
                    return statement.with_changes(body=new_body), True
        return statement, False

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self.replaced = False
            self._inside_target = False
            self._sql_index = 0
            self._sql_literal_index = 0

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(
            self, original_node: cst.ClassDef, updated_node: cst.ClassDef
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            self._class_stack.pop()
            return updated_node

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            if normalized_mode == "sql_blocks" and current_name == qualified_name:
                self._inside_target = True
                self._sql_index = 0
                self._sql_literal_index = 0
            return True

        def leave_FunctionDef(
            self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            current_name = ".".join([*self._class_stack, original_node.name.value]).strip(".")
            if current_name != qualified_name:
                return updated_node
            if normalized_mode == "signature":
                assert signature_node is not None
                self.replaced = True
                return updated_node.with_changes(
                    params=signature_node.params,
                    returns=signature_node.returns,
                    asynchronous=signature_node.asynchronous,
                    whitespace_after_def=signature_node.whitespace_after_def,
                )
            if normalized_mode == "body_insert":
                if not isinstance(updated_node.body, cst.IndentedBlock):
                    raise ValueError("MUTATION_INVALID_BODY_INSERT unexpected_function_body")
                new_body, replaced = _insert_into_block(updated_node.body)
                if not replaced:
                    raise ValueError(f"ANCHOR_NOT_FOUND anchor={anchor_pattern!r}")
                self.replaced = True
                return updated_node.with_changes(body=new_body)
            if normalized_mode == "full":
                assert replacement_node is not None
                self.replaced = True
                return replacement_node.with_changes(
                    leading_lines=original_node.leading_lines,
                    decorators=original_node.decorators,
                )
            if normalized_mode == "sql_blocks":
                self._inside_target = False
                self.replaced = True
                return updated_node
            raise ValueError(f"INVALID_MODE mode={normalized_mode!r}")

        def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
            if normalized_mode != "sql_blocks" or not self._inside_target:
                return updated_node
            if not updated_node.args:
                return updated_node
            first_arg = updated_node.args[0].value
            if not isinstance(first_arg, cst.SimpleString):
                return updated_node
            try:
                original_sql = ast.literal_eval(first_arg.value)
            except Exception:
                return updated_node
            if not isinstance(original_sql, str):
                return updated_node
            normalized = " ".join(original_sql.split())
            upper = normalized.upper()
            if "SELECT " not in upper or " FROM " not in upper:
                return updated_node
            self._sql_index += 1
            payload = sql_replacement_map.get(self._sql_index)
            if payload is None and sql_apply_all_payload is not None:
                payload = sql_apply_all_payload
            if payload is None:
                return updated_node
            if "sql" in payload:
                return updated_node.with_changes(
                    args=[
                        cst.Arg(
                            value=_render_simple_string_like(
                                original_literal=first_arg.value,
                                new_value=str(payload["sql"]),
                            )
                        ),
                        *list(updated_node.args[1:]),
                    ]
                )
            if "conditions" in payload:
                predicates_sql, arguments = _materialize_sql_conditions(
                    conditions=list(payload["conditions"]),
                    insertion_index=_infer_sql_predicate_insertion_index(original_sql=original_sql),
                )
                predicate_text = " AND ".join(predicates_sql)
                argument_values = arguments
            else:
                argument_values = [str(payload.get("argument") or "relation_type").strip()]
                predicate_text = str(payload.get("predicate") or "").strip()
            method_name = (
                updated_node.func.attr.value
                if isinstance(updated_node.func, cst.Attribute) and isinstance(updated_node.func.attr, cst.Name)
                else ""
            )
            args_after_sql = list(updated_node.args[1:])
            insert_at = len(args_after_sql)
            if method_name == "fetch" and len(args_after_sql) >= 2:
                trailing = args_after_sql[-2:]
                trailing_names = [item.value.value if isinstance(item.value, cst.Name) else "" for item in trailing]
                if trailing_names == ["limit", "offset"]:
                    insert_at = len(args_after_sql) - 2
            insertion_index = insert_at + 1
            if "conditions" in payload:
                predicates_sql, argument_values = _materialize_sql_conditions(
                    conditions=list(payload["conditions"]),
                    insertion_index=insertion_index,
                )
                predicate_text = " AND ".join(predicates_sql)
            new_sql = _inject_sql_predicate(
                original_sql=original_sql,
                predicate=predicate_text,
                insertion_index=insertion_index,
            )
            new_args = [updated_node.args[0]]
            new_args.extend(args_after_sql[:insert_at])
            existing_values = {"".join(renderer.code_for_node(arg.value).split()) for arg in args_after_sql}
            for argument_text in argument_values:
                if "".join(argument_text.split()) not in existing_values:
                    new_args.append(cst.Arg(value=cst.parse_expression(argument_text)))
            new_args.extend(args_after_sql[insert_at:])
            return updated_node.with_changes(
                args=[
                    cst.Arg(
                        value=_render_simple_string_like(
                            original_literal=first_arg.value,
                            new_value=new_sql,
                        )
                    ),
                    *new_args[1:],
                ]
            )

        def leave_SimpleString(
            self, original_node: cst.SimpleString, updated_node: cst.SimpleString
        ) -> cst.BaseExpression:
            if normalized_mode != "sql_blocks" or not self._inside_target:
                return updated_node
            try:
                value = ast.literal_eval(original_node.value)
            except Exception:
                return updated_node
            if not isinstance(value, str):
                return updated_node
            normalized = " ".join(value.split())
            upper = normalized.upper()
            if "SELECT " not in upper or " FROM " not in upper:
                return updated_node
            self._sql_literal_index += 1
            replacement = sql_replacement_map.get(self._sql_literal_index)
            if replacement is None or "sql" not in replacement:
                return updated_node
            return _render_simple_string_like(
                original_literal=original_node.value,
                new_value=str(replacement["sql"]),
            )

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={qualified_name}")
    target.write_text(updated.code, encoding="utf-8")


def _update_call_kwarg_with_libcst(
    *,
    target: Path,
    qualified_name: str,
    callee: str,
    kwarg: str,
    new_value: str,
) -> None:
    module = cst.parse_module(target.read_text(encoding="utf-8"))
    replacement_value = cst.parse_expression(new_value)

    class _Transformer(cst.CSTTransformer):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self._inside_target = False
            self.replaced = False

        def visit_ClassDef(self, node: cst.ClassDef) -> bool:
            self._class_stack.append(node.name.value)
            return True

        def leave_ClassDef(
            self,
            original_node: cst.ClassDef,
            updated_node: cst.ClassDef,
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            self._class_stack.pop()
            return updated_node

        def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
            current_name = ".".join([*self._class_stack, node.name.value]).strip(".")
            self._inside_target = current_name == qualified_name
            return True

        def leave_FunctionDef(
            self,
            original_node: cst.FunctionDef,
            updated_node: cst.FunctionDef,
        ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel:
            self._inside_target = False
            return updated_node

        def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
            if not self._inside_target or self.replaced:
                return updated_node
            if not _callee_matches(updated_node.func, callee):
                return updated_node
            new_args: list[cst.Arg] = []
            replaced_here = False
            for arg in updated_node.args:
                if arg.keyword and arg.keyword.value == kwarg:
                    new_args.append(arg.with_changes(value=replacement_value))
                    replaced_here = True
                else:
                    new_args.append(arg)
            if not replaced_here:
                return updated_node
            self.replaced = True
            return updated_node.with_changes(args=new_args)

    transformer = _Transformer()
    updated = module.visit(transformer)
    if not transformer.replaced:
        raise LookupError(f"CALL_KWARG_NOT_FOUND callee={callee!r} kwarg={kwarg!r}")
    target.write_text(updated.code, encoding="utf-8")


def _insert_after_symbol(
    *,
    workspace_root: str,
    relative_path: str,
    symbol_name: str,
    new_content: str,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    raise RuntimeError("REMOVED_MUTATION_TOOL primitive=insert_after_symbol primary_lane=edit_file")


def _last_return_line(*, file_path: Path, function_name: str) -> int:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    requested_symbol = function_name.split(".")[-1].strip()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == requested_symbol:
            returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
            if not returns:
                raise ValueError(f"FUNCTION_RETURN_NOT_FOUND function={function_name}")
            return max(int(child.lineno) for child in returns)
    raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={function_name}")


def _insert_before_return(
    *,
    workspace_root: str,
    relative_path: str,
    function_name: str,
    new_content: str,
    expected_base_commit_sha: str | None = None,
    expected_file_blob_sha: str | None = None,
    expected_symbol_source_hash: str | None = None,
) -> dict[str, Any]:
    raise RuntimeError("REMOVED_MUTATION_TOOL primitive=insert_before_return primary_lane=edit_file")


_STAGED_DELIVERY_ALLOWED_TOOLS = {
    "replace_in_function",
    "update_call_kwarg",
}
_STAGED_DELIVERY_MAX_STAGES = 4
_STAGED_DELIVERY_MAX_FILES = 3
_STAGED_DELIVERY_PHASE_ORDER = {"impl": 0, "router": 1, "tests": 2}


def _classify_staged_delivery_path(relative_path: str) -> str:
    normalized = str(relative_path or "").strip().replace("\\", "/").lower()
    if (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or normalized.endswith("_test.py")
        or normalized.endswith("test_.py")
    ):
        return "tests"
    if "/routers/" in normalized or "/routes/" in normalized or normalized.endswith("_router.py"):
        return "router"
    return "impl"


def _module_scope_import_names(target: Path) -> set[str]:
    try:
        tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except SyntaxError:
        return set()
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(str(alias.asname or alias.name.split(".", 1)[0]).strip())
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(str(alias.asname or alias.name).strip())
    return {name for name in imported if name}


def _validate_staged_delivery_stages(
    *,
    stages: list[dict[str, Any]],
    max_stages: int,
    max_files: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not stages:
        raise ValueError("STAGED_DELIVERY_EMPTY")
    if len(stages) > max_stages:
        raise ValueError(f"STAGED_DELIVERY_STAGE_LIMIT_EXCEEDED limit={max_stages} provided={len(stages)}")
    seen_files: set[str] = set()
    previous_rank = -1
    for index, raw_stage in enumerate(stages, start=1):
        if not isinstance(raw_stage, dict):
            raise ValueError(f"STAGED_DELIVERY_INVALID_STAGE index={index} detail=stage_must_be_object")
        tool = str(raw_stage.get("tool") or "").strip()
        if tool not in _STAGED_DELIVERY_ALLOWED_TOOLS:
            raise ValueError(f"STAGED_DELIVERY_UNSUPPORTED_TOOL index={index} tool={tool or '<empty>'}")
        args = dict(raw_stage.get("args") or {})
        relative_path = str(args.get("relative_path") or "").strip().replace("\\", "/")
        if not relative_path:
            raise ValueError(f"STAGED_DELIVERY_RELATIVE_PATH_REQUIRED index={index} tool={tool}")
        seen_files.add(relative_path)
        if len(seen_files) > max_files:
            raise ValueError(f"STAGED_DELIVERY_FILE_LIMIT_EXCEEDED limit={max_files} provided={len(seen_files)}")
        phase = _classify_staged_delivery_path(relative_path)
        rank = _STAGED_DELIVERY_PHASE_ORDER[phase]
        if rank < previous_rank:
            raise ValueError(
                f"STAGED_DELIVERY_ORDER_INVALID index={index} phase={phase} previous_phase_rank={previous_rank}"
            )
        previous_rank = rank
        required_imports = [str(item).strip() for item in raw_stage.get("required_imports") or [] if str(item).strip()]
        required_symbols = [str(item).strip() for item in raw_stage.get("required_symbols") or [] if str(item).strip()]
        normalized.append(
            {
                "tool": tool,
                "args": args,
                "relative_path": relative_path,
                "phase": phase,
                "required_imports": required_imports,
                "required_symbols": required_symbols,
            }
        )
    return normalized


def _enforce_staged_delivery_consistency(
    *,
    workspace_root: str,
    relative_path: str,
    required_imports: list[str],
    required_symbols: list[str],
) -> dict[str, Any]:
    target = _safe_resolve(workspace_root, relative_path)
    missing_imports = sorted(set(required_imports) - _module_scope_import_names(target))
    if missing_imports:
        raise ValueError(
            "STAGED_IMPORT_CONSISTENCY_FAILED "
            + f"relative_path={relative_path} missing_imports={','.join(missing_imports)}"
        )
    resolved_symbols: list[str] = []
    for symbol_name in required_symbols:
        _start_line, _end_line, _def_col, qualified_name, _source = _resolve_python_symbol_range(
            file_path=target, symbol_name=symbol_name
        )
        resolved_symbols.append(qualified_name)
    return {
        "required_imports": list(required_imports),
        "required_symbols": list(required_symbols),
        "resolved_symbols": resolved_symbols,
    }


def _apply_staged_delivery_slice(
    *,
    workspace_root: str,
    stages: list[dict[str, Any]],
    dry_run: bool,
    max_stages: int,
    max_files: int,
) -> dict[str, Any]:
    normalized_stages = _validate_staged_delivery_stages(
        stages=stages,
        max_stages=max_stages,
        max_files=max_files,
        dry_run=dry_run,
    )
    results: list[dict[str, Any]] = []
    changed_files: set[str] = set()
    for index, stage in enumerate(normalized_stages, start=1):
        tool = stage["tool"]
        raise ValueError(f"STAGED_DELIVERY_UNSUPPORTED_TOOL tool={tool}")
        consistency = _enforce_staged_delivery_consistency(
            workspace_root=workspace_root,
            relative_path=stage["relative_path"],
            required_imports=list(stage.get("required_imports") or []),
            required_symbols=list(stage.get("required_symbols") or []),
        )
        results.append(
            {
                "index": index,
                "tool": tool,
                "relative_path": stage["relative_path"],
                "phase": stage["phase"],
                "consistency": consistency,
            }
        )
    return {
        "operation": "apply_staged_delivery_slice",
        "stage_count": len(results),
        "file_count": len({stage["relative_path"] for stage in normalized_stages}),
        "dry_run": dry_run,
        "validated": True,
        "changed_files": sorted(changed_files),
        "stages": results,
    }


def _apply_patch(
    *,
    workspace_root: str,
    patch_text: str,
    max_conflict_retries: int = 1,
    max_attempts: int = 10,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    patch_sha1 = hashlib.sha1(patch_text.encode("utf-8")).hexdigest()
    changed_files = _extract_changed_files(patch_text)
    approx_diff_bytes = _estimate_diff_bytes(patch_text)
    retries = max(0, int(max_conflict_retries))
    attempts = max(1, int(max_attempts))
    retry_count = 0
    strategy = "apply_then_3way" if retries > 0 else "single_apply"
    for local_attempt in range(retries + 1):
        patch_attempt = local_attempt + 1
        if patch_attempt > attempts:
            raise RuntimeError(f"PATCH_ATTEMPT_LIMIT_EXCEEDED attempts={patch_attempt} max_attempts={attempts}")
        command = [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.safecrlf=false",
            "-C",
            str(root),
            "apply",
        ]
        if local_attempt > 0:
            command.append("--3way")
        command.extend(["--ignore-space-change", "--ignore-whitespace", "--whitespace=nowarn", "-"])
        proc = subprocess.run(
            command,
            input=patch_text,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            return {
                "operation": "apply_patch",
                "patch_attempt": patch_attempt,
                "patch_bytes": len(patch_text.encode("utf-8")),
                "patch_sha1": patch_sha1,
                "approx_diff_bytes": approx_diff_bytes,
                "changed_files": changed_files,
                "retry_count": retry_count,
                "retry_strategy": strategy,
                "adapter": "git_apply",
                "adapter_chain": [{"name": "git_apply", "available": True}],
            }
        status, reason_code, retryable = _classify_patch_failure(proc.stderr or "", proc.stdout or "")
        terminal = (not retryable) or (retry_count >= retries)
        if terminal:
            terminal_reason = reason_code if status != "conflict" else f"{reason_code}_terminal"
            raise RuntimeError(
                f"PATCH_APPLY_FAILED attempt={patch_attempt} reason={terminal_reason}: "
                f"{(proc.stderr or '').strip() or (proc.stdout or '').strip()}"
            )
        retry_count += 1
    raise RuntimeError("PATCH_APPLY_FAILED attempt=1 reason=apply_failed: retry loop exhausted unexpectedly")


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    tool = str(request.get("tool") or "").strip()
    args = dict(request.get("args") or {})
    workspace_root = str(_workspace_root())
    if tool == "write_file":
        payload = _write_file(
            workspace_root=workspace_root,
            relative_path=str(args.get("relative_path") or ""),
            content=str(args.get("content") or ""),
        )
    elif tool == "edit_file":
        payload = _edit_file(
            workspace_root=workspace_root,
            relative_path=str(args.get("relative_path") or ""),
            search_replace_blocks=str(args.get("search_replace_blocks") or ""),
        )
    elif tool == "insert_at_anchor":
        payload = _insert_at_anchor(
            workspace_root=workspace_root,
            relative_path=str(args.get("relative_path") or ""),
            anchor_text=str(args.get("anchor_text") or ""),
            new_content=str(args.get("new_content") or ""),
            position=str(args.get("position") or "after"),
            indent_style=str(args.get("indent_style") or "match_anchor"),
        )
    elif tool == "extend_python_signature":
        payload = _extend_python_signature(
            workspace_root=workspace_root,
            relative_path=str(args.get("relative_path") or ""),
            symbol=str(args.get("symbol") or ""),
            parameter_name=str(args.get("parameter_name") or ""),
            parameter_type=str(args.get("parameter_type") or ""),
            default_value=str(args.get("default_value") or ""),
            parameter_kind=str(args.get("parameter_kind") or "auto"),
            dry_run=bool(args.get("dry_run")),
            expected_base_commit_sha=args.get("expected_base_commit_sha"),
            expected_file_blob_sha=args.get("expected_file_blob_sha"),
            expected_symbol_source_hash=args.get("expected_symbol_source_hash"),
        )
    elif tool == "replace_range":
        payload = _replace_range(
            workspace_root=workspace_root,
            relative_path=str(args.get("relative_path") or ""),
            start_line=int(args.get("start_line") or 1),
            end_line=int(args.get("end_line") or 1),
            replacement=str(args.get("replacement") or ""),
        )
    elif tool == "apply_patch":
        payload = _apply_patch(
            workspace_root=workspace_root,
            patch_text=str(args.get("patch_text") or ""),
            max_conflict_retries=int(args.get("max_conflict_retries") or 1),
            max_attempts=int(args.get("max_attempts") or 10),
        )
    elif tool == "replace_in_function":
        payload = _replace_in_function(
            workspace_root=workspace_root,
            relative_path=str(args.get("relative_path") or ""),
            function_name=str(args.get("function_name") or ""),
            replacement=str(args.get("replacement") or ""),
            dry_run=bool(args.get("dry_run")),
            expected_base_commit_sha=args.get("expected_base_commit_sha"),
            expected_file_blob_sha=args.get("expected_file_blob_sha"),
            expected_symbol_source_hash=args.get("expected_symbol_source_hash"),
        )
    elif tool == "edit_symbol":
        payload = _edit_symbol(
            workspace_root=workspace_root,
            relative_path=str(args.get("relative_path") or ""),
            symbol=str(args.get("symbol") or ""),
            mode=str(args.get("mode") or ""),
            new_signature=args.get("new_signature"),
            anchor_pattern=args.get("anchor_pattern"),
            position=args.get("position"),
            new_code=args.get("new_code"),
            dry_run=bool(args.get("dry_run")),
            expected_base_commit_sha=args.get("expected_base_commit_sha"),
            expected_file_blob_sha=args.get("expected_file_blob_sha"),
            expected_symbol_source_hash=args.get("expected_symbol_source_hash"),
        )
    elif tool == "update_call_kwarg":
        raise RuntimeError("REMOVED_MUTATION_TOOL primitive=update_call_kwarg primary_lane=edit_file")
    elif tool == "insert_after_symbol":
        raise RuntimeError("REMOVED_MUTATION_TOOL primitive=insert_after_symbol primary_lane=edit_file")
    elif tool == "insert_before_return":
        raise RuntimeError("REMOVED_MUTATION_TOOL primitive=insert_before_return primary_lane=edit_file")
    elif tool == "apply_staged_delivery_slice":
        payload = _apply_staged_delivery_slice(
            workspace_root=workspace_root,
            stages=list(args.get("stages") or []),
            dry_run=bool(args.get("dry_run")),
            max_stages=int(args.get("max_stages") or _STAGED_DELIVERY_MAX_STAGES),
            max_files=int(args.get("max_files") or _STAGED_DELIVERY_MAX_FILES),
        )
    else:
        raise ValueError(f"unsupported tool: {tool}")
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
