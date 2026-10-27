"""Regression: semantic verifier treats focused green tests + source fix as proved."""

from backend.aix_core.runtime.verification.semantic_verifier import (
    _diff_touches_non_test_source,
    _focused_tests_passed,
)
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def test_diff_touches_fixture_module_under_tests_tree() -> None:
    diff = """--- a/backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py
+++ b/backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py
@@ -1 +1 @@
-return expiry_date < current_date
+return expiry_date >= current_date
"""
    assert _diff_touches_non_test_source(diff) is True


def test_diff_test_module_only_is_not_non_test_source() -> None:
    diff = """--- a/backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py
+++ b/backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py
@@ -1 +1 @@
-x = 1
+x = 2
"""
    assert _diff_touches_non_test_source(diff) is False


def test_focused_tests_passed_from_command_event() -> None:
    result = RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace="/tmp",
        events=[
            {
                "event_type": "tool.command_run",
                "payload_refs": {
                    "command": "python -m pytest -q backend/tests/.../test_feature_flag.py",
                    "returncode": "0",
                },
            }
        ],
    )
    assert _focused_tests_passed(result) is True
