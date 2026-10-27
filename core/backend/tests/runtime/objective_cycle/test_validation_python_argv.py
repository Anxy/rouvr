"""Validation commands must use the AIX interpreter, not bare PATH `python`."""

from __future__ import annotations

import sys
from pathlib import Path

from backend.aix_core.runtime.executor.multi_step_execute import (
    _safe_validation_argv,
    _validation_commands_from_objective,
)
from backend.aix_core.runtime.executor.python_argv import resolve_python_argv


def test_resolve_python_argv_rewrites_bare_python() -> None:
    assert resolve_python_argv(["python", "-m", "pytest", "-q"]) == [
        sys.executable,
        "-m",
        "pytest",
        "-q",
    ]
    assert resolve_python_argv([sys.executable, "-m", "pytest"])[0] == sys.executable


def test_objective_pytest_command_binds_sys_executable() -> None:
    objective = (
        "update feature_flag.py and test_feature_flag.py. "
        "Run python -m pytest backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py -q"
    )
    cmds = _validation_commands_from_objective(objective)
    assert len(cmds) == 1
    parts = _safe_validation_argv(cmds[0])
    assert parts is not None
    assert parts[0] == sys.executable
    assert parts[1:3] == ["-m", "pytest"]
    assert Path(parts[0]).is_file()


def test_safe_validation_argv_rewrites_literal_python_string() -> None:
    parts = _safe_validation_argv(
        "python -m pytest backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py -q"
    )
    assert parts is not None
    assert parts[0] == sys.executable


def test_safe_validation_argv_strips_collect_only() -> None:
    parts = _safe_validation_argv(
        "python -m pytest backend/tests/aix_core/objective_cycle/live_fixtures/"
        "test_feature_flag.py --collect-only -q"
    )
    assert parts is not None
    assert "--collect-only" not in parts
    assert "--co" not in parts
    assert "pytest" in parts
