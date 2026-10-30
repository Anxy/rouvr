"""rouvr/mcp_server.py — WorkHub MCP front door for the supervisor Muse session.

The interactive supervisor (Muse chat) delivers tasks to WorkHub through
these tools; Temporal workers (coder/tester/reviewer muse sessions) pick the
work up in the background. Backend is the ledger WorkHub (option 1) over HTTP;
workflow start reuses rouvr.starter (idempotent, REJECT_DUPLICATE).

Run: WORKHUB_BASE_URL=http://127.0.0.1:8221 .venv/bin/python -m rouvr.mcp_server
Serves streamable HTTP on 127.0.0.1:8231/mcp (override with ROUVR_MCP_PORT).
"""
from __future__ import annotations

import os

import httpx
from mcp.server.mcpserver import MCPServer

LEDGER = os.environ.get("WORKHUB_BASE_URL", "http://127.0.0.1:8221").rstrip("/")
PROJECT = os.environ.get("ROUVR_PROJECT_ID", "scratch")

mcp = MCPServer("rouvr-workhub", instructions=(
    "Deliver coding tasks to WorkHub with workhub_create_task, then launch the "
    "background coder/reviewer loop with rouvr_start_task. Poll workhub_show_task "
    "for state and evidence. You are the supervisor: never write task code yourself."))


def _ledger() -> httpx.Client:
    return httpx.Client(base_url=LEDGER, timeout=15)


@mcp.tool()
def workhub_create_task(title: str, description: str, acceptance_criteria: list[str]) -> dict:
    """Create a WorkHub task plus its contract. Returns the task (task_number = id)."""
    with _ledger() as c:
        t = c.post("/v1/tasks", json={"title": title, "intent": "supervisor"}).raise_for_status().json()
        c.post(f"/v1/tasks/{t['id']}/contracts", json={
            "project_id": PROJECT, "description": description,
            "acceptance_criteria": acceptance_criteria}).raise_for_status()
        return c.get(f"/v1/tasks/{t['id']}").raise_for_status().json()


@mcp.tool()
def workhub_list_tasks() -> list:
    """List WorkHub tasks with their states."""
    with _ledger() as c:
        return c.get("/v1/tasks").raise_for_status().json()


_PROGRESS = {"temporal_handoff": "claimed", "execution_spec": "building",
             "muse_session": "building", "pr_record": "testing", "test_run": "verifying",
             "submit_attempt": "verifying", "verifier_report": "verifying"}


@mcp.tool()
def workhub_show_task(task_id: int) -> dict:
    """Show one task: state, contracts, evidence, attempts, results.
    Poll this: `state` moves open -> claimed -> needs_review -> done/blocked,
    and `progress` names the live leg from the latest worker evidence."""
    with _ledger() as c:
        task = c.get(f"/v1/tasks/{task_id}").raise_for_status().json()
    kinds = [e.get("kind") for e in task.get("evidence") or []]
    task["progress"] = (task.get("state") if task.get("state") in ("done", "blocked")
                        else next((_PROGRESS[k] for k in reversed(kinds) if k in _PROGRESS), "queued"))
    return task


@mcp.tool()
def workhub_post_message(task_id: int, from_role: str, to_role: str, text: str) -> dict:
    """Post an agent-to-agent message on a task (roles: supervisor|builder|verifier).
    Persona agents also exchange messages inside their turns via message-<to>.json
    files; everything lands as agent_message evidence the next turn reads."""
    import time
    with _ledger() as c:
        return c.post(f"/v1/tasks/{task_id}/evidence",
                      json={"kind": "agent_message",
                            "body": {"fence": f"msg-{time.time_ns()}",
                                     "payload": {"from": from_role, "to": to_role, "text": text}}}
                      ).raise_for_status().json()


@mcp.tool()
def workhub_read_messages(task_id: int, to_role: str = "") -> list:
    """Read agent-to-agent messages on a task, oldest first; filter by recipient if given."""
    with _ledger() as c:
        task = c.get(f"/v1/tasks/{task_id}").raise_for_status().json()
    out = []
    for e in task.get("evidence") or []:
        if e.get("kind") != "agent_message":
            continue
        body = e.get("body") or {}
        if isinstance(body, str):
            import json as _json
            try:
                body = _json.loads(body)
            except ValueError:
                continue
        payload = body.get("payload") if isinstance(body, dict) else None
        msg = payload if isinstance(payload, dict) else body
        if msg and (not to_role or msg.get("to") == to_role):
            out.append(msg)
    return out


@mcp.tool()
async def rouvr_start_task(task_number: int, requested_by: str = "supervisor") -> dict:
    """Start the Temporal workflow for a task (idempotent). Background coder,
    test and reviewer muse sessions deliver it; poll workhub_show_task."""
    from rouvr.starter import run_start
    wid = await run_start(task_number=task_number, requested_by=requested_by)
    return {"workflow_id": wid, "task_number": task_number}


if __name__ == "__main__":
    if os.environ.get("ROUVR_MCP_TRANSPORT") == "stdio":  # plugin-bundled mode
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host="127.0.0.1",
                port=int(os.environ.get("ROUVR_MCP_PORT", "8231")))
