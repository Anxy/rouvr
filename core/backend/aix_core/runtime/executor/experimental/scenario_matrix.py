"""001N-W02: 48-scenario objective-cycle matrix (architecture § scenarios)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ScenarioKind = Literal[
    "admission",
    "planning",
    "identity",
    "budget",
    "execution",
    "verification",
    "recovery",
    "authority",
]


@dataclass(frozen=True)
class ScenarioSpec:
    id: int
    kind: ScenarioKind
    title: str
    expected: str


def _build_matrix() -> tuple[ScenarioSpec, ...]:
    # 48 scenarios covering architecture failure/happy classes in compact form.
    rows: list[ScenarioSpec] = []
    catalog: list[tuple[ScenarioKind, str, str]] = [
        ("admission", "Unsupported task class", "REJECT"),
        ("admission", "Missing task class", "CLARIFY"),
        ("admission", "Forbidden auto_accept effect", "REJECT"),
        ("admission", "Forbidden direct_provider_route", "REJECT"),
        ("admission", "Production mutation unauthorized", "REJECT"),
        ("admission", "Docs-only admit", "ADMIT"),
        ("planning", "Cyclic dependency DAG", "FAIL_CLOSED"),
        ("planning", "Missing acceptance criteria", "FAIL_CLOSED"),
        ("planning", "Scope widening replan", "WAITING_APPROVAL_OR_REJECT"),
        ("planning", "Valid acyclic plan", "READY"),
        ("planning", "Ambiguous objective", "CLARIFICATION_REQUIRED"),
        ("planning", "Stale CodeIntel context", "BLOCKED"),
        ("identity", "Missing tenant", "DENY"),
        ("identity", "Cross-tenant read", "DENY"),
        ("identity", "Forged Authorization", "DENY"),
        ("identity", "Service principal missing", "DENY"),
        ("identity", "Same-tenant parent load", "ALLOW"),
        ("identity", "Actor type executor on review", "DENY"),
        ("budget", "Token budget exhausted", "STOP"),
        ("budget", "Cost budget exhausted", "STOP"),
        ("budget", "Verification reserve protected", "ALLOW_VERIFY_DENY_STEP"),
        ("budget", "Retry overflow", "STOP"),
        ("budget", "Concurrency > 1", "FAIL_CLOSED"),
        ("budget", "Atomic reserve duplicate id", "DENY"),
        ("execution", "Move token reuse", "DENY"),
        ("execution", "Move token expired", "DENY"),
        ("execution", "Executor self-selection", "DENY"),
        ("execution", "Executor fallback self-choice", "DENY"),
        ("execution", "Network not permitted", "FAIL"),
        ("execution", "Tool not in manifest", "FAIL"),
        ("verification", "False success incomplete step", "FAIL"),
        ("verification", "Independent verifier pass", "VERIFIED"),
        ("verification", "Skipped check hidden", "FAIL"),
        ("verification", "Evidence missing", "FAIL"),
        ("verification", "Objective-level verify pass", "COMPLETED_FOR_REVIEW"),
        ("verification", "Objective-level verify fail", "FAILED"),
        ("recovery", "Bounded retry within policy", "NEW_ATTEMPT"),
        ("recovery", "Retry after max attempts", "STOP"),
        ("recovery", "Cancel during wait", "CANCELLED"),
        ("recovery", "Cancel blocks further dispatch", "NO_DISPATCH"),
        ("recovery", "Worker restart resume", "RESUME"),
        ("recovery", "Old history incompatible worker", "FAIL_CLOSED"),
        ("authority", "WorkHub lifecycle mutation attempt", "DENY"),
        ("authority", "Auto-create WorkHub task", "DENY"),
        ("authority", "Temporal declares acceptance", "DENY"),
        ("authority", "Internal step as WH subtask", "DENY"),
        ("authority", "Review packet without accept", "COMPLETED_FOR_REVIEW"),
        ("authority", "Local GO != production GO", "NO_GO"),
    ]
    assert len(catalog) == 48
    for i, (kind, title, expected) in enumerate(catalog, start=1):
        rows.append(ScenarioSpec(id=i, kind=kind, title=title, expected=expected))
    return tuple(rows)


SCENARIO_MATRIX_V1: tuple[ScenarioSpec, ...] = _build_matrix()


def matrix_as_dicts() -> list[dict]:
    return [
        {"id": s.id, "kind": s.kind, "title": s.title, "expected": s.expected, "result": "PENDING"}
        for s in SCENARIO_MATRIX_V1
    ]


def assert_matrix_size() -> None:
    if len(SCENARIO_MATRIX_V1) != 48:
        raise AssertionError(f"SCENARIO_MATRIX_SIZE:{len(SCENARIO_MATRIX_V1)}")
