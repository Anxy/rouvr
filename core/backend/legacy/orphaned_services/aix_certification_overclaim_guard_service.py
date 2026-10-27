from __future__ import annotations

from typing import Any


FORBIDDEN_CAPABILITY_CLAIMS = {
    "production_mutation": "AIX MVP does not have production mutation authority.",
    "workhub_lifecycle_mutation": "WorkHub remains lifecycle owner; AIX may not mutate WorkHub lifecycle.",
    "auto_accept": "Human review remains required; AIX evidence is not auto-acceptance.",
    "review_bypass": "AIX may not bypass human review.",
    "broad_autonomy": "AIX MVP is restricted governed execution, not broad autonomy.",
    "repair_fallback_runtime": "Repair/fallback runtime remains outside MVP certification.",
    "unsupported_task_class_expansion": "Unsupported task classes require a future explicit sprint.",
    "direct_provider_routing": "Provider calls must remain behind the governed Hyeve route.",
}

CERTIFICATION_CLAIM_LEVELS = {"mvp_certified", "full_closure", "production_ready"}
PRODUCTION_CLAIM_LEVELS = {"production_ready"}
NON_PRODUCTION_CLAIM_LEVELS = {"development_evidence_only", "restricted_non_production_pilot_candidate"}
REQUIRED_CURRENT_EVIDENCE_LEVELS = {"local", "vps_staging", "live_vps"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "passed", "certified"}
    return bool(value)


def _add_failure(failures: list[dict[str, Any]], *, code: str, message: str, field: str) -> None:
    failures.append({"code": code, "message": message, "field": field})


def evaluate_certification_overclaim_guard(claim_packet: dict[str, Any]) -> dict[str, Any]:
    """Reject certification claims that exceed current-source evidence and MVP boundaries."""

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    claim_level = str(claim_packet.get("claim_level") or "").strip()
    if not claim_level:
        _add_failure(
            failures,
            code="CLAIM_LEVEL_MISSING",
            message="Certification claim level is required.",
            field="claim_level",
        )
    elif claim_level not in CERTIFICATION_CLAIM_LEVELS | NON_PRODUCTION_CLAIM_LEVELS:
        _add_failure(
            failures,
            code="CLAIM_LEVEL_UNSUPPORTED",
            message="Claim level is not part of the approved closure vocabulary.",
            field="claim_level",
        )

    capabilities = _as_dict(claim_packet.get("claimed_capabilities"))
    for field, message in FORBIDDEN_CAPABILITY_CLAIMS.items():
        if _bool(capabilities.get(field)):
            _add_failure(failures, code="FORBIDDEN_CAPABILITY_OVERCLAIM", message=message, field=f"claimed_capabilities.{field}")

    evidence_levels = {
        str(item).strip()
        for item in _as_list(claim_packet.get("evidence_levels"))
        if str(item).strip()
    }
    if not _bool(claim_packet.get("current_source_verified")):
        _add_failure(
            failures,
            code="CURRENT_SOURCE_NOT_VERIFIED",
            message="Closure claims require current-source proof, not historical artifacts alone.",
            field="current_source_verified",
        )
    missing_evidence = REQUIRED_CURRENT_EVIDENCE_LEVELS - evidence_levels
    if claim_level in CERTIFICATION_CLAIM_LEVELS and missing_evidence:
        _add_failure(
            failures,
            code="CERTIFICATION_EVIDENCE_LEVEL_MISSING",
            message="Certification claims require local, VPS staging, and live VPS evidence to be labelled.",
            field="evidence_levels",
        )
    if claim_level in PRODUCTION_CLAIM_LEVELS and "production" not in evidence_levels:
        _add_failure(
            failures,
            code="PRODUCTION_EVIDENCE_MISSING",
            message="Production readiness cannot be claimed without production-specific evidence.",
            field="evidence_levels",
        )

    ten_scenario = _as_dict(claim_packet.get("ten_scenario_certification"))
    if claim_level in CERTIFICATION_CLAIM_LEVELS:
        if not _bool(ten_scenario.get("certification_allowed")):
            _add_failure(
                failures,
                code="TEN_SCENARIO_CERTIFICATION_NOT_GREEN",
                message="MVP certification requires all ten scenarios to be fully passed.",
                field="ten_scenario_certification.certification_allowed",
            )
        if str(ten_scenario.get("status") or "") != "certified":
            _add_failure(
                failures,
                code="TEN_SCENARIO_STATUS_NOT_CERTIFIED",
                message="Scenario certification status must be certified for certification claims.",
                field="ten_scenario_certification.status",
            )

    benchmark = _as_dict(claim_packet.get("benchmark_evaluation"))
    if claim_level in CERTIFICATION_CLAIM_LEVELS and not _bool(benchmark.get("passed")):
        _add_failure(
            failures,
            code="BENCHMARK_CERTIFICATION_NOT_GREEN",
            message="MVP certification remains blocked until benchmark evidence is fully measured and passed.",
            field="benchmark_evaluation.passed",
        )

    ops_dashboard = _as_dict(claim_packet.get("ops_dashboard_certification"))
    if claim_level in CERTIFICATION_CLAIM_LEVELS and not _bool(ops_dashboard.get("passed")):
        _add_failure(
            failures,
            code="OPS_DASHBOARD_CERTIFICATION_NOT_GREEN",
            message="Required dashboard metrics must be certified before closure claims.",
            field="ops_dashboard_certification.passed",
        )
    if str(ops_dashboard.get("status") or "") == "passed_with_visible_gaps":
        warnings.append({"code": "OPS_DASHBOARD_VISIBLE_GAPS", "field": "ops_dashboard_certification.status"})

    historical_artifacts = _as_list(claim_packet.get("historical_artifact_claims"))
    if historical_artifacts and not _bool(claim_packet.get("current_source_verified")):
        _add_failure(
            failures,
            code="HISTORICAL_ARTIFACTS_USED_AS_CURRENT_PROOF",
            message="Historical restricted artifacts may inform review but cannot replace current-source proof.",
            field="historical_artifact_claims",
        )

    caveats = [
        str(item).strip()
        for item in _as_list(claim_packet.get("remaining_not_certified"))
        if str(item).strip()
    ]
    if claim_level in CERTIFICATION_CLAIM_LEVELS and caveats:
        _add_failure(
            failures,
            code="CERTIFICATION_CAVEATS_REMAIN",
            message="Certification claims cannot keep unresolved not-certified capabilities.",
            field="remaining_not_certified",
        )
    elif caveats:
        warnings.append({"code": "REMAINING_NOT_CERTIFIED_RECORDED", "items": caveats})

    passed = not failures
    return {
        "schema": "aix.certification_overclaim_guard.v1",
        "status": "allowed" if passed else "blocked",
        "passed": passed,
        "claim_level": claim_level or None,
        "failures": failures,
        "warnings": warnings,
        "forbidden_capability_claims": dict(FORBIDDEN_CAPABILITY_CLAIMS),
    }
