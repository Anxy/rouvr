"""Persist one AIX chat session folder under the cycle workspace.

Every discuss turn creates/updates:

    <workspace_base>/<tenant>/<YYYY-MM-DD>/<thread_id>/
      discuss.md
      plan.md
      objective.json
      token_usage.json
      transcript.json
      intake_raw.json

Legacy threads already under ``discuss/<tenant>/<thread_id>/`` keep writing there
so an in-flight chat is not split across two folders.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from backend.aix_core.runtime.executor.real_execute import default_workspace_base
from backend.aix_core.runtime.executor.workspace_isolation import workspace_date_key
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(value: str, *, fallback: str) -> str:
    cleaned = _SAFE.sub("-", str(value or "").strip())
    return cleaned[:80] or fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def discuss_root(*, tenant_id: str, thread_id: str, workspace_base: Path | None = None) -> Path:
    """Return the single session folder for this chat thread.

    Prefers an already-created folder (legacy ``discuss/`` tree or a dated
    tenant folder) so later turns and the accepted plan land in the same place.
    """
    base = Path(workspace_base or default_workspace_base())
    tenant = _safe_segment(tenant_id, fallback="tenant")
    thread = _safe_segment(thread_id, fallback="thread")

    legacy = base / "discuss" / tenant / thread
    if legacy.is_dir():
        return legacy

    tenant_root = base / tenant
    if tenant_root.is_dir():
        existing = [
            path
            for path in tenant_root.glob(f"*/{thread}")
            if path.is_dir() and not any(path.glob("step-*"))
        ]
        if existing:
            return max(existing, key=lambda p: p.stat().st_mtime)

    return tenant_root / workspace_date_key() / thread


def new_thread_id() -> str:
    return f"disc_{new_ulid()[:16]}"


def normalize_usage(raw: dict[str, Any] | None) -> dict[str, int | None]:
    usage = raw if isinstance(raw, dict) else {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    try:
        prompt_i = int(prompt) if prompt is not None else None
    except (TypeError, ValueError):
        prompt_i = None
    try:
        completion_i = int(completion) if completion is not None else None
    except (TypeError, ValueError):
        completion_i = None
    try:
        total_i = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_i = None
    if total_i is None and prompt_i is not None and completion_i is not None:
        total_i = prompt_i + completion_i
    return {
        "prompt_tokens": prompt_i,
        "completion_tokens": completion_i,
        "total_tokens": total_i,
    }


def _add_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, int | None]:
    def _sum(key: str) -> int | None:
        a = left.get(key)
        b = right.get(key)
        if a is None and b is None:
            return None
        return int(a or 0) + int(b or 0)

    return {
        "prompt_tokens": _sum("prompt_tokens"),
        "completion_tokens": _sum("completion_tokens"),
        "total_tokens": _sum("total_tokens"),
    }


def _render_discuss_markdown(transcript: dict[str, Any]) -> str:
    """Human-readable chat log for the session folder."""
    thread_id = str(transcript.get("thread_id") or "")
    tenant_id = str(transcript.get("tenant_id") or "")
    turns = list(transcript.get("turns") or [])
    lines: list[str] = [f"# Discuss thread {thread_id}", ""]
    lines.append(f"- tenant: `{tenant_id}`")
    lines.append(f"- updated_at: {transcript.get('updated_at')}")
    lines.append(f"- turns: {len(turns)}")
    lines.append("")
    lines.append("## Transcript")
    lines.append("")
    for i, turn in enumerate(turns, start=1):
        recorded_at = turn.get("recorded_at")
        model = turn.get("model") or "?"
        lines.append(f"### Turn {i} — {recorded_at} ({model})")
        lines.append("")
        all_messages = turn.get("messages") or []
        newest = all_messages[-1] if all_messages else None
        if newest is not None:
            role = str(newest.get("role") or "?")
            content = str(newest.get("content") or "").strip()
            lines.append(f"**{role}:**")
            lines.append("")
            lines.append(content)
            lines.append("")
        reply = str(turn.get("assistant_reply") or "").strip()
        if reply:
            lines.append("**assistant:**")
            lines.append("")
            lines.append(reply)
            lines.append("")
        lines.append(
            f"_task_class={turn.get('task_class')} ambiguous={turn.get('ambiguous')} "
            f"usage={turn.get('usage')}_"
        )
        lines.append("")
    return "\n".join(lines)


def _render_plan_markdown(transcript: dict[str, Any]) -> str:
    """Proposal plan.md for the chat session (replaced later by the accepted plan)."""
    thread_id = str(transcript.get("thread_id") or "")
    tenant_id = str(transcript.get("tenant_id") or "")
    turns = list(transcript.get("turns") or [])
    latest = turns[-1] if turns else {}
    scope = latest.get("proposed_scope") or []
    lines: list[str] = ["# AIX Discuss Plan", ""]
    lines.append("## Objective")
    lines.append("")
    lines.append(str(latest.get("normalized_objective") or "").strip() or "_(none yet)_")
    lines.append("")
    lines.append("## Proposal")
    lines.append("")
    lines.append(f"- task_class: `{latest.get('task_class')}`")
    lines.append(f"- ambiguous: `{latest.get('ambiguous')}`")
    lines.append(f"- proposed_scope: {', '.join(scope) if scope else '_(none)_'}")
    lines.append(f"- intake_scope_code: `{latest.get('intake_scope_code') or ''}`")
    lines.append("")
    lines.append("## Thread")
    lines.append("")
    lines.append(f"- thread_id: `{thread_id}`")
    lines.append(f"- tenant: `{tenant_id}`")
    lines.append(f"- updated_at: {transcript.get('updated_at')}")
    lines.append(f"- turns: {len(turns)}")
    lines.append("")
    return "\n".join(lines)


def persist_session_plan(
    *,
    tenant_id: str,
    thread_id: str | None,
    plan_md: str,
    objective: dict[str, Any] | None = None,
    workspace_base: Path | None = None,
) -> Path | None:
    """Write accepted plan.md (+ optional objective.json) into the chat session folder."""
    resolved = str(thread_id or "").strip()
    if not resolved:
        return None
    root = discuss_root(tenant_id=tenant_id, thread_id=resolved, workspace_base=workspace_base)
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.md").write_text(plan_md, encoding="utf-8")
    if isinstance(objective, dict):
        existing: dict[str, Any] = {}
        objective_path = root / "objective.json"
        if objective_path.is_file():
            try:
                prior = json.loads(objective_path.read_text(encoding="utf-8"))
                if isinstance(prior, dict):
                    existing = prior
            except (OSError, json.JSONDecodeError):
                existing = {}
        merged = {**existing, **objective, "updated_at": _utc()}
        _write_json(objective_path, merged)
    return root


def persist_discuss_turn(
    *,
    tenant_id: str,
    actor_id: str,
    thread_id: str | None,
    messages: list[dict[str, str]],
    reply: str,
    task_class: str | None,
    proposed_scope: list[str],
    ambiguous: bool,
    provider: str | None,
    model: str | None,
    usage: dict[str, Any] | None,
    workspace_base: Path | None = None,
    raw_model_output: str | None = None,
    normalized_objective: str | None = None,
    intake_scope_code: str | None = None,
    intake_scope_fallback: bool = False,
) -> dict[str, Any]:
    """Create the session folder and write discuss/plan/token/objective together."""
    resolved_thread = (thread_id or "").strip() or new_thread_id()
    root = discuss_root(tenant_id=tenant_id, thread_id=resolved_thread, workspace_base=workspace_base)
    root.mkdir(parents=True, exist_ok=True)

    transcript_path = root / "transcript.json"
    usage_path = root / "token_usage.json"
    intake_raw_path = root / "intake_raw.json"
    discuss_md_path = root / "discuss.md"
    plan_md_path = root / "plan.md"
    objective_path = root / "objective.json"

    turn_usage = normalize_usage(usage)
    recorded_at = _utc()
    raw_text = str(raw_model_output or "")
    raw_parsed: Any = None
    if raw_text.strip():
        try:
            raw_parsed = json.loads(raw_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_parsed = None
    turn = {
        "recorded_at": recorded_at,
        "actor_id": actor_id,
        "messages": messages,
        "assistant_reply": reply,
        "task_class": task_class,
        "proposed_scope": proposed_scope,
        "ambiguous": ambiguous,
        "provider": provider,
        "model": model,
        "usage": turn_usage,
        "normalized_objective": normalized_objective,
        "intake_scope_code": intake_scope_code,
        "intake_scope_fallback": bool(intake_scope_fallback),
        "raw_model_output": raw_text or None,
    }

    if transcript_path.is_file():
        try:
            existing = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    else:
        existing = {}

    turns = list(existing.get("turns") or []) if isinstance(existing, dict) else []
    turns.append(turn)
    transcript = {
        "thread_id": resolved_thread,
        "tenant_id": tenant_id,
        "updated_at": recorded_at,
        "turns": turns,
    }
    _write_json(transcript_path, transcript)

    try:
        discuss_md_path.write_text(_render_discuss_markdown(transcript), encoding="utf-8")
    except OSError:
        pass
    try:
        plan_md_path.write_text(_render_plan_markdown(transcript), encoding="utf-8")
    except OSError:
        pass

    _write_json(
        objective_path,
        {
            "thread_id": resolved_thread,
            "tenant_id": tenant_id,
            "normalized_objective": normalized_objective,
            "task_class": task_class,
            "proposed_scope": list(proposed_scope or []),
            "ambiguous": bool(ambiguous),
            "intake_scope_code": intake_scope_code,
            "intake_scope_fallback": bool(intake_scope_fallback),
            "updated_at": recorded_at,
        },
    )

    intake_doc = {
        "thread_id": resolved_thread,
        "tenant_id": tenant_id,
        "updated_at": recorded_at,
        "turns": [
            {
                "recorded_at": recorded_at,
                "raw_model_output": raw_text or None,
                "raw_model_json": raw_parsed,
                "normalized_objective": normalized_objective,
                "intake_scope_code": intake_scope_code,
                "intake_scope_fallback": bool(intake_scope_fallback),
                "model": model,
                "provider": provider,
            }
        ],
    }
    if intake_raw_path.is_file():
        try:
            prior = json.loads(intake_raw_path.read_text(encoding="utf-8"))
            prior_turns = list(prior.get("turns") or []) if isinstance(prior, dict) else []
        except (OSError, json.JSONDecodeError):
            prior_turns = []
        intake_doc["turns"] = prior_turns + intake_doc["turns"]
    _write_json(intake_raw_path, intake_doc)

    if usage_path.is_file():
        try:
            usage_doc = json.loads(usage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            usage_doc = {}
    else:
        usage_doc = {}
    if not isinstance(usage_doc, dict):
        usage_doc = {}

    cumulative = normalize_usage(usage_doc.get("cumulative") if isinstance(usage_doc.get("cumulative"), dict) else {})
    cumulative = _add_usage(cumulative, turn_usage)
    turn_rows = list(usage_doc.get("turns") or []) if isinstance(usage_doc.get("turns"), list) else []
    turn_rows.append(
        {
            "recorded_at": recorded_at,
            "provider": provider,
            "model": model,
            "usage": turn_usage,
        }
    )
    usage_doc = {
        "thread_id": resolved_thread,
        "tenant_id": tenant_id,
        "updated_at": recorded_at,
        "cumulative": cumulative,
        "turns": turn_rows,
    }
    _write_json(usage_path, usage_doc)

    return {
        "thread_id": resolved_thread,
        "session_path": str(root),
        "transcript_path": str(transcript_path),
        "token_usage_path": str(usage_path),
        "intake_raw_path": str(intake_raw_path),
        "discuss_path": str(discuss_md_path),
        "plan_path": str(plan_md_path),
        "objective_path": str(objective_path),
        "discuss_plan_path": str(discuss_md_path),
        "usage": turn_usage,
        "cumulative_usage": cumulative,
    }


__all__ = [
    "discuss_root",
    "new_thread_id",
    "normalize_usage",
    "persist_discuss_turn",
    "persist_session_plan",
]
