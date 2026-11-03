"""Test-process hygiene: no ambient environment leaks between suites.

Rule: a test module must never mutate os.environ at import time, and any
environment a test needs must be scoped (monkeypatch, or scoped_env below)
so later modules observe pristine state. Rationale: combined runs share one
process, so an import-time ROUVR_INSECURE_DEV=1 once silently authenticated
every later suite — and the failures only appeared when a file ran alone.

Hidden acceptance suites (per-attempt directories) additionally always run
standalone: one pytest process per attempt directory, with that attempt's
worktree as cwd, so neither os.environ nor sys.path leaks between attempts.
"""
from __future__ import annotations

import os

import pytest

from env_scope import scoped_env  # single implementation; re-exported here

__all__ = ["scoped_env"]


@pytest.fixture(autouse=True)
def _no_env_leak():
    """Guardrail: an unscoped os.environ mutation inside one test cannot
    pollute the next — the pre-test environment is restored afterwards."""
    before = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)
