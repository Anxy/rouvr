"""AIX Feature 12 (Packet 07) — Regression Verification.

Verifies that post-repair modifications do not break previously passing OVC items.
Opens a new GapV1 with category='REGRESSION' and severity='BLOCKER' if a regression occurs.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from backend.aix_core.runtime.verification.gap import (
    GapV1,
)
from backend.aix_core.runtime.verification.verification_result import (
    OVItemResultV1,
)


class RegressionChecker:
    """Checks for regressions in previously passing OVC criteria after repair attempts."""

    def __init__(self, baseline_passed_ov_ids: Sequence[str] | set[str] | None = None) -> None:
        self.baseline_passed_ov_ids: set[str] = set(baseline_passed_ov_ids or ())

    def set_baseline(self, passed_ov_ids: Sequence[str] | set[str]) -> None:
        self.baseline_passed_ov_ids = set(passed_ov_ids)

    def detect_regressions(
        self,
        *,
        objective_id: str,
        verification_id: str,
        current_results: list[OVItemResultV1],
        baseline_ov_ids: set[str] | None = None,
    ) -> list[GapV1]:
        """Detect if any previously passing OV item has now failed."""
        baseline = baseline_ov_ids if baseline_ov_ids is not None else self.baseline_passed_ov_ids
        regression_gaps: list[GapV1] = []

        for item_res in current_results:
            if item_res.ov_id in baseline and item_res.status == "FAIL":
                gap_id = f"gap_reg_{objective_id}_{item_res.ov_id.lower()}_{hashlib.md5((verification_id + item_res.ov_id).encode()).hexdigest()[:6]}"
                gap = GapV1(
                    gap_id=gap_id,
                    objective_id=objective_id,
                    verification_id=verification_id,
                    ov_id=item_res.ov_id,
                    category="REGRESSION",
                    expected=item_res.expected or f"Preserve passing baseline for {item_res.ov_id}",
                    observed=f"Regression detected: was passing, now failed. {item_res.observed}",
                    evidence=item_res.evidence_refs,
                    severity="BLOCKER",
                    status="OPEN",
                    provenance={
                        "verifier": "regression_checker",
                        "baseline_ov_id": item_res.ov_id,
                        "verification_id": verification_id,
                    },
                )
                regression_gaps.append(gap)

        return regression_gaps


__all__ = [
    "RegressionChecker",
]
