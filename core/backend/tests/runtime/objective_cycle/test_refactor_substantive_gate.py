from backend.aix_core.runtime.verification.completion_gates import (
    evaluate_completion_gates,
    is_vague_refactor_objective,
    refactor_change_is_substantive,
)
from backend.aix_core.runtime.objective.discovery import DiscoveryReport


def test_vague_refactor_objective_detection() -> None:
    assert is_vague_refactor_objective('refactor "D:/x/web.php"') is True
    assert is_vague_refactor_objective("refactor web.php: group routes by module") is False


def test_trivial_refactor_diff_is_not_substantive() -> None:
    diff = """--- a/web.php
+++ b/web.php
@@ -1,3 +1,3 @@
-Route::Post('x');
+Route::post('x');
"""
    assert refactor_change_is_substantive(diff_text=diff, diff_stats={"web.php": {"additions": 1, "deletions": 1}}) is False


def test_completion_gate_blocks_vague_refactor_with_cosmetic_diff() -> None:
    diff = """--- a/web.php
+++ b/web.php
@@ -1,3 +1,3 @@
-Route::Post('x');
+Route::post('x');
"""
    discovery = DiscoveryReport(config_files={"web.php": "Route::Post('x');"})
    result = evaluate_completion_gates(
        discovery=discovery,
        diff_stats={"web.php": {"additions": 1, "deletions": 1}},
        diff_text=diff,
        test_result=None,
        diff_only_verification_allowed=True,
        task_family="REFACTOR",
        objective='refactor "D:/v2.1/Visions/web.php"',
    )
    assert result.gates.get("refactor_substantive") is False
    assert result.passed is False
    assert any("cosmetic-only" in r for r in result.reasons)
