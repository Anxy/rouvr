"""Evidence-derived mutation summary for cycle/status responses.

Built only from already-recorded execution evidence (`real_execute`, completion
gates, tool events, verifier verdicts, loop tracker counters). Never invents
PASS/files_changed from proposal artifacts or hardcoded defaults.
"""

from __future__ import annotations

from typing import Any


_PROPOSAL_EXECUTOR_MARKERS = (
    ".demo.",
    ".docs.",
    "proposal",
)


def _path_from_changed_entry(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("path") or entry.get("relative_path") or "").strip().replace("\\", "/")
    return str(entry or "").strip().replace("\\", "/")


def _is_proposal_executor(executor: str) -> bool:
    lowered = str(executor or "").casefold()
    return any(marker in lowered for marker in _PROPOSAL_EXECUTOR_MARKERS)


def _files_from_patch_events(events: list[dict[str, Any]] | None) -> list[str]:
    """Paths from applied patches even when `changed_source_files` was never committed.

    Repair attempts clear `cycle.real_execute` after COMPLETION_GATES_FAILED; the failed
    attempt still applied real edits (recorded as patch.application.completed). Those
    must remain visible in the cycle mutation_summary.
    """
    out: list[str] = []
    seen: set[str] = set()
    for event in list(events or []):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload_refs") if isinstance(event.get("payload_refs"), dict) else {}
        candidates: list[str] = []
        if event_type == "patch.application.completed":
            candidates.append(str(payload.get("path") or ""))
        elif event_type == "mutation.round.completed":
            raw = str(payload.get("edits") or "")
            candidates.extend(part.strip() for part in raw.split(",") if part.strip())
        for raw_path in candidates:
            path = raw_path.strip().replace("\\", "/")
            if not path or path in seen:
                continue
            if path.startswith("proposed/") or "/proposed/" in path:
                continue
            seen.add(path)
            out.append(path)
    return out


def _files_changed_from_evidence(
    real_execute: dict[str, Any],
    *,
    events: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Repo-relative paths that were actually mutated. Empty for demo/proposal lanes."""
    executor = str(real_execute.get("executor") or "")
    if _is_proposal_executor(executor):
        return []
    changed_raw = real_execute.get("changed_source_files") or []
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(changed_raw, list):
        for entry in changed_raw:
            path = _path_from_changed_entry(entry)
            if not path or path in seen:
                continue
            # Proposed HTML/CSS under cycle workspace are not repository mutations.
            if path.startswith("proposed/") or "/proposed/" in path:
                continue
            seen.add(path)
            out.append(path)
    for path in _files_from_patch_events(list(events or []) + list(real_execute.get("events") or [])):
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _validation_commands_from_evidence(
    real_execute: dict[str, Any],
    *,
    events: list[dict[str, Any]] | None = None,
) -> list[str]:
    stored = real_execute.get("validation_commands")
    if isinstance(stored, list) and stored:
        return [str(c).strip() for c in stored if str(c).strip()]

    commands: list[str] = []
    seen: set[str] = set()
    for event in list(events or []) + list(real_execute.get("events") or []):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload_refs") if isinstance(event.get("payload_refs"), dict) else {}
        if event_type not in {"tool.command_run", "tool.test_run", "mutation.test.ran"}:
            continue
        cmd = str(payload.get("command") or payload.get("test_command") or "").strip()
        if cmd and cmd not in seen:
            seen.add(cmd)
            commands.append(cmd)

    gates = real_execute.get("completion_gates")
    if isinstance(gates, dict):
        for key in ("validation_commands", "test_commands", "commands"):
            raw = gates.get(key)
            if isinstance(raw, list):
                for item in raw:
                    cmd = str(item).strip()
                    if cmd and cmd not in seen:
                        seen.add(cmd)
                        commands.append(cmd)
    return commands


def _verifier_verdict_from_evidence(
    real_execute: dict[str, Any],
    *,
    verifier_verdict: str | None = None,
) -> str:
    if str(verifier_verdict or "").strip():
        return str(verifier_verdict).strip().upper()
    stored = str(real_execute.get("verifier_verdict") or "").strip()
    if stored:
        return stored.upper()
    status = str(real_execute.get("verification_status") or "").strip().upper()
    if status in {"PASSED", "PASS"}:
        return "PASS"
    if status in {"FAILED", "FAIL"}:
        return "FAIL"
    if status in {"NOT_RUN", ""}:
        return "NOT_RUN"
    return status or "NOT_RUN"


def build_mutation_summary(
    *,
    real_execute: dict[str, Any] | None,
    events: list[dict[str, Any]] | None = None,
    verifier_verdict: str | None = None,
    repair_count: int | None = None,
    replan_count: int | None = None,
) -> dict[str, Any] | None:
    """Derive a concise mutation summary from recorded evidence, or None if absent."""
    if not isinstance(real_execute, dict) or not real_execute:
        return None

    files_changed = _files_changed_from_evidence(real_execute, events=events)
    executor = str(
        real_execute.get("executor")
        or real_execute.get("strategy_id")
        or "objective_cycle.real_execute.v1"
    ).strip()

    if repair_count is None:
        repair_count = int(real_execute.get("repair_count") or 0)
    if replan_count is None:
        replan_count = int(real_execute.get("replan_count") or 0)

    return {
        "files_changed": files_changed,
        "files_changed_count": len(files_changed),
        "executor": executor,
        "validation_commands": _validation_commands_from_evidence(real_execute, events=events),
        "verifier_verdict": _verifier_verdict_from_evidence(
            real_execute, verifier_verdict=verifier_verdict
        ),
        "repair_count": max(0, int(repair_count)),
        "replan_count": max(0, int(replan_count)),
    }


__all__ = ["build_mutation_summary"]
