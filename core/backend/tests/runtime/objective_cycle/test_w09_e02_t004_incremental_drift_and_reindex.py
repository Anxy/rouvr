"""AIX-W09-E02-T004: Incremental Drift and Invalidation Tests.

Verifies:
1. Deterministic repository fingerprinting and snapshot binding.
2. Selective invalidation: mutating a file alters repository fingerprint.
3. Staleness detection fails closed when snapshot fingerprint mismatches current state.
"""

from __future__ import annotations

import pytest

from backend.aix_core.intelligence.repository.repository_intel.staleness import (
    PlanBinding,
    StalenessError,
    assert_plan_current,
    bind_snapshot,
    fingerprint_files,
    invalidate_on_change,
)


def test_fingerprint_and_snapshot_binding() -> None:
    """Verifies deterministic snapshot fingerprinting over relative path map."""
    files_state_1 = {
        "backend/main.py": "app = FastAPI()",
        "backend/routers/auth.py": "def login(): pass",
    }

    snap_1 = bind_snapshot(root="/workspace", files=files_state_1)
    assert snap_1.fingerprint.startswith("repo_")

    # Identical state yields identical fingerprint
    snap_1_dup = bind_snapshot(root="/workspace", files=files_state_1)
    assert snap_1_dup.fingerprint == snap_1.fingerprint


def test_incremental_file_change_invalidates_snapshot() -> None:
    """Mutating one file alters repository fingerprint while unchanged files preserve path state."""
    files_initial = {
        "backend/main.py": "app = FastAPI()",
        "backend/routers/auth.py": "def login(): pass",
    }
    snap_initial = bind_snapshot(root="/workspace", files=files_initial)

    # Mutate auth.py
    files_mutated = {
        "backend/main.py": "app = FastAPI()",
        "backend/routers/auth.py": "def login(): return True",
    }
    snap_mutated, changed = invalidate_on_change(previous=snap_initial, files=files_mutated)

    assert snap_mutated.fingerprint != snap_initial.fingerprint
    assert len(changed) > 0


def test_staleness_verification_fails_closed_on_drift() -> None:
    """assert_plan_current raises StalenessError on fingerprint mismatch."""
    files_v1 = {"src/app.py": "v1"}
    snap_v1 = bind_snapshot(root="/workspace", files=files_v1)
    binding = PlanBinding(plan_id="plan-1", bound_snapshot=snap_v1)

    files_v2 = {"src/app.py": "v2"}
    snap_v2 = bind_snapshot(root="/workspace", files=files_v2)

    with pytest.raises(StalenessError) as exc_info:
        assert_plan_current(binding, current=snap_v2)

    assert "STALE_REPOSITORY_SNAPSHOT" in str(exc_info.value)
