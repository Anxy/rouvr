"""Router ingress tests for lane-ops #23 (auth, binding gate, readback)."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import session_factory_router as router_module
from backend.routers.session_factory_router import router
from backend.session_factory import stores
from backend.session_factory.workhub_binding import BindingRefused


PLAN = "9f" * 32


def selection(**overrides):
    body = dict(
        tenant_id="t",
        objective_id="o",
        cycle_id="c",
        selection_id="s",
        mandate_id="m",
        session_id="sess",
        worker_id="worker",
        accepted=True,
        workflow_version=1,
        candidate_sha="c820b5d119bd",
        plan_sha256=PLAN,
        correlation_id="corr-1",
        workhub_binding=dict(
            schema_version=1,
            tenant_id="t",
            project_id="lane-ops",
            executor_sub="worker",
        ),
    )
    body.update(overrides)
    return body


class Bridge:
    def __init__(self):
        self.queued = []

    async def enqueue(self, event_id, event_type, payload, **kwargs):
        self.queued.append((event_id, event_type, payload))
        return {"event_id": event_id, "status": "QUEUED"}

    async def dequeue(self, **kwargs):
        raise AssertionError("not used")

    async def ack(self, **kwargs):
        raise AssertionError("not used")

    async def retry(self, **kwargs):
        raise AssertionError("not used")

    async def tombstone(self, **kwargs):
        raise AssertionError("not used")


class Store:
    def __init__(self, record=None, attempt_ids=None):
        self._record = record
        self._attempt_ids = attempt_ids

    async def get_activation(self, activation_id):
        return self._record

    async def get_result_by_activation(self, activation_id):
        # Ambiguity is checked before absence: a store flagging
        # distinct rows must surface 409 even when no single row is
        # staged (previously this returned None first and read 200).
        if getattr(self, "_terminal_ambiguous", False):
            raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
        record = getattr(self, "_terminal_result", None)
        if record is None:
            return None
        return dict(record)

    async def find_attempt_activation(self, tenant_id, project_id,
                                      workhub_attempt_id):
        if self._attempt_ids is None:
            return []
        return list(self._attempt_ids)


async def principal(_authorization):
    return SimpleNamespace(tenant_id="t", actor_id="worker")


async def refuse_all(_binding):
    raise BindingRefused("NOPE")


async def accept_all(binding):
    return dict(binding)


def client(**overrides):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[stores.get_activation_bridge] = (
        lambda: overrides.get("bridge", Bridge())
    )
    app.dependency_overrides[stores.get_activation_authenticator] = (
        lambda: principal
    )
    app.dependency_overrides[stores.get_binding_verifier] = (
        lambda: overrides.get("verifier", accept_all)
    )
    app.dependency_overrides[stores.get_activation_store] = (
        lambda: overrides.get("store", Store())
    )
    return TestClient(app, raise_server_exceptions=False)


def test_valid_selection_queues_but_unverified_refuses():
    response = client().post(
        "/v1/aix/activation/selection",
        json=selection(),
        headers={"Authorization": "Bearer x"},
    )
    assert response.status_code == 202, response.text
    refused = client(verifier=refuse_all).post(
        "/v1/aix/activation/selection",
        json=selection(),
        headers={"Authorization": "Bearer x"},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "NOPE"


def test_missing_binding_rejected_before_enqueue():
    bridge = Bridge()
    response = client(bridge=bridge).post(
        "/v1/aix/activation/selection",
        json=selection(workhub_binding={}),
        headers={"Authorization": "Bearer x"},
    )
    assert response.status_code == 422
    assert bridge.queued == []


def test_readback_unknown_is_404_and_known_returns_record():
    record = {"tenant_id": "t", "worker_id": "worker", "status": "STARTED"}
    response = client().get(
        "/v1/aix/activation/nope", headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == 404
    response = client(store=Store(record)).get(
        "/v1/aix/activation/act-x", headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "STARTED"


def test_executor_mismatch_is_forbidden_before_verify():
    body = selection()
    body["workhub_binding"] = dict(
        body["workhub_binding"], executor_sub="intruder")
    response = client().post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer whatever"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "SESSION_ACTIVATION_EXECUTOR_MISMATCH"


def test_by_attempt_readback_unknown_ambiguous_and_known():
    auth = {"Authorization": "Bearer whatever"}
    query = "tenant_id=t&project_id=lane-ops&workhub_attempt_id=attempt-1"
    response = client().get(
        "/v1/aix/activation/by-attempt?" + query, headers=auth)
    assert response.status_code == 404
    record = {"tenant_id": "t", "worker_id": "worker", "status": "STARTED"}
    store = Store(record, attempt_ids=["act-1", "act-2"])
    response = client(store=store).get(
        "/v1/aix/activation/by-attempt?" + query, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == "SESSION_ACTIVATION_AMBIGUOUS"
    store = Store(record, attempt_ids=["act-1"])
    response = client(store=store).get(
        "/v1/aix/activation/by-attempt?" + query, headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "STARTED"
    assert "activation_result" not in response.json()


def test_by_attempt_surfaces_terminal_result_proof():
    """Lane-ops #23: the claimed result row rides the readback for proof."""
    auth = {"Authorization": "Bearer [REDACTED]"}
    query = "tenant_id=t&project_id=lane-ops&workhub_attempt_id=attempt-1"
    record = {"tenant_id": "t", "worker_id": "worker", "status": "SUCCEEDED"}
    store = Store(record, attempt_ids=["act-1"])
    store._terminal_result = {
        "result_id": "r", "activation_id": "act-1", "worker_id": "worker",
        "tenant_id": "t", "status": "SUCCEEDED", "evidence_ref": "ev",
    }
    response = client(store=store).get(
        "/v1/aix/activation/by-attempt?" + query, headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["activation_result"]["evidence_ref"] == "ev"


def test_by_attempt_ambiguous_terminal_result_is_409():
    """Distinct result rows refuse: supervision must hold, not choose."""
    auth = {"Authorization": "Bearer [REDACTED]"}
    query = "tenant_id=t&project_id=lane-ops&workhub_attempt_id=attempt-1"
    record = {"tenant_id": "t", "worker_id": "worker", "status": "SUCCEEDED"}
    store = Store(record, attempt_ids=["act-1"])
    store._terminal_ambiguous = True
    response = client(store=store).get(
        "/v1/aix/activation/by-attempt?" + query, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == "ACTIVATION_RESULT_AMBIGUOUS"


def test_router_module_imports():
    assert router_module.SelectionAccepted is not None


def _proof(selection_block):
    async def verify(binding):
        return {**binding, "selection": dict(selection_block)}

    return verify


def _full_body(**overrides):
    body = selection()
    block = dict(
        tenant_id="t",
        project_id="lane-ops",
        task_id="task-1",
        task_number=23,
        objective_id="o",
        cycle_id="c",
        selection_id="s",
        mandate_id="m",
        session_id="sess",
        worker_id="worker",
        candidate_sha="c820b5d119bd",
        plan_sha256=PLAN,
        correlation_id="corr-1",
    )
    block.update(overrides)
    body["workhub_binding"] = {
        "schema_version": 1,
        "tenant_id": "t",
        "project_id": "lane-ops",
        "task_id": "task-1",
        "task_number": 23,
        "assignment_id": "assign-1",
        "assignment_revision": 2,
        "executor_sub": "worker",
        "workhub_attempt_id": "attempt-1",
        "attempt_version": 1,
        "controller_attempt_id": "canary-23-a1",
        "lease_epoch": 5,
        "lease_holder": "worker",
        "contract_ref": "workhub://tasks/23/execution-contracts/1",
        "authority_ref": "route-1",
        "plan_digest": PLAN,
        "route_digest": "r" * 64,
        "selection": block,
    }
    return body, block


def test_outer_candidate_drift_against_proof_is_409_before_enqueue():
    # Lane-ops #23 R2: an approved nested binding cannot carry a
    # substituted outer candidate into the inbox.
    bridge = Bridge()
    body, block = _full_body()
    body["candidate_sha"] = "ffffffffffffffffffffffffffffffffffffffff"
    response = client(bridge=bridge, verifier=_proof(block)).post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer x"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "SELECTION_BINDING_MISMATCH"
    assert bridge.queued == []


def test_outer_matching_proof_enqueues():
    bridge = Bridge()
    body, block = _full_body()
    response = client(bridge=bridge, verifier=_proof(block)).post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer x"},
    )
    assert response.status_code == 202, response.text
    assert len(bridge.queued) == 1


def test_binding_proof_mismatch_is_409_before_enqueue():
    # The binding's own inner selection must equal the proven selection.
    bridge = Bridge()
    body, block = _full_body()
    tampered = dict(block, candidate_sha="deadbeef00")
    response = client(bridge=bridge, verifier=_proof(tampered)).post(
        "/v1/aix/activation/selection",
        json=body,
        headers={"Authorization": "Bearer x"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "SELECTION_BINDING_MISMATCH"
    assert bridge.queued == []
