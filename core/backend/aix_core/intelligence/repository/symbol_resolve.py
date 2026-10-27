"""006-PREP: Symbol resolution interface and local fallback resolver.

Fails closed with SYMBOL_NOT_FOUND or SYMBOL_AMBIGUOUS — never guess-pastes.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


SymbolKind = Literal["function", "class", "method", "variable", "interface", "type", "symbol"]
SymbolResolutionStatus = Literal["RESOLVED", "SYMBOL_NOT_FOUND", "SYMBOL_AMBIGUOUS"]


@dataclass(frozen=True)
class SymbolLocation:
    file_path: str
    start_line: int
    end_line: int
    start_col: int = 0
    end_col: int = 0
    language: str = "python"
    snippet: str = ""


@dataclass(frozen=True)
class SymbolResolutionResult:
    status: SymbolResolutionStatus
    symbol_name: str
    symbol_kind: SymbolKind | None
    locations: list[SymbolLocation] = field(default_factory=list)
    reason_code: str | None = None
    reason_detail: str = ""


_PY_DEF_RE = re.compile(r"^[ \t]*(async\s+def|def|class)\s+([a-zA-Z_][a-zA-Z0-9_]*)")
_TS_JS_DEF_RE = re.compile(
    r"^[ \t]*(export\s+)?(async\s+)?(function|class|interface|type|const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)"
)


class SymbolResolver:
    """Symbol resolution engine for surgical editing.

    Resolves exact symbol definitions across target files or in-memory content maps.
    Fails closed with SYMBOL_NOT_FOUND or SYMBOL_AMBIGUOUS if resolution is not 1:1.
    """

    def resolve_symbol(
        self,
        *,
        symbol_name: str,
        target_file: str | Path | None = None,
        workspace_root: str | Path | None = None,
        content_by_file: dict[str, str] | None = None,
        symbol_kind: SymbolKind | None = None,
    ) -> SymbolResolutionResult:
        candidates: list[SymbolLocation] = []

        files_to_scan: dict[str, str] = {}

        if content_by_file:
            for path_str, content in content_by_file.items():
                files_to_scan[path_str] = content

        if workspace_root:
            root_path = Path(workspace_root)
            if root_path.exists() and root_path.is_dir():
                for ext in ("*.py", "*.ts", "*.js", "*.tsx", "*.jsx"):
                    for file_p in root_path.rglob(ext):
                        # Skip hidden/node_modules dirs relative to workspace root
                        try:
                            rel_parts = file_p.relative_to(root_path).parts
                        except ValueError:
                            rel_parts = file_p.parts
                        if any(part.startswith(".") or part == "node_modules" for part in rel_parts):
                            continue
                        rel_path = str(file_p)
                        if rel_path not in files_to_scan:
                            try:
                                files_to_scan[rel_path] = file_p.read_text(encoding="utf-8", errors="ignore")
                            except OSError:
                                pass

        target_file_str = str(target_file) if target_file else None

        for path_str, content in files_to_scan.items():
            if target_file_str:
                # If target_file specified, restrict search to matching path
                if not (path_str == target_file_str or path_str.endswith(target_file_str) or target_file_str.endswith(path_str)):
                    continue

            found = self._scan_file_for_symbol(path_str=path_str, content=content, symbol_name=symbol_name)
            candidates.extend(found)

        # Filter by symbol_kind if provided
        if symbol_kind:
            kind_filtered = [c for c in candidates if c.snippet and symbol_kind in c.snippet.lower()]
            if kind_filtered:
                candidates = kind_filtered

        if len(candidates) == 0:
            return SymbolResolutionResult(
                status="SYMBOL_NOT_FOUND",
                symbol_name=symbol_name,
                symbol_kind=symbol_kind,
                locations=[],
                reason_code="SYMBOL_NOT_FOUND",
                reason_detail=f"Symbol '{symbol_name}' was not found in scanned workspace/target context",
            )

        if len(candidates) > 1:
            loc_summary = ", ".join(f"{c.file_path}:{c.start_line}" for c in candidates[:5])
            return SymbolResolutionResult(
                status="SYMBOL_AMBIGUOUS",
                symbol_name=symbol_name,
                symbol_kind=symbol_kind,
                locations=candidates,
                reason_code="SYMBOL_AMBIGUOUS",
                reason_detail=f"Symbol '{symbol_name}' is ambiguous ({len(candidates)} definitions found: {loc_summary})",
            )

        # Exactly 1 location resolved
        resolved_kind = symbol_kind or ("class" if "class" in candidates[0].snippet else "function")
        return SymbolResolutionResult(
            status="RESOLVED",
            symbol_name=symbol_name,
            symbol_kind=resolved_kind,
            locations=candidates,
            reason_code=None,
            reason_detail=f"Symbol '{symbol_name}' resolved to {candidates[0].file_path}:{candidates[0].start_line}",
        )

    def _scan_file_for_symbol(self, *, path_str: str, content: str, symbol_name: str) -> list[SymbolLocation]:
        locations: list[SymbolLocation] = []

        if path_str.endswith(".py"):
            # Try Python AST first
            try:
                tree = ast.parse(content, filename=path_str)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.name == symbol_name:
                            start_l = getattr(node, "lineno", 1)
                            end_l = getattr(node, "end_lineno", start_l)
                            kind_str = "class" if isinstance(node, ast.ClassDef) else "function"
                            locations.append(
                                SymbolLocation(
                                    file_path=path_str,
                                    start_line=start_l,
                                    end_line=end_l,
                                    language="python",
                                    snippet=f"{kind_str} {symbol_name}",
                                )
                            )
            except SyntaxError:
                pass

        if not locations:
            # Fallback regex scanning for python, ts, js
            lines = content.splitlines()
            for idx, line in enumerate(lines, start=1):
                match_py = _PY_DEF_RE.match(line)
                if match_py and match_py.group(2) == symbol_name:
                    kind_token = match_py.group(1)
                    locations.append(
                        SymbolLocation(
                            file_path=path_str,
                            start_line=idx,
                            end_line=idx,
                            language="python",
                            snippet=f"{kind_token} {symbol_name}",
                        )
                    )
                    continue

                match_ts = _TS_JS_DEF_RE.match(line)
                if match_ts and match_ts.group(4) == symbol_name:
                    kind_token = match_ts.group(3)
                    lang = "typescript" if path_str.endswith((".ts", ".tsx")) else "javascript"
                    locations.append(
                        SymbolLocation(
                            file_path=path_str,
                            start_line=idx,
                            end_line=idx,
                            language=lang,
                            snippet=f"{kind_token} {symbol_name}",
                        )
                    )

        return locations


__all__ = [
    "SymbolKind",
    "SymbolResolutionStatus",
    "SymbolLocation",
    "SymbolResolutionResult",
    "SymbolResolver",
]
