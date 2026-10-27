from __future__ import annotations

from backend.services.template_pack_governance_service import validate_template_pack_compatibility


def test_validate_template_pack_compatibility_passes_valid_pack() -> None:
    result = validate_template_pack_compatibility(
        pack_definition={
            "vertical": "insurance",
            "capabilities": {"model_routing": {"allowed_providers": ["slavx"]}},
        },
        active_map_definition={
            "capabilities": {
                "model_routing": {"allowed_providers": ["slavx", "onlineai"]},
            }
        },
        active_map_version=4,
        min_map_version=3,
        max_map_version=6,
    )
    assert result["valid"] is True
    assert result["violations"] == []


def test_validate_template_pack_compatibility_detects_provider_and_version_violation() -> None:
    result = validate_template_pack_compatibility(
        pack_definition={
            "vertical": "",
            "capabilities": {"model_routing": {"allowed_providers": ["onlineai"]}},
        },
        active_map_definition={
            "capabilities": {
                "model_routing": {"allowed_providers": ["slavx"]},
            }
        },
        active_map_version=2,
        min_map_version=3,
        max_map_version=5,
    )
    assert result["valid"] is False
    codes = {item["code"] for item in result["violations"]}
    assert "MAP_VERSION_TOO_LOW" in codes
    assert "VERTICAL_REQUIRED" in codes
    assert "PROVIDER_NOT_ALLOWED_BY_MAP" in codes
