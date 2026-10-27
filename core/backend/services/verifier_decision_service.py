from __future__ import annotations

from typing import Any

from backend.models.verifier_decision_models import VERIFIER_DECISION_SCHEMA_VERSION_Current, validate_verifier_decision


def _normalize_decision(decision: str) -> str:
    value = str(decision or "").strip().lower().replace(" ", "_")
    if value in {"verified", "pass", "ok"}:
        return "verified"
    if value in {"needsfix", "needs_fix", "fail", "failed"}:
        return "needs_fix"
    raise ValueError(f"VERIFIER_DECISION_INVALID unknown decision={decision!r}")


def build_verifier_decision(
    *,
    decision: str,
    summary: str,
    reasons: list[dict[str, Any]] | None = None,
    acceptance_mapping: list[dict[str, Any]] | None = None,
    patch_refs: list[str] | None = None,
    test_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    reason_code: str | None = None,
    recovery_kind: str | None = None,
    requeueable: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    normalized_decision = _normalize_decision(decision)
    acceptance_rows = list(acceptance_mapping or [])
    require_acceptance_mapping = bool(metadata.get("require_acceptance_mapping"))
    if normalized_decision == "verified" and require_acceptance_mapping:
        if not acceptance_rows:
            raise ValueError("VERIFIER_DECISION_INVALID verified requires non-empty acceptance_mapping")
        unmet = [row for row in acceptance_rows if not bool(row.get("met"))]
        if unmet:
            raise ValueError("VERIFIER_DECISION_INVALID verified requires all acceptance_mapping items met")
    normalized_patch_refs = [str(x) for x in list(patch_refs or []) if str(x).strip()]
    normalized_test_refs = [str(x) for x in list(test_refs or []) if str(x).strip()]
    normalized_evidence_refs: list[str] = []
    for candidate in [*(evidence_refs or []), *normalized_patch_refs, *normalized_test_refs]:
        value = str(candidate or "").strip()
        if value and value not in normalized_evidence_refs:
            normalized_evidence_refs.append(value)
    normalized_reason_code = str(reason_code or metadata.get("semantic_drift_code") or "").strip() or (
        "VERIFIED" if normalized_decision == "verified" else "VERIFIER_NEEDS_FIX"
    )
    failed_acceptance_items = _build_failed_acceptance_items(
        reasons=list(reasons or []),
        acceptance_mapping=acceptance_rows,
    )
    payload = {
        "schema_version": VERIFIER_DECISION_SCHEMA_VERSION_Current,
        "decision": normalized_decision,
        "reason_code": normalized_reason_code,
        "summary": summary,
        "reasons": list(reasons or []),
        "failed_acceptance_items": failed_acceptance_items,
        "acceptance_mapping": acceptance_rows,
        "patch_refs": normalized_patch_refs,
        "test_refs": normalized_test_refs,
        "evidence_refs": normalized_evidence_refs,
        "recovery_kind": str(recovery_kind or metadata.get("recovery_kind") or "").strip() or None,
        "requeueable": bool(metadata.get("requeueable")) if requeueable is None else bool(requeueable),
        "capability_contract": {
            "reviewer_tier": "verifier",
            "allowed_actions": [
                "evaluate_acceptance_contract",
                "attach_bounded_evidence",
                "emit_verified_or_needs_fix",
            ],
            "forbidden_actions": [
                "mutate_code",
                "rewrite_task_packet",
                "close_parent_without_structured_decision",
            ],
            "recovery_route": "onlineai_or_plannerai",
        },
        "metadata": metadata,
    }
    validated = validate_verifier_decision(payload)
    result = validated.model_dump(mode="python")
    result["actionable_items_count"] = len(validated.reasons)
    return result


def _build_failed_acceptance_items(
    *,
    reasons: list[dict[str, Any]],
    acceptance_mapping: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reason_by_acceptance: dict[str, dict[str, Any]] = {}
    for reason in reasons:
        acceptance_id = str(reason.get("acceptance_id") or "").strip()
        if acceptance_id and acceptance_id not in reason_by_acceptance:
            reason_by_acceptance[acceptance_id] = dict(reason)
    failed_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in acceptance_mapping:
        acceptance_id = str(row.get("acceptance_id") or "").strip()
        if not acceptance_id or bool(row.get("met")) or acceptance_id in seen:
            continue
        matched_reason = reason_by_acceptance.get(acceptance_id) or {}
        failed_items.append(
            {
                "acceptance_id": acceptance_id,
                "message": str(
                    matched_reason.get("message") or row.get("summary") or "acceptance criterion not satisfied"
                ).strip(),
                "file_path": str(matched_reason.get("file_path") or "").strip() or None,
                "line": int(matched_reason.get("line")) if str(matched_reason.get("line") or "").isdigit() else None,
            }
        )
        seen.add(acceptance_id)
    for acceptance_id, matched_reason in reason_by_acceptance.items():
        if acceptance_id in seen:
            continue
        failed_items.append(
            {
                "acceptance_id": acceptance_id,
                "message": str(matched_reason.get("message") or "acceptance criterion not satisfied").strip(),
                "file_path": str(matched_reason.get("file_path") or "").strip() or None,
                "line": int(matched_reason.get("line")) if str(matched_reason.get("line") or "").isdigit() else None,
            }
        )
    return failed_items
