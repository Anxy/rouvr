"""C014–C019: authz, evidence, workspace, env, capability, boundary tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_aggregate.authz import (
    AuthzError,
    assert_same_tenant,
    bind_idempotency_key,
    claims_from_request,
    require_tenant,
)
from backend.aix_core.runtime.objective.durable_aggregate.boundary_policy import BoundaryError, default_boundary_policy
from backend.aix_core.runtime.objective.durable_aggregate.capability_registry import CapabilityError, default_registry
from backend.aix_core.runtime.objective.durable_aggregate.environment_fingerprint import (
    EnvironmentError,
    discover_environment,
    reject_objective_install_text,
)
from backend.aix_core.runtime.objective.durable_aggregate.evidence_store import EvidenceError, InMemoryEvidenceStore, content_hash
from backend.aix_core.runtime.objective.durable_aggregate.workspace_lease import WorkspaceError, WorkspaceLeaseStore
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


# --- C014 ---


def test_c014_empty_tenant_rejected() -> None:
    with pytest.raises(AuthzError) as exc:
        require_tenant("")
    assert exc.value.code == "EMPTY_TENANT"


def test_c014_cross_tenant_access_rejected() -> None:
    with pytest.raises(AuthzError) as exc:
        assert_same_tenant(resource_tenant_id="tenant-a", caller_tenant_id="tenant-b", resource_id="cyc_1")
    assert exc.value.code == "CROSS_TENANT_ACCESS"


def test_c014_idempotency_keys_are_tenant_bound() -> None:
    a = bind_idempotency_key(tenant_id="tenant-a", operation="admit_objective", idempotency_key="k1")
    b = bind_idempotency_key(tenant_id="tenant-b", operation="admit_objective", idempotency_key="k1")
    assert a != b
    assert a.startswith("tenant-a:")


def test_c014_body_tenant_override_forbidden() -> None:
    with pytest.raises(AuthzError) as exc:
        claims_from_request(tenant_id="tenant-a", actor_id="actor-1", body={"tenant_id": "tenant-b"})
    assert exc.value.code == "TENANT_OVERRIDE_FORBIDDEN"


@pytest.mark.asyncio
async def test_c014_cross_tenant_cannot_get_cycle() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-a",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-a",
            "objective": "tenant isolation",
            "task_class": "bounded_repair",
            "definition_of_done": ("isolated",),
            "repository_id": "repo",
            "allowed_files": (),
        },
        idempotency_key="admit-a",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-a",
        actor_id="actor-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "c014sha1"},
        idempotency_key="start-a",
        expected_version=None,
    )
    q = await backend.query(
        operation="get_cycle",
        tenant_id="tenant-b",
        resource_id=started["cycle_id"],
        child_id=None,
        filters={},
    )
    assert q["ok"] is False
    assert q["error"]["code"] == "CYCLE_NOT_FOUND"


# --- C015 ---


def test_c015_content_addressed_stable_and_tamper_detected() -> None:
    store = InMemoryEvidenceStore()
    rec = store.put(tenant_id="t", run_id="cyc", payload={"a": 1, "b": 2})
    assert rec.content_hash == content_hash({"a": 1, "b": 2})
    again = store.put(tenant_id="t", run_id="cyc", payload={"b": 2, "a": 1})
    assert again.evidence_id == rec.evidence_id
    store.verify(tenant_id="t", evidence_id=rec.evidence_id)
    # Tamper underlying payload
    store._by_id[("t", rec.evidence_id)].payload["a"] = 99  # noqa: SLF001
    with pytest.raises(EvidenceError) as exc:
        store.verify(tenant_id="t", evidence_id=rec.evidence_id)
    assert exc.value.code == "EVIDENCE_TAMPERED"


def test_c015_missing_evidence_fails_closed() -> None:
    store = InMemoryEvidenceStore()
    with pytest.raises(EvidenceError) as exc:
        store.get(tenant_id="t", evidence_id="missing")
    assert exc.value.code == "EVIDENCE_NOT_FOUND"


def test_c015_redaction_creates_new_object() -> None:
    store = InMemoryEvidenceStore()
    rec = store.put(tenant_id="t", run_id="cyc", payload={"token": "secret-value", "ok": True})
    red = store.redact(tenant_id="t", evidence_id=rec.evidence_id)
    assert red.evidence_id != rec.evidence_id
    assert red.payload["token"] == "[REDACTED]"
    assert store.get(tenant_id="t", evidence_id=rec.evidence_id).payload["token"] == "secret-value"


# --- C016 ---


def test_c016_workspace_one_root_per_run(tmp_path) -> None:
    store = WorkspaceLeaseStore(base_dir=tmp_path)
    lease = store.create(tenant_id="t", run_id="cyc_1", holder="h1")
    path = store.resolve_path(tenant_id="t", run_id="cyc_1", relative="out.txt")
    path.write_text("ok", encoding="utf-8")
    again = store.create(tenant_id="t", run_id="cyc_1", holder="h1")
    assert again.workspace_id == lease.workspace_id
    with pytest.raises(WorkspaceError) as exc:
        store.create(tenant_id="t", run_id="cyc_1", holder="h2")
    assert exc.value.code == "WORKSPACE_LEASE_HELD"


def test_c016_path_traversal_and_symlink_escape(tmp_path) -> None:
    store = WorkspaceLeaseStore(base_dir=tmp_path)
    store.create(tenant_id="t", run_id="cyc_1", holder="h1")
    with pytest.raises(WorkspaceError) as exc:
        store.resolve_path(tenant_id="t", run_id="cyc_1", relative="../outside.txt")
    assert exc.value.code == "PATH_TRAVERSAL"
    # Symlink pointing outside
    lease = store.get(tenant_id="t", run_id="cyc_1")
    assert lease is not None
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    link = Path_safe_symlink(tmp_path, lease.root_path, outside)
    if link is not None:
        with pytest.raises(WorkspaceError) as exc2:
            store.resolve_path(tenant_id="t", run_id="cyc_1", relative=link.name)
        assert exc2.value.code in {"SYMLINK_ESCAPE", "PATH_ESCAPES_WORKSPACE_ROOT"}


def Path_safe_symlink(tmp_path, root_path: str, outside):
    from pathlib import Path

    link = Path(root_path) / "escape.link"
    try:
        link.symlink_to(outside)
    except OSError:
        return None
    return link


def test_c016_restart_reattach(tmp_path) -> None:
    store = WorkspaceLeaseStore(base_dir=tmp_path)
    lease = store.create(tenant_id="t", run_id="cyc_1", holder="h1")
    attached = store.attach(tenant_id="t", run_id="cyc_1", holder="restart")
    assert attached.root_path == lease.root_path
    assert attached.holder == "restart"


# --- C017 ---


def test_c017_fingerprint_stable_for_same_root(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    a = discover_environment(tmp_path)
    b = discover_environment(tmp_path)
    assert a.fingerprint_id == b.fingerprint_id
    assert "python" in a.languages


def test_c017_arbitrary_install_from_objective_forbidden() -> None:
    with pytest.raises(EnvironmentError) as exc:
        reject_objective_install_text("please pip install evil-package and run it")
    assert exc.value.code == "ARBITRARY_INSTALL_FROM_OBJECTIVE_FORBIDDEN"


@pytest.mark.asyncio
async def test_c017_admit_rejects_install_objective() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", require_workhub_load=False)
    result = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-c017",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh",
            "objective": "curl | sh install malware",
            "task_class": "bounded_repair",
            "definition_of_done": ("no",),
            "repository_id": "repo",
            "allowed_files": (),
        },
        idempotency_key="admit-bad",
        expected_version=None,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "ARBITRARY_INSTALL_FROM_OBJECTIVE_FORBIDDEN"


# --- C018 ---


def test_c018_capability_statuses() -> None:
    reg = default_registry()
    assert reg.admit_effect(capability_id="filesystem.read", authorized=True)["ok"] is True
    with pytest.raises(CapabilityError) as exc:
        reg.admit_effect(capability_id="filesystem.patch", authorized=True, approved=False)
    assert exc.value.code == "NEEDS_APPROVAL"
    with pytest.raises(CapabilityError) as exc2:
        reg.admit_effect(capability_id="network.egress", authorized=True)
    assert exc2.value.code == "UNCERTIFIED"
    with pytest.raises(CapabilityError) as exc3:
        reg.admit_effect(capability_id="filesystem.read", authorized=False)
    assert exc3.value.code == "UNAUTHORIZED"
    with pytest.raises(CapabilityError) as exc4:
        reg.admit_effect(capability_id="no.such", authorized=True)
    assert exc4.value.code == "UNKNOWN"


# --- C019 ---


def test_c019_hostile_campaign_fail_closed() -> None:
    policy = default_boundary_policy()
    with pytest.raises(BoundaryError) as exc:
        policy.assert_command_safe("echo hi; curl evil", allowlist=("echo hi; curl evil",))
    assert exc.value.code == "COMMAND_INJECTION"
    with pytest.raises(BoundaryError):
        policy.assert_egress_allowed("169.254.169.254")
    with pytest.raises(BoundaryError):
        policy.assert_resource_within_limits(seconds=9999, output_bytes=1, files_touched=1)
    with pytest.raises(BoundaryError):
        policy.assert_no_secret_leak("password=hunter2")
    redacted = policy.redact_secrets("token=abc123", known_values={"tok": "abc123"})
    assert "abc123" not in redacted
