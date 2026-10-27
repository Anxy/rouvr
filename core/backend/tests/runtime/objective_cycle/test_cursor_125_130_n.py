"""Tests for Cursor #125–130 certification packages."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.experimental.certification_harness import assert_all_levels_defined, harness_plan
from backend.aix_core.runtime.objective.experimental.feature_flags import FeatureFlagError, FeatureFlagRegistry
from backend.aix_core.runtime.executor.experimental.hostile_campaign import HOSTILE_CAMPAIGN_V1, campaign_domains, evaluate_hostile
from backend.aix_core.runtime.executor.experimental.release_provenance import build_provenance
from backend.aix_core.runtime.recovery.experimental.rollback import evaluate_same_boundary_rollback
from backend.aix_core.runtime.executor.experimental.scenario_matrix import assert_matrix_size, matrix_as_dicts


def test_unified_harness_levels() -> None:
    assert_all_levels_defined()
    plan = harness_plan()
    assert {p["level"] for p in plan} == {"unit", "contract", "integration", "live", "browser", "evidence"}


def test_scenario_matrix_has_48() -> None:
    assert_matrix_size()
    rows = matrix_as_dicts()
    assert len(rows) == 48
    assert rows[0]["id"] == 1
    assert rows[-1]["id"] == 48


def test_hostile_campaign_covers_domains() -> None:
    domains = set(campaign_domains())
    assert domains == {"tenant", "identity", "prompt", "tool", "secret", "event", "workflow", "recovery"}
    case = HOSTILE_CAMPAIGN_V1[0]
    assert evaluate_hostile(case, observed="DENY") is True
    assert evaluate_hostile(case, observed="ALLOW") is False


def test_release_provenance_immutable_digest() -> None:
    base = Path(__file__).resolve().parents[4] / ".tmp_prov_test"
    base.mkdir(parents=True, exist_ok=True)
    f = base / "artifact.txt"
    f.write_text("hello", encoding="utf-8")
    rec = build_provenance(git_sha="abc1234", files=[f], signed=False)
    assert rec.immutable is True
    assert len(rec.sbom_digest) == 64
    assert rec.signature_status == "UNSIGNED_LOCAL"


def test_feature_flags_fail_closed_and_no_safety_bypass() -> None:
    reg = FeatureFlagRegistry()
    assert reg.is_enabled("unknown_flag") is False
    assert reg.is_enabled("direct_provider_route", stage_now="on") is False
    with pytest.raises(FeatureFlagError):
        reg.promote("auto_accept_review", "on")
    rolled = reg.rollback("cycle_ui_controls")
    assert rolled.stage == "off"


def test_same_boundary_rollback() -> None:
    ok = evaluate_same_boundary_rollback(
        current={"app": 3, "schema": 2, "workflow": 2, "config": 1, "event": 1},
        target={"app": 2, "schema": 2, "workflow": 1, "config": 1, "event": 1},
        compatibility={
            "app": (1, 2, 3),
            "schema": (2,),
            "workflow": (1, 2),
            "config": (1,),
            "event": (1,),
        },
        target_sha="deadbeef",
    )
    assert ok.allowed is True
    bad = evaluate_same_boundary_rollback(
        current={"app": 3, "schema": 2, "workflow": 2, "config": 1, "event": 1},
        target={"app": 4, "schema": 2, "workflow": 2, "config": 1, "event": 1},
        compatibility={"app": (1, 2, 3), "schema": (2,), "workflow": (2,), "config": (1,), "event": (1,)},
        target_sha="cafebabe",
    )
    assert bad.allowed is False
