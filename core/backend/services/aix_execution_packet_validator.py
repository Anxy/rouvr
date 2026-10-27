from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from backend.models.aix_execution_packet_models import AixExecutionPacketCurrent


@dataclass(slots=True)
class AixExecutionPacketValidationError(ValueError):
    code: str
    message: str
    locus: str
    schema_name: str | None
    retryable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "about:blank",
            "title": self.message,
            "message": self.message,
            "status": 422,
            "code": self.code,
            "locus": self.locus,
            "schema_name": self.schema_name,
            "retryable": self.retryable,
        }


def validate_aix_execution_packet(payload: dict[str, Any], *, locus: str) -> str:
    if not isinstance(payload, dict):
        raise AixExecutionPacketValidationError(
            code="execution_packet_required",
            message="AIX execution packet required",
            locus=locus,
            schema_name=None,
            retryable=False,
        )
    schema_name = payload.get("schema_name")
    if not isinstance(schema_name, str) or not schema_name.strip():
        raise AixExecutionPacketValidationError(
            code="execution_packet_required",
            message="AIX execution packet required",
            locus=locus,
            schema_name=None,
            retryable=False,
        )
    if schema_name != "aix_execution_packet_current":
        raise AixExecutionPacketValidationError(
            code="execution_packet_schema_unknown",
            message="Unknown execution packet schema",
            locus=locus,
            schema_name=schema_name,
            retryable=False,
        )
    try:
        AixExecutionPacketCurrent.model_validate(payload)
    except ValidationError as exc:
        raise AixExecutionPacketValidationError(
            code="execution_packet_invalid",
            message="Invalid execution packet",
            locus=locus,
            schema_name=schema_name,
            retryable=False,
        ) from exc
    return schema_name
