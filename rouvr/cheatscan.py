"""rouvr/cheatscan.py — flag test-gaming in the PR diff (R8). Pure function.

Rules: SKIP (skipped/xfail/deleted tests), MOCK (new mocks/monkeypatches),
LITERAL (added line contains a hidden-test literal), EMPTY_TODO (stub bodies),
BROAD_EXCEPT. Test-file rules apply to added lines in test files only; LITERAL
applies to all added lines (prod hardcoding is the real cheat).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SKIP = re.compile(r"@pytest\.mark\.(skip|xfail)|pytest\.skip\(|@unittest\.skip")
_MOCK = re.compile(r"\b(Mock|MagicMock|patch|monkeypatch)\b")
_TODO = re.compile(r"\bTODO\b|\bFIXME\b|\bXXX\b", re.IGNORECASE)
_BROAD_EXCEPT = re.compile(r"^\s*except\s*(:|Exception\b)")
_PASS_ONLY = re.compile(r"^\s*pass\s*(#.*)?$")
_DELETED_TEST = re.compile(r"^-def (test_\w+)")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class CheatFlag:
    rule: str
    file: str
    line: int
    excerpt: str


def _is_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("test_") or "/tests/" in path or base.endswith("_test.py")


def _literals_in(line: str) -> list[str]:
    return [t.lower() for t in re.findall(r"'([^']{6,})'|\"([^\"]{6,})", line) for t in t if t]


def scan_diff(diff_text: str, *, hidden_literals: frozenset[str]) -> list[CheatFlag]:
    flags: list[CheatFlag] = []
    current, lineno = "", 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            current, lineno = raw[len("+++ b/"):], 0
        elif raw.startswith("@@ "):
            m = _HUNK.match(raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            line, excerpt = raw[1:], raw[1:120]
            if _is_test_file(current):
                if _SKIP.search(line):
                    flags.append(CheatFlag("SKIP", current, lineno, excerpt))
                if _MOCK.search(line):
                    flags.append(CheatFlag("MOCK", current, lineno, excerpt))
                if _TODO.search(line) or _PASS_ONLY.match(line):
                    flags.append(CheatFlag("EMPTY_TODO", current, lineno, excerpt))
                if _BROAD_EXCEPT.match(line):
                    flags.append(CheatFlag("BROAD_EXCEPT", current, lineno, excerpt))
            for lit in _literals_in(line):
                if lit in hidden_literals:
                    flags.append(CheatFlag("LITERAL", current, lineno, excerpt))
            lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            m = _DELETED_TEST.match(raw)
            if m and _is_test_file(current):
                flags.append(CheatFlag("DELETED_TEST", current, lineno, raw[1:120]))
        else:
            lineno += 1
    return flags
