from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aix_orchestrator_v2.models import AixOrchestratorV2RunState
from backend.aix_orchestrator_v2.state_store import AixOrchestratorV2StateStore
from backend.routers import aix_mvp_product_router as router_module
from backend.routers.aix_mvp_product_router import router
from backend.services.aix_evidence_bundle_v2_service import build_signed_evidence_bundle_v2
from backend.services.aix_operator_run_api_service import OperatorRunApiError, OperatorRunReadResult


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _admission_payload(**overrides):
    payload = {
        "id": "task-1",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "repo_ref": "repo-a",
        "task_kind": "docs_only_change",
        "description": "Update bounded docs.",
        "risk_hint": "low",
        "user_id": "user-a",
        "created_at": "2026-07-23T00:00:00Z",
        "bounded_scope": ["docs/current/example.md"],
        "validation_commands": ["python -m pytest -q"],
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def _execution_payload(**overrides):
    payload = _admission_payload(
        project_id="project-a",
        task_number=42,
        workspace_root="/srv/rouvr/proposals/task-1",
        repo_key="hyeve-aix-service",
        mode="inline",
        writeback=False,
    )
    payload.update(overrides)
    return payload


def _headers(**overrides):
    headers = {
        "Authorization": "Bearer test",
        "X-Tenant-ID": "tenant-a",
        "X-Actor-ID": "actor-a",
    }
    headers.update(overrides)
    return headers


def _review_decision_payload(**overrides):
    payload = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "task_id": "task-1",
        "run_id": "run-1",
        "review_packet_version": "packet-v1",
        "decision_id": "decision-1",
        "decision": "approve",
        "human_actor_id": "actor-a",
        "actor_type": "human",
        "actor_role": "human",
        "rationale": "Reviewed evidence and accepted the bounded result.",
    }
    payload.update(overrides)
    return payload


def _save_reviewable_state(tmp_path, monkeypatch) -> AixOrchestratorV2StateStore:
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)
    state = AixOrchestratorV2RunState(
        task_id="task-1",
        run_id="run-1",
        status="success",
        current_phase="verified",
        packet={"runtime": {"tenant_id": "tenant-a"}, "review_packet": {"version": "packet-v1"}},
        task_payload={"id": "task-1", "project_id": "project-a"},
    )
    store.save(state)
    return store


def _signed_evidence_bundle_v2() -> dict:
    return build_signed_evidence_bundle_v2(
        tenant_id="tenant-a",
        actor_id="aix-orchestrator-v2",
        project_id="project-a",
        task_id="task-1",
        run_id="run-1",
        task_payload={
            "workspace_ref": {"workspace_id": "workspace-a", "repo_ref": "repo-a"},
            "metadata": {"task_kind": "docs_only_change", "risk_level": "low"},
        },
        execution={
            "status": "completed",
            "changed_files": ["docs/current/example.md"],
            "run_result": {"status": "completed"},
        },
        verification={
            "classification": "success",
            "summary": "passed",
            "checks": [{"name": "declared_validation", "passed": True}],
        },
        read_contract={"active_contract_id": "contract-1", "active_contract_hash": "sha256:contract"},
        evidence_bundle={
            "bundle_id": "aix-evidence-run-1",
            "artifact_refs": [
                {"kind": "diff", "sha256": "sha256:diff"},
                {"kind": "validation_output", "sha256": "sha256:validation"},
            ],
        },
        created_at="2026-07-23T00:00:00Z",
    )


def _save_review_packet_state(tmp_path, monkeypatch, *, tamper_signature: bool = False) -> AixOrchestratorV2StateStore:
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)
    evidence_bundle_v2 = _signed_evidence_bundle_v2()
    if tamper_signature:
        evidence_bundle_v2["diff_proof"]["changed_files"].append("docs/current/other.md")
    state = AixOrchestratorV2RunState(
        task_id="task-1",
        run_id="run-1",
        status="success",
        current_phase="verified",
        packet={
            "runtime": {"tenant_id": "tenant-a"},
            "review_packet": {"version": "packet-v1"},
            "evidence_bundle_v2": evidence_bundle_v2,
        },
        task_payload={"id": "task-1", "project_id": "project-a"},
    )
    store.save(state)
    return store


