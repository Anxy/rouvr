"""Rewrite bare `python` argv heads to the AIX process interpreter."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_python_argv(argv: list[str]) -> list[str]:
    """Rewrite `python`/`python3`/`py` heads to `sys.executable` (venv-aware).

    Live S3: bare `python` on Windows PATH often points at a system install without
    pytest, so gates failed with "No module named pytest" after a correct repair
    mutation. Always prefer the interpreter running AIX.
    """
    if not argv:
        return argv
    head = Path(str(argv[0])).name.lower()
    if head in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return [sys.executable, *[str(a) for a in argv[1:]]]
    return [str(a) for a in argv]
