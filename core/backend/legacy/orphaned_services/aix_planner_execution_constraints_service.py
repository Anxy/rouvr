from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_CLI_ALLOWED_TOOLS = ("search", "patch", "file_ops", "command_execution")


@dataclass(frozen=True)
class PlannerExecutionConstraintsCurrent:
    schema_version: str = "aix.planner_execution_constraints.current"
    target_files: list[str] = field(default_factory=list)
    target_symbols: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    success_definition: str = ""
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    source: str = "metadata"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_files": list(self.target_files),
            "target_symbols": list(self.target_symbols),
            "allowed_tools": list(self.allowed_tools),
            "validation_commands": list(self.validation_commands),
            "success_definition": self.success_definition,
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "source": self.source,
        }


def build_planner_execution_constraints(
    *,
    metadata: dict[str, Any] | None,
    title: str = "",
    description: str = "",
) -> PlannerExecutionConstraintsCurrent:
    payload = dict(metadata or {})
    ticket_contract = _dict(payload.get("ticket_contract_current"))
    execution_slice = _dict(payload.get("execution_slice")) or _dict(ticket_contract.get("execution_slice"))
    planner_packet = _dict(payload.get("planner_packet")) or _dict(payload.get("planner_slice_packet"))

    target_files = _dedupe_strings(
        _string_list(payload.get("target_files"))
        or _string_list(execution_slice.get("target_files"))
        or _string_list((ticket_contract.get("target_surface") or {}).get("target_files"))
        or _single_item(planner_packet.get("target_file"))
    )
    target_symbols = _dedupe_strings(
        _string_list(payload.get("target_symbols"))
        or _string_list(payload.get("prompt_symbol_targets"))
        or _single_item(planner_packet.get("target_symbol"))
    )
    validation_commands = _dedupe_strings(
        _string_list(payload.get("validation_commands"))
        or _string_list(execution_slice.get("validation_commands"))
        or _single_item(planner_packet.get("validation_command"))
    )
    allowed_tools = _dedupe_strings(_string_list(payload.get("allowed_tools")) or list(DEFAULT_CLI_ALLOWED_TOOLS))
    constraints = _dedupe_strings(
        [
            *_string_list(payload.get("constraints")),
            *_string_list(ticket_contract.get("constraints")),
            *_string_list(planner_packet.get("hard_failure_rules")),
        ]
    )
    non_goals = _dedupe_strings(
        [*_string_list(payload.get("non_goals")), *_string_list(ticket_contract.get("non_goals"))]
    )
    success_definition = _first_text(
        payload.get("success_definition"),
        ticket_contract.get("success_definition"),
        _acceptance_criteria_text(ticket_contract.get("acceptance_criteria")),
        planner_packet.get("bounded_mutation_goal"),
        execution_slice.get("summary"),
        description,
        title,
    )
    source = "planner_packet" if planner_packet else "ticket_contract" if ticket_contract else "metadata"
    return PlannerExecutionConstraintsCurrent(
        target_files=target_files,
        target_symbols=target_symbols,
        allowed_tools=allowed_tools,
        validation_commands=validation_commands,
        success_definition=success_definition,
        constraints=constraints,
        non_goals=non_goals,
        source=source,
    )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _single_item(value: Any) -> list[str]:
    cleaned = str(value or "").strip()
    return [cleaned] if cleaned else []


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def _first_text(*values: Any) -> str:
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return ""


def _acceptance_criteria_text(value: Any) -> str:
    return "; ".join(_string_list(value))
