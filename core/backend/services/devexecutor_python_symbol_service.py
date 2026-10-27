from __future__ import annotations

import ast
import hashlib
import logging
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.services.devexecutor_pyright_lsp_service import resolve_python_symbol_bounds_via_lsp

try:
    import tree_sitter_python
    from tree_sitter import Language, Node, Parser, Tree
except ImportError:  # pragma: no cover - exercised in environments without tree-sitter installed.
    Language = None
    Node = Any
    Parser = None
    Tree = Any
    tree_sitter_python = None


class AmbiguousPythonSymbolError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class PythonSymbolRange:
    symbol_name: str
    qualified_name: str
    symbol_type: str
    start_line: int
    def_line: int
    def_col: int
    end_line: int
    decorator_lines: tuple[int, ...]
    symbol_source: str
    semantic_valid: bool | None = None
    resolution_source: str = "ast"


@dataclass(frozen=True, slots=True)
class PythonSymbolContext:
    symbol_range: PythonSymbolRange
    symbol_excerpt: str
    imports_excerpt: str
    class_init_excerpt: str


@dataclass(slots=True)
class _ParsedPythonSource:
    path: Path
    source: str
    source_bytes: bytes
    source_lines: list[str]
    tree: Any
    parser_backend: str
    mtime_ns: int


@dataclass(slots=True)
class PythonSymbolSessionCache:
    parsed_sources: dict[str, _ParsedPythonSource]
    symbol_ranges: dict[tuple[str, str, str], PythonSymbolRange]
    symbol_contexts: dict[tuple[str, str, str, int, int], PythonSymbolContext]

    def __init__(self) -> None:
        self.parsed_sources = {}
        self.symbol_ranges = {}
        self.symbol_contexts = {}


_ACTIVE_SYMBOL_SESSION_CACHE: ContextVar[PythonSymbolSessionCache | None] = ContextVar(
    "active_python_symbol_session_cache",
    default=None,
)
_PYTHON_TS_LANGUAGE: Language | None = (
    Language(tree_sitter_python.language()) if Language and tree_sitter_python else None
)
_PYTHON_TS_PARSER: Parser | None = Parser(_PYTHON_TS_LANGUAGE) if _PYTHON_TS_LANGUAGE and Parser else None
logger = logging.getLogger(__name__)


@contextmanager
def activate_python_symbol_session_cache(
    cache: PythonSymbolSessionCache | None = None,
) -> Iterator[PythonSymbolSessionCache]:
    active_cache = cache or PythonSymbolSessionCache()
    token = _ACTIVE_SYMBOL_SESSION_CACHE.set(active_cache)
    try:
        yield active_cache
    finally:
        _ACTIVE_SYMBOL_SESSION_CACHE.reset(token)


def current_python_symbol_session_cache() -> PythonSymbolSessionCache | None:
    return _ACTIVE_SYMBOL_SESSION_CACHE.get()


def resolve_python_symbol_range(
    *,
    file_path: str | Path,
    symbol_name: str,
    symbol_type: str = "function",
) -> PythonSymbolRange:
    path = Path(file_path)
    normalized_symbol = str(symbol_name or "").strip()
    if not normalized_symbol:
        raise ValueError("symbol_name is required")
    normalized_type = str(symbol_type or "function").strip().lower() or "function"
    cache = current_python_symbol_session_cache()
    cache_key = (_normalize_symbol_cache_path(path), normalized_symbol, normalized_type)
    if cache is not None and cache_key in cache.symbol_ranges:
        return cache.symbol_ranges[cache_key]
    lsp_result = resolve_python_symbol_bounds_via_lsp(
        file_path=path,
        symbol_name=normalized_symbol,
        symbol_type=normalized_type,
    )
    if lsp_result is None:
        logger.warning(
            "Pyright LSP unavailable or unresolved for %s:%s; falling back to tree-sitter/AST symbol resolution",
            path,
            normalized_symbol,
        )
    else:
        result = PythonSymbolRange(
            symbol_name=lsp_result.symbol_name,
            qualified_name=lsp_result.qualified_name,
            symbol_type=lsp_result.symbol_type,
            start_line=lsp_result.start_line,
            def_line=lsp_result.def_line,
            def_col=lsp_result.def_col,
            end_line=lsp_result.end_line,
            decorator_lines=lsp_result.decorator_lines,
            symbol_source=lsp_result.symbol_source,
            semantic_valid=lsp_result.semantic_valid,
            resolution_source=lsp_result.resolution_source,
        )
        if cache is not None:
            cache.symbol_ranges[cache_key] = result
        return result
    parsed = _load_parsed_python_source(path=path, cache=cache)
    matches = _find_symbols(
        parsed=parsed,
        symbol_name=normalized_symbol,
        symbol_type=normalized_type,
        source_lines=parsed.source_lines,
    )
    if not matches:
        fallback_symbol = _module_qualified_function_fallback(path=path, symbol_name=normalized_symbol)
        if fallback_symbol:
            matches = _find_symbols(
                parsed=parsed,
                symbol_name=fallback_symbol,
                symbol_type=normalized_type,
                source_lines=parsed.source_lines,
            )
    if not matches:
        raise LookupError(f"PYTHON_SYMBOL_NOT_FOUND symbol={normalized_symbol} type={normalized_type}")
    if len(matches) > 1:
        names = ", ".join(match.qualified_name for match in matches)
        raise AmbiguousPythonSymbolError(
            f"PYTHON_SYMBOL_AMBIGUOUS symbol={normalized_symbol} type={normalized_type} matches={names}"
        )
    result = matches[0]
    if cache is not None:
        cache.symbol_ranges[cache_key] = result
    return result


