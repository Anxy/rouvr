from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVIDENCE_PACK_SCHEMA_VERSION_Current = "evidence_pack.current"


class EvidenceArtifactRefCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    exists: bool
    optional: bool = False
    size_bytes: int | None = Field(default=None, ge=0)


class EvidenceAcceptanceMapItemCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptance_id: str = Field(..., min_length=1)
    acceptance_text: str = Field(..., min_length=1)
    met: bool
    evidence_refs: list[str] = Field(default_factory=list)
    proof_kind: Literal[
        "artifact", "patch", "command", "pytest", "live_http", "static_check", "partial_failure"
    ] | None = None
    command_refs: list[str] = Field(default_factory=list)
    assertion_summary: str | None = None
    skipped_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    note: str | None = None

    @model_validator(mode="after")
    def _validate_proof_semantics(self) -> EvidenceAcceptanceMapItemCurrent:
        if self.met and not self.proof_kind:
            raise ValueError("met acceptance items require proof_kind")
        if self.met and not (self.evidence_refs or self.command_refs or (self.assertion_summary or "").strip()):
            raise ValueError("met acceptance items require evidence_refs, command_refs, or assertion_summary")
        return self


class EvidencePackCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evidence_pack.current"]
    status: Literal["pass", "fail"]
    summary: str = Field(..., min_length=1)
    diff_artifact: str = Field(..., min_length=1)
    commands_run: list[dict] = Field(default_factory=list)
    test_results: dict = Field(default_factory=dict)
    dev_run_proof: dict = Field(default_factory=dict)
    risk_notes: list[str] = Field(default_factory=list)
    acceptance_mapping: list[EvidenceAcceptanceMapItemCurrent] = Field(..., min_length=1)
    artifacts: list[EvidenceArtifactRefCurrent] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


def get_evidence_pack_schema_current() -> dict:
    return EvidencePackCurrent.model_json_schema()


def validate_evidence_pack(payload: dict) -> EvidencePackCurrent:
    return EvidencePackCurrent.model_validate(payload)
