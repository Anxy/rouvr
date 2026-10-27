"""AIX-E2E-CYCLE-001M-W01 correlation and structured log tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.correlation import (
    CorrelationContextV1,
    CorrelationError,
    build_log_record,
)


def _ctx(**overrides: object) -> CorrelationContextV1:
    base = dict(tenant_id="tenant-1", correlation_id="corr-1", task_id="task-1")
    base.update(overrides)
    return CorrelationContextV1(**base)


class TestCorrelationContext:
    def test_requires_tenant_id(self) -> None:
        with pytest.raises(CorrelationError):
            CorrelationContextV1(tenant_id="", correlation_id="c1")

    def test_requires_correlation_id(self) -> None:
        with pytest.raises(CorrelationError):
            CorrelationContextV1(tenant_id="t1", correlation_id="")

    def test_as_dict_omits_none_fields(self) -> None:
        ctx = _ctx()
        assert "step_id" not in ctx.as_dict()
        assert ctx.as_dict()["task_id"] == "task-1"


class TestBuildLogRecord:
    def test_task_stage_requires_task_id(self) -> None:
        ctx = CorrelationContextV1(tenant_id="t1", correlation_id="c1")
        with pytest.raises(CorrelationError):
            build_log_record(context=ctx, stage="task", level="info", message="x")

    def test_step_stage_requires_full_chain(self) -> None:
        ctx = _ctx(cycle_id="cycle-1")  # missing step_id
        with pytest.raises(CorrelationError):
            build_log_record(context=ctx, stage="step", level="info", message="x")

    def test_valid_step_stage_record(self) -> None:
        ctx = _ctx(cycle_id="cycle-1", step_id="step-1")
        record = build_log_record(context=ctx, stage="step", level="info", message="stepped")
        assert record["step_id"] == "step-1"
        assert record["message"] == "stepped"

    def test_extra_field_colliding_with_context_field_rejected(self) -> None:
        ctx = _ctx()
        with pytest.raises(CorrelationError):
            build_log_record(context=ctx, stage="task", level="info", message="x", extra={"task_id": "other"})

    def test_extra_fields_merged(self) -> None:
        ctx = _ctx()
        record = build_log_record(context=ctx, stage="task", level="warning", message="x", extra={"retry": 2})
        assert record["retry"] == 2
        assert record["level"] == "warning"
