"""AIX-E2E-CYCLE-001M-W09: security scanning gate.

A lightweight, pluggable security scan gate that runs before any executor dispatch
and after any code-generation step. Scans are pluggable (SAST, dependency, secret
scanning); each scanner returns a `ScanResultV1`. The gate fails closed — any CRITICAL
or HIGH finding halts execution and triggers StopTheLine.

Severity levels (in descending order): CRITICAL, HIGH, MEDIUM, LOW, INFORMATIONAL
Gate threshold (default): block on CRITICAL or HIGH
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Literal


class Severity(IntEnum):
    INFORMATIONAL = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


SeverityLabel = Literal["INFORMATIONAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

_SEVERITY_MAP: dict[str, Severity] = {s.name: s for s in Severity}


@dataclass(frozen=True)
class ScanFindingV1:
    scanner_id: str
    rule_id: str
    severity: SeverityLabel
    title: str
    detail: str
    location: str | None = None  # file path or resource ref

    @property
    def severity_level(self) -> Severity:
        return _SEVERITY_MAP[self.severity]


@dataclass(frozen=True)
class ScanResultV1:
    scanner_id: str
    scan_target: str
    findings: tuple[ScanFindingV1, ...]
    scan_completed: bool = True
    error: str | None = None  # set when scan_completed=False

    @property
    def highest_severity(self) -> Severity:
        if not self.findings:
            return Severity.INFORMATIONAL
        return max(f.severity_level for f in self.findings)


@dataclass(frozen=True)
class SecurityGateResultV1:
    passed: bool
    scan_results: tuple[ScanResultV1, ...]
    blocking_findings: tuple[ScanFindingV1, ...]
    reason: str


Scanner = Callable[[str], ScanResultV1]


class SecurityScanGate:
    """Runs all registered scanners against a scan target and applies the severity gate.

    Gate rule: any finding at or above `block_at` severity causes the gate to fail.
    A failed or incomplete scan (scan_completed=False) also blocks by default
    (see `block_on_scan_failure`).
    """

    def __init__(
        self,
        *,
        scanners: list[Scanner] | None = None,
        block_at: Severity = Severity.HIGH,
        block_on_scan_failure: bool = True,
    ) -> None:
        self._scanners: list[Scanner] = scanners or []
        self._block_at = block_at
        self._block_on_scan_failure = block_on_scan_failure

    def register(self, scanner: Scanner) -> None:
        self._scanners.append(scanner)

    def scan(self, scan_target: str) -> SecurityGateResultV1:
        if not scan_target or not scan_target.strip():
            return SecurityGateResultV1(
                passed=False,
                scan_results=(),
                blocking_findings=(),
                reason="scan_target must not be empty",
            )

        all_results: list[ScanResultV1] = []
        for scanner in self._scanners:
            result = scanner(scan_target)
            all_results.append(result)

        scan_results = tuple(all_results)
        blocking: list[ScanFindingV1] = []

        for result in scan_results:
            if not result.scan_completed:
                if self._block_on_scan_failure:
                    return SecurityGateResultV1(
                        passed=False,
                        scan_results=scan_results,
                        blocking_findings=(),
                        reason=f"Scanner {result.scanner_id!r} failed to complete: {result.error}",
                    )
                continue
            for finding in result.findings:
                if finding.severity_level >= self._block_at:
                    blocking.append(finding)

        if blocking:
            severity_labels = sorted({f.severity for f in blocking}, reverse=True)
            return SecurityGateResultV1(
                passed=False,
                scan_results=scan_results,
                blocking_findings=tuple(blocking),
                reason=f"Security scan blocked: {len(blocking)} finding(s) at or above "
                       f"threshold ({self._block_at.name}). Severities: {severity_labels}",
            )

        return SecurityGateResultV1(
            passed=True,
            scan_results=scan_results,
            blocking_findings=(),
            reason="All scanners completed with no findings above the gate threshold.",
        )


# Built-in no-op scanner (for environments without real SAST tooling)
def noop_scanner(scan_target: str) -> ScanResultV1:
    """Placeholder scanner — records that no scan was performed."""
    return ScanResultV1(
        scanner_id="noop",
        scan_target=scan_target,
        findings=(),
        scan_completed=True,
        error=None,
    )


__all__ = [
    "Severity",
    "SeverityLabel",
    "ScanFindingV1",
    "ScanResultV1",
    "SecurityGateResultV1",
    "Scanner",
    "SecurityScanGate",
    "noop_scanner",
]
