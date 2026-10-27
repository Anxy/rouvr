from __future__ import annotations

import shlex
from typing import Any

from backend.models.compact_carrier_models import (
    CompactCarrierActionV21,
    CompactCarrierPayloadV21,
    validate_compact_carrier_payload,
)


class CompactCarrierValidationError(ValueError):
    """Raised when compact carrier payload cannot be validated."""


class CompactCarrierTranslationError(ValueError):
    """Raised when compact carrier actions cannot be translated."""


def _repair_action_item(item: Any) -> list[Any]:
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        op = str(item[0] or "").strip()
        target = str(item[1] or "").strip()
        args = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
        return [op, target, dict(args)]
    if isinstance(item, dict):
        op = str(item.get("op") or item.get("verb") or "").strip()
        target = str(item.get("target") or item.get("path") or item.get("resource") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        return [op, target, dict(args)]
    raise CompactCarrierValidationError(f"compact action item invalid: {item!r}")


def repair_compact_carrier_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """One-shot repair for legacy/variant compact payload shapes."""
    repaired = dict(payload or {})
    if "v" not in repaired:
        repaired["v"] = 1
    if "id" not in repaired:
        repaired["id"] = "compact-auto"
    if "a" not in repaired:
        if isinstance(repaired.get("actions"), list):
            repaired["a"] = repaired.get("actions")
        else:
            repaired["a"] = []
    repaired.pop("actions", None)
    actions = repaired.get("a")
    if not isinstance(actions, list):
        raise CompactCarrierValidationError("compact payload field 'a' must be a list")
    repaired_actions: list[dict[str, Any]] = []
    for raw in actions:
        op, target, args = _repair_action_item(raw)
        repaired_actions.append({"op": op, "target": target, "args": args})
    repaired["a"] = repaired_actions
    for key in ("c", "p", "o", "r"):
        if not isinstance(repaired.get(key), dict):
            repaired[key] = {}
    if repaired.get("e") is not None and not isinstance(repaired.get("e"), str):
        repaired["e"] = str(repaired.get("e"))
    return repaired


def validate_or_repair_compact_carrier(payload: dict[str, Any]) -> tuple[CompactCarrierPayloadV21, bool]:
    try:
        return validate_compact_carrier_payload(payload), False
    except Exception:
        repaired = repair_compact_carrier_payload(payload)
        try:
            return validate_compact_carrier_payload(repaired), True
        except Exception as exc:
            raise CompactCarrierValidationError(f"compact carrier validation failed after repair: {exc}") from exc


def _as_relative_path(target: str, args: dict[str, Any]) -> str:
    value = str(args.get("relative_path") or target or ".").strip()
    return value or "."


def _translate_action(action: CompactCarrierActionV21) -> dict[str, Any]:
    op = action.op.lower().strip()
    target = action.target.strip()
    args = dict(action.args or {})

    if op in {"list", "list_dir"}:
        return {"tool": "list_dir", "args": {"relative_path": _as_relative_path(target, args)}}
    if op in {"read", "fetch", "read_file"}:
        relative_path = str(args.get("relative_path") or target).strip()
        if not relative_path:
            raise CompactCarrierTranslationError("read/fetch requires target or args.relative_path")
        return {"tool": "read_file", "args": {"relative_path": relative_path}}
    if op in {"search"}:
        pattern = str(args.get("pattern") or target).strip()
        if not pattern:
            raise CompactCarrierTranslationError("search requires pattern in target or args.pattern")
        mapped = {"pattern": pattern}
        if str(args.get("relative_path") or "").strip():
            mapped["relative_path"] = str(args["relative_path"])
        if isinstance(args.get("max_results"), int):
            mapped["max_results"] = int(args["max_results"])
        return {"tool": "search", "args": mapped}
    if op in {"write", "write_file"}:
        relative_path = str(args.get("relative_path") or target).strip()
        content = str(args.get("content") or "")
        if not relative_path:
            raise CompactCarrierTranslationError("write requires target or args.relative_path")
        return {"tool": "write_file", "args": {"relative_path": relative_path, "content": content}}
    if op in {"patch", "apply_patch"}:
        patch_text = str(args.get("patch_text") or args.get("patch") or "")
        if not patch_text:
            raise CompactCarrierTranslationError("patch requires args.patch_text or args.patch")
        mapped = {"patch_text": patch_text}
        if isinstance(args.get("max_attempts"), int):
            mapped["max_attempts"] = int(args["max_attempts"])
        return {"tool": "apply_patch", "args": mapped}
    if op in {"cmd", "run", "cmd.run"}:
        command = args.get("command")
        if isinstance(command, list):
            command_list = [str(item) for item in command if str(item).strip()]
        else:
            source = str(command or target).strip()
            if not source:
                raise CompactCarrierTranslationError("cmd requires args.command or target")
            command_list = shlex.split(source)
        mapped = {"command": command_list}
        if isinstance(args.get("timeout_seconds"), (int, float)):
            mapped["timeout_seconds"] = float(args["timeout_seconds"])
        return {"tool": "cmd.run", "args": mapped}
    if op in {"status", "get_status"}:
        return {"tool": "get_status", "args": {}}
    if op in {"evidence", "evidence.attach", "attach_evidence"}:
        evidence_ref = str(args.get("evidence_ref") or target).strip()
        if not evidence_ref:
            raise CompactCarrierTranslationError("evidence.attach requires args.evidence_ref or target")
        mapped = {
            "evidence_ref": evidence_ref,
            "evidence_type": str(args.get("evidence_type") or "artifact"),
            "metadata": dict(args.get("metadata") or {}),
        }
        return {"tool": "evidence.attach", "args": mapped}
    raise CompactCarrierTranslationError(f"unsupported compact action op='{action.op}'")


def translate_compact_carrier_to_tool_steps(payload: CompactCarrierPayloadV21) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for action in payload.a:
        steps.append(_translate_action(action))
    return steps


def resolve_compact_or_steps(
    *,
    steps: list[dict[str, Any]],
    compact_carrier: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if steps:
        return list(steps), {"input_mode": "steps", "compact_repaired": False}
    if compact_carrier is None:
        return [], {"input_mode": "steps", "compact_repaired": False}
    payload, repaired = validate_or_repair_compact_carrier(compact_carrier)
    translated = translate_compact_carrier_to_tool_steps(payload)
    return translated, {
        "input_mode": "compact_carrier",
        "compact_repaired": repaired,
        "compact_action_count": len(payload.a),
        "compact_task_type": payload.t,
        "compact_id": payload.id,
    }


__all__ = [
    "CompactCarrierTranslationError",
    "CompactCarrierValidationError",
    "repair_compact_carrier_payload",
    "resolve_compact_or_steps",
    "translate_compact_carrier_to_tool_steps",
    "validate_or_repair_compact_carrier",
]