def verify_symbol_anchor(
    *,
    file_path: str | Path,
    symbol_name: str,
    anchor_quote: str,
    symbol_type: str = "function",
) -> dict[str, object]:
    symbol = resolve_python_symbol_range(file_path=file_path, symbol_name=symbol_name, symbol_type=symbol_type)
    anchor = _normalize_anchor_text(anchor_quote)
    source = _normalize_anchor_text(symbol.symbol_source)
    matched = bool(anchor) and anchor in source
    result = {
        "matched": matched,
        "symbol_name": symbol.symbol_name,
        "qualified_name": symbol.qualified_name,
        "symbol_type": symbol.symbol_type,
        "relative_start_line": symbol.start_line,
        "relative_end_line": symbol.end_line,
        "anchor_quote": anchor_quote,
        "symbol_source": symbol.symbol_source,
        "reason_code": "MATCH" if matched else "GROUNDING_MISMATCH",
    }
    result.update(build_symbol_grounding_payload(file_path=file_path, symbol_range=symbol))
    return result


def build_symbol_grounding_payload(
    *,
    file_path: str | Path,
    symbol_range: PythonSymbolRange,
) -> dict[str, object]:
    path = Path(file_path).resolve()
    commit_sha, head_file_blob_sha = _git_grounding_for_path(path)
    file_blob_sha = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    return {
        "file_path": str(path),
        "symbol_source_hash": f"sha256:{hashlib.sha256(symbol_range.symbol_source.encode('utf-8')).hexdigest()}",
        "commit_sha": commit_sha,
        "file_blob_sha": file_blob_sha,
        "head_file_blob_sha": head_file_blob_sha,
    }


def build_python_symbol_context(
    *,
    file_path: str | Path,
    symbol_name: str,
    symbol_type: str = "function",
    context_lines: int = 12,
    import_max_lines: int = 20,
) -> PythonSymbolContext:
    path = Path(file_path)
    normalized_type = str(symbol_type or "function").strip().lower() or "function"
    cache = current_python_symbol_session_cache()
    cache_key = (
        _normalize_symbol_cache_path(path),
        str(symbol_name or "").strip(),
        normalized_type,
        max(0, int(context_lines)),
        max(1, int(import_max_lines)),
    )
    if cache is not None and cache_key in cache.symbol_contexts:
        return cache.symbol_contexts[cache_key]
    parsed = _load_parsed_python_source(path=path, cache=cache)
    symbol_range = resolve_python_symbol_range(file_path=path, symbol_name=symbol_name, symbol_type=normalized_type)
    symbol_excerpt = _build_excerpt(
        source_lines=parsed.source_lines,
        start_line=symbol_range.start_line,
        end_line=symbol_range.end_line,
        context_lines=context_lines,
    )
    imports_excerpt = _build_top_excerpt(source_lines=parsed.source_lines, max_lines=import_max_lines)
    class_init_excerpt = ""
    class_name: str | None = _qualified_class_name(symbol_range.qualified_name)
    if class_name:
        try:
            init_range = resolve_python_symbol_range(
                file_path=path,
                symbol_name=f"{class_name}.__init__",
                symbol_type="function",
            )
            class_init_excerpt = _build_excerpt(
                source_lines=parsed.source_lines,
                start_line=init_range.start_line,
                end_line=init_range.end_line,
                context_lines=4,
            )
        except LookupError:
            class_init_excerpt = ""
    context = PythonSymbolContext(
        symbol_range=symbol_range,
        symbol_excerpt=symbol_excerpt,
        imports_excerpt=imports_excerpt,
        class_init_excerpt=class_init_excerpt,
    )
    if cache is not None:
        cache.symbol_contexts[cache_key] = context
    return context


