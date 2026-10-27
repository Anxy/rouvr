"""Tests for F8-P3 Production Import Migration & Zero Legacy Direct Imports."""

from __future__ import annotations

import inspect
import pytest

from backend.aix_core.runtime.executor import real_execute
from backend.aix_core.runtime.objective import mark_workhub_done


def test_production_modules_import_canonical_workhub_client():
    real_exec_src = inspect.getsource(real_execute)
    assert "from backend.aix_core.framework.integration.workhub.client import WorkHubTaskClient" in real_exec_src
    assert "from backend.aix_orchestrator_v2.workhub_client import WorkHubTaskClient" not in real_exec_src

    mark_done_src = inspect.getsource(mark_workhub_done)
    assert "from backend.aix_core.framework.integration.workhub.client import WorkHubTaskClient" in mark_done_src
    assert "from backend.aix_orchestrator_v2.workhub_client import WorkHubTaskClient" not in mark_done_src
