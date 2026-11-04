"""Slice A bootstrap: startup context is live, capped, and specifies the prompt."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "supervisor_context", ROOT / "scripts" / "supervisor_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_manifest_tool_names_equal_live_mcp_tools():
    """Manifest names must exactly equal the live MCP tool list.

    The script enumerates the registry (never a hand-written list), so a
    tool added to rouvr.mcp_server flows through; a hard-coded manifest
    would fail here on the next tool addition.
    """
    from rouvr.mcp_server import mcp

    ctx = _load_script()
    live = [t.name for t in await mcp.list_tools()]
    manifest = await ctx.live_tool_manifest()
    assert [m["name"] for m in manifest] == live
    assert all(m["summary"] for m in manifest)  # docstring first lines


async def test_bundle_respects_cap_and_reports_truncation():
    ctx = _load_script()
    tiny = await ctx.build_context(tenant="t", project="p", cap=200)
    assert len(tiny["text"]) <= 200
    assert tiny["truncated"] is True
    assert "200" in tiny["text"]  # stated cap
    assert tiny["full_length"] > 200
    roomy = await ctx.build_context(tenant="t", project="p", cap=6000)
    assert len(roomy["text"]) <= 6000
    assert roomy["truncated"] == (roomy["full_length"] > 6000)


def test_prompt_file_contains_required_sections():
    text = (ROOT / "SUPERVISOR.md").read_text().lower()
    for section in ("role", "rules", "3-round", "autonomy", "capture"):
        assert section in text, f"SUPERVISOR.md missing section: {section}"


def test_bundle_tolerates_zero_skills_and_dead_ledger(monkeypatch):
    ctx = _load_script()
    monkeypatch.setattr(ctx, "_ledger_get", lambda *a, **k: None)
    assert "unreachable" in ctx.open_tasks_summary("t")
    assert "unreachable" in ctx.skill_catalogue("p")
    assert "unreachable" in ctx.top_knowledge_slugs("p")
    monkeypatch.setattr(ctx, "_ledger_get",
                        lambda *a, **k: {"_http_error": 401})
    assert "refused" in ctx.open_tasks_summary("t")
    assert "401" in ctx.skill_catalogue("p")
    monkeypatch.setattr(ctx, "_ledger_get",
                        lambda *a, **k: {"items": [{"kind": "note"}]})
    assert ctx.skill_catalogue("p") == "(no skill entries in knowledge)"
