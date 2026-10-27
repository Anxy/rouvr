"""C033: test discovery and symbol/service linkage for proposed effects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class TestLinkError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TestCandidate:
    test_path: str
    score: float
    reason: str
    linked_symbols: tuple[str, ...] = ()


_TEST_NAME = re.compile(r"^(?:test_|.*_test\.py$|.*\.test\.(?:js|ts)$|.*_spec\.(?:js|ts)$)")


def discover_tests(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        name = p.replace("\\", "/").split("/")[-1]
        if name.startswith("test_") and name.endswith(".py"):
            out.append(p)
        elif name.endswith(("_test.py", ".test.js", ".test.ts", "_spec.js", "_spec.ts")):
            out.append(p)
        elif "/tests/" in p.replace("\\", "/") or "/__tests__/" in p.replace("\\", "/"):
            out.append(p)
    return sorted(set(out))


def link_tests_for_effect(
    *,
    effect_path: str,
    symbols: list[str],
    test_paths: list[str],
) -> list[TestCandidate]:
    """Score test candidates for a proposed file/symbol effect."""
    stem = effect_path.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
    candidates: list[TestCandidate] = []
    for tp in test_paths:
        score = 0.0
        reasons: list[str] = []
        linked: list[str] = []
        base = tp.replace("\\", "/").lower()
        if stem.lower() in base:
            score += 0.6
            reasons.append("path_stem_match")
        for sym in symbols:
            if sym and sym.lower() in base:
                score += 0.3
                linked.append(sym)
                reasons.append(f"symbol:{sym}")
        if "/tests/" in base or base.startswith("test_"):
            score += 0.1
            reasons.append("test_layout")
        if score > 0:
            candidates.append(
                TestCandidate(
                    test_path=tp,
                    score=min(score, 1.0),
                    reason=",".join(reasons),
                    linked_symbols=tuple(linked),
                )
            )
    candidates.sort(key=lambda c: (-c.score, c.test_path))
    return candidates


def require_test_coverage_or_gap(
    *,
    effect_path: str,
    symbols: list[str],
    test_paths: list[str],
) -> dict[str, Any]:
    linked = link_tests_for_effect(effect_path=effect_path, symbols=symbols, test_paths=test_paths)
    if linked and linked[0].score >= 0.5:
        return {"ok": True, "candidates": [c.__dict__ for c in linked[:5]], "gap": None}
    return {
        "ok": False,
        "candidates": [c.__dict__ for c in linked[:5]],
        "gap": "NO_JUSTIFIED_TEST_CANDIDATE",
        "effect_path": effect_path,
    }


__all__ = [
    "TestLinkError",
    "TestCandidate",
    "discover_tests",
    "link_tests_for_effect",
    "require_test_coverage_or_gap",
]
