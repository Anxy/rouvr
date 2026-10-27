"""AIX-E2E-CYCLE-001H-W02: quality gates.

Named, independent gates that all must pass for a step attempt to be verifiable as
successful. Written first (before `verification.py`, which composes these) because the
verification contract's acceptance gate is defined in terms of this module's aggregate
result, not the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GateName = Literal["test", "lint", "type", "security", "diff", "acceptance"]

REQUIRED_GATES: frozenset[str] = frozenset({"test", "lint", "type", "security", "diff", "acceptance"})


@dataclass(frozen=True)
class QualityGateResultV1:
    gate: GateName
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class QualityGateReportV1:
    results: tuple[QualityGateResultV1, ...]

    @property
    def all_passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def failed_gates(self) -> tuple[str, ...]:
        return tuple(result.gate for result in self.results if not result.passed)

    @property
    def gate_names(self) -> frozenset[str]:
        return frozenset(result.gate for result in self.results)


class QualityGateError(ValueError):
    pass


def build_report(results: dict[str, bool | tuple[bool, str]]) -> QualityGateReportV1:
    """Build a report from a caller-supplied `{gate_name: passed}` or
    `{gate_name: (passed, detail)}` mapping. Fails closed if any of the 6 required gates
    (`REQUIRED_GATES`) is missing — an omitted gate is never treated as passing."""
    missing = REQUIRED_GATES - set(results)
    if missing:
        raise QualityGateError(f"missing required quality gate(s): {sorted(missing)}")
    unknown = set(results) - REQUIRED_GATES
    if unknown:
        raise QualityGateError(f"unknown quality gate(s): {sorted(unknown)}")

    built: list[QualityGateResultV1] = []
    for gate, value in results.items():
        if isinstance(value, tuple):
            passed, detail = value
        else:
            passed, detail = value, ""
        built.append(QualityGateResultV1(gate=gate, passed=passed, detail=detail))  # type: ignore[arg-type]
    return QualityGateReportV1(results=tuple(built))


__all__ = [
    "GateName",
    "REQUIRED_GATES",
    "QualityGateResultV1",
    "QualityGateReportV1",
    "QualityGateError",
    "build_report",
]
