"""rouvr/mcp_server.py — WorkHub MCP front door for the supervisor Muse session.

The interactive supervisor (Muse chat) delivers tasks to WorkHub through
these tools; Temporal workers (coder/tester/reviewer muse sessions) pick the
work up in the background. Backend is the ledger WorkHub (option 1) over HTTP;
workflow start reuses rouvr.starter (idempotent, REJECT_DUPLICATE).

Run: WORKHUB_BASE_URL=http://127.0.0.1:8221 .venv/bin/python -m rouvr.mcp_server
Serves streamable HTTP on 127.0.0.1:8231/mcp (override with ROUVR_MCP_PORT).
"""
from __future__ import annotations

import json
import os

import httpx
from mcp.server.mcpserver import MCPServer

LEDGER = os.environ.get("WORKHUB_BASE_URL", "http://127.0.0.1:8221").rstrip("/")

mcp = MCPServer("rouvr-workhub", instructions=(
    "Deliver coding tasks to WorkHub with workhub_create_task, then launch the "
    "background coder/reviewer loop with rouvr_start_task. Poll workhub_show_task "
    "for state and evidence. You are the supervisor: never write task code yourself."))


def _ledger() -> httpx.Client:
    token = os.environ.get("ROUVR_LEDGER_TOKEN", "")
    headers = {"X-Rouvr-Token": token} if token else {}
    return httpx.Client(base_url=LEDGER, timeout=15, headers=headers)


def _prose_warnings(description: str, criteria: list[str]) -> list[str]:
    """S013: instructions living only in prose get skipped by agents;
    criteria are what is enforced. Warn, never block."""
    hints = ("note for", "message", "tell the", "leave a", "inform", "let the reviewer",
             "let the coder")
    desc, crit = description.lower(), " ".join(criteria).lower()
    if any(h in desc for h in hints) and not any(h in crit for h in hints):
        return ["instruction in description is not an acceptance criterion; "
                "agents may skip it — promote it to a criterion if it must happen"]
    return []


def _author_hidden_suite(criteria: list[str]) -> dict[str, str]:
    """Author hidden checks via a test_author model turn. Never placeholders.

    Override point for tests: monkeypatch rouvr.mcp_server._author_hidden_suite.
    Raises RuntimeError (fail closed, nothing written) when no model is
    available or the model output is unusable — the create is refused
    instead of degrading to assert True.
    """
    from backend.rouvr_models.providers import get_provider
    provider = get_provider()
    if provider.name == "stub":
        raise RuntimeError(
            "hidden suite needs a real model: set ROUVR_MODEL_PROVIDER "
            "(meta|ollama|openrouter) with its key, then retry the create")
    raw = provider.chat([
        {"role": "system", "content": (
            "Write pytest checks for the acceptance criteria. Reply with ONLY a "
            "JSON object mapping flat filenames (*.py, no directories) to file "
            "contents. Every test name must start with test_. No placeholders: "
            "every test must assert real behavior.")},
        {"role": "user", "content": "\n".join(f"- {c}" for c in criteria)}])
    try:
        files = json.loads(raw)
    except ValueError:
        raise RuntimeError("test_author returned unparsable output; create refused") from None
    if not isinstance(files, dict) or not files or \
            any(not isinstance(k, str) or not isinstance(v, str) for k, v in files.items()):
        raise RuntimeError("test_author returned an unusable suite; create refused")
    for name in files:
        if "/" in name or name in {".", ".."} or not name.endswith(".py"):
            raise RuntimeError(f"test_author returned a bad test name: {name!r}; create refused")
    return files


@mcp.tool()
def workhub_create_tenant(tenant: str) -> dict:
    """Register a tenant explicitly (S022). Must exist before any project."""
    with _ledger() as c:
        return c.post("/v1/tenants", json={"tenant": tenant}).raise_for_status().json()


@mcp.tool()
def workhub_create_project(tenant: str, project: str) -> dict:
    """Register a project under an existing tenant (S022). Must exist before any task."""
    with _ledger() as c:
        return c.post("/v1/projects", json={"tenant": tenant, "project": project}
                      ).raise_for_status().json()