def _find_symbols(
    *,
    parsed: _ParsedPythonSource,
    symbol_name: str,
    symbol_type: str,
    source_lines: list[str],
) -> list[PythonSymbolRange]:
    if parsed.parser_backend == "tree_sitter":
        return _find_symbols_tree_sitter(
            tree=parsed.tree,
            source_bytes=parsed.source_bytes,
            symbol_name=symbol_name,
            symbol_type=symbol_type,
            source_lines=source_lines,
        )
    matches: list[PythonSymbolRange] = []
    requested_class: str | None = None
    requested_symbol = symbol_name
    if "." in symbol_name:
        requested_class, requested_symbol = symbol_name.split(".", 1)
        requested_class = requested_class.strip() or None
        requested_symbol = requested_symbol.strip()

    for node in ast.iter_child_nodes(parsed.tree):
        matches.extend(
            _walk_node(
                node=node,
                requested_class=requested_class,
                requested_symbol=requested_symbol,
                symbol_type=symbol_type,
                parent_qualifier="",
                source_lines=source_lines,
            )
        )
    return matches


def _find_symbols_tree_sitter(
    *,
    tree: Tree,
    source_bytes: bytes,
    symbol_name: str,
    symbol_type: str,
    source_lines: list[str],
) -> list[PythonSymbolRange]:
    matches: list[PythonSymbolRange] = []
    requested_class: str | None = None
    requested_symbol = symbol_name
    if "." in symbol_name:
        requested_class, requested_symbol = symbol_name.split(".", 1)
        requested_class = requested_class.strip() or None
        requested_symbol = requested_symbol.strip()

    def walk_container(node: Node, *, qualifier: str) -> None:
        for child in node.named_children:
            walk_definition(child, qualifier=qualifier, decorator_nodes=(), wrapped_node=None)

    def walk_definition(
        node: Node,
        *,
        qualifier: str,
        decorator_nodes: tuple[Node, ...],
        wrapped_node: Node | None,
    ) -> None:
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
            if inner is not None:
                walk_definition(inner, qualifier=qualifier, decorator_nodes=decorators, wrapped_node=node)
            return

        if node.type == "class_definition":
            name_node = _tree_sitter_child_of_type(node, "identifier")
            if name_node is None:
                return
            class_symbol_name = _node_text(source_bytes=source_bytes, node=name_node)
            current_qualifier = f"{qualifier}.{class_symbol_name}".strip(".")
            owner = wrapped_node or node
            if symbol_type == "class" and requested_class is None and class_symbol_name == requested_symbol:
                matches.append(
                    _build_tree_sitter_symbol_range(
                        owner_node=owner,
                        definition_node=node,
                        decorator_nodes=decorator_nodes,
                        symbol_name=class_symbol_name,
                        qualified_name=current_qualifier,
                        symbol_type="class",
                        source_bytes=source_bytes,
                        source_lines=source_lines,
                    )
                )
            block = _tree_sitter_child_of_type(node, "block")
            if block is not None:
                walk_container(block, qualifier=current_qualifier)
            return

        if node.type != "function_definition":
            return

        name_node = _tree_sitter_child_of_type(node, "identifier")
        if name_node is None:
            return
        function_name = _node_text(source_bytes=source_bytes, node=name_node)
        current_symbol_type = "async_function" if _tree_sitter_is_async_function(node) else "function"
        current_qualifier = f"{qualifier}.{function_name}".strip(".")
        enclosing_class_name: str | None = qualifier.split(".")[-1] if qualifier else None
        if symbol_type in {"function", "async_function", "method"}:
            type_match = symbol_type == current_symbol_type or symbol_type in {"function", "method"}
            class_match = requested_class is None or enclosing_class_name == requested_class
            if type_match and class_match and function_name == requested_symbol:
                matches.append(
                    _build_tree_sitter_symbol_range(
                        owner_node=wrapped_node or node,
                        definition_node=node,
                        decorator_nodes=decorator_nodes,
                        symbol_name=function_name,
                        qualified_name=current_qualifier,
                        symbol_type=current_symbol_type,
                        source_bytes=source_bytes,
                        source_lines=source_lines,
                    )
                )
        block = _tree_sitter_child_of_type(node, "block")
        if block is not None:
            walk_container(block, qualifier=current_qualifier)

    walk_container(tree.root_node, qualifier="")
    return matches


