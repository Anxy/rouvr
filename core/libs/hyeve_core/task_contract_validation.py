"""Structural validation for canonical task contracts.

This module is intentionally limited to type/shape validation and intrinsic
invariants. It must not own orchestration readiness policy.
"""

from __future__ import annotations

from typing import Any


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


def _symbol_targets(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        relative_path = _text(item.get("relative_path") or item.get("path"))
        symbol = _text(item.get("symbol"))
        label = _text(item.get("label"))
        if not relative_path and not symbol:
            continue
        normalized.append(
            {
                "relative_path": relative_path,
                "symbol": symbol,
                "label": label,
            }
        )
    return normalized


def handoff_requires_execution_slice(handoff_status: str | None) -> bool:
    return _text(handoff_status) in {"ready_for_execution", "ready_for_verification"}


def validate_task_ticket_contract(
    *,
    title: str,
    description: str | None,
    metadata: dict[str, Any] | None,
    require_execution_slice: bool | None = None,
) -> dict[str, Any]:
    _ = (title, description)
    payload = dict(metadata or {})
    existing = payload.get("canonical_task_contract.current")
    contract = dict(existing) if isinstance(existing, dict) else dict(payload)
    handoff_status = _text(contract.get("handoff_status")) or "ready_for_planning"
    execution_slice_required = (
        handoff_requires_execution_slice(handoff_status)
        if require_execution_slice is None
        else bool(require_execution_slice)
    )
    missing_fields: list[str] = []
    if not _text(contract.get("objective")):
        missing_fields.append("objective")
    scope = dict(contract.get("scope") or {})
    if not _text_list(scope.get("in_scope")):
        missing_fields.append("scope")
    target_surface = dict(contract.get("target_surface") or {})
    target_grounded = bool(
        _text_list(target_surface.get("target_files"))
        or _symbol_targets(target_surface.get("prompt_symbol_targets"))
        or _text_list(target_surface.get("owning_symbols"))
        or _text_list(target_surface.get("bounded_search_surface"))
    )
    if not target_grounded:
        missing_fields.append("target_surface")
    if not _text_list(contract.get("acceptance_criteria")):
        missing_fields.append("acceptance_criteria")
    if not _text_list(contract.get("validation_plan")):
        missing_fields.append("validation_plan")
    if not _text_list(contract.get("constraints")):
        missing_fields.append("constraints")
    if execution_slice_required and not isinstance(contract.get("execution_slice"), dict):
        missing_fields.append("execution_slice")
    warnings: list[dict[str, str]] = []
    warning_codes: list[str] = []
    if missing_fields:
        warning_codes.append("TICKET_CONTRACT_INVALID")
        warnings.append(
            {
                "code": "TICKET_CONTRACT_INVALID",
                "field": "canonical_task_contract.current",
                "message": (
                    "Current ticket contract is incomplete; missing fields: " + ", ".join(missing_fields) + "."
                ),
            }
        )
    if execution_slice_required and "execution_slice" in missing_fields:
        warning_codes.append("TICKET_EXECUTION_SLICE_REQUIRED")
        warnings.append(
            {
                "code": "TICKET_EXECUTION_SLICE_REQUIRED",
                "field": "canonical_task_contract.current.execution_slice",
                "message": "Execution slice is required for the current handoff status.",
            }
        )
    return {
        "valid": len(missing_fields) == 0,
        "missing_fields": missing_fields,
        "execution_slice_required": execution_slice_required,
        "canonical_task_contract.current": contract,
        "handoff_status": handoff_status,
        "failure_category": _text(contract.get("failure_category")) or None,
        "warning_codes": warning_codes,
        "warnings": warnings,
    }


__all__ = [
    "handoff_requires_execution_slice",
    "validate_task_ticket_contract",
]
