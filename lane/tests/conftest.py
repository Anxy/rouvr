import pytest
# These tests exercise the full canary controller (tools/canary_lane.py), which is
# not part of the public cut.
_NEEDS_CANARY_LANE = {
    "test_canary_lane_wires_verifier_through_adapter",
    "test_cli_recovery_runs_before_continuous_halt_and_never_launches",
}
def pytest_collection_modifyitems(config, items):
    m = pytest.mark.skip(reason="requires private canary controller (not in public cut)")
    for it in items:
        if it.name in _NEEDS_CANARY_LANE:
            it.add_marker(m)
