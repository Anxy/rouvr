"""Contract amendment gate tests (Wave 2 build).

No approval -> 403 AMENDMENT_APPROVAL_REQUIRED + nothing appended.
Approval -> 200, chain length +1, GET re-read verifies the record.
Narrowing-only without approval follows the flag (default require).

Run from hyeve-aix-service/:  pytest backend/tests/test_contract_amendment_gate.py -q
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import contract_amendment_router as _router_mod
from backend.routers.contract_amendment_router import router as amendment_router

AUTH = {"Authorization": "Test amendment-header"}


def _base_contract() -> dict:
    return {
        "objective_id": "obj-amend-1",
        "tenant_id": "tenant-a",
        "target_surface": {"target_files": ["owned.py"], "owning_symbols": ["pkg.mod.fn"]},
        "execution_slice": {"target_files": ["owned.py"], "validation_commands": ["pytest -q"]},
        "acceptance_criteria": ["green"],
        "commit_sha": "abc123",
    }


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(_router_mod, "_LOG_PATH", tmp_path / "amendments.jsonl")
    monkeypatch.setattr(_router_mod, "REQUIRE_APPROVAL_FOR_NARROWING", True)
    app = FastAPI()
    app.include_router(amendment_router)
    return TestClient(app)


def _post(client: TestClient, delta: dict, **extra) -> object:
    body = {
        "contract": _base_contract(),
        "delta": delta,
        "who": "lane-op",
        "why": "need one more file",
        "evidence_path": "evidence/amend.md",
        **extra,
    }
    return client.post("/contracts/amendments", json=body, headers=AUTH)


def test_growth_without_approval_is_403_and_appends_nothing(client: TestClient) -> None:
    resp = _post(client, {"added_files": ["extra.py"]})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "AMENDMENT_APPROVAL_REQUIRED"
    chain = client.get(
        "/contracts/amendments", params={"objective_id": "obj-amend-1"}, headers=AUTH
    )
    assert chain.status_code == 200, chain.text
    assert chain.json()["chain_length"] == 0


def test_growth_with_approval_appends_and_rereads(client: TestClient) -> None:
    resp = _post(
        client,
        {"added_files": ["extra.py"]},
        lane_approval_id="lane-123",
        parent_commit_sha="abc123",
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["chain_length"] == 1
    assert payload["files_in_scope"] == ["owned.py", "extra.py"]

    exported = client.get(
        "/contracts/amendments", params={"objective_id": "obj-amend-1"}, headers=AUTH
    )
    assert exported.status_code == 200, exported.text
    record = exported.json()["amendments"][0]
    assert record["amendment_id"] == payload["amendment_id"]
    assert record["lane_approval_id"] == "lane-123"
    assert record["parent_commit_sha"] == "abc123"
    assert record["new_scope_delta"]["added_files"] == ["extra.py"]


def test_narrowing_without_approval_follows_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    narrowing = {"removed_files": ["owned.py"]}
    resp = _post(client, narrowing)
    assert resp.status_code == 403, resp.text

    monkeypatch.setattr(_router_mod, "REQUIRE_APPROVAL_FOR_NARROWING", False)
    resp = _post(client, narrowing)
    assert resp.status_code == 200, resp.text
    assert resp.json()["files_in_scope"] == []
    assert resp.json()["chain_length"] == 1


def test_export_alias_serves_same_chain(client: TestClient) -> None:
    _post(client, {"added_files": ["extra.py"]}, lane_approval_id="lane-123")
    for path in ("/contracts/amendments", "/contracts/amendments/export"):
        resp = client.get(path, params={"objective_id": "obj-amend-1"}, headers=AUTH)
        assert resp.status_code == 200, (path, resp.text)
        assert resp.json()["chain_length"] == 1


def test_missing_auth_is_401(client: TestClient) -> None:
    resp = client.post(
        "/contracts/amendments",
        json={"contract": _base_contract(), "delta": {}},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["code"] == "CONTRACT_AUTH_REQUIRED"


def test_old_chain_lines_read_back_without_parent_sha(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.aix_core.contracts.task_contract import ContractAmendment

    log = tmp_path / "amendments.jsonl"
    monkeypatch.setattr(_router_mod, "_LOG_PATH", log)
    old = {
        "amendment_id": "amd-legacy",
        "objective_id": "obj-amend-1",
        "who": "lane-op",
        "why": "pre-wave2 line",
        "new_scope_delta": {},
        "evidence_path": "",
        "lane_approval_id": "lane-0",
        "timestamp": "2026-09-09T00:00:00+00:00",
    }
    log.write_text(json.dumps(old) + "\n", encoding="utf-8")
    assert ContractAmendment.model_validate(old).parent_commit_sha == ""

    exported = client.get(
        "/contracts/amendments", params={"objective_id": "obj-amend-1"}, headers=AUTH
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["chain_length"] == 1