def test_admit_requires_authorization() -> None:
    response = _client().post("/aix/tasks/admit", json=_admission_payload())

    assert response.status_code == 401
    assert response.json()["detail"]["sub_code"] == "AUTH_MISSING"


def test_admit_requires_trusted_tenant_and_actor_headers() -> None:
    client = _client()

    missing_tenant = client.post(
        "/aix/tasks/admit",
        headers={"Authorization": "Bearer test", "X-Actor-ID": "actor-a"},
        json=_admission_payload(),
    )
    missing_actor = client.post(
        "/aix/tasks/admit",
        headers={"Authorization": "Bearer test", "X-Tenant-ID": "tenant-a"},
        json=_admission_payload(),
    )

    assert missing_tenant.status_code == 400
    assert missing_tenant.json()["detail"]["sub_code"] == "TENANT_ID_REQUIRED"
    assert missing_actor.status_code == 400
    assert missing_actor.json()["detail"]["sub_code"] == "ACTOR_ID_REQUIRED"


def test_admit_rejects_tenant_mismatch() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(**{"X-Tenant-ID": "tenant-b"}),
        json=_admission_payload(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["sub_code"] == "TENANT_MISMATCH"


def test_admit_rejects_unsupported_task_kind() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(task_kind="multi_file_refactor"),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "UNSUPPORTED_TASK_KIND"


def test_admit_accepts_supported_task_kind_without_execution_authority() -> None:
    response = _client().post("/aix/tasks/admit", headers=_headers(), json=_admission_payload())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema_version"] == "aix.mvp.task_admission.v1"
    assert data["task_id"] == "task-1"
    assert data["tenant_id"] == "tenant-a"
    assert data["admission"] == "admitted"
    assert data["execution_authorized"] is False
    assert data["review_state"] == "review_pending"
    assert data["authority"]["workhub_lifecycle_authority"] is False
    assert data["authority"]["executor_self_retry_allowed"] is False
    assert data["authority"]["production_mutation_allowed"] is False


def test_admit_accepts_hyeve_tenant_header_alias() -> None:
    headers = _headers()
    headers.pop("X-Tenant-ID")
    headers["X-Hyeve-Tenant-Id"] = "tenant-a"

    response = _client().post("/aix/tasks/admit", headers=headers, json=_admission_payload())

    assert response.status_code == 200
    assert response.json()["data"]["tenant_id"] == "tenant-a"


def test_admit_rejects_explicit_production_mutation_request() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(
            task_kind="docs_only_change",
            metadata={"target_environment": "production", "production_mutation_requested": True},
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "PRODUCTION_MUTATION_FORBIDDEN"


def test_admit_rejects_single_file_helper_without_single_file_scope() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(
            task_kind="single_file_helper",
            bounded_scope=["backend/helper.py", "backend/other.py"],
            metadata={"max_diff_lines": 25},
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "SINGLE_FILE_SCOPE_REQUIRED"


def test_admit_rejects_single_file_helper_without_loc_bound() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(task_kind="single_file_helper", bounded_scope=["backend/helper.py"]),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "SINGLE_FILE_LOC_BOUND_REQUIRED"


def test_admit_rejects_single_file_helper_over_50_loc() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(
            task_kind="single_file_helper",
            bounded_scope=["backend/helper.py"],
            metadata={"max_diff_lines": 51},
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "SINGLE_FILE_LOC_BOUND_EXCEEDED"


def test_admit_accepts_bounded_single_file_helper() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(
            task_kind="single_file_helper",
            bounded_scope=["backend/helper.py"],
            metadata={"max_diff_lines": 50},
        ),
    )

    assert response.status_code == 200
    assert response.json()["data"]["task_kind"] == "single_file_helper"


def test_admit_rejects_test_generation_source_path_scope() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(
            task_kind="test_generation",
            bounded_scope=["backend/service.py"],
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "TEST_GENERATION_TEST_PATH_REQUIRED"


def test_admit_rejects_test_generation_source_mutation_authority() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(
            task_kind="test_generation",
            bounded_scope=["backend/tests/test_service.py"],
            metadata={"source_mutation_allowed": True},
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "TEST_GENERATION_SOURCE_MUTATION_FORBIDDEN"


def test_admit_accepts_test_generation_test_path_scope() -> None:
    response = _client().post(
        "/aix/tasks/admit",
        headers=_headers(),
        json=_admission_payload(
            task_kind="test_generation",
            bounded_scope=["backend/tests/test_service.py"],
            metadata={"source_mutation_allowed": False},
        ),
    )

    assert response.status_code == 200
    assert response.json()["data"]["task_kind"] == "test_generation"


def test_status_requires_tenant_header() -> None:
    response = _client().get("/aix/tasks/task-1/status", headers={"Authorization": "Bearer test"})

    assert response.status_code == 400
    assert response.json()["detail"]["sub_code"] == "TENANT_ID_REQUIRED"


def test_status_missing_task_returns_404_without_workhub_lifecycle_claim() -> None:
    response = _client().get(
        "/aix/tasks/task-missing/status",
        headers={"Authorization": "Bearer test", "X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["sub_code"] == "TASK_ID_NOT_FOUND"


def test_evidence_missing_task_returns_404() -> None:
    response = _client().get(
        "/aix/tasks/task-missing/evidence",
        headers={"Authorization": "Bearer test", "X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["sub_code"] == "TASK_ID_NOT_FOUND"


def test_replay_requires_trusted_tenant_and_actor_headers() -> None:
    client = _client()

    missing_tenant = client.post(
        "/aix/replay/evidence-1",
        headers={"Authorization": "Bearer test", "X-Actor-ID": "actor-a"},
        json={},
    )
    missing_actor = client.post(
        "/aix/replay/evidence-1",
        headers={"Authorization": "Bearer test", "X-Tenant-ID": "tenant-a"},
        json={},
    )

    assert missing_tenant.status_code == 400
    assert missing_tenant.json()["detail"]["sub_code"] == "TENANT_ID_REQUIRED"
    assert missing_actor.status_code == 400
    assert missing_actor.json()["detail"]["sub_code"] == "ACTOR_ID_REQUIRED"


def test_replay_evidence_not_found_returns_404_without_leak(monkeypatch) -> None:
    def _not_found(**kwargs):
        assert kwargs == {"tenant_id": "tenant-a", "evidence_id": "evidence-missing"}
        return OperatorRunReadResult(
            status="not_found",
            payload={
                "success": False,
                "failure_class": "EVIDENCE_NOT_FOUND",
                "data": {"evidence_id": "evidence-missing"},
            },
        )

    monkeypatch.setattr(router_module, "get_operator_run_replay_by_evidence_id", _not_found)

    response = _client().post("/aix/replay/evidence-missing", headers=_headers(), json={})

    assert response.status_code == 404
    assert response.json()["detail"]["sub_code"] == "EVIDENCE_ID_NOT_FOUND"
    assert "tenant-b" not in str(response.json())


def test_replay_rejects_ambiguous_evidence_id(monkeypatch) -> None:
    def _ambiguous(**kwargs):
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="EVIDENCE_ID_AMBIGUOUS",
            detail="evidence_id resolves to more than one run for the requested tenant.",
        )

    monkeypatch.setattr(router_module, "get_operator_run_replay_by_evidence_id", _ambiguous)

    response = _client().post("/aix/replay/evidence-duplicate", headers=_headers(), json={})

    assert response.status_code == 409
    assert response.json()["detail"]["sub_code"] == "EVIDENCE_ID_AMBIGUOUS"


def test_replay_rejects_non_replay_evidence(monkeypatch) -> None:
    def _not_replay(**kwargs):
        raise OperatorRunApiError(
            failure_class="CONTRACT_VIOLATION",
            reason="EVIDENCE_NOT_REPLAYABLE",
            detail="evidence_id does not reference a replay run record.",
        )

    monkeypatch.setattr(router_module, "get_operator_run_replay_by_evidence_id", _not_replay)

    response = _client().post("/aix/replay/evidence-not-replay", headers=_headers(), json={})

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "EVIDENCE_NOT_REPLAYABLE"


def test_replay_returns_product_shape_without_workhub_lifecycle_or_execution_authority(monkeypatch) -> None:
    def _success(**kwargs):
        assert kwargs == {"tenant_id": "tenant-a", "evidence_id": "evidence-replay-1"}
        return OperatorRunReadResult(
            status="ok",
            payload={
                "success": True,
                "data": {
                    "schema_version": "aix.operator_run.evidence_replay.v1",
                    "authority": {"source": "aix_run_ledger"},
                    "evidence_id": "evidence-replay-1",
                    "replay_mode": "read_only_comparison",
                    "executed": False,
                    "original_run_id": "run-original",
                    "replay_run_id": "run-replay",
                    "drift_status": "match",
                    "compared_fields": [],
                    "duplicate_suppression": {
                        "mode": "read_only",
                        "executed": False,
                        "duplicate_execution": False,
                    },
                },
            },
        )

    monkeypatch.setattr(router_module, "get_operator_run_replay_by_evidence_id", _success)

    response = _client().post("/aix/replay/evidence-replay-1", headers=_headers(), json={})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema_version"] == "aix.mvp.evidence_replay.v1"
    assert data["tenant_id"] == "tenant-a"
    assert data["requested_by_actor_id"] == "actor-a"
    assert data["evidence_id"] == "evidence-replay-1"
    assert data["executed"] is False
    assert data["duplicate_suppression"]["duplicate_execution"] is False
    assert data["authority"]["workhub_lifecycle_authority"] is False
    assert data["authority"]["evidence_is_acceptance"] is False
    assert data["authority"]["production_mutation_allowed"] is False


def test_status_returns_latest_tenant_scoped_orchestrator_state(tmp_path, monkeypatch) -> None:
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)
    state = AixOrchestratorV2RunState(
        task_id="task-1",
        run_id="run-1",
        status="blocked",
        current_phase="planner",
        packet={"runtime": {"tenant_id": "tenant-a"}},
        task_payload={"id": "task-1"},
    )
    store.save(state)

    response = _client().get(
        "/aix/tasks/task-1/status",
        headers={"Authorization": "Bearer test", "X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema_version"] == "aix.mvp.task_status.v1"
    assert data["task_id"] == "task-1"
    assert data["tenant_id"] == "tenant-a"
    assert data["run_id"] == "run-1"
    assert data["authority"]["workhub_lifecycle_authority"] is False
    assert data["authority"]["evidence_is_acceptance"] is False


def test_status_cross_tenant_returns_404_without_leaking_state(tmp_path, monkeypatch) -> None:
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)
    state = AixOrchestratorV2RunState(
        task_id="task-1",
        run_id="run-1",
        packet={"runtime": {"tenant_id": "tenant-a"}},
        task_payload={"id": "task-1", "secret": "tenant-a-only"},
    )
    store.save(state)

    response = _client().get(
        "/aix/tasks/task-1/status",
        headers={"Authorization": "Bearer test", "X-Tenant-ID": "tenant-b"},
    )

    assert response.status_code == 404
    assert "tenant-a-only" not in str(response.json())


def test_execute_rejects_workhub_writeback() -> None:
    response = _client().post(
        "/aix/tasks/execute",
        headers=_headers(),
        json=_execution_payload(writeback=True),
    )

    # C004-T03: execute path retired before writeback policy checks.
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "MVP_EXECUTE_RETIRED"


def test_execute_rejects_unsupported_task_kind_before_orchestrator(monkeypatch) -> None:
    calls: list[dict] = []

    async def _unexpected_run_inline(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("orchestrator must not be called")

    monkeypatch.setattr(router_module.AixOrchestratorV2Service, "run_inline", _unexpected_run_inline)

    response = _client().post(
        "/aix/tasks/execute",
        headers=_headers(),
        json=_execution_payload(task_kind="multi_file_refactor"),
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "MVP_EXECUTE_RETIRED"
    assert calls == []


def test_execute_rejects_task_kind_policy_before_orchestrator(monkeypatch) -> None:
    calls: list[dict] = []

    async def _unexpected_run_inline(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("orchestrator must not be called")

    monkeypatch.setattr(router_module.AixOrchestratorV2Service, "run_inline", _unexpected_run_inline)

    response = _client().post(
        "/aix/tasks/execute",
        headers=_headers(),
        json=_execution_payload(
            task_kind="test_generation",
            bounded_scope=["backend/service.py"],
        ),
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "MVP_EXECUTE_RETIRED"
    assert calls == []


def test_execute_invokes_orchestrator_inline_with_writeback_disabled(monkeypatch) -> None:
    calls: list[dict] = []

    async def _fake_run_inline(self, **kwargs):
        calls.append(kwargs)
        return AixOrchestratorV2RunState(
            task_id="task-1",
            run_id="run-exec-1",
            status="success",
            current_phase="verified",
            packet={"runtime": {"tenant_id": kwargs["tenant_id"], "actor_id": kwargs["actor_id"]}},
            task_payload={"id": "task-1"},
        )

    monkeypatch.setattr(router_module.AixOrchestratorV2Service, "run_inline", _fake_run_inline)

    response = _client().post("/aix/tasks/execute", headers=_headers(), json=_execution_payload())

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "MVP_EXECUTE_RETIRED"
    assert calls == []


def test_review_packet_renders_from_signed_evidence_without_workhub_lifecycle_authority(tmp_path, monkeypatch) -> None:
    _save_review_packet_state(tmp_path, monkeypatch)

    response = _client().get("/aix/tasks/task-1/review-packet", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema_version"] == "aix.review_packet.workhub_handoff.v1"
    assert data["version"] == "packet-v1"
    assert data["evidence_signature_status"] == "passed"
    assert data["summary"]["accepted"] is False
    assert data["summary"]["recommendation"] == "ready_for_human_review"
    assert data["workhub_render_contract"]["workhub_lifecycle_owner"] is True
    assert data["authority_limits"]["aix_mutates_workhub_lifecycle"] is False
    assert data["handoff_validation"]["passed"] is True
    assert data["authority"]["workhub_lifecycle_authority"] is False


def test_review_packet_rejects_missing_signed_evidence(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)

    response = _client().get("/aix/tasks/task-1/review-packet", headers=_headers())

    assert response.status_code == 404
    assert response.json()["detail"]["sub_code"] == "SIGNED_EVIDENCE_BUNDLE_V2_NOT_FOUND"


def test_review_packet_rejects_tampered_signed_evidence(tmp_path, monkeypatch) -> None:
    _save_review_packet_state(tmp_path, monkeypatch, tamper_signature=True)

    response = _client().get("/aix/tasks/task-1/review-packet", headers=_headers())

    assert response.status_code == 422
    assert response.json()["detail"]["sub_code"] == "REVIEW_PACKET_SIGNED_EVIDENCE_REQUIRED"


def test_review_decision_requires_human_actor_header(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)

    response = _client().post(
        "/aix/tasks/task-1/review-decision",
        headers={"Authorization": "Bearer test", "X-Tenant-ID": "tenant-a"},
        json=_review_decision_payload(),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["sub_code"] == "ACTOR_ID_REQUIRED"


def test_review_decision_rejects_body_actor_mismatch(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)

    response = _client().post(
        "/aix/tasks/task-1/review-decision",
        headers=_headers(),
        json=_review_decision_payload(human_actor_id="actor-b"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["sub_code"] == "HUMAN_ACTOR_MISMATCH"


def test_review_decision_rejects_non_human_actor_roles(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)

    for actor_type in ("executor", "service", "automated", "persona"):
        response = _client().post(
            "/aix/tasks/task-1/review-decision",
            headers=_headers(),
            json=_review_decision_payload(actor_type=actor_type),
        )
        assert response.status_code == 403
        assert response.json()["detail"]["sub_code"] == "HUMAN_REVIEW_REQUIRED"


def test_review_decision_rejects_cross_tenant_without_leaking_state(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)

    response = _client().post(
        "/aix/tasks/task-1/review-decision",
        headers=_headers(**{"X-Tenant-ID": "tenant-b"}),
        json=_review_decision_payload(tenant_id="tenant-b"),
    )

    assert response.status_code == 404
    assert response.json()["detail"]["sub_code"] == "TASK_ID_NOT_FOUND"
    assert "tenant-a" not in str(response.json())


def test_review_decision_rejects_stale_run_or_packet_version(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)

    stale_run = _client().post(
        "/aix/tasks/task-1/review-decision",
        headers=_headers(),
        json=_review_decision_payload(run_id="run-stale"),
    )
    stale_packet = _client().post(
        "/aix/tasks/task-1/review-decision",
        headers=_headers(),
        json=_review_decision_payload(review_packet_version="packet-stale"),
    )

    assert stale_run.status_code == 409
    assert stale_run.json()["detail"]["sub_code"] == "STALE_REVIEW_RUN"
    assert stale_packet.status_code == 409
    assert stale_packet.json()["detail"]["sub_code"] == "STALE_REVIEW_PACKET"


def test_review_decision_records_aix_review_state_without_workhub_lifecycle_authority(tmp_path, monkeypatch) -> None:
    store = _save_reviewable_state(tmp_path, monkeypatch)

    response = _client().post(
        "/aix/tasks/task-1/review-decision",
        headers=_headers(),
        json=_review_decision_payload(decision="request_changes"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema_version"] == "aix.mvp.review_decision.v1"
    assert data["task_id"] == "task-1"
    assert data["review_state"] == "changes_requested"
    assert data["idempotent_replay"] is False
    assert data["authority"]["workhub_lifecycle_authority"] is False
    state = store.get_latest_by_task_id("task-1")
    assert state is not None
    assert state.validator_ledger["mvp_review_decision"]["review_state"] == "changes_requested"
    assert state.validator_ledger["mvp_review_decision"]["workhub_lifecycle_mutation"] is False


def test_review_decision_duplicate_same_payload_is_idempotent(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)
    client = _client()

    first = client.post("/aix/tasks/task-1/review-decision", headers=_headers(), json=_review_decision_payload())
    second = client.post("/aix/tasks/task-1/review-decision", headers=_headers(), json=_review_decision_payload())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["idempotent_replay"] is False
    assert second.json()["data"]["idempotent_replay"] is True
    assert second.json()["data"]["review_state"] == "review_accepted"


def test_review_decision_duplicate_different_payload_is_rejected(tmp_path, monkeypatch) -> None:
    _save_reviewable_state(tmp_path, monkeypatch)
    client = _client()

    first = client.post("/aix/tasks/task-1/review-decision", headers=_headers(), json=_review_decision_payload())
    second = client.post(
        "/aix/tasks/task-1/review-decision",
        headers=_headers(),
        json=_review_decision_payload(decision="reject"),
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["sub_code"] == "DUPLICATE_DECISION_CONFLICT"
