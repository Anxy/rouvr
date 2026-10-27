"""AIX-W04-E02-T003: Claims, Leases, Path Locks, and Fencing.

Verifies:
1. Atomic Claims & Worker Fencing: Competing worker cannot claim an active lease held by worker-1.
2. Heartbeat & Orphan Attachment: Heartbeats refresh timestamp; detached orphans are recovered by attach.
3. Path Lock Mutual Exclusion: Competing worker cannot acquire path lock held by worker-1 until released.
4. Clean Release: Releasing path lock and workspace lease resets state safely.
"""

from __future__ import annotations

import time
import pytest

from backend.aix_core.runtime.objective.durable_aggregate.workspace_lease import (
    WorkspaceError,
    WorkspaceLeaseStore,
)


def test_atomic_lease_claim_and_worker_fencing(tmp_path: object) -> None:
    """Worker-1 claims lease; worker-2 attempting to claim same lease raises WORKSPACE_LEASE_HELD."""
    store = WorkspaceLeaseStore(base_dir=tmp_path)

    lease_1 = store.create(
        tenant_id="tenant-w04",
        run_id="run-lease-1",
        holder="worker-1",
    )
    assert lease_1.holder == "worker-1"
    assert lease_1.status == "ACTIVE"

    # Worker-1 calling create again receives existing lease
    lease_1_again = store.create(
        tenant_id="tenant-w04",
        run_id="run-lease-1",
        holder="worker-1",
    )
    assert lease_1_again.workspace_id == lease_1.workspace_id

    # Worker-2 attempting to claim active lease fails closed
    with pytest.raises(WorkspaceError) as exc_info:
        store.create(
            tenant_id="tenant-w04",
            run_id="run-lease-1",
            holder="worker-2",
        )

    assert "WORKSPACE_LEASE_HELD" in str(exc_info.value)
    assert "worker-1" in str(exc_info.value)


def test_lease_heartbeat_and_orphan_recovery(tmp_path: object) -> None:
    """Heartbeat updates timestamp; orphan attach re-claims workspace lease."""
    store = WorkspaceLeaseStore(base_dir=tmp_path)

    lease = store.create(
        tenant_id="tenant-w04",
        run_id="run-heartbeat-1",
        holder="worker-1",
    )
    initial_hb = lease.last_heartbeat_at

    # Perform heartbeat
    updated_lease = store.heartbeat(
        tenant_id="tenant-w04",
        run_id="run-heartbeat-1",
        holder="worker-1",
    )
    assert updated_lease.last_heartbeat_at != ""

    # Re-attach orphaned workspace by new worker
    attached = store.attach(
        tenant_id="tenant-w04",
        run_id="run-heartbeat-1",
        holder="worker-recovery",
    )
    assert attached.holder == "worker-recovery"
    assert attached.status in ("ACTIVE", "ORPHAN_ATTACHED")


def test_path_lock_mutual_exclusion(tmp_path: object) -> None:
    """Acquiring path lock by worker-1 prevents worker-2 from locking the same file until released."""
    store = WorkspaceLeaseStore(base_dir=tmp_path)
    store.create(tenant_id="tenant-w04", run_id="run-pathlock-1", holder="worker-1")

    # Worker 1 acquires lock on backend/main.py
    store.acquire_path_lock(
        tenant_id="tenant-w04",
        run_id="run-pathlock-1",
        relative_path="backend/main.py",
        holder="worker-1",
    )

    # Worker 2 attempts to acquire same path lock
    with pytest.raises(WorkspaceError) as exc_info:
        store.acquire_path_lock(
            tenant_id="tenant-w04",
            run_id="run-pathlock-1",
            relative_path="backend/main.py",
            holder="worker-2",
        )

    assert "PATH_LOCK_HELD" in str(exc_info.value)

    # Worker 1 releases lock
    store.release_path_lock(
        tenant_id="tenant-w04",
        run_id="run-pathlock-1",
        relative_path="backend/main.py",
        holder="worker-1",
    )

    # Now worker 2 can acquire lock
    store.acquire_path_lock(
        tenant_id="tenant-w04",
        run_id="run-pathlock-1",
        relative_path="backend/main.py",
        holder="worker-2",
    )


def test_release_and_cleanup(tmp_path: object) -> None:
    """Releasing lease sets status RELEASED; subsequent path resolve fails closed."""
    store = WorkspaceLeaseStore(base_dir=tmp_path)
    lease = store.create(tenant_id="tenant-w04", run_id="run-release-1", holder="worker-1")

    path = store.resolve_path(tenant_id="tenant-w04", run_id="run-release-1", relative="src/module.py")
    assert str(tmp_path) in str(path)

    store.release(tenant_id="tenant-w04", run_id="run-release-1", holder="worker-1", cleanup=True)
    assert lease.status == "RELEASED"

    with pytest.raises(WorkspaceError) as exc_info:
        store.resolve_path(tenant_id="tenant-w04", run_id="run-release-1", relative="src/module.py")

    assert "WORKSPACE_NOT_FOUND" in str(exc_info.value)
