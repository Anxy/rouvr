"""Offline TypedDict intro for bound Python helpers."""

from __future__ import annotations

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.source_patch_codegen import (
    local_fallback_python_typeddict_intro,
)


def test_typeddict_intro_rewrites_dict_annotated_helper() -> None:
    report = DiscoveryReport(
        config_files={
            "backend/tests/helpers.py": (
                'def bump_version(feature: dict) -> dict:\n'
                '    result = feature.copy()\n'
                '    result["version"] += 1\n'
                "    return result\n"
            )
        }
    )
    objective = (
        "introduce a FeatureFlag TypedDict with keys name (str) and version (int), "
        "and change bump_version to accept FeatureFlag and return FeatureFlag"
    )
    result = local_fallback_python_typeddict_intro(
        report, objective, preferred_path="backend/tests/helpers.py"
    )
    assert result is not None
    assert len(result.edits) == 1
    new = result.edits[0].new_content or ""
    assert "class FeatureFlag(TypedDict):" in new
    assert "name: str" in new
    assert "version: int" in new
    assert "def bump_version(feature: FeatureFlag) -> FeatureFlag:" in new
    assert 'result["version"] += 1' in new
