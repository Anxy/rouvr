from __future__ import annotations

"""Legacy structured-packet compatibility bridge.

This module exists only to preserve backward-compatible enrichment from older
``observation_packet_current`` / ``execution_intent_current`` payloads.

Future deletion target:
- remove once task creation and mutation surfaces emit canonical
  ``canonical_task_contract.current`` payloads directly.
"""

from typing import Any

from pydantic import BaseModel, Field


class ObservationPacketCurrent(BaseModel):
    problem_statement: str | None = None
    evidence: list[str] = Field(default_factory=list)
    repo_context: list[str] = Field(default_factory=list)


class ExecutionIntentCurrent(BaseModel):
    objective: str | None = None
    constraints: list[str] = Field(default_factory=list)
    acceptance_contract: list[str] = Field(default_factory=list)
    target_surface_hints: list[str] = Field(default_factory=list)
    ambiguity_tolerance: str | None = None
    validation_commands: list[str] = Field(default_factory=list)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _structured_packet_warnings(
    observation_packet: dict[str, Any],
    execution_intent: dict[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not _text(observation_packet.get("problem_statement")):
        warnings.append(
            {
                "code": "OBSERVATION_PROBLEM_STATEMENT_MISSING",
                "field": "observation_packet_current.problem_statement",
                "message": "ObservationPacket is missing a concrete problem statement.",
            }
        )
    if not (_text_list(observation_packet.get("evidence")) or _text_list(observation_packet.get("repo_context"))):
        warnings.append(
            {
                "code": "OBSERVATION_CONTEXT_WEAK",
                "field": "observation_packet_current",
                "message": "ObservationPacket should include evidence or repo_context to ground the task.",
            }
        )
    if not _text(execution_intent.get("objective")):
        warnings.append(
            {
                "code": "EXECUTION_OBJECTIVE_MISSING",
                "field": "execution_intent_current.objective",
                "message": "ExecutionIntent is missing an explicit objective.",
            }
        )
    if not _text_list(execution_intent.get("acceptance_contract")):
        warnings.append(
            {
                "code": "EXECUTION_ACCEPTANCE_WEAK",
                "field": "execution_intent_current.acceptance_contract",
                "message": "ExecutionIntent should include an acceptance contract for verifier-ready work.",
            }
        )
    if not (
        _text_list(execution_intent.get("target_surface_hints"))
        or _text_list(execution_intent.get("validation_commands"))
    ):
        warnings.append(
            {
                "code": "EXECUTION_TARGETING_WEAK",
                "field": "execution_intent_current",
                "message": "ExecutionIntent should include target_surface_hints or validation_commands.",
            }
        )
    return warnings


def attach_structured_task_packet(metadata: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(metadata or {})
    raw_observation = normalized.get("observation_packet_current")
    raw_execution = normalized.get("execution_intent_current")
    if not isinstance(raw_observation, dict) and not isinstance(raw_execution, dict):
        return normalized

    observation_packet = ObservationPacketCurrent.model_validate(raw_observation or {}).model_dump()
    execution_intent = ExecutionIntentCurrent.model_validate(raw_execution or {}).model_dump()
    normalized["observation_packet_current"] = observation_packet
    normalized["execution_intent_current"] = execution_intent

    if not _text(normalized.get("objective")) and _text(execution_intent.get("objective")):
        normalized["objective"] = _text(execution_intent.get("objective"))
    if not _text_list(normalized.get("constraints")):
        constraints = _text_list(execution_intent.get("constraints"))
        if constraints:
            normalized["constraints"] = constraints
    if not _text_list(normalized.get("acceptance_criteria")):
        acceptance_contract = _text_list(execution_intent.get("acceptance_contract"))
        if acceptance_contract:
            normalized["acceptance_criteria"] = acceptance_contract
    if not _text_list(normalized.get("validation_commands")):
        validation_commands = _text_list(execution_intent.get("validation_commands"))
        if validation_commands:
            normalized["validation_commands"] = validation_commands
    if not _text_list(normalized.get("scope_in")):
        target_hints = _text_list(execution_intent.get("target_surface_hints"))
        repo_context = _text_list(observation_packet.get("repo_context"))
        merged_scope = target_hints or repo_context
        if merged_scope:
            normalized["scope_in"] = merged_scope

    warnings = _structured_packet_warnings(observation_packet, execution_intent)
    normalized["task_packet_quality_current"] = {
        "weak": bool(warnings),
        "warning_codes": [item["code"] for item in warnings],
        "warnings": warnings,
    }
    return normalized


__all__ = [
    "ExecutionIntentCurrent",
    "ObservationPacketCurrent",
    "attach_structured_task_packet",
]
