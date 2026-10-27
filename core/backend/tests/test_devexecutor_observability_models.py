from __future__ import annotations

import pytest

from backend.models.devexecutor_observability_models import (
    DEVEXECUTOR_OBSERVABILITY_SCHEMA_VERSION_Current,
    validate_observability_ref,
)


def test_validate_observability_ref_success() -> None:
    payload = {
        "schema_version": DEVEXECUTOR_OBSERVABILITY_SCHEMA_VERSION_Current,
        "correlation_id": "corr-1",
        "logs_ref": "opsbus://logs/corr-1",
        "metrics_ref": "prom://metrics/devexecutor",
        "traces_ref": "otel://trace/corr-1",
    }
    ref = validate_observability_ref(payload)
    assert ref.correlation_id == "corr-1"


def test_validate_observability_ref_rejects_missing_fields() -> None:
    with pytest.raises(Exception):
        validate_observability_ref({"schema_version": DEVEXECUTOR_OBSERVABILITY_SCHEMA_VERSION_Current})
