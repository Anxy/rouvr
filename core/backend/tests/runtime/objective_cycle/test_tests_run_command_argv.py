"""Regression: tests.run must pass argv into RepositoryToolbox.run_tests (cyc_01M0FG780D0Z)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from backend.aix_core.runtime.foundation.capability_executor import _execute_capability
from backend.aix_core.runtime.objective.contracts import ActionRequestV1

_ROOT = Path(__file__).resolve().parents[4]


def test_tests_run_passes_command_argv() -> None:
    box = MagicMock()
    box.run_tests.return_value = {"ok": True, "returncode": 0}
    request = ActionRequestV1(
        capability="tests",
        operation="run",
        arguments={
            "command": "pytest -q backend/tests/runtime/objective_cycle/test_html_css_real_execute.py"
        },
    )
    obs = _execute_capability(
        request,
        "tests",
        "run",
        dict(request.arguments),
        _ROOT,
        box,
        apply_edits=False,
    )
    assert obs.status == "SUCCESS"
    box.run_tests.assert_called_once()
    argv = box.run_tests.call_args.args[0]
    assert argv[0] == "pytest"
    assert "test_html_css_real_execute.py" in " ".join(argv)


def test_tests_run_without_command_defaults_pytest() -> None:
    box = MagicMock()
    box.run_tests.return_value = {"ok": True, "returncode": 0}
    request = ActionRequestV1(
        capability="tests",
        operation="run",
        arguments={"path": "backend/tests/foo.py"},
    )
    obs = _execute_capability(
        request,
        "tests",
        "run",
        dict(request.arguments),
        _ROOT,
        box,
        apply_edits=False,
    )
    assert obs.status == "SUCCESS"
    argv = box.run_tests.call_args.args[0]
    assert argv[:2] == ["pytest", "-q"]
    assert argv[2] == "backend/tests/foo.py"
