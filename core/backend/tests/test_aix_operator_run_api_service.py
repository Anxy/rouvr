from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.aix_operator_runs_router import router
from backend.services.aix_operator_run_api_service import (
    OperatorRunApiError,
    get_operator_run_detail,
    get_operator_run_evidence_bundle,
    get_operator_run_replay_by_evidence_id,
    get_operator_run_replay_comparison,
    list_operator_runs,
)
from backend.services.aix_run_ledger_service import persist_run_ledger_record


def _payload(*, tenant_id: str = "tenant-a", run_id: str = "run-a", correlation_id: str = "corr-a") -> dict:
    return {
        "tenant_id": tenant_id,
        "ok": False,
        "project_id": "project-a",
        "task_number": 42,
        "task_id": "task-42",
        "task_intent": "code_change_required",
        "final_status": "blocked",
        "run_id": run_id,
        "result": {
            "run_id": run_id,
            "correlation_id": correlation_id,
            "project_id": "project-a",
            "task_id": "task-42",
            "task_number": 42,
            "status": "failed",
            "evidence": {
                "proposal_artifacts": {"diff_ref": "local://diff.patch"},
                "commands": ["pytest tests/test_example.py -q"],
                "tests": {"detected": True, "commands": ["pytest tests/test_example.py -q"]},
                "transcript": [{"tool": "cmd.run", "ok": False, "result": {"reason_code": "TEST_FAILED"}}],
            },
            "error": {"code": "TEST_FAILED", "message": "validation failed"},
            "evidence_refs": ["local://evidence/ref-1"],
        },
        "control_plane_outcome": {
            "phase": "final",
            "status": "blocked",
            "substate": "validation_failed",
            "reason_code": "TEST_FAILED",
            "reason_summary": "validation command failed",
            "handoff_status": "blocked",
            "failure_category": "execution_failed",
        },
        "planner": {
            "planner_used": True,
            "provider": "local",
            "planner_packet": {
                "target_files": ["backend/example.py"],
                "validation_commands": ["pytest tests/test_example.py -q"],
            },
        },
        "quality_gate": {"passed": False, "code": "TEST_FAILED", "message": "validation failed"},
        "diagnostic_outcome": {
            "status": "blocked",
            "reason_code": "TEST_FAILED",
            "reason_summary": "validation command failed",
        },
        "writeback": {"status": "failed", "opsbus_written": False, "workhub_written": False},
        "repo_profile": {"profile_id": "repo-a"},
        "task_packet": {"task_id": "task-42", "task_number": 42, "title": "Fix example"},
        "validation_contract": {
            "expected_commands": ["pytest tests/test_example.py -q"],
            "target_files": ["backend/example.py"],
            "code_change_required": True,
        },
        "refs": {"proposal_diff_ref": "local://diff.patch"},
        "execution_digest": {
            "mutation": {"touched_files": ["backend/example.py"], "primary_mutation_tool": "apply_patch"},
            "postcheck": {
                "passed": False,
                "exit_code": 1,
                "reason_code": "TEST_FAILED",
                "output": "1 failed",
            },
        },
        "audit_events": [{"kind": "run.created", "actor": "tester", "timestamp_utc": "2026-04-30T00:00:00Z"}],
    }


