"""AIX Feature 12 (Packet 04) — Unit tests for Durable Gap Framework (GapV1)."""

from pathlib import Path
import pytest

from backend.aix_core.runtime.verification.gap import (
    GapStore,
    GapV1,
)
from backend.aix_core.runtime.verification.objective_truth import (
    derive_canonical_truth,
)
from backend.aix_core.runtime.verification.objective_verification_contract import (
    derive_ovc,
)
from backend.aix_core.runtime.verification.objective_verifier import (
    ObjectiveVerifier,
)


def test_gap_v1_creation_and_blocking_check():
    gap = GapV1(
        gap_id="gap-001",
        objective_id="obj-1",
        verification_id="v-1",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite persistence authority",
        observed="PostgreSQL detected without SQLite authority",
        severity="BLOCKER",
        status="OPEN",
    )
    assert gap.is_blocking()
    assert gap.repair_attempt_count == 0


def test_gap_v1_lifecycle_transitions():
    gap = GapV1(
        gap_id="gap-002",
        objective_id="obj-1",
        verification_id="v-1",
        category="RUNTIME_FAILURE",
        expected="Server starts on port 8000",
        observed="Port 8000 connection refused",
        severity="BLOCKER",
        status="OPEN",
    )

    gap.transition_to_in_repair()
    assert gap.status == "IN_REPAIR"
    assert gap.repair_attempt_count == 1
    assert gap.is_blocking()

    # Verifier closes the gap
    gap.close_by_verifier(verification_id="v-2")
    assert gap.status == "CLOSED"
    assert not gap.is_blocking()
    assert gap.provenance.get("closed_by_verification_id") == "v-2"


def test_gap_store_filtering():
    store = GapStore()
    gap1 = GapV1(
        gap_id="g1",
        objective_id="obj-100",
        verification_id="v-1",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite",
        observed="Postgres",
        severity="BLOCKER",
        status="OPEN",
    )
    gap2 = GapV1(
        gap_id="g2",
        objective_id="obj-100",
        verification_id="v-1",
        category="RUNTIME_FAILURE",
        expected="Auth",
        observed="Login 401",
        severity="HIGH",
        status="OPEN",
    )
    gap3 = GapV1(
        gap_id="g3",
        objective_id="obj-100",
        verification_id="v-1",
        category="SYNTAX_ERROR",
        expected="Clean syntax",
        observed="SyntaxError",
        severity="BLOCKER",
        status="CLOSED",
    )

    store.put_gap(gap1)
    store.put_gap(gap2)
    store.put_gap(gap3)

    assert len(store.get_gaps_for_objective("obj-100")) == 3
    # Only gap1 is a blocker with status OPEN/IN_REPAIR
    blocking = store.get_open_blocking_gaps("obj-100")
    assert len(blocking) == 1
    assert blocking[0].gap_id == "g1"
    assert store.count_blocking_gaps("obj-100") == 1


def test_ov002_fail_creates_gap_with_objective_deviation(tmp_path: Path):
    app_file = tmp_path / "app.py"
    app_file.write_text(
        "DATABASE_URL = 'postgresql://localhost:5432/db'\n",
        encoding="utf-8",
    )

    objective = "Build FastAPI + SQLite Expense Tracker"
    truth = derive_canonical_truth(objective, objective_id="obj_ov_gap")
    ovc = derive_ovc(truth)

    verifier = ObjectiveVerifier()
    v_res, v_dec, gaps = verifier.verify_objective(ovc=ovc, workspace_path=tmp_path)

    assert len(gaps) > 0
    sqlite_gap = next((g for g in gaps if g.ov_id == "OV-002"), None)
    assert sqlite_gap is not None
    assert sqlite_gap.category == "OBJECTIVE_DEVIATION"
    assert sqlite_gap.severity == "BLOCKER"
    assert sqlite_gap.status == "OPEN"
