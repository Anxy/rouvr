from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

COMPACT_CARRIER_SCHEMA_VERSION_V21 = "compact_carrier.v2.1"


class CompactCarrierActionV21(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: str = Field(..., min_length=1, max_length=64)
    target: str = Field(default="", max_length=2048)
    args: dict[str, Any] = Field(default_factory=dict)


class CompactCarrierPayloadV21(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: str = Field(..., min_length=1, max_length=128)
    v: int = Field(default=1, ge=1, le=1)
    id: str = Field(..., min_length=1, max_length=128)
    c: dict[str, Any] = Field(default_factory=dict)
    p: dict[str, Any] = Field(default_factory=dict)
    a: list[CompactCarrierActionV21] = Field(default_factory=list)
    o: dict[str, Any] = Field(default_factory=dict)
    r: dict[str, Any] = Field(default_factory=dict)
    e: str | None = Field(default=None, max_length=512)


def validate_compact_carrier_payload(payload: dict[str, Any]) -> CompactCarrierPayloadV21:
    return CompactCarrierPayloadV21.model_validate(payload)


__all__ = [
    "COMPACT_CARRIER_SCHEMA_VERSION_V21",
    "CompactCarrierActionV21",
    "CompactCarrierPayloadV21",
    "validate_compact_carrier_payload",
]
