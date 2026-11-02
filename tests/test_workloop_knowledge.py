"""Work-loop wiring: supervisor cites, bundle carries rules, receipt lists them."""
import threading

import httpx
import pytest
import uvicorn


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    import os
    os.environ["ROUVR_LEDGER_TOKEN"] = "stack-secret"
    os.environ.pop("ROUVR_INSECURE_DEV", None)
    from ledger.app import make_app
    db = tmp_path_factory.mktemp("workloop") / "w.db"
    config = uvicorn.Config(make_app(f"sqlite:///{db}"), host="127.0.0.1",
                            port=18233, log_level="error")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    url = "http://127.0.0.1:18233"
    for _ in range(100):
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        import time
        time.sleep(0.1)
    yield url
    server.should_exit = True


def test_rule_changes_outcome_with_citation(live, tmp_path, monkeypatch):
    import importlib
    import rouvr.mcp_server as ms
    monkeypatch.setenv("WORKHUB_BASE_URL", live)
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    importlib.reload(ms)
    monkeypatch.setattr(ms, "_author_hidden_suite",
                        lambda criteria: {"test_authored.py": "def test_x():\n    assert 1 == 1\n"})
    monkeypatch.setattr(ms, "_decompose_brief",
                        lambda t, d, c: {"status": "enriched"})
    stack = {"X-Rouvr-Token": "stack-secret"}
    with httpx.Client(base_url=live, timeout=10, headers=stack) as c:
        assert c.post("/v1/tenants", json={"tenant": "acme"}).status_code == 200
        assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).status_code == 200
        r = c.post("/v1/knowledge", json={
            "tenant": "acme", "project": "site", "kind": "rule",
            "title": "All functions need type hints",
            "body": "Every Python function must declare type hints."}).json()
        assert c.post(f"/v1/knowledge/{r['id']}/confirm", json={}).status_code == 200
        cite = f"knowledge:{r['id']}@v{r['version']}"
    # supervisor creates: auto-search cites the verified rule
    task = ms.workhub_create_task("Add helper", "Add a helper with types",
                                  ["helper has type hints"], "acme", "site")
    assert cite in (task.get("knowledge_citations") or [])
    import json as _json
    spec = _json.loads(task["contracts"][0]["body"])
    assert cite in spec["description"] and "Knowledge in force" in spec["description"]
    # coder/reviewer bundle carries the rule in force
    bundle = ms.knowledge_context_bundle("acme", "site")
    assert cite in bundle["citations"]
    # reviewer requests changes citing the rule; receipt carries the citation
    with httpx.Client(base_url=live, timeout=10, headers=stack) as c:
        c.post(f"/v1/tasks/{task['id']}/evidence", json={
            "kind": "reviewer_report",
            "body": {"fence": "review:1", "verdict": "request_changes",
                     "knowledge_citation": cite,
                     "note": f"missing type hints, see {cite}"}}).raise_for_status()
        shown = ms.workhub_show_task(task["id"])
        assert cite in shown["summary"]["knowledge_citations"]
        tr = c.post(f"/v1/tasks/{task['id']}/transition", json={
            "to": "claimed", "idempotency_key": "rev-1",
            "note": f"reviewer requested changes per {cite}"}).json()
        assert tr["receipt"]["task_id"] == task["id"]
        detail = c.get(f"/v1/tasks/{task['id']}").json()
        assert any(e.get("kind") == "reviewer_report" for e in detail["evidence"])
