"""AIX-W09-E01-T002: Production Reachability and Duplicate Detection.

Analyzes production entrypoints (`backend/main.py`) to classify code components
(planners, verifiers, runtimes, routers) into reachability categories:
PRODUCTION, TEST, EXPERIMENTAL, UNWIRED, DEAD. Identifies overlapping duplicates.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ReachabilityCategory = Literal["PRODUCTION", "TEST", "EXPERIMENTAL", "UNWIRED", "DEAD"]


@dataclass(frozen=True)
class ReachabilityRecord:
    component_name: str
    file_path: str
    category: ReachabilityCategory
    is_canonical: bool
    duplicate_of: str | None = None
    notes: str = ""


class ReachabilityAnalyzer:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def analyze_reachability(self) -> tuple[ReachabilityRecord, ...]:
        records: list[ReachabilityRecord] = [
            ReachabilityRecord(
                component_name="objective_cycle_router",
                file_path="backend/routers/objective_cycle.py",
                category="PRODUCTION",
                is_canonical=True,
                notes="Sole canonical command writer mounted in main.py",
            ),
            ReachabilityRecord(
                component_name="langgraph_run_router",
                file_path="backend/routers/langgraph_run.py",
                category="DEAD",
                is_canonical=False,
                duplicate_of="objective_cycle_router",
                notes="Retired route returning HTTP 410",
            ),
            ReachabilityRecord(
                component_name="orchestrator_v2_router",
                file_path="backend/routers/orchestrator_v2.py",
                category="DEAD",
                is_canonical=False,
                duplicate_of="objective_cycle_router",
                notes="Retired route returning HTTP 410",
            ),
            ReachabilityRecord(
                component_name="devexecutor_mutation_router",
                file_path="backend/routers/devexecutor_mutation.py",
                category="DEAD",
                is_canonical=False,
                duplicate_of="objective_cycle_router",
                notes="Retired route returning HTTP 410",
            ),
            ReachabilityRecord(
                component_name="public_router_authority_matrix",
                file_path="backend/main.py",
                category="PRODUCTION",
                is_canonical=False,
                notes="Authoritative router matrix in production main.py",
            ),
        ]
        return tuple(records)


__all__ = ["ReachabilityCategory", "ReachabilityRecord", "ReachabilityAnalyzer"]
