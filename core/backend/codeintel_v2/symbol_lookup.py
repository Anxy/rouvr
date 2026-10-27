"""Symbol lookup for CodeIntel v2 (Wave 4 CI4, AIX lane).

Candidate-only, advisory-only: :func:`lookup_symbol` AST-parses ONE
explicit file and returns the definitions matching ``name``. It never
searches other files, never guesses, and never infers:

- no match => ``([], "not-found:<class>")`` with a classified reason
  (``empty-name``, ``missing-file``, ``outside-workspace``,
  ``unreadable``, ``unparseable``, ``no-match``);
- the explicit file is the only file ever read;
- confidence is fixed by definition kind (``def``/``class`` => 1.0,
  assignment => 0.8) and always lies in [0, 1].
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["SymbolCandidate", "symbol_to_dict", "lookup_symbol"]


def _check_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"SymbolCandidate.confidence must be a number in [0, 1], got {value!r}"
        )
    number = float(value)
    if math.isnan(number) or math.isinf(number) or not 0.0 <= number <= 1.0:
        raise ValueError(
            f"SymbolCandidate.confidence must be a number in [0, 1], got {value!r}"
        )
    return number


def _check_text(value: object, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"SymbolCandidate.{field_name} must be a non-empty string")


def _check_line(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"SymbolCandidate.line must be an int >= 1, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class SymbolCandidate:
    """One symbol definition found in the explicit file only."""

    symbol: str
    file: str
    line: int
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _check_text(self.symbol, "symbol"))
        object.__setattr__(self, "file", _check_text(self.file, "file"))
        object.__setattr__(self, "line", _check_line(self.line))
        object.__setattr__(self, "confidence", _check_confidence(self.confidence))


def symbol_to_dict(candidate: SymbolCandidate) -> dict[str, Any]:
    """Serialize one symbol candidate (serializer, not a chooser)."""
    if not isinstance(candidate, SymbolCandidate):
        raise ValueError("symbol_to_dict expects a SymbolCandidate instance")
    return {
        "symbol": candidate.symbol,
        "file": candidate.file,
        "line": candidate.line,
        "confidence": candidate.confidence,
    }


def _resolve_explicit_file(file_path: Any, workspace_root: str | Path) -> Path | None:
    """Resolve ``file_path`` inside ``workspace_root``; ``None`` if outside."""
    try:
        root = Path(workspace_root).resolve()
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
    except Exception:
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def lookup_symbol(
    name: Any, file_path: Any, workspace_root: str | Path
) -> tuple[list[dict[str, Any]], str]:
    """AST-parse the EXPLICIT file only and return matches for ``name``.

    Returns:
        ``(candidates, outcome)`` where ``candidates`` is a list of
        symbol dicts (``symbol``, ``file``, ``line``, ``confidence``)
        and ``outcome`` is ``"found"`` or a classified
        ``"not-found:<class>"`` string. No other file is ever read and
        nothing is ever guessed across files.
    """
    try:
        root = Path(workspace_root)
        if not root.is_dir():
            return [], "not-found:outside-workspace"
    except Exception:
        return [], "not-found:outside-workspace"
    if not isinstance(name, str) or not name.strip():
        return [], "not-found:empty-name"
    if not isinstance(file_path, (str, Path)) or not str(file_path).strip():
        return [], "not-found:missing-file"
    resolved = _resolve_explicit_file(file_path, root)
    if resolved is None:
        return [], "not-found:outside-workspace"
    try:
        if not resolved.is_file():
            return [], "not-found:missing-file"
    except Exception:
        return [], "not-found:missing-file"
    try:
        source = resolved.read_text(encoding="utf-8")
    except Exception:
        return [], "not-found:unreadable"
    try:
        tree = ast.parse(source)
    except Exception:
        return [], "not-found:unparseable"

    wanted = name.strip()
    rel = resolved.relative_to(root.resolve()).as_posix()
    found: list[SymbolCandidate] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == wanted:
                found.append(
                    SymbolCandidate(
                        symbol=wanted, file=rel, line=node.lineno, confidence=1.0
                    )
                )
        elif isinstance(node, ast.Assign):
            for target_node in node.targets:
                if isinstance(target_node, ast.Name) and target_node.id == wanted:
                    found.append(
                        SymbolCandidate(
                            symbol=wanted,
                            file=rel,
                            line=node.lineno,
                            confidence=0.8,
                        )
                    )
        elif isinstance(node, ast.AnnAssign):
            target_node = node.target
            if isinstance(target_node, ast.Name) and target_node.id == wanted:
                found.append(
                    SymbolCandidate(
                        symbol=wanted, file=rel, line=node.lineno, confidence=0.8
                    )
                )
    if not found:
        return [], "not-found:no-match"
    found.sort(key=lambda item: item.line)
    return [symbol_to_dict(item) for item in found], "found"
