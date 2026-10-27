"""EX3-S007 / DRIFT-023: repeatable static-scan regression guard proving no
backend source file imports or instantiates a provider SDK directly, which
would bypass the mandated Hyeve Integration gateway route. Prior to this
test, the equivalent check only ever ran as a one-off manual grep."""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]

# Directories that are legitimately exempt: tests exercise mocked/forbidden
# patterns on purpose (see test_validator_rejects_direct_provider_tool),
# and archived/legacy code is not part of the live agent-facing surface.
_EXEMPT_DIR_PARTS = {"tests", "archive", "__pycache__", ".git"}

_DIRECT_PROVIDER_PATTERNS = [
    re.compile(r"^\s*import\s+openai\b"),
    re.compile(r"^\s*from\s+openai\b"),
    re.compile(r"^\s*import\s+anthropic\b"),
    re.compile(r"^\s*from\s+anthropic\b"),
    re.compile(r"\bOpenAI\s*\("),
    re.compile(r"\bAnthropic\s*\("),
]


def _iter_backend_source_files() -> list[Path]:
    files = []
    for path in BACKEND_ROOT.rglob("*.py"):
        if any(part in _EXEMPT_DIR_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def test_no_direct_provider_sdk_usage_in_live_backend_source() -> None:
    """Candidate-level static evidence for DRIFT-023: the five agent roles
    (and everything else in the live backend) must route all model traffic
    through Hyeve Integration, never a direct provider SDK call. This does
    not prove a live network-level bypass is impossible (that needs a real
    running gateway + hostile test, out of reach here) — it proves the
    currently-shipped source code contains no such call site, repeatably."""
    offenders: list[str] = []
    for path in _iter_backend_source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _DIRECT_PROVIDER_PATTERNS):
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "Direct provider SDK usage found outside the sanctioned Hyeve "
        f"Integration route:\n" + "\n".join(offenders)
    )


def test_scan_actually_detects_a_planted_violation() -> None:
    """Proves the scan logic itself works (would have caught a real
    violation) rather than vacuously passing because the regex never
    matches anything real."""
    sample = "client = OpenAI(api_key=key)"
    assert any(pattern.search(sample) for pattern in _DIRECT_PROVIDER_PATTERNS)
