"""001N-W01: Unified test harness levels for AIX-E2E-CYCLE-001."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HarnessLevel = Literal["unit", "contract", "integration", "live", "browser", "evidence"]

HARNESS_LEVELS: tuple[HarnessLevel, ...] = (
    "unit",
    "contract",
    "integration",
    "live",
    "browser",
    "evidence",
)


@dataclass(frozen=True)
class HarnessSuite:
    level: HarnessLevel
    pytest_nodeid_globs: tuple[str, ...]
    requires_runtime: bool
    evidence_required: bool


UNIFIED_HARNESS: tuple[HarnessSuite, ...] = (
    HarnessSuite("unit", ("tests/aix_core/objective_cycle/test_*.py",), False, True),
    HarnessSuite("contract", ("tests/test_objective_cycle_temporal_contracts.py",), False, True),
    HarnessSuite("integration", ("tests/aix_core/objective_cycle/test_cursor_*.py",), False, True),
    HarnessSuite("live", ("tests/aix_core/test_runtime_*.py",), True, True),
    HarnessSuite("browser", ("# owned by AIX-Frontend Playwright — not in backend tree",), True, True),
    HarnessSuite("evidence", ("docs/current/benchmarks/AIX-E2E-CYCLE-001/**/result.json",), False, True),
)


def harness_plan() -> list[dict]:
    return [
        {
            "level": s.level,
            "globs": list(s.pytest_nodeid_globs),
            "requires_runtime": s.requires_runtime,
            "evidence_required": s.evidence_required,
        }
        for s in UNIFIED_HARNESS
    ]


def assert_all_levels_defined() -> None:
    found = {s.level for s in UNIFIED_HARNESS}
    missing = set(HARNESS_LEVELS) - found
    if missing:
        raise AssertionError(f"HARNESS_LEVELS_MISSING:{sorted(missing)}")
