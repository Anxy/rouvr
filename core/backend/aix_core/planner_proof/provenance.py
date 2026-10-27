"""Prompt/model/version/cost capture for the step-contract planner."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

PLANNER_PROMPT_ID = "aix.step_contract_planner"
PLANNER_PROMPT_VERSION = "v1"
PLANNER_PARSER_VERSION = "aix.step_contract_parser.v1"


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlannerCallProvenance:
    prompt_id: str
    prompt_version: str
    prompt_body_hash: str
    model_ref: str | None
    provider: str | None = None
    resolved_provider: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    captured_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_prompt_provenance(
    *,
    prompt_body: str,
    model_ref: str | None,
    provider: str | None = None,
    resolved_provider: str | None = None,
    usage: dict[str, Any] | None = None,
) -> PlannerCallProvenance:
    return PlannerCallProvenance(
        prompt_id=PLANNER_PROMPT_ID,
        prompt_version=PLANNER_PROMPT_VERSION,
        prompt_body_hash=body_hash(prompt_body),
        model_ref=model_ref,
        provider=provider,
        resolved_provider=resolved_provider,
        usage=dict(usage or {}),
    )


def evidence_bundle(
    *,
    provenance: PlannerCallProvenance | None,
    plan_step_count: int,
    reason: str | None,
    quality_ok: bool | None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cost/evidence ledger row for C040 DoD (no secret material)."""
    return {
        "schema": "aix.planner_proof.evidence.v1",
        "provenance": provenance.to_dict() if provenance else None,
        "plan_step_count": plan_step_count,
        "reason": reason,
        "quality_ok": quality_ok,
        "fallback": fallback,
        "captured_at": _utc(),
    }
