from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEVEXECUTOR_TOOL_SCHEMA_VERSION_Current = "devexecutor.tool.current"
ToolName = Literal[
    "list_dir",
    "read_file",
    "read_range",
    "search",
    "edit_file",
    "insert_at_anchor",
    "apply_patch",
    "replace_range",
    "edit_symbol",
    "replace_in_function",
    "update_call_kwarg",
    "insert_after_symbol",
    "insert_before_return",
    "confirm_anchor",
    "write_file",
    "get_status",
    "cmd.run",
    "evidence.attach",
]
ToolErrorClass = Literal["policy", "runtime", "user"]


class ToolErrorCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1)
    class_name: ToolErrorClass = Field(..., alias="class")
    message: str = Field(..., min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ToolRequestCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["devexecutor.tool.current"] = Field(...)
    correlation_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    tool_name: ToolName
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResponseCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["devexecutor.tool.current"] = Field(...)
    correlation_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    tool_name: ToolName
    success: bool
    output: dict[str, Any] | None = None
    error: ToolErrorCurrent | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


def validate_tool_request(payload: dict[str, Any]) -> ToolRequestCurrent:
    return ToolRequestCurrent.model_validate(payload)


def validate_tool_response(payload: dict[str, Any]) -> ToolResponseCurrent:
    response = ToolResponseCurrent.model_validate(payload)
    if response.success and response.error is not None:
        raise ValueError("Tool response cannot contain error when success=true.")
    if (not response.success) and response.error is None:
        raise ValueError("Tool response must include error when success=false.")
    return response
