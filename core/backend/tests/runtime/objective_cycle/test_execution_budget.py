"""Tests for execution_budget module — counter tracking, loop detectors, thrash prevention, and BLOCKED state enforcement."""

import pytest
from backend.aix_core.runtime.governance.execution_budget import (
    ExecutionBudgetConfig,
    ExecutionBudgetManager,
)


def test_budget_counter_tracking_and_max_exhaustion() -> None:
    """Exploration budget limit exceeded forces objective_status=BLOCKED."""
    config = ExecutionBudgetConfig(max_searches=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)

    res1 = mgr.record_search("search 1", scope="all")
    assert res1.allowed is True
    assert res1.objective_status == "RUNNING"

    res2 = mgr.record_search("search 2", scope="all")
    assert res2.allowed is True
    assert res2.objective_status == "RUNNING"

    res3 = mgr.record_search("search 3", scope="all")
    assert res3.allowed is False
    assert res3.objective_status == "BLOCKED"
    assert res3.reason_code == "EXPLORATION_BUDGET_EXCEEDED"
    assert mgr.summary()["is_blocked"] is True


def test_repeated_identical_search_detection() -> None:
    """Repeated identical searches across multiple distinct queries trip the loop detector."""
    config = ExecutionBudgetConfig(max_identical_searches=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)

    assert mgr.record_search("find retries", scope="src").allowed is True
    assert mgr.record_search("other query", scope="src").allowed is True
    assert mgr.record_search("find retries", scope="src").allowed is True

    # 3rd identical search of the first query while other queries also ran
    res = mgr.record_search("find retries", scope="src")
    assert res.allowed is False
    assert res.objective_status == "BLOCKED"
    assert res.reason_code == "REPEATED_IDENTICAL_SEARCH"


def test_single_query_researches_are_not_an_explore_loop() -> None:
    """Focused text-replace seeding re-runs the same OLD-token search; that is expected."""
    config = ExecutionBudgetConfig(max_identical_searches=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)
    for _ in range(6):
        res = mgr.record_search("AIX live-summary-s1", scope="")
        assert res.allowed is True
        assert res.reason_code is None


def test_repeated_identical_read_detection() -> None:
    """Identical re-reads are a loop only when more than one file is in play."""
    config = ExecutionBudgetConfig(max_identical_reads=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)

    assert mgr.record_read("config.py", file_hash="hash_a").allowed is True
    assert mgr.record_read("other.py", file_hash="hash_b").allowed is True
    assert mgr.record_read("config.py", file_hash="hash_a").allowed is True

    # 3rd identical read of config.py, after another file was also read
    res = mgr.record_read("config.py", file_hash="hash_a")
    assert res.allowed is False
    assert res.objective_status == "BLOCKED"
    assert res.reason_code == "REPEATED_IDENTICAL_READ"


def test_single_bound_file_rereads_are_not_an_explore_loop() -> None:
    """Focused discovery + allowed-files reload of one mutation target is expected."""
    config = ExecutionBudgetConfig(max_identical_reads=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)
    for _ in range(5):
        res = mgr.record_read("CronJobController.php", file_hash="hash_a")
        assert res.allowed is True
        assert res.reason_code is None


def test_bound_multi_file_rereads_are_not_an_explore_loop() -> None:
    """Repair rounds reload the same approved multi-file set; that is not thrash."""
    config = ExecutionBudgetConfig(max_identical_reads=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)
    mgr.set_bound_mutation_paths(
        [
            "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py",
            "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py",
        ]
    )
    for _ in range(4):
        assert mgr.record_read(
            "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py",
            file_hash="hash_a",
        ).allowed is True
        assert mgr.record_read(
            "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py",
            file_hash="hash_b",
        ).allowed is True


def test_unbound_multi_file_identical_reads_still_trip() -> None:
    """Without a bound set, thrashing one file while reading others still blocks."""
    config = ExecutionBudgetConfig(max_identical_reads=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)
    assert mgr.record_read("config.py", file_hash="hash_a").allowed is True
    assert mgr.record_read("other.py", file_hash="hash_b").allowed is True
    assert mgr.record_read("config.py", file_hash="hash_a").allowed is True
    res = mgr.record_read("config.py", file_hash="hash_a")
    assert res.allowed is False
    assert res.reason_code == "REPEATED_IDENTICAL_READ"


def test_manifest_probes_do_not_trip_single_file_reread_guard() -> None:
    """Live README edit: focused discovery also reads package.json; that is not multi-file thrash."""
    config = ExecutionBudgetConfig(max_identical_reads=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)
    assert mgr.record_read("package.json", file_hash="pkg").allowed is True
    for _ in range(5):
        res = mgr.record_read("README.md", file_hash="readme")
        assert res.allowed is True
        assert res.reason_code is None


def test_repeated_clarification_detection() -> None:
    """Repeated identical clarification question triggers REPEATED_CLARIFICATION."""
    config = ExecutionBudgetConfig(max_identical_clarifications=1)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)

    assert mgr.record_clarification("Which database table?").allowed is True

    # 2nd identical question attempt
    res = mgr.record_clarification("Which database table?")
    assert res.allowed is False
    assert res.objective_status == "BLOCKED"
    assert res.reason_code == "REPEATED_CLARIFICATION"


def test_plan_revision_budget_exceeded() -> None:
    """Repeated plan revision with identical plan hash triggers PLAN_REVISION_BUDGET_EXCEEDED."""
    config = ExecutionBudgetConfig(max_identical_plan_revisions=2)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)

    assert mgr.record_plan_revision("hash_plan_x").allowed is True
    assert mgr.record_plan_revision("hash_plan_x").allowed is True

    # 3rd identical plan revision attempt
    res = mgr.record_plan_revision("hash_plan_x")
    assert res.allowed is False
    assert res.objective_status == "BLOCKED"
    assert res.reason_code == "PLAN_REVISION_BUDGET_EXCEEDED"


def test_budget_exhaustion_is_never_completed() -> None:
    """Invariants check: budget exhaustion status is ALWAYS BLOCKED (never COMPLETED)."""
    config = ExecutionBudgetConfig(max_commands=1)
    mgr = ExecutionBudgetManager(cycle_id="cyc_1", objective_id="obj_1", config=config)

    mgr.record_command("pytest test1.py")
    res_exceeded = mgr.record_command("pytest test2.py")

    assert res_exceeded.allowed is False
    assert res_exceeded.objective_status != "COMPLETED"
    assert res_exceeded.objective_status == "BLOCKED"
