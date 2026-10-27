from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models.command_allowlist_models import (
    COMMAND_ALLOWLIST_SCHEMA_VERSION_Current,
    validate_command_allowlist_profile,
)


def test_validate_command_allowlist_profile_accepts_exact_and_prefix_rules() -> None:
    payload = {
        "schema_version": COMMAND_ALLOWLIST_SCHEMA_VERSION_Current,
        "profile_id": "ACP-CODER-L2-CURRENT",
        "rules": [
            {"mode": "prefix", "argv": ["python"]},
            {"mode": "exact", "argv": ["git", "status"]},
        ],
    }
    profile = validate_command_allowlist_profile(payload)
    assert len(profile.rules) == 2


def test_validate_command_allowlist_profile_rejects_malformed_rules() -> None:
    payload = {
        "schema_version": COMMAND_ALLOWLIST_SCHEMA_VERSION_Current,
        "profile_id": "ACP-CODER-L2-CURRENT",
        "rules": [{"mode": "bad", "argv": []}],
    }
    with pytest.raises(Exception):
        validate_command_allowlist_profile(payload)


def test_validate_command_allowlist_profile_verifier_fixture() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/command_allowlists/ACP-VERIFIER-RO-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    profile = validate_command_allowlist_profile(payload)
    assert profile.profile_id == "ACP-VERIFIER-RO-CURRENT"
    assert len(profile.rules) >= 1


def test_validate_command_allowlist_profile_coder_l2_fixture() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/command_allowlists/ACP-CODER-L2-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    profile = validate_command_allowlist_profile(payload)
    assert profile.profile_id == "ACP-CODER-L2-CURRENT"
    assert len(profile.rules) >= 1


def test_validate_command_allowlist_profile_coder_l3_fixture() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/command_allowlists/ACP-CODER-L3-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    profile = validate_command_allowlist_profile(payload)
    assert profile.profile_id == "ACP-CODER-L3-CURRENT"
    assert len(profile.rules) >= 1
