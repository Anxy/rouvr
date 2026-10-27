from __future__ import annotations

from backend.aix_core.runtime.objective.experimental.api_compatibility import (
    COMPATIBILITY_ROUTES,
    CompatibilityReadiness,
    CompatibilityState,
    compatibility_headers,
    evaluate_compatibility_sunset,
)


def _ready(**changes) -> CompatibilityReadiness:
    payload = {
        "replacement_api_live": True,
        "frontend_bff_migrated": True,
        "sdk_cli_migrated": True,
        "no_legacy_traffic_days": 90,
        "contract_tests_green": True,
        "operator_runbook_updated": True,
        "named_human_removal_approval": True,
    }
    payload.update(changes)
    return CompatibilityReadiness(**payload)


def test_all_seven_mvp_routes_have_explicit_successors() -> None:
    assert len(COMPATIBILITY_ROUTES) == 7
    assert all(route.inclusion_class == "COMPAT" for route in COMPATIBILITY_ROUTES)
    assert all(route.lifecycle_mutation_authority is False for route in COMPATIBILITY_ROUTES)


def test_review_decision_successor_remains_workhub_human_authority() -> None:
    route = next(item for item in COMPATIBILITY_ROUTES if item.legacy_path.endswith("/review-decision"))

    assert route.replacement_path == "WORKHUB_HUMAN_REVIEW_DISPOSITION"


def test_compatibility_headers_are_machine_readable() -> None:
    headers = compatibility_headers(COMPATIBILITY_ROUTES[0])

    assert headers["Deprecation"] == "true"
    assert headers["X-AIX-Inclusion-Class"] == "COMPAT"
    assert 'rel="successor-version"' in headers["Link"]


def test_sunset_stays_active_until_every_gate_passes() -> None:
    decision = evaluate_compatibility_sunset(_ready(frontend_bff_migrated=False))

    assert decision.state is CompatibilityState.ACTIVE
    assert decision.removal_allowed is False
    assert decision.reason_codes == ("FRONTEND_BFF_NOT_MIGRATED",)


def test_sunset_requires_named_human_removal_approval() -> None:
    decision = evaluate_compatibility_sunset(_ready(named_human_removal_approval=False))

    assert decision.state is CompatibilityState.REMOVAL_APPROVAL_REQUIRED
    assert decision.removal_allowed is False


def test_sunset_allows_removal_only_after_all_gates_and_human_approval() -> None:
    decision = evaluate_compatibility_sunset(_ready())

    assert decision.state is CompatibilityState.DEPRECATION_ELIGIBLE
    assert decision.removal_allowed is True


def test_legacy_traffic_window_is_at_least_90_days() -> None:
    decision = evaluate_compatibility_sunset(_ready(no_legacy_traffic_days=89))

    assert decision.reason_codes == ("LEGACY_TRAFFIC_WINDOW_INCOMPLETE",)