def _walk_node(
    *,
    node: ast.AST,
    requested_class: str | None,
    requested_symbol: str,
    symbol_type: str,
    parent_qualifier: str,
    source_lines: list[str],
) -> list[PythonSymbolRange]:
    qualifier = parent_qualifier
    matches: list[PythonSymbolRange] = []
    if isinstance(node, ast.ClassDef):
        qualifier = f"{parent_qualifier}.{node.name}".strip(".")
        if symbol_type == "class" and requested_class is None and node.name == requested_symbol:
            matches.append(
                _build_symbol_range(
                    node=node,
                    symbol_name=node.name,
                    qualified_name=qualifier,
                    symbol_type="class",
                    source_lines=source_lines,
                )
            )
        for child in node.body:
            matches.extend(
                _walk_node(
                    node=child,
                    requested_class=requested_class,
                    requested_symbol=requested_symbol,
                    symbol_type=symbol_type,
                    parent_qualifier=qualifier,
                    source_lines=source_lines,
                )
            )
        return matches

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        is_async = isinstance(node, ast.AsyncFunctionDef)
        current_symbol_type = "async_function" if is_async else "function"
        qualified_name = f"{qualifier}.{node.name}".strip(".")
        class_name = qualifier.split(".", 1)[-1] if qualifier else None
        if symbol_type in {"function", "async_function", "method"}:
            type_match = symbol_type == current_symbol_type or symbol_type in {"function", "method"}
            class_match = requested_class is None or class_name == requested_class
            if type_match and class_match and node.name == requested_symbol:
                matches.append(
                    _build_symbol_range(
                        node=node,
                        symbol_name=node.name,
                        qualified_name=qualified_name,
                        symbol_type=current_symbol_type,
                        source_lines=source_lines,
                    )
                )
        for grandchild in ast.iter_child_nodes(node):
            matches.extend(
                _walk_node(
                    node=grandchild,
                    requested_class=requested_class,
                    requested_symbol=requested_symbol,
                    symbol_type=symbol_type,
                    parent_qualifier=qualified_name,
                    source_lines=source_lines,
                )
            )
    return matches


def _build_symbol_range(
    *,
    node: ast.AST,
    symbol_name: str,
    qualified_name: str,
    symbol_type: str,
    source_lines: list[str],
) -> PythonSymbolRange:
    def_line = int(getattr(node, "lineno", 1))
    def_col = len(source_lines[def_line - 1]) - len(source_lines[def_line - 1].lstrip(" "))
    decorator_lines = tuple(int(getattr(item, "lineno", def_line)) for item in getattr(node, "decorator_list", []))
    start_line = min((def_line, *decorator_lines))
    end_line = int(getattr(node, "end_lineno", def_line))
    snippet = "\n".join(source_lines[start_line - 1 : end_line]).rstrip()
    return PythonSymbolRange(
        symbol_name=symbol_name,
        qualified_name=qualified_name,
        symbol_type=symbol_type,
        start_line=start_line,
        def_line=def_line,
        def_col=def_col,
        end_line=end_line,
        decorator_lines=decorator_lines,
        symbol_source=snippet,
        semantic_valid=None,
        resolution_source="ast",
    )


def _build_tree_sitter_symbol_range(
    *,
    owner_node: Node,
    definition_node: Node,
    decorator_nodes: tuple[Node, ...],
    symbol_name: str,
    qualified_name: str,
    symbol_type: str,
    source_bytes: bytes,
    source_lines: list[str],
) -> PythonSymbolRange:
    def_line = int(definition_node.start_point.row) + 1
    def_col = int(definition_node.start_point.column)
    decorator_lines = tuple(int(node.start_point.row) + 1 for node in decorator_nodes)
    start_line = int(owner_node.start_point.row) + 1
    end_line = int(owner_node.end_point.row) + 1
    if not source_lines:
        end_line = max(def_line, end_line)
    snippet = source_bytes[owner_node.start_byte : owner_node.end_byte].decode("utf-8", errors="replace").rstrip()
    return PythonSymbolRange(
        symbol_name=symbol_name,
        qualified_name=qualified_name,
        symbol_type=symbol_type,
        start_line=start_line,
        def_line=def_line,
        def_col=def_col,
        end_line=end_line,
        decorator_lines=decorator_lines,
        symbol_source=snippet,
        semantic_valid=None,
        resolution_source="tree_sitter",
    )


