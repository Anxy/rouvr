"""Adversarial / schema-hostile planner payloads for C040 validation."""

from __future__ import annotations

from typing import Any

# Each case: model JSON payload → expected reason prefix (or None if must succeed)
ADVERSARIAL_CASES: list[dict[str, Any]] = [
    {
        "id": "not_json_object",
        "payload": ["array"],
        "expect_reason_prefix": "PLANNER_MODEL_ERROR",
    },
    {
        "id": "needs_more_evidence",
        "payload": {"reply": "need file", "needs_more_evidence": True},
        "expect_reason_prefix": "NEEDS_MORE_EVIDENCE",
    },
    {
        "id": "ungrounded_scope",
        "payload": {
            "reply": "x",
            "needs_more_evidence": False,
            "purpose": "touch unknown",
            "target_scope": ["NeverSeen.php"],
            "acceptance_criteria": ["x"],
            "validation_commands": ["x"],
            "risk_tier": "LOW",
        },
        "expect_reason_prefix": "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE",
    },
    {
        "id": "invalid_step_type",
        "payload": {
            "reply": "x",
            "needs_more_evidence": False,
            "steps": ["not-an-object"],
        },
        "expect_reason_prefix": "INVALID_STEP_CONTRACT",
    },
]


def assert_adversarial_refusal(*, case_id: str, reason: str | None) -> None:
    case = next(c for c in ADVERSARIAL_CASES if c["id"] == case_id)
    prefix = case["expect_reason_prefix"]
    if not reason or not str(reason).startswith(prefix):
        raise AssertionError(f"case {case_id}: expected reason starting with {prefix}, got {reason!r}")
