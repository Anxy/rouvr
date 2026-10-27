"""AC-25: GapStore survives process restart via durable JSON backend."""

from pathlib import Path

from backend.aix_core.runtime.verification.gap import GapStore, GapV1


def test_gap_persistence_survives_restart(tmp_path: Path):
    persist = tmp_path / "gaps.json"
    store1 = GapStore(persist_path=persist)
    gap = GapV1(
        gap_id="gap-102",
        objective_id="obj_01M1B6WVFSNS",
        verification_id="v-1",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite persistence authority",
        observed="PostgreSQL/psycopg hardcoded",
        severity="BLOCKER",
        status="OPEN",
    )
    store1.put_gap(gap)
    store1.save()
    assert persist.exists()

    # Simulate process restart: new store instance loads from disk
    store2 = GapStore(persist_path=persist)
    loaded = store2.load("obj_01M1B6WVFSNS")
    assert len(loaded) == 1
    assert loaded[0].gap_id == "gap-102"
    assert loaded[0].status == "OPEN"
    assert store2.count_open("obj_01M1B6WVFSNS") == 1
    assert store2.count_blocking_gaps("obj_01M1B6WVFSNS") == 1
