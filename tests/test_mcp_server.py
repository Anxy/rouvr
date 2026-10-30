"""MCP front door: tool registration + create/show against a live ledger app."""
import asyncio
import threading

import httpx
import pytest
import uvicorn


@pytest.fixture(scope="module")
def ledger_url(tmp_path_factory):
    from ledger.app import make_app
    db = tmp_path_factory.mktemp("mcpledger") / "l.db"
    config = uvicorn.Config(make_app(f"sqlite:///{db}"), host="127.0.0.1", port=18221,
                            log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get("http://127.0.0.1:18221/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        import time; time.sleep(0.1)
    yield "http://127.0.0.1:18221"
    server.should_exit = True


async def test_mcp_tools_registered():
    from rouvr.mcp_server import mcp
    names = [t.name for t in await mcp.list_tools()]
    assert names == ["workhub_create_task", "workhub_list_tasks",
                     "workhub_show_task", "workhub_post_message",
                     "workhub_read_messages", "workhub_peer_sessions",
                     "workhub_message_agent", "workhub_agent_windows",
                     "workhub_reap_windows", "rouvr_start_task"]


def test_mcp_create_and_show(monkeypatch, ledger_url):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    task = ms.workhub_create_task("t", "d", ["c1"])
    assert task["state"] == "open" and task["id"] == 1
    shown = ms.workhub_show_task(1)
    assert shown["contracts"] and shown["contracts"][0]["body"]


def test_mcp_agent_messages_roundtrip(monkeypatch, ledger_url):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    ms.workhub_create_task("t2", "d", ["c1"])
    ms.workhub_post_message(1, "builder", "verifier", "used punct param name from criteria")
    ms.workhub_post_message(1, "verifier", "builder", "please also cover empty name")
    assert ms.workhub_read_messages(1, "verifier") == [
        {"from": "builder", "to": "verifier", "text": "used punct param name from criteria"}]
    assert len(ms.workhub_read_messages(1)) == 2
