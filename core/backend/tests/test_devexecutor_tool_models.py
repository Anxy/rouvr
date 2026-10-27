from __future__ import annotations

import pytest

from backend.models.devexecutor_tool_models import (
    DEVEXECUTOR_TOOL_SCHEMA_VERSION_Current,
    validate_tool_request,
    validate_tool_response,
)


def test_validate_tool_request_accepts_known_tool_names() -> None:
    payload = {
        "schema_version": DEVEXECUTOR_TOOL_SCHEMA_VERSION_Current,
        "correlation_id": "corr-100",
        "job_id": "job-100",
        "tool_name": "apply_patch",
        "args": {"patch": "*** Begin Patch"},
    }
    req = validate_tool_request(payload)
    assert req.tool_name == "apply_patch"


def test_validate_tool_request_rejects_unknown_tool_names() -> None:
    payload = {
        "schema_version": DEVEXECUTOR_TOOL_SCHEMA_VERSION_Current,
        "correlation_id": "corr-101",
        "job_id": "job-101",
        "tool_name": "unknown_tool",
        "args": {},
    }
    with pytest.raises(Exception):
        validate_tool_request(payload)


def test_validate_tool_response_enforces_success_error_consistency() -> None:
    ok_payload = {
        "schema_version": DEVEXECUTOR_TOOL_SCHEMA_VERSION_Current,
        "correlation_id": "corr-102",
        "job_id": "job-102",
        "tool_name": "get_status",
        "success": True,
        "output": {"state": "ok"},
    }
    ok = validate_tool_response(ok_payload)
    assert ok.success is True

    bad_payload = {
        "schema_version": DEVEXECUTOR_TOOL_SCHEMA_VERSION_Current,
        "correlation_id": "corr-103",
        "job_id": "job-103",
        "tool_name": "cmd.run",
        "success": False,
        "output": {"stdout": ""},
    }
    with pytest.raises(ValueError, match="must include error when success=false"):
        validate_tool_response(bad_payload)
