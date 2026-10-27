"""AIX-E2E-CYCLE-001A-W07 schema evolution tests: every registered contract has a fixture
that round-trips through `load_versioned`, and unknown/missing/future versions fail closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.aix_core.runtime.governance.experimental.schema_evolution import (
    CONTRACT_REGISTRY,
    SchemaEvolutionError,
    UnknownContractError,
    UnsupportedVersionError,
    load_versioned,
    registered_contract_names,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "objective_cycle"

_CONTRACT_TO_FIXTURE = {
    "ObjectiveEnvelope": "objective_envelope_v1.json",
    "ExecutionCycle": "execution_cycle_v1.json",
    "StepContract": "step_contract_v1.json",
    "StepAttempt": "step_attempt_v1.json",
    "ReplanDecision": "replan_decision_v1.json",
    "CycleOutcome": "cycle_outcome_v1.json",
}


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class TestFixtureMatrixCompleteness:
    def test_every_registered_contract_has_a_fixture(self) -> None:
        assert set(registered_contract_names()) == set(_CONTRACT_TO_FIXTURE)

    def test_every_fixture_file_exists(self) -> None:
        for filename in _CONTRACT_TO_FIXTURE.values():
            assert (FIXTURES_DIR / filename).is_file(), f"missing fixture: {filename}"


@pytest.mark.parametrize("contract_name,fixture_file", sorted(_CONTRACT_TO_FIXTURE.items()))
def test_fixture_round_trips_through_load_versioned(contract_name: str, fixture_file: str) -> None:
    payload = _load_fixture(fixture_file)
    model = load_versioned(contract_name, payload)
    assert model.version == "v1"
    # round trip: dump then reload must validate again with no loss
    redumped = model.model_dump(mode="json")
    again = load_versioned(contract_name, redumped)
    assert again == model


class TestFailClosedOnBadVersionInput:
    def test_unknown_contract_name_raises(self) -> None:
        with pytest.raises(UnknownContractError):
            load_versioned("NotARealContract", {"version": "v1"})

    def test_missing_version_field_raises(self) -> None:
        payload = _load_fixture("objective_envelope_v1.json")
        del payload["version"]
        with pytest.raises(SchemaEvolutionError):
            load_versioned("ObjectiveEnvelope", payload)

    def test_future_unknown_version_raises_instead_of_guessing(self) -> None:
        payload = _load_fixture("objective_envelope_v1.json")
        payload["version"] = "v99-does-not-exist"
        with pytest.raises(UnsupportedVersionError):
            load_versioned("ObjectiveEnvelope", payload)

    def test_registry_contains_exactly_the_six_canonical_contracts(self) -> None:
        assert set(CONTRACT_REGISTRY) == {
            "ObjectiveEnvelope",
            "ExecutionCycle",
            "StepContract",
            "StepAttempt",
            "ReplanDecision",
            "CycleOutcome",
        }
