from __future__ import annotations

from backend.services.marketplace_contract_service import (
    build_marketplace_install_audit_event,
    validate_marketplace_template_record,
)


def test_validate_marketplace_template_record_passes_with_provenance() -> None:
    result = validate_marketplace_template_record(
        record={
            "template_id": "tpl-1",
            "template_name": "Core Template",
            "template_version": "1.0.0",
            "provenance": {"publisher": "hyeve", "signature": "sig-abc"},
            "compatibility": {"min_map_version": 3, "verticals": ["insurance"]},
        }
    )
    assert result["valid"] is True
    assert result["trust_score"] == 1.0


def test_build_marketplace_install_audit_event_contains_required_fields() -> None:
    event = build_marketplace_install_audit_event(
        tenant_id="default",
        project_id="proj-1",
        actor_id="user-1",
        correlation_id="corr-1",
        template_id="tpl-1",
        template_version="1.0.0",
    )
    assert event["event"] == "aix.marketplace.install"
    assert event["audit_required"] is True