def _normalize_anchor_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_symbol_cache_path(path: Path) -> str:
    try:
        return str(path.resolve()).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _module_qualified_function_fallback(*, path: Path, symbol_name: str) -> str:
    normalized = str(symbol_name or "").strip()
    if normalized.count(".") < 1:
        return ""
    parts = [part.strip() for part in normalized.split(".") if part.strip()]
    if len(parts) < 2:
        return ""
    path_parts = [part.strip() for part in path.with_suffix("").parts if part.strip()]
    normalized_path = ".".join(part.replace("-", "_") for part in path_parts)
    for split_index in range(len(parts) - 1, 0, -1):
        requested_module = ".".join(parts[:split_index])
        requested_symbol = ".".join(parts[split_index:])
        if normalized_path == requested_module or normalized_path.endswith(f".{requested_module}"):
            return requested_symbol
    return ""


def _load_parsed_python_source(*, path: Path, cache: PythonSymbolSessionCache | None) -> _ParsedPythonSource:
    normalized_path = _normalize_symbol_cache_path(path)
    try:
        mtime_ns = int(path.stat().st_mtime_ns)
    except OSError:
        mtime_ns = -1
    if cache is not None:
        cached = cache.parsed_sources.get(normalized_path)
        if cached is not None and cached.mtime_ns == mtime_ns:
            return cached
    source = path.read_text(encoding="utf-8")
    source_bytes = source.encode("utf-8")
    tree: Any
    parser_backend = "ast"
    if _PYTHON_TS_PARSER is not None:
        tree = _PYTHON_TS_PARSER.parse(source_bytes)
        parser_backend = "tree_sitter"
    else:
        tree = ast.parse(source, filename=str(path))
    parsed = _ParsedPythonSource(
        path=path,
        source=source,
        source_bytes=source_bytes,
        source_lines=source.splitlines(),
        tree=tree,
        parser_backend=parser_backend,
        mtime_ns=mtime_ns,
    )
    if cache is not None:
        cache.parsed_sources[normalized_path] = parsed
    return parsed


def _tree_sitter_child_of_type(node: Node, node_type: str) -> Node | None:
    for child in node.named_children:
        if child.type == node_type:
            return child
    return None


def _tree_sitter_is_async_function(node: Node) -> bool:
    return any((not child.is_named) and child.type == "async" for child in node.children)


def _node_text(*, source_bytes: bytes, node: Node) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _git_grounding_for_path(path: Path) -> tuple[str | None, str | None]:
    repo_root = _find_git_repo_root(path)
    if repo_root is None:
        return None, None
    try:
        relative_path = path.relative_to(repo_root).as_posix()
    except ValueError:
        return None, None
    commit_sha = _run_git(repo_root, ["rev-parse", "HEAD"])
    file_blob_sha = _run_git(repo_root, ["rev-parse", f"HEAD:{relative_path}"])
    return commit_sha, file_blob_sha


def _find_git_repo_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _run_git(repo_root: Path, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _build_excerpt(*, source_lines: list[str], start_line: int, end_line: int, context_lines: int) -> str:
    start = max(0, int(start_line) - max(0, int(context_lines)) - 1)
    end = min(len(source_lines), int(end_line) + max(0, int(context_lines)))
    return "\n".join(f"{line_number + 1}: {source_lines[line_number]}" for line_number in range(start, end)).strip()


def _build_top_excerpt(*, source_lines: list[str], max_lines: int) -> str:
    snippet = source_lines[: max(1, int(max_lines))]
    return "\n".join(f"{index + 1}: {line}" for index, line in enumerate(snippet)).strip()


def _qualified_class_name(qualified_name: str) -> str:
    parts = [part.strip() for part in str(qualified_name or "").split(".") if part.strip()]
    if len(parts) < 2:
        return ""
    return ".".join(parts[:-1])


__all__ = [
    "AmbiguousPythonSymbolError",
    "PythonSymbolContext",
    "PythonSymbolRange",
    "PythonSymbolSessionCache",
    "activate_python_symbol_session_cache",
    "build_symbol_grounding_payload",
    "build_python_symbol_context",
    "current_python_symbol_session_cache",
    "resolve_python_symbol_range",
    "verify_symbol_anchor",
]
