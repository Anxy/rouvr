"""Regression coverage: `_run_tests_intent_execute` must convert a missing test-runner
executable into a typed `RealExecuteError`, not let it escape as a bare
`FileNotFoundError`.

Live-proof context (2026-08-15): driving a real objective through `aix-chat-cli.ps1`
against the live :8100 service reproduced exactly this -- `_run_test_command_cancellable`
called `asyncio.create_subprocess_exec` with a runner (`npm`/`pytest`) that
`_infer_test_command` picked from a discovered manifest but never checked actually
resolves on this process's PATH. The bare `FileNotFoundError: [WinError 2]` propagated
all the way up through `_maybe_real_execute` / `_control` and surfaced to the CLI as an
opaque `UNCAUGHT_CONTROL_ERROR` with zero diagnostic detail -- structurally the same
"can't run this test command" situation as the `NO_APPLICABLE_TEST_COMMAND` case
already handled two lines above it, just without that case's typed error.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.aix_core.runtime.executor import real_execute as real_execute_module
from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteError,
    _run_tests_intent_execute,
)


class TestRunTestsIntentExecuteMissingRunner:
    def test_missing_test_runner_raises_typed_error_not_bare_filenotfound(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "package.json").write_text('{"name": "demo", "scripts": {}}', encoding="utf-8")

        monkeypatch.setattr(
            real_execute_module, "resolve_repository_root", lambda repository_id, *, settings=None: tmp_path
        )

        async def _fake_run_missing_executable(*args, **kwargs):
            raise FileNotFoundError(2, "The system cannot find the file specified")

        monkeypatch.setattr(
            real_execute_module, "_run_test_command_cancellable", _fake_run_missing_executable
        )

        async def scenario() -> RealExecuteError:
            with pytest.raises(RealExecuteError) as excinfo:
                await _run_tests_intent_execute(
                    objective="run the tests",
                    repository_id="demo-repo",
                    settings=None,
                    workspace=tmp_path,
                    workhub_loaded=False,
                    artifacts={},
                    budget_mgr=None,
                )
            return excinfo.value

        error = asyncio.run(scenario())
        assert error.code == "TEST_RUNNER_NOT_FOUND"
        assert "playwright" in error.detail or "npm" in error.detail
