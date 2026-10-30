import importlib, json
import pytest


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_DEMO_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv("ROUVR_LEDGER_DATABASE_URL", f"sqlite:///{tmp_path}/d.db")
    monkeypatch.setenv("ROUVR_MODEL_PROVIDER", "stub")
    monkeypatch.delenv("ROUVR_INJECT", raising=False)
    import demo.morning_report as m
    return importlib.reload(m), tmp_path


def test_happy_path_then_day2(demo):
    m, tmp = demo
    assert m.run() == "Done. No action needed."
    assert m.run() == "Done. No action needed."
    assert len(list((tmp / "outbox").iterdir())) == 1


def test_kill_midstep_no_double_send(demo, monkeypatch):
    m, tmp = demo
    monkeypatch.setenv("ROUVR_INJECT", "kill_midstep")
    with pytest.raises(SystemExit):
        m.run()
    assert m.run() == "Done. No action needed."
    assert len(list((tmp / "outbox").iterdir())) == 1


def test_silent_delivery_caught_and_fallback_used(demo, monkeypatch):
    m, tmp = demo
    monkeypatch.setenv("ROUVR_INJECT", "silent_delivery")
    assert m.run() == "Done. No action needed."
    c = m.ledger_client(None)
    ev = c.get("/v1/tasks/1").json()["evidence"]
    verdicts = [json.loads(e["body"])["verdict"] for e in ev if e["kind"] == "reviewer"]
    assert verdicts == ["fail", "pass"]


def test_verify_malformed_outbox_is_no_readback(demo):
    m, tmp = demo
    (tmp / "outbox").mkdir(parents=True, exist_ok=True)
    (tmp / "outbox" / "msg1.txt").write_text("no-newline-here")

    class Resp:
        def __init__(self, payload=None, status=200):
            self._p, self.status_code = payload, status

        def json(self):
            return self._p

    class Client:
        def get(self, path):
            return Resp({"items": []}) if path == "/v1/tasks" else Resp(status=200)

    assert m.verify(Client(), "msg1", {}) == {
        "verdict": "fail", "readback": False, "figures_match": False, "health": True}


def test_revoked_key_parks_and_escalates(demo, monkeypatch):
    m, _ = demo
    monkeypatch.setenv("ROUVR_INJECT", "revoked_key")
    assert m.run().startswith("ESCALATED")
    assert m.ledger_client(None).get("/v1/tasks/1").json()["state"] == "blocked"
