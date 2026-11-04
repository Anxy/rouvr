"""Convert-time hidden-suite sealing: sync seal at Convert, async hold fallback,
retry endpoint, and exec-spec presence flags. Model turn is faked at the
provider seam so the real _author_hidden_suite + validation run seal plumbing
with no model spend."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from ledger.app import make_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_INSECURE_DEV", "1")
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    monkeypatch.delenv("ROUVR_MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    monkeypatch.setenv("SPEC_ROOT", str(tmp_path / "specs"))
    (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
    c = TestClient(make_app(f"sqlite:///{tmp_path / 'l.db'}"))
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    return c


class _SealingProvider:
    """Fake Meta provider: builds a compliant suite from the requested
    criterion ids. Records call kwargs (timeout/max_tokens/reasoning)."""
    name = "meta"

    def __init__(self, calls, fail=False):
        self._calls = calls
        self._fail = fail

    def chat_complete(self, messages, timeout=60.0, **kw):
        self._calls.append({"timeout": timeout, **kw})
        if self._fail:
            raise RuntimeError("boom")
        ids = re.findall(r"\[(c\d+)\]", messages[-1]["content"])
        ids = ids or ["c1"]
        n = min(8, max(5, len(ids)))
        lines = []
        for i in range(n):
            cite = ids[i] if i < len(ids) else ids[0]
            lines += [f"def test_sealed_{i}():  # [{cite}]",
                      f"    assert {i} + {i} == {2 * i}", ""]
        return {"content": json.dumps(
            {"test_rouvr_hidden_seal.py": "\n".join(lines)}),
            "finish_reason": "stop", "usage": {}}


def _install(monkeypatch, calls, fail=False):
    import backend.rouvr_models.providers as providers

    monkeypatch.setattr(providers, "get_provider",
                        lambda *a, **k: _SealingProvider(calls, fail=fail))


def _plan_entry(c, spec, tasks):
    body = json.dumps({"source": "m1", "category": "feature", "class": "task",
                       "slice": spec, "tasks": tasks})
    r = c.post("/v1/knowledge", json={"tenant": "acme", "project": "site",
                                      "kind": "note", "title": "Plan: x",
                                      "body": body, "tags": ["plan", "turn-m1"]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _spec():
    return {"title": "Slice S", "intent": "do S",
            "acceptance_criteria": ["S works", "S is fast"]}


def _tasks():
    return [{"title": "Step one", "intent": "do one"},
            {"title": "Step two", "intent": "do two"}]


def _result_frames(r):
    """Last SSE result payload from a POST /v1/chat response."""
    texts = []
    for line in r.text.splitlines():
        if line.startswith("data:"):
            texts.append(line[len("data:"):].strip())
    return [json.loads(t) for t in texts]


def test_convert_seals_every_created_task(tmp_path, monkeypatch):
    calls = []
    _install(monkeypatch, calls)
    c = _client(tmp_path, monkeypatch)
    pid = _plan_entry(c, _spec(), _tasks())
    r = c.post("/v1/chat", json={"tenant": "acme", "project": "site",
                                 "message": "convert it",
                                 "plan_action": "convert", "plan_entry": pid})
    assert r.status_code == 200, r.text
    res = _result_frames(r)[-1]
    assert res["classification"] == "task"
    created = [t["id"] for t in res["tasks"]]
    assert len(created) == 3  # result cards: head + 2 kids (slice rides in res["slice"])
    created.append(res["slice"]["id"])
    for tid in created:
        assert (tmp_path / "hidden" / str(tid) / "MANIFEST.json").exists()
    items = {t["id"]: t for t in c.get("/v1/tasks").json()["items"]}
    assert all(items[i]["sealing"] is False for i in created)
    assert all(items[i]["needs_spec"] is True for i in created)
    detail = c.get(f"/v1/tasks/{created[0]}").json()
    kinds = [e["kind"] for e in detail["evidence"]]
    assert "suite_sealed" in kinds
    assert all(call["timeout"] <= 90 for call in calls)


def test_convert_failure_marks_pending_and_stays_200(tmp_path, monkeypatch):
    calls = []
    _install(monkeypatch, calls, fail=True)
    c = _client(tmp_path, monkeypatch)
    pid = _plan_entry(c, _spec(), _tasks())
    r = c.post("/v1/chat", json={"tenant": "acme", "project": "site",
                                 "message": "convert it",
                                 "plan_action": "convert", "plan_entry": pid})
    assert r.status_code == 200, r.text
    res = _result_frames(r)[-1]
    created = [t["id"] for t in res["tasks"]]
    items = {t["id"]: t for t in c.get("/v1/tasks").json()["items"]}
    assert all(items[i]["sealing"] is True for i in created)
    detail = c.get(f"/v1/tasks/{created[0]}").json()
    assert [e["kind"] for e in detail["evidence"]].count("seal_pending") == 1


def test_seal_endpoint_retries_pending_then_replays(tmp_path, monkeypatch):
    calls = []
    _install(monkeypatch, calls, fail=True)
    c = _client(tmp_path, monkeypatch)
    pid = _plan_entry(c, _spec(), _tasks())
    r = c.post("/v1/chat", json={"tenant": "acme", "project": "site",
                                 "message": "convert it",
                                 "plan_action": "convert", "plan_entry": pid})
    tid = _result_frames(r)[-1]["tasks"][0]["id"]
    before = len(calls)
    _install(monkeypatch, calls, fail=False)
    r = c.post(f"/v1/tasks/{tid}/seal", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"sealed": True, "reason": None, "task_id": tid}
    assert len(calls) == before + 1
    assert (tmp_path / "hidden" / str(tid) / "MANIFEST.json").exists()
    assert c.get("/v1/tasks").json()["items"][0]["sealing"] is False
    r = c.post(f"/v1/tasks/{tid}/seal", json={})
    assert r.json()["replayed"] is True
    assert len(calls) == before + 1  # converged without spending


def test_seal_endpoint_refuses_task_without_criteria(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    tid = c.post("/v1/tasks", json={"title": "bare", "intent": "y",
                                    "tenant": "acme",
                                    "project": "site"}).json()["id"]
    r = c.post(f"/v1/tasks/{tid}/seal", json={})
    assert r.status_code == 422


def test_needs_spec_clears_with_yaml(tmp_path, monkeypatch):
    calls = []
    _install(monkeypatch, calls)
    c = _client(tmp_path, monkeypatch)
    pid = _plan_entry(c, _spec(), _tasks())
    c.post("/v1/chat", json={"tenant": "acme", "project": "site",
                             "message": "convert it",
                             "plan_action": "convert", "plan_entry": pid})
    items = c.get("/v1/tasks").json()["items"]
    assert all(t["needs_spec"] is True for t in items)
    spec_dir = tmp_path / "specs" / "acme"
    spec_dir.mkdir(parents=True)
    (spec_dir / "site.yaml").write_text(
        "repo: acme/site\nbase_branch: main\ntest_command: [\"x\"]\n")
    items = c.get("/v1/tasks").json()["items"]
    assert all(t["needs_spec"] is False for t in items)
