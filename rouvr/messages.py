"""Agent-to-agent messages over WorkHub evidence (kind=agent_message).

Personas never talk directly; a message is an evidence row {from, to, text}
on the task. Inside a turn, an agent writes message-<to>.json in its
workspace (same file transport as submit.json); the activity attaches it
after the turn and injects unread peer messages into the next prompt.
The supervisor uses the MCP tools (workhub_post_message/read_messages)."""
from __future__ import annotations

import json
from pathlib import Path

MESSAGE_TYPE = "agent_message"


def read_message_file(ws: Path, to_role: str) -> str:
    """Optional message-<to>.json {\"text\": ...}; absent/malformed -> '' (never fails a turn)."""
    p = ws / f"message-{to_role}.json"
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text())
    except ValueError:
        return ""
    return str(data.get("text") or "") if isinstance(data, dict) else ""


def messages_for(rows: list[dict], to_role: str) -> list[str]:
    """['coder: text', ...] from evidence rows addressed to to_role, oldest first."""
    out = []
    for r in rows:
        if r.get("type") != MESSAGE_TYPE:
            continue
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if isinstance(payload, dict) and payload.get("to") == to_role and payload.get("text"):
            out.append(f"{payload.get('from', 'agent')}: {payload['text']}")
    return out
