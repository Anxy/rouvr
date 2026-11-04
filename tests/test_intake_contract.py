"""Slice D: intake contract tests (no duplicates of test_chat_intake.py).

Covers what the end-to-end intake suite does not:
- classifier contract through the stubbed model (all four classes,
  unknown-class coercion, confidence/category normalization);
- tenant scoping on the chat surface (foreign tenant cannot chat/read).

Everything else in the D slice is already covered end-to-end in
tests/test_chat_intake.py and recorded in the final report instead of
being duplicated here.
"""
import json

import pytest
from fastapi.testclient import TestClient

from ledger import chat as chatmod


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-chat-intake-12345678901")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "chat-intake-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/intake-contract.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return app


@pytest.fixture()
def session_client(app):
    c = TestClient(app)
    login = c.post("/auth/login", data={"username": "admin",
                                        "password": "chat-intake-password-0123456789"})
    assert login.status_code == 200, login.text
    return TestClient(app, cookies=login.cookies)


def _stubbed_parse(payload: dict) -> dict:
    """Run a turn payload through the stubbed model, then the classifier."""
    provider = chatmod.provider_from_env({})
    assert provider["name"] == "stub"
    text = chatmod.complete_once(
        [{"role": "system", "content": chatmod.SYSTEM_PROMPT},
         {"role": "user", "content": json.dumps(payload)}], provider)
    return chatmod.parse_intake_turn(text)


def test_classifier_stub_contract_all_four_classes():
    chat = _stubbed_parse({"question": None, "slice": None, "tasks": [],
                           "answer": "hello there",
                           "intake": {"class": "chat", "confidence": 0.9,
                                      "category": None}})
    assert chat["intake"] == {"class": "chat", "confidence": pytest.approx(0.9),
                              "category": None}
    assert chat["answer"] == "hello there"

    knowledge = _stubbed_parse(
        {"question": None, "slice": None, "tasks": [], "answer": "noted",
         "knowledge": {"kind": "decision", "title": "Use sqlite",
                       "body": "sqlite for the ledger"},
         "intake": {"class": "knowledge", "confidence": 0.8,
                    "category": None}})
    assert knowledge["intake"]["class"] == "knowledge"
    assert knowledge["knowledge"]["title"] == "Use sqlite"

    task = _stubbed_parse(
        {"question": None, "slice": None, "tasks": [], "answer": "on it",
         "task": {"title": "Fix login", "intent": "repair the flow"},
         "contract": {"acceptance_criteria": ["login works"]},
         "intake": {"class": "task", "confidence": 0.95,
                    "category": "bugfix"}})
    assert task["intake"] == {"class": "task", "confidence": pytest.approx(0.95),
                              "category": "bugfix"}
    assert task["task"] == {"title": "Fix login", "intent": "repair the flow"}
    assert task["contract"] == {"acceptance_criteria": ["login works"]}

    ambiguous = _stubbed_parse(
        {"question": "Which project?", "slice": None, "tasks": [],
         "answer": "",
         "intake": {"class": "ambiguous", "confidence": 0.4,
                    "category": None}})
    assert ambiguous["question"] == "Which project?"
    assert ambiguous["slice"] is None and ambiguous["tasks"] == []
    assert ambiguous["intake"]["class"] == "ambiguous"


def test_classifier_unknown_class_coerces_and_confidence_clamps():
    coerced = _stubbed_parse({"question": None, "slice": None, "tasks": [],
                              "answer": "hi",
                              "intake": {"class": "frobnicate",
                                         "confidence": 0.7,
                                         "category": None}})
    assert coerced["intake"]["class"] == "ambiguous"  # never errors, degrades

    high = _stubbed_parse({"question": None, "slice": None, "tasks": [],
                           "answer": "hi",
                           "intake": {"class": "chat", "confidence": 7,
                                      "category": "frobnicate"}})
    assert high["intake"]["confidence"] == pytest.approx(1.0)
    assert high["intake"]["category"] is None  # unknown category dropped

    low = _stubbed_parse({"question": None, "slice": None, "tasks": [],
                          "answer": "hi",
                          "intake": {"class": "chat", "confidence": -3,
                                     "category": None}})
    assert low["intake"]["confidence"] == pytest.approx(0.0)

    defaulted = _stubbed_parse({"question": None, "slice": None, "tasks": [],
                                "answer": "hi",
                                "intake": {"class": "chat", "category": None}})
    assert defaulted["intake"]["confidence"] == pytest.approx(0.5)

    legacy = chatmod.parse_intake_turn(
        json.dumps({"question": None,
                    "slice": {"title": "Legacy", "intent": "old",
                              "acceptance_criteria": ["works"]},
                    "tasks": [{"title": "chore", "intent": "do it"}]}))
    assert legacy["intake"] is None  # missing block = legacy turn marker


def test_chat_tenant_scope_foreign_cannot_chat_or_read(session_client):
    body = {"message": json.dumps({"question": None, "slice": None,
                                   "tasks": [], "answer": "hi",
                                   "intake": {"class": "chat",
                                              "confidence": 0.9,
                                              "category": None}}),
            "project": "demo", "tenant": "other", "message_id": "x-1"}
    r = session_client.post("/api/v1/chat", json=body,
                            headers={"Origin": "http://testserver"})
    assert r.status_code == 404, r.text  # refused before any project oracle

    for path, params in (
        ("/api/v1/chat/turns", {"project": "demo", "tenant": "other"}),
        ("/api/v1/chat/classifications",
         {"project": "demo", "tenant": "other"}),
        ("/v1/chat/autonomy", {"project": "demo", "tenant": "other"}),
    ):
        got = session_client.get(path, params=params)
        assert got.status_code == 404, (path, got.status_code, got.text)

    # nothing leaked into the caller's own scope from the refused attempts
    assert session_client.get("/api/v1/chat/turns",
                              params={"project": "demo"}).json()["items"] == []
    assert session_client.get("/api/v1/chat/classifications",
                              params={"project": "demo"}).json()["items"] == []

    # own scope still works
    own = dict(body, tenant="demo", message_id="x-2")
    assert session_client.post("/api/v1/chat", json=own,
                               headers={"Origin": "http://testserver"}).status_code == 200
