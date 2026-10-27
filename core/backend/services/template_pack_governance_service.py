"""Template pack governance checks for map compatibility and version constraints."""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def validate_template_pack_compatibility(
    *,
    pack_definition: dict[str, Any],
    active_map_definition: dict[str, Any],
    active_map_version: int,
    min_map_version: int | None = None,
    max_map_version: int | None = None,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []

    if min_map_version is not None and active_map_version < int(min_map_version):
        violations.append(
            {
                "code": "MAP_VERSION_TOO_LOW",
                "expected_min": int(min_map_version),
                "observed": int(active_map_version),
            }
        )
    if max_map_version is not None and active_map_version > int(max_map_version):
        violations.append(
            {
                "code": "MAP_VERSION_TOO_HIGH",
                "expected_max": int(max_map_version),
                "observed": int(active_map_version),
            }
        )

    pack_vertical = str(pack_definition.get("vertical") or "").strip().lower()
    if not pack_vertical:
        violations.append({"code": "VERTICAL_REQUIRED"})

    pack_caps = _as_dict(pack_definition.get("capabilities"))
    map_caps = _as_dict(active_map_definition.get("capabilities"))
    for capability_name, pack_cap in pack_caps.items():
        if capability_name not in map_caps:
            violations.append(
                {
                    "code": "CAPABILITY_NOT_IN_MAP",
                    "capability": str(capability_name),
                }
            )
            continue
        pack_cap_dict = _as_dict(pack_cap)
        map_cap = _as_dict(map_caps.get(capability_name))
        pack_allowed = {
            str(item).strip().lower()
            for item in (pack_cap_dict.get("allowed_providers") or [])
            if str(item).strip()
        }
        map_allowed = {
            str(item).strip().lower() for item in (map_cap.get("allowed_providers") or []) if str(item).strip()
        }
        if pack_allowed and not pack_allowed.issubset(map_allowed):
            violations.append(
                {
                    "code": "PROVIDER_NOT_ALLOWED_BY_MAP",
                    "capability": str(capability_name),
                    "requested": sorted(list(pack_allowed)),
                    "allowed": sorted(list(map_allowed)),
                }
            )

    return {
        "valid": len(violations) == 0,
        "active_map_version": int(active_map_version),
        "violations": violations,
    }