def _client(root: Path, monkeypatch) -> TestClient:
    from backend.services import aix_operator_run_api_service as module

    monkeypatch.setattr(module, "get_run_ledger_root", lambda: root)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _write_raw(root: Path, payload: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    run_id = str(payload.get("run_id") or payload.get("correlation_id") or "run")
    (root / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_run_list_returns_authoritative_tenant_scoped_runs(tmp_path: Path) -> None:
    persist_run_ledger_record(_payload(tenant_id="tenant-a", run_id="run-a", correlation_id="corr-a"), root=tmp_path)
    persist_run_ledger_record(_payload(tenant_id="tenant-b", run_id="run-b", correlation_id="corr-b"), root=tmp_path)

    result = list_operator_runs(tenant_id="tenant-a", root=tmp_path)

    assert result.payload["success"] is True
    data = result.payload["data"]
    assert data["schema_version"] == "aix.operator_runs.list.v1"
    assert data["authority"]["source"] == "aix_run_ledger"
    assert data["authority"]["workhub_task_derived"] is False
    assert data["authority"]["opsbus_event_text_derived"] is False
    assert data["total"] == 1
    assert data["items"][0]["run_id"] == "run-a"
    assert data["items"][0]["tenant_id"] == "tenant-a"


def test_run_detail_returns_s200_5_section_model(tmp_path: Path) -> None:
    persist_run_ledger_record(_payload(tenant_id="tenant-a", run_id="run-a"), root=tmp_path)

    result = get_operator_run_detail(tenant_id="tenant-a", run_id="run-a", root=tmp_path)

    assert result.status == "ok"
    data = result.payload["data"]
    assert data["schema_version"] == "aix.operator_run.detail.v1"
    assert data["header"]["run_id"] == "run-a"
    assert data["decision"]["disabled_reasons"]["approve"]
    assert data["execution_summary"]["result_summary"] == "blocked"
    assert data["evidence_bundle"]["status"] in {"complete", "incomplete"}
    assert data["failure"]["failure_class"]
    assert "replay" in data
    assert data["audit"]["events"][0]["kind"] == "run.created"


def test_missing_tenant_is_rejected(tmp_path: Path) -> None:
    persist_run_ledger_record(_payload(), root=tmp_path)

    try:
        list_operator_runs(tenant_id="", root=tmp_path)
    except OperatorRunApiError as exc:
        assert exc.failure_class == "CONTRACT_VIOLATION"
        assert exc.reason == "TENANT_ID_REQUIRED"
    else:
        raise AssertionError("missing tenant must reject")


def test_cross_tenant_detail_denies_without_leaking_fields(tmp_path: Path) -> None:
    persist_run_ledger_record(_payload(tenant_id="tenant-a", run_id="run-a"), root=tmp_path)

    result = get_operator_run_detail(tenant_id="tenant-b", run_id="run-a", root=tmp_path)

    assert result.status == "not_found"
    assert result.payload["success"] is False
    assert result.payload["failure_class"] == "RUN_NOT_FOUND"
    assert "project-a" not in str(result.payload)
    assert "task-42" not in str(result.payload)


def test_empty_list_is_successful(tmp_path: Path) -> None:
    result = list_operator_runs(tenant_id="tenant-a", root=tmp_path)

    assert result.payload["success"] is True
    assert result.payload["data"]["items"] == []
    assert result.payload["data"]["total"] == 0


def test_invalid_payload_becomes_classified_failure(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "bad.json").write_text("{not-json", encoding="utf-8")

    try:
        list_operator_runs(tenant_id="tenant-a", root=tmp_path)
    except OperatorRunApiError as exc:
        assert exc.failure_class == "RUN_API_INVALID_PAYLOAD"
        assert exc.reason == "LEDGER_RECORD_INVALID_JSON"
    else:
        raise AssertionError("invalid run payload must reject")


def test_router_requires_auth_and_tenant(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    assert client.get("/api/aix/runs").status_code == 401
    missing_tenant = client.get("/api/aix/runs", headers={"Authorization": "Bearer token"})
    assert missing_tenant.status_code == 400
    assert missing_tenant.json()["detail"]["context"]["failure_class"] == "CONTRACT_VIOLATION"


def test_router_exposes_list_and_detail(tmp_path: Path, monkeypatch) -> None:
    persist_run_ledger_record(_payload(tenant_id="tenant-a", run_id="run-a"), root=tmp_path)
    client = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer token", "X-Tenant-ID": "tenant-a"}

    listing = client.get("/api/aix/runs", headers=headers)
    detail = client.get("/api/aix/runs/run-a", headers=headers)

    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["run_id"] == "run-a"
    assert detail.status_code == 200
    assert detail.json()["data"]["header"]["tenant_id"] == "tenant-a"


def test_router_accepts_hyeve_tenant_header_alias(tmp_path: Path, monkeypatch) -> None:
    persist_run_ledger_record(_payload(tenant_id="tenant-a", run_id="run-a"), root=tmp_path)
    client = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer token", "X-Hyeve-Tenant-Id": "tenant-a"}

    listing = client.get("/api/aix/runs", headers=headers)
    detail = client.get("/api/aix/runs/run-a", headers=headers)

    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["run_id"] == "run-a"
    assert detail.status_code == 200
    assert detail.json()["data"]["header"]["tenant_id"] == "tenant-a"


def test_router_cross_tenant_detail_returns_404(tmp_path: Path, monkeypatch) -> None:
    persist_run_ledger_record(_payload(tenant_id="tenant-a", run_id="run-a"), root=tmp_path)
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        "/api/aix/runs/run-a",
        headers={"Authorization": "Bearer token", "X-Tenant-ID": "tenant-b"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["sub_code"] == "RUN_ID_NOT_FOUND"


def test_evidence_bundle_exposes_complete_proof_shape(tmp_path: Path) -> None:
    persist_run_ledger_record(_payload(tenant_id="tenant-a", run_id="run-a"), root=tmp_path)

    result = get_operator_run_evidence_bundle(tenant_id="tenant-a", run_id="run-a", root=tmp_path)

    assert result.status == "ok"
    data = result.payload["data"]
    bundle = data["evidence_bundle"]
    assert data["schema_version"] == "aix.operator_run.evidence_bundle.v1"
    assert bundle["complete"] is True
    assert bundle["diff"]["diff_refs"] == ["local://diff.patch"]
    assert bundle["test_output"]["commands"][0]["command"] == "pytest tests/test_example.py -q"
    assert bundle["test_output"]["commands"][0]["exit_code"] == 1
    assert bundle["test_output"]["commands"][0]["passed"] is False
    assert bundle["command_output"][0]["tool"] == "cmd.run"
    assert bundle["verifier_result"]["verdict"] == "FAIL"


def test_missing_diff_is_explicit_not_silent(tmp_path: Path) -> None:
    payload = _payload(tenant_id="tenant-a", run_id="run-a")
    payload.pop("refs")
    payload["result"]["evidence"]["proposal_artifacts"] = {}
    payload["execution_digest"]["mutation"] = {}
    _write_raw(tmp_path, payload)

    bundle = get_operator_run_evidence_bundle(tenant_id="tenant-a", run_id="run-a", root=tmp_path).payload["data"][
        "evidence_bundle"
    ]

    assert bundle["diff"]["no_mutation_reason"] == "no changed files or diff reference recorded in run ledger"
    assert bundle["completeness"]["missing_fields"] == []


def test_missing_test_output_is_explicit(tmp_path: Path) -> None:
    payload = _payload(tenant_id="tenant-a", run_id="run-a")
    payload.pop("validation_contract")
    payload["result"]["evidence"]["commands"] = []
    payload["result"]["evidence"]["tests"] = {"detected": True, "commands": []}
    persist_run_ledger_record(payload, root=tmp_path)

    bundle = get_operator_run_evidence_bundle(tenant_id="tenant-a", run_id="run-a", root=tmp_path).payload["data"][
        "evidence_bundle"
    ]

    assert bundle["complete"] is False
    assert "test_output.commands" in bundle["completeness"]["missing_fields"]


def test_missing_verifier_is_explicit(tmp_path: Path) -> None:
    payload = _payload(tenant_id="tenant-a", run_id="run-a")
    payload.pop("quality_gate")
    _write_raw(tmp_path, payload)

    bundle = get_operator_run_evidence_bundle(tenant_id="tenant-a", run_id="run-a", root=tmp_path).payload["data"][
        "evidence_bundle"
    ]

    assert bundle["complete"] is False
    assert "verifier_result" in bundle["completeness"]["missing_fields"]


def test_malformed_evidence_is_marked_incomplete(tmp_path: Path) -> None:
    payload = _payload(tenant_id="tenant-a", run_id="run-a")
    payload["result"]["evidence"]["transcript"] = "not-a-list"
    _write_raw(tmp_path, payload)

    bundle = get_operator_run_evidence_bundle(tenant_id="tenant-a", run_id="run-a", root=tmp_path).payload["data"][
        "evidence_bundle"
    ]

    assert bundle["complete"] is False
    assert "command_output" in bundle["completeness"]["malformed_fields"]


def test_replay_mismatch_is_visible_drift(tmp_path: Path) -> None:
    original = _payload(tenant_id="tenant-a", run_id="run-original")
    replay = _payload(tenant_id="tenant-a", run_id="run-replay")
    replay["replay_of_run_id"] = "run-original"
    replay["execution_digest"]["mutation"]["touched_files"] = ["backend/changed.py"]
    _write_raw(tmp_path, original)
    _write_raw(tmp_path, replay)

    result = get_operator_run_replay_comparison(tenant_id="tenant-a", run_id="run-replay", root=tmp_path)

    data = result.payload["data"]
    assert data["schema_version"] == "aix.operator_run.replay_comparison.v1"
    assert data["original_run_id"] == "run-original"
    assert data["replay_run_id"] == "run-replay"
    assert data["drift_status"] == "drift"
    assert len(data["deterministic_checksum"]) == 64
    assert any(item["field"] == "diff" and item["status"] == "drift" for item in data["compared_fields"])


def test_duplicate_replay_comparison_is_read_only(tmp_path: Path) -> None:
    original = _payload(tenant_id="tenant-a", run_id="run-original")
    replay = _payload(tenant_id="tenant-a", run_id="run-replay")
    replay["replay_of_run_id"] = "run-original"
    _write_raw(tmp_path, original)
    _write_raw(tmp_path, replay)
    files_before = sorted(path.name for path in tmp_path.glob("*.json"))

    first = get_operator_run_replay_comparison(tenant_id="tenant-a", run_id="run-replay", root=tmp_path)
    second = get_operator_run_replay_comparison(tenant_id="tenant-a", run_id="run-replay", root=tmp_path)

    assert first.payload == second.payload
    assert second.payload["data"]["duplicate_suppression"]["duplicate_execution"] is False
    assert sorted(path.name for path in tmp_path.glob("*.json")) == files_before


def test_evidence_id_replay_resolves_tenant_scoped_replay_record(tmp_path: Path) -> None:
    original = _payload(tenant_id="tenant-a", run_id="run-original")
    replay = _payload(tenant_id="tenant-a", run_id="run-replay")
    replay["replay_of_run_id"] = "run-original"
    replay["result"]["evidence_id"] = "evidence-replay-1"
    _write_raw(tmp_path, original)
    _write_raw(tmp_path, replay)

    result = get_operator_run_replay_by_evidence_id(
        tenant_id="tenant-a",
        evidence_id="evidence-replay-1",
        root=tmp_path,
    )

    data = result.payload["data"]
    assert result.status == "ok"
    assert data["schema_version"] == "aix.operator_run.evidence_replay.v1"
    assert data["evidence_id"] == "evidence-replay-1"
    assert data["original_run_id"] == "run-original"
    assert data["replay_run_id"] == "run-replay"
    assert data["executed"] is False
    assert len(data["deterministic_checksum"]) == 64
    assert data["duplicate_suppression"]["duplicate_execution"] is False


def test_evidence_ref_replay_resolution_is_supported_without_execution(tmp_path: Path) -> None:
    original = _payload(tenant_id="tenant-a", run_id="run-original")
    replay = _payload(tenant_id="tenant-a", run_id="run-replay")
    replay["replay_of_run_id"] = "run-original"
    replay["result"]["evidence_refs"] = ["local://evidence/replay-ref"]
    _write_raw(tmp_path, original)
    _write_raw(tmp_path, replay)
    files_before = sorted(path.name for path in tmp_path.glob("*.json"))

    result = get_operator_run_replay_by_evidence_id(
        tenant_id="tenant-a",
        evidence_id="local://evidence/replay-ref",
        root=tmp_path,
    )

    assert result.status == "ok"
    assert result.payload["data"]["replay_run_id"] == "run-replay"
    assert sorted(path.name for path in tmp_path.glob("*.json")) == files_before


def test_evidence_id_replay_cross_tenant_returns_not_found_without_leak(tmp_path: Path) -> None:
    original = _payload(tenant_id="tenant-a", run_id="run-original")
    replay = _payload(tenant_id="tenant-a", run_id="run-replay")
    replay["replay_of_run_id"] = "run-original"
    replay["result"]["evidence_id"] = "evidence-secret-1"
    _write_raw(tmp_path, original)
    _write_raw(tmp_path, replay)

    result = get_operator_run_replay_by_evidence_id(
        tenant_id="tenant-b",
        evidence_id="evidence-secret-1",
        root=tmp_path,
    )

    assert result.status == "not_found"
    assert result.payload["failure_class"] == "EVIDENCE_NOT_FOUND"
    assert "run-replay" not in str(result.payload)
    assert "tenant-a" not in str(result.payload)


def test_evidence_id_replay_rejects_ambiguous_tenant_match(tmp_path: Path) -> None:
    first = _payload(tenant_id="tenant-a", run_id="run-replay-1")
    second = _payload(tenant_id="tenant-a", run_id="run-replay-2")
    first["replay_of_run_id"] = "run-original"
    second["replay_of_run_id"] = "run-original"
    first["result"]["evidence_id"] = "evidence-duplicate"
    second["result"]["evidence_id"] = "evidence-duplicate"
    _write_raw(tmp_path, _payload(tenant_id="tenant-a", run_id="run-original"))
    _write_raw(tmp_path, first)
    _write_raw(tmp_path, second)

    try:
        get_operator_run_replay_by_evidence_id(
            tenant_id="tenant-a",
            evidence_id="evidence-duplicate",
            root=tmp_path,
        )
    except OperatorRunApiError as exc:
        assert exc.failure_class == "CONTRACT_VIOLATION"
        assert exc.reason == "EVIDENCE_ID_AMBIGUOUS"
    else:
        raise AssertionError("ambiguous evidence id must reject")


def test_evidence_id_replay_rejects_non_replay_evidence(tmp_path: Path) -> None:
    payload = _payload(tenant_id="tenant-a", run_id="run-a")
    payload["result"]["evidence_id"] = "evidence-not-replay"
    _write_raw(tmp_path, payload)

    try:
        get_operator_run_replay_by_evidence_id(
            tenant_id="tenant-a",
            evidence_id="evidence-not-replay",
            root=tmp_path,
        )
    except OperatorRunApiError as exc:
        assert exc.failure_class == "CONTRACT_VIOLATION"
        assert exc.reason == "EVIDENCE_NOT_REPLAYABLE"
    else:
        raise AssertionError("non-replay evidence must reject")


def test_router_exposes_evidence_and_replay_comparison(tmp_path: Path, monkeypatch) -> None:
    original = _payload(tenant_id="tenant-a", run_id="run-original")
    replay = _payload(tenant_id="tenant-a", run_id="run-replay")
    replay["replay_of_run_id"] = "run-original"
    _write_raw(tmp_path, original)
    _write_raw(tmp_path, replay)
    client = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer token", "X-Tenant-ID": "tenant-a"}

    evidence = client.get("/api/aix/runs/run-replay/evidence", headers=headers)
    replay_response = client.get("/api/aix/runs/run-replay/replay-comparison", headers=headers)

    assert evidence.status_code == 200
    assert evidence.json()["data"]["evidence_bundle"]["complete"] is True
    assert replay_response.status_code == 200
    assert replay_response.json()["data"]["duplicate_suppression"]["executed"] is False
