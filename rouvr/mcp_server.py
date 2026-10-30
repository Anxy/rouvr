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


_PROGRESS = {"credential_request": "awaiting_git_credential",
             "temporal_handoff": "claimed", "execution_spec": "building",
             "muse_session": "building", "pr_record": "testing", "test_run": "verifying",
             "submit_attempt": "verifying", "reviewer_report": "verifying"}


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
    """Post an agent-to-agent message on a task (roles: supervisor|coder|reviewer).
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


def _peer_sessions(task: dict) -> list[dict]:
    out = []
    for e in task.get("evidence") or []:
        if e.get("kind") != "peer_session":
            continue
        body = e.get("body") or {}
        if isinstance(body, str):
            import json as _json
            try:
                body = _json.loads(body)
            except ValueError:
                continue
        payload = body.get("payload") if isinstance(body, dict) else None
        if isinstance(payload, dict) and payload.get("session_id"):
            out.append(payload)
    return out


def _live_peers(task_id: int, task: dict) -> list[dict]:
    """Evidence peers + live discovery (a chat in flight has no evidence row
    yet, so its session is found in the muse session store by workspace)."""
    from pathlib import Path

    from rouvr.muse import discover_session_id
    peers = _peer_sessions(task)
    root = Path(os.environ.get("WORKSPACE_ROOT", "/tmp/rouvr-supervisor/workspaces"))
    known = {p["session_id"] for p in peers}
    for role, suffix in (("coder", f"rouvr__{task_id}__a1"), ("reviewer", f"rouvr__{task_id}__a1__verify")):
        ws = root / suffix
        if ws.exists():
            sid = discover_session_id(ws)
            if sid and sid not in known:
                peers.append({"role": role, "session_id": sid, "workspace": str(ws), "live": True})
    return peers


@mcp.tool()
def workhub_peer_sessions(task_id: int) -> list:
    """Native-messaging addresses of the task's persona chat sessions
    (session UUIDs for `muse session-message send --target`)."""
    with _ledger() as c:
        task = c.get(f"/v1/tasks/{task_id}").raise_for_status().json()
    return _live_peers(task_id, task)


@mcp.tool()
def workhub_message_agent(task_id: int, to_role: str, text: str) -> dict:
    """Send a live message to a persona's muse chat via Meta's native
    `muse session-message`. Returns the real delivery status; on hosts
    where external agent ingress is closed this reports unavailable —
    use workhub_post_message there (durable, next-turn delivery)."""
    import subprocess
    with _ledger() as c:
        task = c.get(f"/v1/tasks/{task_id}").raise_for_status().json()
    peers = [p for p in _live_peers(task_id, task) if p.get("role") == to_role]
    if not peers:
        return {"status": "no_live_session", "to_role": to_role}
    target = peers[-1]["session_id"]
    r = subprocess.run(["muse", "session-message", "send", "--target", target, "--json"],
                       input=text, capture_output=True, text=True, timeout=30)
    import json as _json
    try:
        return {"target": target, **_json.loads(r.stdout or "{}")}
    except ValueError:
        return {"target": target, "status": "error", "detail": (r.stdout + r.stderr)[:200]}


def _parse_windows(ps_text: str) -> list[dict]:
    """Live persona windows from `ps` output: any process whose argv carries
    a rouvr__<task>__ workspace (the gnome-terminal client, its bash, or the
    muse chat itself — killing the muse pid closes its window)."""
    import re
    out, seen = [], set()
    for line in ps_text.splitlines():
        if "gnome-terminal-server" in line:
            continue  # shared server hosts every window; never a reap target
        m = re.search(r"(?P<ws>/\S*?/rouvr__(?P<tid>\d+)__\S*?)(?:\s|&&|')", line)
        pid = re.match(r"\s*\S+\s+(?P<pid>\d+)", line)
        if not m or not pid:
            continue
        # Anchor on the real window executables (ps aux COMMAND is field 11):
        # a shell/grep merely mentioning the workspace path is not a window.
        parts = line.split(None, 10)
        cmd = parts[10] if len(parts) > 10 else ""
        if not (re.search(r"(?<![\w\"=])(?:\S*/)?(?:muse|muse-bin\S*|gnome-terminal)(?:\s|$)", cmd)
                or re.match(r"(?:\S*/)?bash -c cd ", cmd)):
            continue
        if int(pid.group("pid")) not in seen:
            seen.add(int(pid.group("pid")))
            out.append({"pid": int(pid.group("pid")), "workspace": m.group("ws"),
                        "task_id": int(m.group("tid"))})
    return out


def _stale_windows(windows: list[dict], states: dict[int, str]) -> list[dict]:
    """Windows not in use: task done/blocked, or task no longer in WorkHub
    (orphaned window). Only called with states from a successful ledger
    fetch, so 'unknown' never means 'ledger unreachable'."""
    return [w for w in windows
            if states.get(w["task_id"], "unknown") in ("done", "blocked", "unknown")]


def _live_windows() -> tuple[list[dict], dict[int, str]]:
    import subprocess
    ps = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=15).stdout
    wins = _parse_windows(ps)
    states: dict[int, str] = {}
    with _ledger() as c:
        for t in c.get("/v1/tasks").raise_for_status().json().get("items", []):
            states[int(t["id"])] = t["state"]
    for w in wins:
        w["state"] = states.get(w["task_id"], "unknown")
    return wins, states


@mcp.tool()
def workhub_agent_windows() -> list:
    """List live persona chat windows with their task states (supervisor view)."""
    wins, _ = _live_windows()
    return wins


def _reap_stale() -> dict:
    """Close every window not in use. Windows of in-flight tasks are
    never touched. Used by the MCP tool, the CLI, and the auto-reaper."""
    import os
    import signal
    wins, states = _live_windows()
    killed = []
    for w in _stale_windows(wins, states):
        try:
            os.kill(w["pid"], signal.SIGTERM)
            killed.append(w)
        except ProcessLookupError:
            pass
    return {"killed": killed, "kept": [w for w in wins if w not in killed]}


def _auto_reap_loop() -> None:
    """Background reaper (HTTP server only): sweep stale windows every
    ROUVR_WINDOW_REAP_S seconds (default 30; 0 disables)."""
    import time
    interval = float(os.environ.get("ROUVR_WINDOW_REAP_S", "30"))
    while interval > 0:
        time.sleep(interval)
        try:
            out = _reap_stale()
            if out["killed"]:
                print(f"[rouvr] auto-reaped windows: {out['killed']}", flush=True)
        except Exception:
            pass  # ledger/Temporal hiccup: skip this sweep, never kill blind


@mcp.tool()
def workhub_reap_windows() -> dict:
    """Supervisor lifecycle duty: close persona windows whose task is
    done/blocked or no longer exists. Windows of in-flight tasks are
    never touched. Also runs automatically in the background."""
    return _reap_stale()


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
        import threading
        threading.Thread(target=_auto_reap_loop, daemon=True).start()
        mcp.run(transport="streamable-http", host="127.0.0.1",
                port=int(os.environ.get("ROUVR_MCP_PORT", "8231")))
