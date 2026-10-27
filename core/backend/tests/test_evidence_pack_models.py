from __future__ import annotations

import pytest

from backend.models.evidence_pack_models import EVIDENCE_PACK_SCHEMA_VERSION_Current, validate_evidence_pack


def test_validate_evidence_pack_requires_acceptance_mapping() -> None:
    payload = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION_Current,
        "status": "pass",
        "summary": "ok",
        "diff_artifact": "diff.patch",
        "acceptance_mapping": [
            {
                "acceptance_id": "AC-1",
                "acceptance_text": "tests pass",
                "met": True,
                "proof_kind": "pytest",
                "evidence_refs": ["tests.log"],
                "command_refs": ["cmd:pytest"],
                "assertion_summary": "tests passed",
                "skipped_count": 0,
                "warning_count": 0,
            }
        ],
    }
    pack = validate_evidence_pack(payload)
    assert pack.schema_version == EVIDENCE_PACK_SCHEMA_VERSION_Current


def test_validate_evidence_pack_rejects_met_acceptance_without_proof_kind() -> None:
    payload = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION_Current,
        "status": "pass",
        "summary": "ok",
        "diff_artifact": "diff.patch",
        "acceptance_mapping": [
            {
                "acceptance_id": "AC-1",
                "acceptance_text": "tests pass",
                "met": True,
                "evidence_refs": ["tests.log"],
            }
        ],
    }
    with pytest.raises(Exception):
        validate_evidence_pack(payload)


def test_validate_evidence_pack_rejects_empty_acceptance_mapping() -> None:
    payload = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION_Current,
        "status": "pass",
        "summary": "ok",
        "diff_artifact": "diff.patch",
        "acceptance_mapping": [],
    }
    with pytest.raises(Exception):
        validate_evidence_pack(payload)
