from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models.repo_profile_models import (
    REPO_PROFILE_SCHEMA_VERSION_Current,
    get_repo_profile_schema_current,
    validate_repo_profile,
)


def _profile_payload() -> dict:
    return {
        "schema_version": REPO_PROFILE_SCHEMA_VERSION_Current,
        "profile_id": "RP-HYEVE-PY-FASTAPI-CURRENT",
        "version": 1,
        "repo_patterns": ["hyeve*"],
        "command_policy": {"allow_prefixes": [["python"], ["pytest"]], "default_timeout_seconds": 30.0},
        "drift_guards": {"max_files_changed": 20, "max_diff_bytes": 100000},
    }


def test_validate_repo_profile_minimal() -> None:
    profile = validate_repo_profile(_profile_payload())
    assert profile.schema_version == REPO_PROFILE_SCHEMA_VERSION_Current
    assert profile.profile_id == "RP-HYEVE-PY-FASTAPI-CURRENT"
    assert profile.command_policy.default_timeout_seconds == 30.0


def test_validate_repo_profile_rejects_unknown_schema_version() -> None:
    payload = _profile_payload()
    payload["schema_version"] = "repo_profile.v9"
    with pytest.raises(ValueError, match="Unsupported repo profile schema_version"):
        validate_repo_profile(payload)


def test_repo_profile_schema_contains_core_fields() -> None:
    schema = get_repo_profile_schema_current()
    required = set(schema.get("required") or [])
    assert {"schema_version", "profile_id"} <= required


def test_repo_profile_fixture_json_is_valid() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/repo_profiles/RP-HYEVE-PY-FASTAPI-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    profile = validate_repo_profile(payload)
    assert profile.profile_id == "RP-HYEVE-PY-FASTAPI-CURRENT"
    contract = profile.metadata.get("instruction_contract")
    assert isinstance(contract, dict)
    assert contract.get("service_root") == "projects/AIX/hyeve-aix-service"
    assert contract.get("package_root") == "backend"
    assert contract.get("test_root") == "backend/tests"


def test_codeintel_repo_profile_fixture_json_is_valid() -> None:
    fixture = Path(
        "projects/AIX/hyeve-aix-service/backend/config/repo_profiles/RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    profile = validate_repo_profile(payload)
    assert profile.profile_id == "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT"
    contract = profile.metadata.get("instruction_contract")
    assert isinstance(contract, dict)
    assert contract.get("service_root") == "services/hyeve-codeintel-service"
    assert "pytest tests/test_codeintel_query_write_routes.py -q" in (contract.get("allowed_validation_commands") or [])


def test_auth_repo_profile_fixture_json_is_valid() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/repo_profiles/RP-HYEVE-AUTH-PY-FASTAPI-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    profile = validate_repo_profile(payload)
    assert profile.profile_id == "RP-HYEVE-AUTH-PY-FASTAPI-CURRENT"
    contract = profile.metadata.get("instruction_contract")
    assert isinstance(contract, dict)
    assert contract.get("service_root") == "services/hyeve-auth-service"
    assert contract.get("package_root") == "app"
    assert contract.get("test_root") == "tests"
    assert "pytest tests/test_auth.py -q" in (contract.get("allowed_validation_commands") or [])
