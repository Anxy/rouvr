"""Hermetic Stage-5 tests: artifact mapping, staleness, tiers."""

import pytest

from backend.session_factory.knowledge import (
    ARTIFACT_KIND,
    capsule_to_artifact,
    knowledge_status,
    tenant_check,
)
from backend.session_factory.retention import (
    compact_capsule,
    tier_for_age,
)

CAPSULE = {
    "capsule_version": 1,
    "session_id": "env-1",
    "tenant_id": "t",
    "intent": "do x",
    "voice_transcript_ref": "s3://big",
    "lane_checkpoint": "c",
    "lane_tag": "g",
    "persona_bindings": ["worker-aix"],
    "guardrails_hash": "h",
    "created_at": "2026-09-10T00:00:00Z",
    "codeintel_snapshot": {"queried": True, "artifacts": ["a1"]},
    "workhub_snapshot": {"open_mandate_ids": ["m1"]},
}


def test_artifact_mapping_shape() -> None:
    art = capsule_to_artifact(
        CAPSULE, project_id="p", repo_id="r", created_by="worker-aix",
    )
    assert art["artifact_kind"] == ARTIFACT_KIND
    assert art["tenant_id"] == "t"
    meta = art["metadata_json"]
    assert meta["session_id"] == "env-1"
    assert meta["capsule_version"] == 1
    assert meta["artifacts"] == ["a1"]


def test_tenant_isolation_and_staleness() -> None:
    assert tenant_check(session_tenant="t", artifact_tenant="t") is True
    assert tenant_check(session_tenant="t", artifact_tenant="other") is False
    assert tenant_check(session_tenant="", artifact_tenant="t") is False
    assert knowledge_status(capsule_version=1, artifact_version=1) == "current"
    assert knowledge_status(capsule_version=1, artifact_version=2) == "stale"
    assert knowledge_status(capsule_version=2, artifact_version=1) == "ahead"


def test_tiers_and_compaction() -> None:
    assert tier_for_age(age_days=0) == "hot"
    assert tier_for_age(age_days=30) == "hot"
    assert tier_for_age(age_days=31) == "warm"
    assert tier_for_age(age_days=90) == "warm"
    assert tier_for_age(age_days=91) == "cold"
    assert tier_for_age(age_days=366) == "expired"
    with pytest.raises(ValueError):
        tier_for_age(age_days=-1)
    compacted = compact_capsule(CAPSULE)
    assert compacted["compacted"] is True
    assert "voice_transcript_ref" not in compacted
    assert compacted["workhub_snapshot"] == {"open_mandate_ids": ["m1"]}
    assert compacted["codeintel_snapshot"]["artifacts"] == ["a1"]