@mcp.tool()
def workhub_create_task(title: str, description: str, acceptance_criteria: list[str],
                        tenant: str, project: str) -> dict:
    """Create a WorkHub task plus its contract. Returns the task (task_number = id).

    Tenant and project are mandatory (S022): create them first with
    workhub_create_tenant / workhub_create_project — no defaults.
    Also writes the hidden acceptance suite as the distinct test_author
    principal (never the coder): HIDDEN_TESTS_ROOT/<id>/MANIFEST.json with
    created_by=test_author. The coder workspace is never touched.
    """
    from pathlib import Path

    from rouvr.hiddentests import write_hidden_suite

    files = _author_hidden_suite(acceptance_criteria)  # fail closed before any ledger write
    try:
        candidate = _decompose_brief(title, description, acceptance_criteria)
        decomposition: dict = {"status": "enriched", "candidate": candidate}
    except Exception as e:  # enrichment is best-effort; the suite stays fail-closed
        decomposition = {"status": "skipped", "reason": f"{type(e).__name__}: {e}"[:200]}
    with _ledger() as c:
        task = c.post("/v1/tasks_with_contract", json={
            "title": title, "intent": "supervisor", "tenant": tenant, "project": project,
            "contract": {
                "project_id": project, "description": description,
                "acceptance_criteria": acceptance_criteria}}).raise_for_status().json()
        task = c.get(f"/v1/tasks/{task['id']}").raise_for_status().json()
        if (w := _prose_warnings(description, acceptance_criteria)):
            task["warnings"] = w
        c.post(f"/v1/tasks/{task['id']}/evidence",
               json={"kind": "decomposition", "fence": f"{task['id']}:decompose",
                     "body": decomposition}).raise_for_status()
    root = Path(os.environ.get("HIDDEN_TESTS_ROOT", "/srv/rouvr/hidden-tests"))
    if files:
        try:
            write_hidden_suite(root, task_id=str(task["id"]), files=files, created_by="test_author")
        except Exception as e:
            if "locked" not in str(e).lower():
                raise
    return task


def _decompose_brief(title: str, description: str, criteria: list[str]) -> dict:
    """Enrich a creation brief into a v1.3 candidate (S021).

    Override point for tests: monkeypatch rouvr.mcp_server._decompose_brief.
    Runs the real decompose_brief model call; the coder stays blind (the
    candidate lands as task evidence, never in the workspace).
    """
    from rouvr.decompose import decompose_brief
    brief = (f"Title: {title}\nDescription: {description}\nAcceptance criteria:\n"
             + "\n".join(f"- {c}" for c in criteria))
    return decompose_brief(brief)


def _escalation_reason(evidence: list | None) -> str:
    """Newest escalation reason, or "blocked". Ledger bodies may be JSON strings."""
    for e in evidence or []:
        if e.get("kind") != "escalation":
            continue
        body = e.get("body") or {}
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                continue
        if not isinstance(body, dict):
            continue
        payload = body.get("payload")
        if isinstance(payload, dict) and payload.get("reason"):
            return f"escalation:{payload['reason']}"
        if body.get("reason"):
            return f"escalation:{body['reason']}"
    return "blocked"


def _annotate(task: dict) -> dict:
    """S012: age + stuck flag (display only) from latest activity time."""
    from datetime import datetime, timezone
    stamps = [task.get("updated_at") or ""] + \
             [e.get("created_at") or "" for e in task.get("evidence") or []]
    last = max(stamps) if stamps else ""
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        age = -1
    task["last_activity_age_s"] = int(age) if age >= 0 else None
    threshold = float(os.environ.get("ROUVR_STUCK_S", "1800"))
    task["stuck"] = bool(task.get("state") in ("open", "claimed", "needs_review")
                         and age > threshold)
    return task


