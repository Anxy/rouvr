"""AIX-EC-004 Packet EC-04: language-dispatched file structure outlines.

Planner, coder, and verifier receive class/method/import/ranges — not an 80+40
line excerpt of a large file.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.aix_core.intelligence.repository.php_structural import PhpStructuralError, outline_lines, parse_php_structure

_SIG_OUTLINE_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static|final|async|export)\s+)*"
    r"(?:function|def|class|interface|trait|enum)\s+\w+",
    re.IGNORECASE,
)


@dataclass
class FileOutline:
    path: str
    language: str
    class_name: str | None = None
    imports: list[str] = field(default_factory=list)
    methods: list[dict[str, object]] = field(default_factory=list)
    text: str = ""
    public_symbols: tuple[str, ...] = ()
    public_signatures: tuple[str, ...] = ()


def detect_language(path: str, content: str = "") -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".php" or str(content).lstrip().startswith("<?php"):
        return "php"
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        return "javascript"
    return "text"


def _python_outline(path: str, src: str, *, max_chars: int) -> FileOutline:
    methods: list[dict[str, object]] = []
    imports: list[str] = []
    class_name = None
    public: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return FileOutline(path=path, language="python", text=src[:max_chars])
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node) if hasattr(ast, "unparse") else src.splitlines()[node.lineno - 1])
        if isinstance(node, ast.ClassDef):
            class_name = class_name or node.name
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        {
                            "name": item.name,
                            "visibility": "private" if item.name.startswith("_") else "public",
                            "start_line": item.lineno,
                            "end_line": getattr(item, "end_lineno", item.lineno) or item.lineno,
                        }
                    )
                    if not item.name.startswith("_"):
                        public.append(item.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(
                {
                    "name": node.name,
                    "visibility": "public",
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno) or node.lineno,
                }
            )
            public.append(node.name)
    lines = [f"file {path} language python"]
    if class_name:
        lines.append(f"class {class_name}")
    if imports:
        lines.append("imports:")
        lines.extend(f"  {i}" for i in imports[:40])
    lines.append("methods:")
    for m in methods:
        lines.append(f"  {m['visibility']} {m['name']} L{m['start_line']}-{m['end_line']}")
    text = "\n".join(lines)[:max_chars]
    return FileOutline(
        path=path,
        language="python",
        class_name=class_name,
        imports=imports,
        methods=methods,
        text=text,
        public_symbols=tuple(public),
    )


def _php_outline(path: str, src: str, *, max_chars: int) -> FileOutline:
    try:
        struct = parse_php_structure(src)
    except PhpStructuralError:
        return FileOutline(path=path, language="php", text=outline_lines(src, max_chars=max_chars))
    methods = []
    public: list[str] = []
    sigs: list[str] = []
    for method in struct.methods:
        start_line = src[: method.body_start].count("\n") + 1
        end_line = src[: method.end].count("\n")
        methods.append(
            {
                "name": method.name,
                "visibility": method.visibility,
                "start_line": start_line,
                "end_line": end_line,
                "params": method.params.strip(),
            }
        )
        if method.visibility == "public":
            public.append(method.name)
            sigs.append(re.sub(r"\s+", " ", method.signature).strip())
    return FileOutline(
        path=path,
        language="php",
        class_name=struct.class_name,
        imports=[imp.text.strip() for imp in struct.imports],
        methods=methods,
        text=outline_lines(src, max_chars=max_chars),
        public_symbols=tuple(public),
        public_signatures=tuple(sigs),
    )


def _fallback_outline(path: str, src: str, language: str, *, max_chars: int) -> FileOutline:
    uses: list[str] = []
    sigs: list[str] = []
    for raw in src.splitlines():
        stripped = raw.strip()
        if stripped.startswith("use ") and stripped.endswith(";") and len(uses) < 40:
            uses.append(stripped[:160])
        elif _SIG_OUTLINE_RE.match(stripped):
            sigs.append(stripped[:160])
    text = "\n".join(["imports:"] + uses + ["methods:"] + sigs)[:max_chars]
    return FileOutline(path=path, language=language, imports=uses, text=text)


def build_file_outline(path: str, content: str, *, max_chars: int = 4000) -> FileOutline:
    language = detect_language(path, content)
    if language == "php":
        return _php_outline(path, content, max_chars=max_chars)
    if language == "python":
        return _python_outline(path, content, max_chars=max_chars)
    return _fallback_outline(path, content, language, max_chars=max_chars)


def outlines_for_files(files: dict[str, str], *, max_chars_each: int = 4000) -> dict[str, FileOutline]:
    return {path: build_file_outline(path, content, max_chars=max_chars_each) for path, content in files.items()}


def outline_prompt_block(files: dict[str, str], *, max_chars_each: int = 4000) -> str:
    parts: list[str] = []
    for path, content in files.items():
        outline = build_file_outline(path, content, max_chars=max_chars_each)
        parts.append(f"--- {path} STRUCTURE ---\n{outline.text}")
    return "\n\n".join(parts) if parts else "(no structured files)"


__all__ = [
    "FileOutline",
    "build_file_outline",
    "detect_language",
    "outline_prompt_block",
    "outlines_for_files",
]
