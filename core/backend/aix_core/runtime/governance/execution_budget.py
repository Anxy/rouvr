"""007-PREP: Execution budget tracking and deterministic loop detection.

Tracks tool activity counts, detects non-progress loops (identical search, identical read,
repeated clarification, repeated planning, exploration budget exhaustion), and enforces
that budget exhaustion ALWAYS results in objective_status=BLOCKED (never COMPLETED).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# Lightweight discovery probes (focused-file mode always tries these). They must
# not flip the single-target re-read exemption into a multi-file explore-loop.
_MANIFEST_PROBE_NAMES = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "pipfile",
        "composer.json",
        "go.mod",
        "cargo.toml",
        "gemfile",
        "pom.xml",
    }
)


def _is_manifest_probe(path: str) -> bool:
    name = str(path or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    return name in _MANIFEST_PROBE_NAMES


@dataclass
class ExecutionBudgetConfig:
    """Caps for tool usage and execution loop protection."""
    max_searches: int = 15
    max_reads: int = 25
    max_commands: int = 10
    max_tests: int = 10
    max_clarifications: int = 3
    max_retries: int = 3
    max_plan_revisions: int = 5
    max_mutation_rounds: int = 3
    max_identical_searches: int = 2
    max_identical_reads: int = 2
    max_identical_clarifications: int = 1
    max_identical_plan_revisions: int = 2


@dataclass
class BudgetCheckResult:
    allowed: bool
    objective_status: Literal["RUNNING", "BLOCKED", "FAILED"]
    reason_code: str | None
    reason_detail: str
    event_payload: dict[str, Any] | None = None


class ExecutionBudgetManager:
    """Cycle execution budget & loop detector.

    Tracks execution activity counts, detects identical search/read/clarify thrashing,
    and blocks non-progress loops deterministically.
    """

    def __init__(
        self,
        cycle_id: str,
        objective_id: str,
        config: ExecutionBudgetConfig | None = None,
    ) -> None:
        self.cycle_id = cycle_id
        self.objective_id = objective_id
        self.config = config or ExecutionBudgetConfig()

        # Activity counters
        self.search_count: int = 0
        self.read_count: int = 0
        self.command_count: int = 0
        self.test_count: int = 0
        self.clarification_count: int = 0
        self.retry_count: int = 0
        self.plan_revision_count: int = 0

        # Fingerprint histories for loop detection
        self.search_fingerprints: dict[str, int] = {}
        self.read_fingerprints: dict[str, int] = {}
        self.read_paths: set[str] = set()
        self.clarification_fingerprints: dict[str, int] = {}
        self.plan_fingerprints: dict[str, int] = {}
        # Admit/plan-bound mutation targets. Reloading this fixed set each repair
        # round is not an exploration loop (same rationale as single-file focus).
        self.bound_mutation_paths: set[str] = set()

        # Status tracking
        self.is_blocked: bool = False
        self.blocked_reason_code: str | None = None

    def set_bound_mutation_paths(self, paths: list[str] | tuple[str, ...] | set[str]) -> None:
        """Register the approved multi-file mutation set for re-read exemption."""
        self.bound_mutation_paths = {
            str(p or "").strip().lower().replace("\\", "/")
            for p in paths
            if str(p or "").strip()
        }

    def record_search(self, query: str, scope: str = "") -> BudgetCheckResult:
        """Record search tool call and check search budget & duplicate search loops."""
        if self.is_blocked:
            return self._already_blocked_result()

        self.search_count += 1
        fp = hashlib.sha256(f"{query.strip().lower()}|{scope.strip().lower()}".encode("utf-8")).hexdigest()
        self.search_fingerprints[fp] = self.search_fingerprints.get(fp, 0) + 1

        if (
            self.search_fingerprints[fp] > self.config.max_identical_searches
            and (bool(scope.strip()) or len(self.search_fingerprints) > 1)
        ):
            return self._block(
                reason_code="REPEATED_IDENTICAL_SEARCH",
                detail=f"Identical search executed {self.search_fingerprints[fp]} times: '{query}'",
                event_name="budget.loop_detected",
            )

        if self.search_count > self.config.max_searches:
            return self._block(
                reason_code="EXPLORATION_BUDGET_EXCEEDED",
                detail=f"Search limit exceeded: {self.search_count} > {self.config.max_searches}",
                event_name="budget.exhausted",
            )

        return BudgetCheckResult(allowed=True, objective_status="RUNNING", reason_code=None, reason_detail="")

    def record_read(self, file_path: str, file_hash: str = "") -> BudgetCheckResult:
        """Record file read tool call and check read budget & duplicate read loops."""
        if self.is_blocked:
            return self._already_blocked_result()

        self.read_count += 1
        path_key = str(file_path or "").strip().lower().replace("\\", "/")
        if path_key:
            self.read_paths.add(path_key)
        fp = hashlib.sha256(f"{path_key}|{file_hash}".encode("utf-8")).hexdigest()
        self.read_fingerprints[fp] = self.read_fingerprints.get(fp, 0) + 1

        # Bound single-file mutation (focused discovery + allowed-files reload +
        # expand-on-miss) legitimately re-reads the same target. Bound multi-file
        # repair rounds reload the same approved target set — also not a loop.
        # Exploration thrash is identical re-reads while roaming outside a bound set
        # (or across unbound discovery). Manifest probes never count as targets.
        mutation_paths = {p for p in self.read_paths if not _is_manifest_probe(p)}
        within_bound_set = bool(self.bound_mutation_paths) and mutation_paths.issubset(
            self.bound_mutation_paths
        )
        if (
            self.read_fingerprints[fp] > self.config.max_identical_reads
            and len(mutation_paths) > 1
            and not within_bound_set
        ):
            return self._block(
                reason_code="REPEATED_IDENTICAL_READ",
                detail=f"Identical file read executed {self.read_fingerprints[fp]} times on '{file_path}'",
                event_name="budget.loop_detected",
            )

        if self.read_count > self.config.max_reads:
            return self._block(
                reason_code="EXPLORATION_BUDGET_EXCEEDED",
                detail=f"Read limit exceeded: {self.read_count} > {self.config.max_reads}",
                event_name="budget.exhausted",
            )

        return BudgetCheckResult(allowed=True, objective_status="RUNNING", reason_code=None, reason_detail="")

    def record_command(self, command: str) -> BudgetCheckResult:
        """Record command execution tool call."""
        if self.is_blocked:
            return self._already_blocked_result()

        self.command_count += 1
        if self.command_count > self.config.max_commands:
            return self._block(
                reason_code="EXPLORATION_BUDGET_EXCEEDED",
                detail=f"Command count limit exceeded: {self.command_count} > {self.config.max_commands}",
                event_name="budget.exhausted",
            )
        return BudgetCheckResult(allowed=True, objective_status="RUNNING", reason_code=None, reason_detail="")

    def record_test(self, test_name: str) -> BudgetCheckResult:
        """Record test execution tool call."""
        if self.is_blocked:
            return self._already_blocked_result()

        self.test_count += 1
        if self.test_count > self.config.max_tests:
            return self._block(
                reason_code="EXPLORATION_BUDGET_EXCEEDED",
                detail=f"Test count limit exceeded: {self.test_count} > {self.config.max_tests}",
                event_name="budget.exhausted",
            )
        return BudgetCheckResult(allowed=True, objective_status="RUNNING", reason_code=None, reason_detail="")

    def record_clarification(self, question_text: str) -> BudgetCheckResult:
        """Record clarification attempt and detect repeated questions without progress."""
        if self.is_blocked:
            return self._already_blocked_result()

        self.clarification_count += 1
        fp = hashlib.sha256(question_text.strip().lower().encode("utf-8")).hexdigest()
        self.clarification_fingerprints[fp] = self.clarification_fingerprints.get(fp, 0) + 1

        if self.clarification_fingerprints[fp] > self.config.max_identical_clarifications:
            return self._block(
                reason_code="REPEATED_CLARIFICATION",
                detail=f"Repeated identical clarification question asked: '{question_text}'",
                event_name="budget.loop_detected",
            )

        if self.clarification_count > self.config.max_clarifications:
            return self._block(
                reason_code="EXPLORATION_BUDGET_EXCEEDED",
                detail=f"Clarification limit exceeded: {self.clarification_count} > {self.config.max_clarifications}",
                event_name="budget.exhausted",
            )

        return BudgetCheckResult(allowed=True, objective_status="RUNNING", reason_code=None, reason_detail="")

    def record_retry(self, attempt_purpose: str = "") -> BudgetCheckResult:
        """Record execution retry attempt."""
        if self.is_blocked:
            return self._already_blocked_result()

        self.retry_count += 1
        if self.retry_count > self.config.max_retries:
            return self._block(
                reason_code="EXECUTION_LOOP_DETECTED",
                detail=f"Maximum retries exceeded: {self.retry_count} > {self.config.max_retries}",
                event_name="budget.loop_detected",
            )

        return BudgetCheckResult(allowed=True, objective_status="RUNNING", reason_code=None, reason_detail="")

    def record_plan_revision(self, plan_hash: str) -> BudgetCheckResult:
        """Record plan revision and check for repeated identical plans."""
        if self.is_blocked:
            return self._already_blocked_result()

        self.plan_revision_count += 1
        self.plan_fingerprints[plan_hash] = self.plan_fingerprints.get(plan_hash, 0) + 1

        if self.plan_fingerprints[plan_hash] > self.config.max_identical_plan_revisions:
            return self._block(
                reason_code="PLAN_REVISION_BUDGET_EXCEEDED",
                detail=f"Identical plan generated {self.plan_fingerprints[plan_hash]} times without execution progress",
                event_name="budget.loop_detected",
            )

        if self.plan_revision_count > self.config.max_plan_revisions:
            return self._block(
                reason_code="PLAN_REVISION_BUDGET_EXCEEDED",
                detail=f"Plan revision limit exceeded: {self.plan_revision_count} > {self.config.max_plan_revisions}",
                event_name="budget.exhausted",
            )

        return BudgetCheckResult(allowed=True, objective_status="RUNNING", reason_code=None, reason_detail="")

    def summary(self) -> dict[str, Any]:
        """Return execution budget summary counters."""
        return {
            "search_count": self.search_count,
            "read_count": self.read_count,
            "command_count": self.command_count,
            "test_count": self.test_count,
            "clarification_count": self.clarification_count,
            "retry_count": self.retry_count,
            "plan_revision_count": self.plan_revision_count,
            "is_blocked": self.is_blocked,
            "blocked_reason_code": self.blocked_reason_code,
        }

    def _block(self, *, reason_code: str, detail: str, event_name: str) -> BudgetCheckResult:
        self.is_blocked = True
        self.blocked_reason_code = reason_code

        event_payload = {
            "event_name": event_name,
            "cycle_id": self.cycle_id,
            "objective_id": self.objective_id,
            "status": "blocked",
            "reason_code": reason_code,
            "occurred_at": _utc_iso(),
            "evidence": {
                "detail": detail,
                "counters": self.summary(),
            },
            "recommended_action": "Inspect agent exploration plan and resolve loop or increase budget limits.",
        }

        return BudgetCheckResult(
            allowed=False,
            objective_status="BLOCKED",
            reason_code=reason_code,
            reason_detail=detail,
            event_payload=event_payload,
        )

    def _already_blocked_result(self) -> BudgetCheckResult:
        return BudgetCheckResult(
            allowed=False,
            objective_status="BLOCKED",
            reason_code=self.blocked_reason_code or "EXECUTION_LOOP_DETECTED",
            reason_detail="Execution budget already exhausted or cycle blocked",
        )


__all__ = [
    "ExecutionBudgetConfig",
    "BudgetCheckResult",
    "ExecutionBudgetManager",
]