@mcp.tool()
def workhub_list_tasks(tenant: str = "") -> dict:
    """List WorkHub tasks with states, stuck flags, and a
    waiting_on_supervisor rollup (blocked tasks, credential requests).
    Optional tenant filter; the ledger still enforces caller visibility."""
    with _ledger() as c:
        params = {"tenant": tenant} if tenant else None
        data = c.get("/v1/tasks", params=params).raise_for_status().json()
        items = [_annotate(dict(t)) for t in data.get("items", [])]
        waiting = []
        for t in items:
            if t.get("state") != "blocked":
                continue
            reason = "blocked"
            try:
                detail = c.get(f"/v1/tasks/{t['id']}").raise_for_status().json()
                reason = _escalation_reason(detail.get("evidence"))
            except Exception:
                pass
            waiting.append({"task_id": t["id"], "reason": reason})
        for t in items:
            if t.get("state") in ("open", "claimed", "needs_review"):
                d = c.get(f"/v1/tasks/{t['id']}").raise_for_status().json()
                kinds = [e.get("kind") for e in d.get("evidence") or []]
                if "credential_request" in kinds and \
                        kinds.index("credential_request") > (kinds.index("pr_record")
                                                             if "pr_record" in kinds else -1):
                    waiting.append({"task_id": t["id"], "reason": "awaiting_git_credential"})
        data["items"] = items
        data["waiting_on_supervisor"] = waiting
        return data


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
    import json as _json
    for e in task.get("evidence") or []:  # S013: decoded payloads, no double-parse
        body = e.get("body")
        if isinstance(body, str):
            try:
                body = _json.loads(body)
            except ValueError:
                pass
        if isinstance(body, dict):
            e["body"] = body
            if isinstance(body.get("payload"), dict):
                e["payload"] = body["payload"]
    kinds = [e.get("kind") for e in task.get("evidence") or []]
    # S015: cost visibility derived from existing evidence (no new storage)
    from datetime import datetime
    try:
        _elapsed = (datetime.fromisoformat((task.get("updated_at") or "").replace("Z", "+00:00"))
                    - datetime.fromisoformat((task.get("created_at") or "").replace("Z", "+00:00"))
                    ).total_seconds()
    except ValueError:
        _elapsed = -1
    task["summary"] = {"elapsed_s": int(_elapsed) if _elapsed >= 0 else None,
                       "windows": kinds.count("peer_session"),
                       "submits": kinds.count("submit_attempt"),
                       "messages": kinds.count("agent_message")}
    task["progress"] = (task.get("state") if task.get("state") in ("done", "blocked")
                        else next((_PROGRESS[k] for k in reversed(kinds) if k in _PROGRESS), "queued"))
    return _annotate(task)


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
    for ws in sorted(root.glob(f"rouvr__{task_id}__a*")) if root.exists() else []:
        sid = discover_session_id(ws)
        if sid and sid not in known:
            known.add(sid)
            role = "reviewer" if ws.name.endswith("__verify") else "coder"
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
    try:
        r = subprocess.run(["muse", "session-message", "send", "--target", target, "--json"],
                           input=text, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"target": target, "status": "error", "detail": "timeout"}
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


@mcp.tool()
async def rouvr_schedule(task_number: int, cron: str, schedule_id: str = "",
                         requested_by: str = "supervisor", tz: str = "Asia/Kolkata") -> dict:
    """Schedule a task on a cron. Refuses without an active standing grant for
    the task's schedule scope (owner approves via POST /v1/grants)."""
    from rouvr.starter import run_schedule, schedule_scope
    scope = schedule_scope(task_number)
    with _ledger() as c:
        g = c.get("/v1/grants/active", params={"scope": scope}).raise_for_status().json()
    if not g.get("active"):
        raise RuntimeError(f"schedule refused: no active grant for scope {scope!r}")
    sid = schedule_id or f"task-{task_number}"
    await run_schedule(task_number=task_number, requested_by=requested_by,
                       schedule_id=sid, cron=cron, tz=tz)
    return {"schedule_id": sid, "task_number": task_number, "scope": scope}


def _mcp_http_auth() -> str:
    """Token guard for the HTTP transport. Returns the token, or "" when
    explicitly anonymous. Refuses to serve an open port otherwise."""
    token = os.environ.get("ROUVR_LEDGER_TOKEN", "")
    if token or os.environ.get("ROUVR_ALLOW_ANON") == "1":
        return token
    raise RuntimeError("ROUVR_LEDGER_TOKEN is unset: refusing an open MCP port "
                       "(set the token, or explicit ROUVR_ALLOW_ANON=1)")


if __name__ == "__main__":
    if os.environ.get("ROUVR_MCP_TRANSPORT") == "stdio":  # plugin-bundled mode
        mcp.run(transport="stdio")
    else:
        import threading
        threading.Thread(target=_auto_reap_loop, daemon=True).start()
        _token = _mcp_http_auth()
        if _token:  # S011: same shared token guards the MCP port
            import uvicorn
            from starlette.middleware.base import BaseHTTPMiddleware
            from starlette.responses import JSONResponse

            async def _auth(request, call_next):
                if request.headers.get("x-rouvr-token") != _token:
                    return JSONResponse({"detail": "unauthorized"}, status_code=401)
                return await call_next(request)

            _app = mcp.streamable_http_app()
            _app.add_middleware(BaseHTTPMiddleware, dispatch=_auth)
            uvicorn.run(_app, host="127.0.0.1",
                        port=int(os.environ.get("ROUVR_MCP_PORT", "8231")), log_level="warning")
        else:
            mcp.run(transport="streamable-http", host="127.0.0.1",
                    port=int(os.environ.get("ROUVR_MCP_PORT", "8231")))
