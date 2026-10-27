from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EXECUTION_PROOF_BUNDLE_SCHEMA_VERSION_CURRENT: Literal[
    "aix.execution_proof_bundle.current"
] = "aix.execution_proof_bundle.current"


class ExecutionCommandResultCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., min_length=1)
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(..., ge=0)


class ExecutionTimestampsCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at_utc: datetime
    ended_at_utc: datetime

    @model_validator(mode="after")
    def _validate_order(self) -> ExecutionTimestampsCurrent:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("ended_at_utc must be greater than or equal to started_at_utc")
        return self


class ExecutionProofBundleCurrent(BaseModel):
    """Strict proof object for CLI lane outcome evaluation.

    This is intentionally narrow. Rich transcripts, logs, and narrative evidence
    belong in referenced artifacts or broader evidence packs, not in this object.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aix.execution_proof_bundle.current"] = EXECUTION_PROOF_BUNDLE_SCHEMA_VERSION_CURRENT
    start_commit: str = Field(..., min_length=7)
    workspace_root: str = Field(..., min_length=1)
    execution_command: list[str] = Field(..., min_length=1)
    changed_files: list[str]
    git_diff: str
    validation_commands: list[str] = Field(..., min_length=1)
    validation_results: list[ExecutionCommandResultCurrent] = Field(..., min_length=1)
    linter_results: list[ExecutionCommandResultCurrent] = Field(default_factory=list)
    test_results: list[ExecutionCommandResultCurrent] = Field(default_factory=list)
    executor_status: str = Field(..., min_length=1)
    verifier_decision: Literal["success", "failure", "incomplete"]
    timestamps: ExecutionTimestampsCurrent


def get_execution_proof_bundle_schema_current() -> dict:
    return ExecutionProofBundleCurrent.model_json_schema()


def validate_execution_proof_bundle_model(payload: dict) -> ExecutionProofBundleCurrent:
    return ExecutionProofBundleCurrent.model_validate(payload)
