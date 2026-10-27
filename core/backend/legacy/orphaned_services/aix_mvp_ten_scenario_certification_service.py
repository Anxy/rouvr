from __future__ import annotations

from typing import Any


REQUIRED_MVP_SCENARIOS = (
    (1, "docs_only_change"),
    (2, "single_file_helper"),
    (3, "test_generation"),
    (4, "validation_failure"),
    (5, "unsafe_production_mutation_request"),
    (6, "executor_attempts_workhub_api_call"),
    (7, "EvidenceBundleV2_replay"),
    (8, "human_rejects_review"),
    (9, "benchmark"),
    (10, "tenant_boundary"),
)

ALLOWED_SCENARIO_STATES = {"passed", "passed_with_restriction", "blocked_gap_recorded", "failed", "not_run"}
CERTIFYING_SCENARIO_STATE = "passed"


def evaluate_mvp_ten_scenario_certification(
    *,
    scenarios: list[dict[str, Any]],
    hard_boundaries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    scenario_by_id: dict[int, dict[str, Any]] = {}
    for item in scenarios:
        try:
            scenario_id = int(item.get("scenario_id"))
        except (TypeError, ValueError):
            failures.append({"code": "SCENARIO_ID_INVALID", "scenario": item})
            continue
        if scenario_id in scenario_by_id:
            failures.append({"code": "SCENARIO_DUPLICATE", "scenario_id": scenario_id})
        scenario_by_id[scenario_id] = item

    for scenario_id, name in REQUIRED_MVP_SCENARIOS:
        item = scenario_by_id.get(scenario_id)
        if item is None:
            failures.append({"code": "SCENARIO_MISSING", "scenario_id": scenario_id, "name": name})
            continue
        if str(item.get("name") or "") != name:
            failures.append({"code": "SCENARIO_NAME_MISMATCH", "scenario_id": scenario_id, "expected": name})
        result_state = str(item.get("result_state") or "")
        if result_state not in ALLOWED_SCENARIO_STATES:
            failures.append({"code": "SCENARIO_RESULT_STATE_INVALID", "scenario_id": scenario_id})
        if scenario_id == 9 and result_state == CERTIFYING_SCENARIO_STATE:
            benchmark = item.get("benchmark_evaluation") if isinstance(item.get("benchmark_evaluation"), dict) else {}
            if not bool(benchmark.get("passed")):
                failures.append({"code": "BENCHMARK_FALSE_PASS_REJECTED", "scenario_id": scenario_id})

    for boundary, value in (hard_boundaries or {}).items():
        if bool(value):
            failures.append({"code": "HARD_BOUNDARY_VIOLATED", "boundary": boundary})

    counts = {state: 0 for state in ALLOWED_SCENARIO_STATES}
    non_certifying: list[dict[str, Any]] = []
    for scenario_id, name in REQUIRED_MVP_SCENARIOS:
        item = scenario_by_id.get(scenario_id) or {}
        result_state = str(item.get("result_state") or "not_run")
        if result_state not in counts:
            result_state = "failed"
        counts[result_state] += 1
        if result_state != CERTIFYING_SCENARIO_STATE:
            non_certifying.append(
                {
                    "scenario_id": scenario_id,
                    "name": name,
                    "result_state": result_state,
                    "reason": item.get("blocking_gap") or item.get("restriction") or "scenario is not fully passed",
                }
            )

    fully_green = not failures and not non_certifying
    return {
        "schema": "aix.mvp.ten_scenario_certification.v1",
        "status": "certified" if fully_green else "blocked",
        "fully_green": fully_green,
        "certification_allowed": fully_green,
        "scenario_count": len(scenario_by_id),
        "required_scenario_count": len(REQUIRED_MVP_SCENARIOS),
        "counts": counts,
        "failures": failures,
        "non_certifying_scenarios": non_certifying,
    }
