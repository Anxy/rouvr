from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_s25_replay_fixtures_cover_required_terminals() -> None:
    data = _load("s25_loop_replay_fixtures.json")
    fixtures = data["fixtures"]
    terminals = {item["expected"]["terminal"] for item in fixtures}
    assert {"done", "policy_block", "drift_block"}.issubset(terminals)


def test_s25_replay_includes_explicit_escalation_reason() -> None:
    data = _load("s25_loop_replay_fixtures.json")
    escalations = [item for item in data["fixtures"] if item["expected"].get("route") == "onlineai"]
    assert len(escalations) >= 1
    for item in escalations:
        assert item["expected"].get("reason_code")


def test_s25_adversarial_pack_is_terminally_classified() -> None:
    data = _load("s25_adversarial_loop_cases.json")
    cases = data["cases"]
    assert len(cases) >= 4
    terminals = {case["expected_terminal"] for case in cases}
    assert {"needs_human", "drift_block", "policy_block", "escalation_exhausted"} == terminals
