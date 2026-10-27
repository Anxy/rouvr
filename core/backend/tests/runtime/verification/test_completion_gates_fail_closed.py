"""AIX-EDIT-001 fail-closed regression: test-only diff without verification.

Reproduces the EDIT-1 false PASS (wrong-file edit touching only tests,
test_result null, gates passed on diff-only + model-judged state
assertions). The mandated regression: wrong-file edit + untouched target
= FAIL. Source-bearing diffs (e.g. a README rename) keep prior behavior.
No contract-engine dependency; runs in the default suite.
"""

from __future__ import annotations

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.verification.completion_gates import (
    evaluate_completion_gates,
)


def _discovery() -> DiscoveryReport:
    return DiscoveryReport(
        manifests={},
        src_tree={},
        config_files={"tests/test_ship.py": "content"},
        test_files=["tests/test_ship.py"],
    )


def _test_only_diff() -> tuple[dict[str, dict[str, int]], str]:
    stats = {"tests/test_ship.py": {"additions": 2, "deletions": 0}}
    text = "\n".join(
        [
            "--- a/tests/test_ship.py",
            "+++ b/tests/test_ship.py",
            "@@ -7,3 +7,5 @@",
            "     assert manifest_line(\"BX1\", 12) == \"BX1:12.0kg\"",
            "+    assert manifest_line(\"BX3\", 0) == \"BX3:0.0kg\"",
            "+    assert manifest_line(\"BX4\", 100) == \"BX4:100.0kg\"",
        ]
    )
    return stats, text


def _readme_diff() -> tuple[dict[str, dict[str, int]], str]:
    stats = {"README.md": {"additions": 1, "deletions": 1}}
    text = "\n".join(
        [
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -1 +1 @@",
            "-# Demo Repository",
            "+# Renamed Application",
        ]
    )
    return stats, text


def _call(stats, text, **over) -> object:
    args: dict = {
        "discovery": _discovery(),
        "diff_stats": stats,
        "diff_text": text,
        "test_result": None,
        "already_satisfied": False,
        "diff_only_verification_allowed": True,
        "state_assertions_passed": True,
        "task_family": "EDIT",
        "objective": "fix manifest_line in ship.py",
    }
    args.update(over)
    return evaluate_completion_gates(**args)


def test_edit1_reproduction_test_only_diff_without_tests_fails_closed() -> None:
    """EDIT-1 shape: test-only diff, no test run -> FAIL with TESTS_NOT_RUN."""
    stats, text = _test_only_diff()
    result = _call(stats, text)
    assert result.passed is False
    assert any("TESTS_NOT_RUN" in reason for reason in result.reasons)
    assert result.gates.get("verification_passed") is False


def test_source_bearing_diff_keeps_prior_behavior() -> None:
    """README-rename shape: non-test diff without tests -> unchanged verdict."""
    stats, text = _readme_diff()
    result = _call(stats, text)
    assert result.passed is True


def test_executed_tests_still_govern() -> None:
    """A passing test run is untouched by the fail-closed rule."""
    stats, text = _test_only_diff()
    result = _call(stats, text, test_result={"returncode": 0, "stdout": "3 passed"})
    assert result.gates.get("verification_passed") is True


def test_failing_tests_still_fail() -> None:
    stats, text = _test_only_diff()
    result = _call(stats, text, test_result={"returncode": 1, "stdout": "1 failed"})
    assert result.gates.get("verification_passed") is False
