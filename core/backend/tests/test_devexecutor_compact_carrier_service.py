from __future__ import annotations

import pytest

from backend.services.devexecutor_compact_carrier_service import (
    CompactCarrierTranslationError,
    CompactCarrierValidationError,
    resolve_compact_or_steps,
    validate_or_repair_compact_carrier,
)


def test_validate_or_repair_compact_carrier_accepts_native_shape() -> None:
    payload = {
        "t": "plan.execute",
        "v": 1,
        "id": "req-1",
        "c": {},
        "p": {},
        "a": [["list", ".", {}], ["read", "README.md", {}], ["status", "", {}]],
        "o": {},
        "r": {},
        "e": "fallback",
    }
    validated, repaired = validate_or_repair_compact_carrier(payload)
    assert repaired is True
    assert validated.t == "plan.execute"
    assert len(validated.a) == 3


def test_validate_or_repair_compact_carrier_repairs_dict_action_shape() -> None:
    payload = {
        "t": "plan.execute",
        "id": "req-2",
        "actions": [
            {"verb": "write", "target": "tmp.txt", "args": {"content": "hello"}},
        ],
    }
    validated, repaired = validate_or_repair_compact_carrier(payload)
    assert repaired is True
    assert validated.v == 1
    assert validated.a[0].op == "write"
    assert validated.a[0].target == "tmp.txt"


def test_resolve_compact_or_steps_translates_known_ops() -> None:
    payload = {
        "t": "plan.execute",
        "v": 1,
        "id": "req-3",
        "c": {},
        "p": {},
        "a": [
            ["list", ".", {}],
            ["search", "TODO", {"relative_path": "src"}],
            ["write", "notes.txt", {"content": "done"}],
            ["evidence", "artifacts/e2e.log", {"evidence_type": "log"}],
        ],
        "o": {},
        "r": {},
    }
    steps, diag = resolve_compact_or_steps(steps=[], compact_carrier=payload)
    assert diag["input_mode"] == "compact_carrier"
    assert len(steps) == 4
    assert steps[0]["tool"] == "list_dir"
    assert steps[1]["tool"] == "search"
    assert steps[2]["tool"] == "write_file"
    assert steps[3]["tool"] == "evidence.attach"


def test_resolve_compact_or_steps_rejects_unsupported_op() -> None:
    payload = {
        "t": "plan.execute",
        "v": 1,
        "id": "req-4",
        "c": {},
        "p": {},
        "a": [["delete_everything", "/", {}]],
        "o": {},
        "r": {},
    }
    with pytest.raises(CompactCarrierTranslationError):
        resolve_compact_or_steps(steps=[], compact_carrier=payload)


def test_resolve_compact_or_steps_rejects_malformed_actions_type() -> None:
    payload = {
        "t": "plan.execute",
        "v": 1,
        "id": "req-5",
        "a": "not-a-list",
    }
    with pytest.raises(CompactCarrierValidationError):
        resolve_compact_or_steps(steps=[], compact_carrier=payload)


@pytest.mark.parametrize(
    ("compact_payload", "expected_tools"),
    [
        (
            {
                "t": "plan.scan",
                "v": 1,
                "id": "rep-1",
                "a": [["list", ".", {}], ["read", "README.md", {}]],
            },
            ["list_dir", "read_file"],
        ),
        (
            {
                "t": "plan.search",
                "v": 1,
                "id": "rep-2",
                "a": [["search", "TODO", {"relative_path": "src"}], ["status", "", {}]],
            },
            ["search", "get_status"],
        ),
        (
            {
                "t": "plan.mutate",
                "v": 1,
                "id": "rep-3",
                "a": [["write", "notes.txt", {"content": "ok"}], ["cmd", "python -V", {"timeout_seconds": 5}]],
            },
            ["write_file", "cmd.run"],
        ),
        (
            {
                "t": "plan.patch",
                "v": 1,
                "id": "rep-4",
                "a": [
                    ["patch", "", {"patch_text": "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -0,0 +1 @@\n+x\n"}],
                    ["evidence", "artifacts/report.json", {"evidence_type": "report"}],
                ],
            },
            ["apply_patch", "evidence.attach"],
        ),
    ],
)
def test_resolve_compact_or_steps_representative_plan_matrix(
    compact_payload: dict,
    expected_tools: list[str],
) -> None:
    steps, diag = resolve_compact_or_steps(steps=[], compact_carrier=compact_payload)
    assert diag["input_mode"] == "compact_carrier"
    assert [step["tool"] for step in steps] == expected_tools
