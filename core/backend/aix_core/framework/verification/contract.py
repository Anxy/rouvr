"""AIX Framework Verification Contracts & Types.

Sprint: F5 Verification Framework (F5-P1)
Grounded in: StepContractV1, CompletionGateResult, SEM-001 Law C.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

CheckStatus = str  # "PASSED" | "FAILED" | "NOT_RUN" | "BLOCKED"
VERIFIER_VERSION = "aix.verification.framework.v1"

VERIFICATION_TYPES = frozenset(
    {
        "resource_resolved",
        "file_read_non_empty",
        "file_exists",
        "file_contains",
        "file_hash_changed",
        "file_hash_unchanged",
        "diff_matches_expected",
        "command_exit_code_zero",
        "test_suite_passed",
        "target_resource_accessed",
        "no_unintended_files_changed",
        "secret_values_redacted",
        "keys_found_gt_zero",
        "tool_output_schema_valid",
    }
)


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _uuid_str() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class VerificationCheck:
    check_type: str
    mandatory: bool
    status: CheckStatus
    evidence_ref: str = ""
    failure_reason: str | None = None
    started_at: str = field(default_factory=_utc)
    completed_at: str = field(default_factory=_utc)
    verifier_version: str = VERIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_type": self.check_type,
            "mandatory": self.mandatory,
            "status": self.status,
            "evidence_ref": self.evidence_ref,
            "failure_reason": self.failure_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "verifier_version": self.verifier_version,
        }


@dataclass(frozen=True)
class VerificationOutcome:
    checks: tuple[VerificationCheck, ...]
    all_mandatory_passed: bool
    failed_mandatory: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "all_mandatory_passed": self.all_mandatory_passed,
            "failed_mandatory": list(self.failed_mandatory),
        }


@dataclass(frozen=True)
class VerificationContract:
    """Canonical verification contract for a step execution attempt.

    Enforces that criteria, expected checks, and continuity references are
    derived strictly from typed step contracts, never inferred from objective prose.
    """

    contract_id: str
    objective_id: str
    cycle_id: str
    step_id: str
    criteria: tuple[str, ...]
    expected_checks: tuple[str, ...]
    required_evidence: tuple[str, ...]
    continuity_required: bool
    facet_refs: tuple[str, ...] = ()
    operation_refs: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    mapping_refs: tuple[str, ...] = ()
    obligation_refs: tuple[str, ...] = ()
    outcome_contract_id: str | None = None
    outcome_contract_hash: str | None = None

    @classmethod
    def for_step(cls, step: Any) -> "VerificationContract":
        """Construct canonical VerificationContract from a StepContractV1 instance."""
        from backend.aix_core.runtime.verification.product_structure_coverage import step_has_continuity_refs

        step_id = getattr(step, "id", "") or str(getattr(step, "step_id", ""))
        cycle_id = getattr(step, "cycle_id", "") or str(getattr(step, "objective_id", ""))
        objective_id = getattr(step, "objective_id", "") or cycle_id

        criteria = tuple(str(c) for c in (getattr(step, "acceptance_criteria", ()) or ()))
        expected_checks = tuple(str(c) for c in (getattr(step, "expected_checks", ()) or ()))
        required_evidence = tuple(str(e) for e in (getattr(step, "required_evidence", ()) or ()))

        facet_refs = tuple(str(r) for r in (getattr(step, "facet_refs", ()) or ()))
        operation_refs = tuple(str(r) for r in (getattr(step, "operation_refs", ()) or ()))
        entity_refs = tuple(str(r) for r in (getattr(step, "entity_refs", ()) or ()))
        mapping_refs = tuple(str(r) for r in (getattr(step, "mapping_refs", ()) or ()))
        obligation_refs = tuple(str(r) for r in (getattr(step, "obligation_refs", ()) or ()))

        outcome_id = getattr(step, "outcome_contract_id", None)
        outcome_hash = getattr(step, "outcome_contract_hash", None)

        continuity_req = step_has_continuity_refs(step)

        return cls(
            contract_id=_uuid_str(),
            objective_id=str(objective_id),
            cycle_id=str(cycle_id),
            step_id=str(step_id),
            criteria=criteria,
            expected_checks=expected_checks,
            required_evidence=required_evidence,
            continuity_required=continuity_req,
            facet_refs=facet_refs,
            operation_refs=operation_refs,
            entity_refs=entity_refs,
            mapping_refs=mapping_refs,
            obligation_refs=obligation_refs,
            outcome_contract_id=str(outcome_id) if outcome_id else None,
            outcome_contract_hash=str(outcome_hash) if outcome_hash else None,
        )


@dataclass(frozen=True)
class GateResult:
    """Result of evaluating a single verification gate."""

    gate_id: str
    passed: bool
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    mandatory: bool = True
    immutable: bool = False
    evidence_ref: str = ""
    started_at: str = field(default_factory=_utc)
    completed_at: str = field(default_factory=_utc)


@dataclass(frozen=True)
class GateContext:
    """Execution and workspace context passed to GateDefinition evaluators."""

    contract: VerificationContract
    discovery: Any = None
    diff_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    diff_text: str = ""
    test_result: dict[str, Any] | None = None
    already_satisfied: bool = False
    already_satisfied_reason: str = ""
    already_satisfied_evidence_refs: tuple[str, ...] = ()
    secret_values: list[str] | None = None
    diff_only_verification_allowed: bool = False
    state_assertions_passed: bool = True
    state_assertion_detail: str = ""
    task_family: str = ""
    objective: str = ""
    required_effects: tuple[Any, ...] = ()
    before_files: dict[str, str] | None = None
    after_files: dict[str, str] | None = None
    step_contract: Any = None
    evidence: Any = None
    outcome_contract: Any = None
    execution_result: Any = None
    obligations: tuple[Any, ...] = ()
