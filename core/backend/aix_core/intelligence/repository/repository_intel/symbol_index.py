"""C030: language-aware symbol index with provenance-linked definitions/references."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

Language = Literal["python", "javascript", "typescript", "go", "unknown"]

INDEX_SCHEMA = "aix.symbol_index.v1"


class SymbolIndexError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SymbolRef:
    name: str
    kind: str  # def | class | function | method | const | type
    language: Language
    path: str
    line: int
    content_sha256: str
    role: Literal["definition", "reference"]


@dataclass
class FileIndexEntry:
    path: str
    language: Language
    content_sha256: str
    symbols: list[SymbolRef] = field(default_factory=list)
    indexed_at: str = field(default_factory=_utc)


@dataclass
class SymbolIndex:
    root: str
    schema_version: str = INDEX_SCHEMA
    files: dict[str, FileIndexEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "files": {
                p: {
                    "path": e.path,
                    "language": e.language,
                    "content_sha256": e.content_sha256,
                    "indexed_at": e.indexed_at,
                    "symbols": [s.__dict__ for s in e.symbols],
                }
                for p, e in self.files.items()
            },
        }


def detect_language(path: str) -> Language:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
    }.get(suffix, "unknown")


_ADAPTERS: dict[Language, list[tuple[str, re.Pattern[str]]]] = {
    "python": [
        ("class", re.compile(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("function", re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("function", re.compile(r"^async\s+def\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ],
    "javascript": [
        ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("class", re.compile(r"^(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("const", re.compile(r"^(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")),
    ],
    "typescript": [
        ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("class", re.compile(r"^(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("type", re.compile(r"^(?:export\s+)?(?:type|interface)\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("const", re.compile(r"^(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")),
    ],
    "go": [
        ("function", re.compile(r"^func\s+(?:\([^)]+\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
        ("type", re.compile(r"^type\s+([A-Za-z_][A-Za-z0-9_]*)\s+")),
    ],
}


def extract_symbols(*, path: str, content: str, language: Language | None = None) -> list[SymbolRef]:
    lang = language or detect_language(path)
    if lang == "unknown" or lang not in _ADAPTERS:
        return []
    digest = _sha(content)
    defs: list[SymbolRef] = []
    names: set[str] = set()
    for kind, pattern in _ADAPTERS[lang]:
        for i, line in enumerate(content.splitlines(), start=1):
            m = pattern.match(line.strip())
            if not m:
                continue
            name = m.group(1)
            names.add(name)
            defs.append(
                SymbolRef(
                    name=name,
                    kind=kind,
                    language=lang,
                    path=path,
                    line=i,
                    content_sha256=digest,
                    role="definition",
                )
            )
    # References: simple name mentions excluding definition lines.
    ref_patterns = [re.compile(rf"\b{re.escape(n)}\b") for n in names]
    for i, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if any(p.match(stripped) for _, p in _ADAPTERS[lang]):
            continue
        for name, pat in zip(names, ref_patterns):
            if pat.search(line):
                defs.append(
                    SymbolRef(
                        name=name,
                        kind="ref",
                        language=lang,
                        path=path,
                        line=i,
                        content_sha256=digest,
                        role="reference",
                    )
                )
    return defs


class LanguageAwareSymbolIndex:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self.index = SymbolIndex(root=str(self._root))

    def index_file(self, relative_path: str, content: str | None = None) -> FileIndexEntry:
        path = (self._root / relative_path).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise SymbolIndexError("PATH_ESCAPES_ROOT", relative_path) from exc
        text = content if content is not None else path.read_text(encoding="utf-8", errors="replace")
        lang = detect_language(relative_path)
        digest = _sha(text)
        existing = self.index.files.get(relative_path)
        if existing and existing.content_sha256 == digest:
            return existing  # incremental: unchanged
        symbols = extract_symbols(path=relative_path, content=text, language=lang)
        entry = FileIndexEntry(
            path=relative_path,
            language=lang,
            content_sha256=digest,
            symbols=symbols,
        )
        self.index.files[relative_path] = entry
        return entry

    def refresh_after_edit(self, relative_path: str, new_content: str) -> FileIndexEntry:
        return self.index_file(relative_path, content=new_content)

    def definitions(self, name: str) -> list[SymbolRef]:
        needle = name.casefold()
        out: list[SymbolRef] = []
        for entry in self.index.files.values():
            for sym in entry.symbols:
                if sym.role == "definition" and sym.name.casefold() == needle:
                    out.append(sym)
        return out

    def references(self, name: str) -> list[SymbolRef]:
        needle = name.casefold()
        out: list[SymbolRef] = []
        for entry in self.index.files.values():
            for sym in entry.symbols:
                if sym.role == "reference" and sym.name.casefold() == needle:
                    out.append(sym)
        return out

    def query(self, name: str) -> dict[str, Any]:
        defs = self.definitions(name)
        refs = self.references(name)
        return {
            "name": name,
            "definitions": [d.__dict__ for d in defs],
            "references": [r.__dict__ for r in refs],
            "provenance_linked": all(bool(d.content_sha256) for d in defs + refs),
        }


__all__ = [
    "Language",
    "INDEX_SCHEMA",
    "SymbolIndexError",
    "SymbolRef",
    "FileIndexEntry",
    "SymbolIndex",
    "detect_language",
    "extract_symbols",
    "LanguageAwareSymbolIndex",
]
