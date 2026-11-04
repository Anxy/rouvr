"""Supervisor startup context (Slice A bootstrap): self-describe live.

Generates, LIVE at startup, from live sources only (never hand-written):

- capabilities manifest: the actual ``@mcp.tool`` functions registered in
  :mod:`rouvr.mcp_server` — names plus docstring first lines;
- context bundle: tenant/project, open-tasks summary, skill catalogue
  (knowledge entries of kind ``skill``, tolerant of zero skills), and
  top relevant knowledge slugs.

Size-capped: the rendered text is truncated to ``cap`` chars (default
6000, override with ``ROUVR_CONTEXT_CAP``) and truncation is reported.
Ledger-tolerant: when the ledger is unreachable every ledger-backed
section degrades to a stated ``(unavailable: ...)`` note instead of
raising, so startup never breaks.

Usage: ``.venv/bin/python scripts/supervisor_context.py``
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_CAP = 6000
OPEN_TASK_LIMIT = 10
SKILL_LIMIT = 10
RULE_SLUG_LIMIT = 8


async def live_tool_manifest() -> list[dict]:
    """Enumerate the actual @mcp.tool functions in rouvr.mcp_server.

    Names come from the live MCP tool registry; summaries are the first
    line of each backing function's docstring. No hand-written list.
    """
    import rouvr.mcp_server as server

    tools = await server.mcp.list_tools()
    manifest = []
    for tool in tools:
        doc = (getattr(getattr(server, tool.name, None), "__doc__", None)
               or tool.description or "")
        first = next((ln.strip() for ln in doc.splitlines() if ln.strip()), "")
        manifest.append({"name": tool.name, "summary": first})
    return manifest


def _ledger_base() -> str:
    return os.environ.get("WORKHUB_BASE_URL", "http://127.0.0.1:8221").rstrip("/")


def _ledger_get(path: str, params: dict | None = None) -> dict | list | None:
    """GET one ledger endpoint.

    None when the ledger is unreachable; a ``{"_http_error": status}``
    marker when it answered with an error (e.g. 401 without a token).
    """
    import httpx

    token = os.environ.get("ROUVR_LEDGER_TOKEN", "")
    headers = {"X-Rouvr-Token": token} if token else {}
    try:
        with httpx.Client(base_url=_ledger_base(), timeout=5,
                          headers=headers) as client:
            return client.get(path, params=params).raise_for_status().json()
    except httpx.HTTPStatusError as e:
        return {"_http_error": e.response.status_code}
    except Exception:
        return None


def _ledger_problem(data: dict | list | None) -> str | None:
    """Stated degradation note when a ledger fetch did not yield data."""
    if data is None:
        return f"(unavailable: ledger at {_ledger_base()} unreachable)"
    if isinstance(data, dict) and "_http_error" in data:
        return (f"(unavailable: ledger at {_ledger_base()} refused "
                f"HTTP {data['_http_error']})")
    return None


def tenant_project() -> tuple[str, str, str]:
    """Resolve (tenant, project, source-note) from the environment."""
    tenant = (os.environ.get("ROUVR_TENANT")
              or os.environ.get("ROUVR_TENANT_ID") or "supervisor")
    project = (os.environ.get("ROUVR_PROJECT")
               or os.environ.get("ROUVR_PROJECT_ID") or "scratch")
    if os.environ.get("ROUVR_TENANT") or os.environ.get("ROUVR_PROJECT"):
        src = "ROUVR_TENANT/ROUVR_PROJECT"
    elif os.environ.get("ROUVR_TENANT_ID") or os.environ.get("ROUVR_PROJECT_ID"):
        src = "ROUVR_TENANT_ID/ROUVR_PROJECT_ID"
    else:
        src = "defaults (no ROUVR_TENANT*/ROUVR_PROJECT* set)"
    return tenant, project, src


def open_tasks_summary(tenant: str) -> str:
    """One-line-per-open-task summary, or a stated unavailable note."""
    data = _ledger_get("/v1/tasks", {"tenant": tenant})
    if (note := _ledger_problem(data)) is not None:
        return note
    items = data.get("items", [])
    live = [t for t in items
            if t.get("state") in ("open", "claimed", "needs_review", "blocked")]
    if not live:
        return f"{len(items)} task(s) total, none in-flight."
    lines = [f"{len(live)} in-flight of {len(items)} total:"]
    for task in live[:OPEN_TASK_LIMIT]:
        lines.append(f"#{(task.get('id'))} [{task.get('state')}] "
                     f"{task.get('title', '')[:70]}")
    if len(live) > OPEN_TASK_LIMIT:
        lines.append(f"... and {len(live) - OPEN_TASK_LIMIT} more")
    return "\n".join(lines)


def skill_catalogue(project: str) -> str:
    """kind=skill knowledge entries (title + one line); tolerant of zero."""
    data = _ledger_get("/v1/knowledge/search",
                       {"q": "skill", "project": project, "n": 50})
    if (note := _ledger_problem(data)) is not None:
        return note
    skills = [e for e in data.get("items", []) if e.get("kind") == "skill"]
    if not skills:
        return "(no skill entries in knowledge)"
    lines = []
    for entry in skills[:SKILL_LIMIT]:
        one_line = (entry.get("summary") or "").strip().splitlines()
        lines.append(f"- {entry.get('title', '?')}"
                     + (f": {one_line[0][:100]}" if one_line and one_line[0] else ""))
    if len(skills) > SKILL_LIMIT:
        lines.append(f"... and {len(skills) - SKILL_LIMIT} more")
    return "\n".join(lines)


def top_knowledge_slugs(project: str) -> str:
    """Top relevant knowledge slugs (verified rules in force)."""
    data = _ledger_get("/v1/knowledge-rules", {"project": project})
    if (note := _ledger_problem(data)) is not None:
        return note
    items = data.get("items", [])
    if not items:
        return "(no verified rules for this project)"
    slugs = []
    for rule in items[:RULE_SLUG_LIMIT]:
        slugs.append(str(rule.get("citation")
                         or rule.get("slug") or rule.get("title", "?")))
    return ", ".join(slugs)


def render(tenant: str, project: str, src: str,
           manifest: list[dict]) -> str:
    """Render the full startup context text (pre-cap)."""
    lines = [f"# Supervisor startup context (live) — "
             f"tenant={tenant} project={project} [{src}]",
             "",
             "## Capabilities manifest (live @mcp.tool registry)"]
    for item in manifest:
        lines.append(f"- {item['name']}: {item['summary']}")
    lines += ["",
              "## Open tasks",
              open_tasks_summary(tenant),
              "",
              "## Skill catalogue (knowledge kind=skill)",
              skill_catalogue(project),
              "",
              "## Top knowledge in force",
              top_knowledge_slugs(project)]
    return "\n".join(lines)


async def build_context(tenant: str | None = None, project: str | None = None,
                      cap: int = DEFAULT_CAP) -> dict:
    """Build the capped startup context.

    Returns ``{"text", "truncated", "cap", "full_length", "tool_count"}``.
    ``text`` never exceeds ``cap`` chars; truncation is stated inline.
    """
    env_tenant, env_project, src = tenant_project()
    tenant = tenant or env_tenant
    project = project or env_project
    manifest = await live_tool_manifest()
    full = render(tenant, project, src, manifest)
    if len(full) <= cap:
        return {"text": full, "truncated": False, "cap": cap,
                "full_length": len(full), "tool_count": len(manifest)}
    marker = f"\n...[truncated at {cap} chars of {len(full)} — " \
             f"cap={cap} via ROUVR_CONTEXT_CAP]"
    return {"text": full[:cap - len(marker)] + marker, "truncated": True,
            "cap": cap, "full_length": len(full),
            "tool_count": len(manifest)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--cap", type=int,
                        default=int(os.environ.get("ROUVR_CONTEXT_CAP",
                                                   str(DEFAULT_CAP))))
    args = parser.parse_args(argv)
    ctx = asyncio.run(build_context(tenant=args.tenant, project=args.project,
                                    cap=args.cap))
    sys.stdout.write(ctx["text"] + "\n")
    if ctx["truncated"]:
        print(f"[supervisor_context] output truncated: "
              f"{ctx['full_length']} -> {ctx['cap']} chars",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
