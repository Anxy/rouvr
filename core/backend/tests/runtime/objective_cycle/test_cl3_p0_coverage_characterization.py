"""AIX Closed-Loop 003, Packet CL3-P0: characterization test for the incident this
sprint exists to fix (`cyc_f45ad72bf0c6`, see `.validation/closed-loop-003-baseline.md`).

The frozen fixture diff (`tests/fixtures/objective_cycle/closed_loop_003/
cyc_f45ad72bf0c6_diff.txt`) is a real +48/-9 import-banner restyle that named zero of
the objective's asked work (extract repeated DB/query boilerplate, remove unused
use-imports) done. Today, `evaluate_completion_gates` has no concept of "did the diff
cover what the objective asked" -- only line-count substantiveness -- so this cosmetic
diff passes every gate, exactly matching the real incident's own recorded
`completion_gates.json` (`"passed": true`).

Written as a failing characterization test before CL3-P1 (`objective_coverage.py`)
existed; now that CL3-P1 has landed, it is the permanent regression proof. All
non-coverage gate inputs are pre-satisfied (discovery, verification, secrets) so this
isolates the coverage gate specifically, not an unrelated pre-existing gate.
"""

from __future__ import annotations

from pathlib import Path

from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.objective.discovery import DiscoveryReport

_FIXTURE_DIFF = (
    Path(__file__).parent.parent.parent
    / "fixtures" / "objective_cycle" / "closed_loop_003" / "cyc_f45ad72bf0c6_diff.txt"
).read_text(encoding="utf-8")

_OBJECTIVE = (
    "Refactor D:\\v2.1\\Visions\\CronJobController.php: group public cron action "
    "methods by domain with clear section comments (Fee, Broadcast/FCM, Student, "
    "Payments/Webhooks, Reporting), extract repeated DB/query boilerplate into "
    "private helper methods inside the same file, remove unused use-imports, and "
    "keep all public method names and HTTP/middleware behavior unchanged."
)

# Matches the incident's own recorded stats (+48/-9 on the one changed file).
_DIFF_STATS = {"CronJobController.php": {"additions": 48, "deletions": 9}}


def test_cosmetic_diff_fails_objective_coverage_gate() -> None:
    """The actual requirement (sprint Definition of Done #1). Was a genuinely
    failing characterization test before CL3-P1 (`objective_coverage.py`) landed --
    kept as the permanent regression proof now that it passes for real, rather than
    deleted once green (a companion test documenting the pre-fix "still broken"
    state was removed here since it now correctly fails its own assertion)."""
    result = evaluate_completion_gates(
        discovery=DiscoveryReport(config_files={"CronJobController.php": "<?php\nclass CronJobController {}\n"}),
        diff_stats=_DIFF_STATS,
        diff_text=_FIXTURE_DIFF,
        test_result={"returncode": 0},
        task_family="REFACTOR",
        objective=_OBJECTIVE,
    )
    assert result.gates.get("objective_coverage") is False
    assert result.passed is False
    assert any("OBJECTIVE_COVERAGE_FAILED" in r for r in result.reasons)
