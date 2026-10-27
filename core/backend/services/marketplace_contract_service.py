"""Marketplace contract validation and trust/audit payload helpers."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

REQUIRED_TEMPLATE_FIELDS = [
    "template_id",
    "template_name",
    "template_version",
    "provenance",
    "compatibility",
]


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def validate_marketplace_template_record(*, record: dict[str, Any]) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for field in REQUIRED_TEMPLATE_FIELDS:
        if record.get(field) in (None, "", {}):
            violations.append({"code": "MISSING_REQUIRED_FIELD", "field": field})

    provenance = _as_dict(record.get("provenance"))
    if not str(provenance.get("publisher") or "").strip():
        violations.append({"code": "PROVENANCE_PUBLISHER_REQUIRED"})
    if not str(provenance.get("signature") or "").strip():
        violations.append({"code": "PROVENANCE_SIGNATURE_REQUIRED"})

    compatibility = _as_dict(record.get("compatibility"))
    if int(compatibility.get("min_map_version") or 0) <= 0:
        violations.append({"code": "COMPAT_MIN_MAP_VERSION_REQUIRED"})
    allowed_verticals = compatibility.get("verticals") if isinstance(compatibility.get("verticals"), list) else []
    if not allowed_verticals:
        violations.append({"code": "COMPAT_VERTICALS_REQUIRED"})

    trusted = len(violations) == 0
    fingerprint_src = "|".join(
        [
            str(record.get("template_id") or ""),
            str(record.get("template_version") or ""),
            str(provenance.get("publisher") or ""),
            str(provenance.get("signature") or ""),
        ]
    )
    return {
        "valid": trusted,
        "violations": violations,
        "trust_score": 1.0 if trusted else 0.0,
        "record_fingerprint": sha256(fingerprint_src.encode("utf-8")).hexdigest(),
    }


def build_marketplace_install_audit_event(
    *,
    tenant_id: str,
    project_id: str,
    actor_id: str,
    correlation_id: str,
    template_id: str,
    template_version: str,
) -> dict[str, Any]:
    return {
        "event": "aix.marketplace.install",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "actor_id": actor_id,
        "correlation_id": correlation_id,
        "template_id": template_id,
        "template_version": template_version,
        "status": "accepted",
        "audit_required": True,
    }
