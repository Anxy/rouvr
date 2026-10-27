"""Hermetic Stage-4 tests: verdict transitions and repair bound."""

import pytest

from backend.session_factory.verdict import record_verdict


def test_direct_pass_no_repair() -> None:
    assert record_verdict(attempt_count=0, max_attempts=3, outcome="pass") == "VERIFIED"


def test_fail_repairs_then_passes() -> None:
    assert record_verdict(attempt_count=0, max_attempts=3, outcome="fail") == "REPAIR"
    assert record_verdict(attempt_count=1, max_attempts=3, outcome="pass") == "VERIFIED"


def test_exhausted_budget_blocks() -> None:
    assert record_verdict(attempt_count=2, max_attempts=3, outcome="fail") == "BLOCKED"
    assert record_verdict(attempt_count=5, max_attempts=3, outcome="fail") == "BLOCKED"


def test_genuine_blocker_never_passes() -> None:
    assert record_verdict(attempt_count=0, max_attempts=3, outcome="blocked") == "BLOCKED"


def test_unknown_outcome_rejected() -> None:
    with pytest.raises(ValueError):
        record_verdict(attempt_count=0, max_attempts=3, outcome="greenish")
