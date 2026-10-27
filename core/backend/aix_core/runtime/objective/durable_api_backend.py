"""Concrete ObjectiveCycleApiBackend for mounted router wiring.

Two durability modes:
  - inprocess: process-local ledger + deterministic workflow engine (local/dev proof only)
  - postgres: append-only ObjectiveCycleRepository when a working database URL is supplied

Never claims WorkHub lifecycle authority or production GO.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import traceback
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.aix_core.runtime.objective.execution_auth_store import ExecutionAuthStore
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

import httpx

from backend.aix_core.runtime.objective.repository import (
    ObjectiveCycleRepository,
    ObjectiveCycleRepositoryError,
)
from backend.aix_core.runtime.objective.ledger_schema import AGGREGATE_TYPES
from backend.aix_core.runtime.objective.events import EventSchemaError, family_for
from backend.aix_core.runtime.objective.mark_workhub_done import MarkWorkHubDoneError, mark_workhub_task_done
from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteError,
    build_workhub_client,
    default_workspace_base,
    is_workhub_task_id_placeholder,
    looks_like_create_empty_directory_objective,
    looks_like_markdown_doc_objective,
    looks_like_read_only_summary_objective,
    looks_like_unbounded_codebase_audit,
    codebase_audit_markdown_filename,
    resolve_empty_directory_marker_path,
    run_real_execute,
)
from backend.aix_core.framework.execution.port import ExecuteStepRequest, ExecutionPort
from backend.workflows.temporal.objective_cycle_contracts import (
    muse_owner_refs_from_mapping,
)
from backend.aix_core.framework.execution.muse_execute_adapter import (
    ActivationBindingLookup,
    MuseExecuteAdapter,
)
from backend.session_factory.muse_preflight import MusePreflightVerifier
from backend.aix_core.framework.execution.muse_session import (
    MuseSessionPort,
    is_muse_model_id,
)
from backend.aix_core.framework.execution.real_execute_adapter import RealExecuteAdapter
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot
from backend.aix_core.runtime.executor.execution_reasons import reason_details
from backend.aix_core.runtime.objective.message_binding import (
    MessageBindingError,
    bind_objective_source_message,
)
from backend.aix_core.runtime.executor.read_file_execute import (
    ReadFileError,
    extract_target_path,
    is_test_like_path,
    objective_asserts_existing_file,
    resolve_relative_path,
    split_named_objective_files,
)
from backend.aix_core.runtime.executor.filesystem_primitives import (
    looks_like_rename_path_objective,
    resolve_rename_paths,
)
from backend.aix_core.runtime.objective.task_family import classify_task_family
from backend.aix_core.runtime.objective.provenance.admit import (
    IntentFrozenError,
    append_user_turn,
    freeze_user_intent,
    infer_dod_user_authored,
    original_objective_alias,
)
from backend.aix_core.runtime.objective.provenance.contracts import (
    ApprovalBinding,
    AttemptEvidence,
    ExplorationNeed,
    GovernanceRevision,
    PlanRevision,
    UserIntent,
)
from backend.aix_core.runtime.objective.provenance.history import ProvenanceHistory
from backend.aix_core.runtime.objective.provenance.explore import (
    ExploreHit,
    build_understanding_v2,
    evidence_from_hits,
    execute_need,
    needs_from_understanding,
    run_explore,
)
from backend.aix_core.runtime.objective.provenance.understanding import (
    UnderstandingV1Result,
    build_understanding_v1,
)
from backend.aix_core.runtime.objective.provenance.plan_request import (
    build_plan_request,
    default_governance,
)
from backend.aix_core.runtime.objective.provenance.validators import (
    create_root_contains,
    validate_approval_for_execute,
    validate_execute_diff,
    validate_explore_gate_sources,
    validate_plan_revision,
    validate_replan_revision,
    validate_reunderstand,
)

# AIX-REAL-EXECUTION-003/004: explore-before-plan gate and per-objective execution
# memory. Both modules are intentionally not self-wiring (see their own module
# docstrings) -- this is the one production integration point, owned here so the
# gate can run before `run_real_execute` is ever called.
from backend.aix_core.runtime.objective.discovery import _objective_keywords
from backend.aix_core.runtime.planner.execution_context import (
    ContextToolExecutor,
    DirectoryRecord,
    DuplicateToolCallError,
    ExecutionContext,
    FileReadRecord,
    SearchRecord,
    SymbolRecord,
)
from backend.aix_core.runtime.planner.exploration_planner import (
    MUTATION_FAMILIES,
    ExplorationPlan,
    build_discovery_config_for_exploration,
    build_exploration_plan,
    gate_execution_planning,
    requires_exploration,
)
from backend.aix_core.runtime.executor.final_report import build_final_report
from backend.aix_core.runtime.verification.mutation_summary import build_mutation_summary
from backend.aix_core.runtime.planner.incremental_replan import (
    find_transitive_dependents,
    perform_incremental_replan,
)
from backend.aix_core.runtime.planner.plan_validation import validate_plan_capabilities
from backend.aix_core.runtime.objective.contracts import (
    ActionRequestV1,
    BudgetV1,
    CycleOutcomeV1,
    ExecutorPackageV1,
    FailureEvidenceV1,
    FailureReportV1,
    ObjectiveEnvelopeV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    ReplanDecisionV1,
    StepContractV1,
    StepRuntimeStateV1,
    UnderstandingEvidenceV1,
    coerce_executor_reason,
)
from backend.aix_core.runtime.objective.failure_report import (
    AgentFeedbackCollector,
    build_failure_report,
    summarize_failure_chain,
)
from backend.aix_core.runtime.objective.obligations import derive_obligations
from backend.aix_core.runtime.verification.obligation_coverage import validate_obligation_coverage
from backend.aix_core.runtime.planner.plan_quality import PlanQualityResult, evaluate_plan_quality
from backend.aix_core.runtime.objective.product_class import (
    is_product_class_objective,
    requires_outcome_contract,
)
from backend.aix_core.runtime.objective.outcome_contract_proposer import (
    OutcomeContractProposal,
    bind_steps_to_contract,
    propose_and_seal_outcome_contract,
)
from backend.aix_core.runtime.objective.outcome_obligations import (
    OutcomeContractObligationV1,
    compile_outcome_obligations,
)
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage
from backend.aix_core.runtime.verification.objective_truth import (
    CanonicalObjectiveTruthV1,
    derive_canonical_truth,
)
from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
    derive_ovc,
    evaluate_ovc_disposition_coverage,
)
from backend.aix_core.runtime.verification.verification_result import (
    VerificationResultV1,
    OVItemResultV1,
)
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionV1,
)
from backend.aix_core.runtime.verification.terminalization_guard import (
    TerminalizationDecisionV1,
)
from backend.aix_core.runtime.objective.services.terminalization_service import (
    TerminalizationEvaluationResult,
    TerminalizationService,
)
from backend.aix_core.runtime.objective.services.verification_service import (
    VerificationService,
)
from backend.aix_core.runtime.verification.gap import (
    GapCategory,
    GapSeverity,
    GapStatus,
    GapStore,
    GapV1,
)
from backend.aix_core.runtime.objective.step_runtime import init_step_runtime, replace_runtime_status
from backend.aix_core.runtime.objective.step_scheduler import (
    select_next_step,
    all_required_steps_passed,
    remaining_required_obligation_ids,
)
from backend.aix_core.runtime.boundaries.intent import log_intent_success
from backend.aix_core.runtime.boundaries.planner import log_plan_accepted, log_planning_started
from backend.aix_core.runtime.boundaries.dispatcher import (
    observe_accepted_plan,
    log_plan_observed,
    log_step_ready,
    log_step_claimed,
)
from backend.aix_core.runtime.boundaries.executor import (
    log_execution_started,
    log_execution_completed,
)
from backend.aix_core.runtime.boundaries.verifier import log_verifier_verdict
from backend.aix_core.runtime.recovery.loop_budget import AutonomousLoopTracker
from backend.aix_core.runtime.recovery.repair_directive import build_repair_directive
from backend.aix_core.runtime.recovery.repair_loop import AutonomousRepairCoordinator
from backend.aix_core.runtime.governance.cost_accounting import CostLedger
from backend.aix_core.runtime.model_registry.registry import get_model_registry
from backend.aix_core.runtime.objective.step_evidence import missing_required_evidence
from backend.aix_core.runtime.executor.executor_profile import default_executor_profile
from backend.aix_core.runtime.objective.world_state import build_world_state
from backend.aix_core.runtime.executor.executor_package import build_executor_package
from backend.aix_core.runtime.objective.cycle_outcome import (
    MARK_DONE_FAIL_CLOSE_REASONS,
    build_cycle_outcome,
    is_genuinely_terminal_phase,
)
from backend.aix_core.framework.recovery.runtime_bridge import (
    authorize_replan_routed,
    decide_retry_routed,
)
from backend.aix_core.runtime.recovery.replan import BoundedReplanPolicy
from backend.aix_core.intelligence.context.step_context_capsule import build_step_context_capsule
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract
from backend.aix_core.runtime.planner.evidence_requests import (
    default_evidence_requests,
    evidence_request_fingerprint,
    evidence_requests_are_unbounded,
    normalize_evidence_requests,
    query_looks_unbounded,
)
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1
from backend.aix_core.runtime.verification.failure_records import NormalizedFailureRecord, normalize_failure
from backend.aix_core.runtime.verification.repair_protocol import (
    IdenticalRetryError,
    RepairHypothesisV1,
    validate_repair_hypothesis,
)
from backend.aix_core.runtime.verification.objective_proof import evaluate_objective_proof
from backend.aix_core.runtime.verification.integration_proof import (
    evaluate_integration_proof,
    evaluate_objective_proven,
)
from backend.aix_core.runtime.objective.task_class_mapping import task_family_to_task_class
from backend.aix_core.runtime.objective.cycle_status_mapping import workflow_phase_to_cycle_status
from backend.aix_core.runtime.objective.understanding_evidence import build_understanding_evidence
from backend.aix_core.runtime.objective.step_attempt import build_step_attempt
from backend.aix_core.runtime.verification.semantic_verifier import SemanticVerificationOutcome, verify_step
from backend.aix_core.runtime.governance.policy import SUPPORTED_TASK_CLASSES
from pydantic import ValidationError as _ContractValidationError
from backend.aix_core.intelligence.repository.repository_location import (
    RepositoryLocationError,
    is_registered_repository,
    rebind_repository_for_objective,
    resolve_repository_for_path,
    resolve_repository_root,
)
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox, RepositoryToolError
from backend.aix_core.intelligence.repository.topic_path_resolve import (
    extract_topic_snake_hints,
    resolve_topic_hints_in_repository,
)
from backend.aix_core.intelligence.repository.symbol_resolve import SymbolResolver
from backend.workflows.temporal.objective_cycle_contracts import ObjectiveCycleWorkflowInput
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflowEngine,
    replay_history,
)
from backend.aix_core.runtime.objective.durable_aggregate.replay import (
    DECISION_STATE_SCHEMA,
    AggregateSnapshot,
    InMemoryProjectionStore,
    ProjectionStore,
    ReplayError,
    decision_bytes,
    decision_state_objective,
    decision_state_run,
    wrap_objective_snapshot,
    wrap_run_snapshot,
)
from backend.aix_core.runtime.objective.durable_aggregate.session_lock import (
    InMemorySessionLockStore,
    SessionLockError,
    lock_conflict_payload,
)
from backend.aix_core.runtime.objective.durable_aggregate.attempt_journal import (
    AttemptJournalError,
    InMemoryAttemptJournal,
)
from backend.aix_core.runtime.objective.durable_aggregate.authz import (
    AuthzError,
    authz_error_payload,
    bind_idempotency_key,
    claims_from_request,
    require_tenant,
)
from backend.aix_core.runtime.objective.durable_aggregate.evidence_store import InMemoryEvidenceStore
from backend.aix_core.runtime.objective.durable_aggregate.environment_fingerprint import (
    EnvironmentError,
    discover_environment,
    reject_objective_install_text,
)
from backend.aix_core.runtime.objective.durable_aggregate.workspace_lease import WorkspaceLeaseStore
from backend.aix_core.runtime.objective.lifecycle_integration import (
    LifecycleContext,
    ObjectiveLifecycleIntegration,
)
from backend.aix_core.runtime.objective.durable_aggregate.capability_registry import default_registry
from backend.aix_core.runtime.objective.durable_aggregate.boundary_policy import default_boundary_policy
from backend.aix_core.runtime.objective.objective_contract import (
    ObjectiveContractError,
    ObjectiveContractV1,
    assert_planning_allowed,
    understand_objective,
)


logger = logging.getLogger(__name__)

# Reason codes `_run_exploration_gate` can BLOCK a cycle with via
# `_blocked_exploration_payload` that a human answer can genuinely resolve: the two
# explicit clarification-required codes from `exploration_planner
# ._clarification_question` / the exhausted-exploration branch, plus
# `EXPLORATION_REQUIRED` -- the bounded (3-round) exploration loop not finishing in one
# call is expected for a DEBUG/REVIEW-shaped objective and the next `clarify` call
# (answer text folds into search terms for the next round) resumes the SAME loop from
# where `execution_context` left off, not a restart. Deliberately excludes real
# failures (`CAPABILITY_NOT_REGISTERED`, `WORKSPACE_NOT_BOUND`, `NO_EXECUTABLE_BRANCH_
# MATCHED`, ...) so `clarify` can never become a back door around those.
_CLARIFICATION_REASON_CODES = frozenset(
    {"INTENT_UNCLEAR", "NO_NEW_EXPLORATION_AVAILABLE", "EXPLORATION_REQUIRED"}
)

# Real retry lifecycle (gap-closure sprint 3, Priority 4): maps a cycle's real
# `terminal_reason` (a `RealExecuteError.code` -- see `real_execute.py`'s reason
# codes) onto `failure_taxonomy.py`'s failure classes, so `retry_repair.decide_retry`
# -- real authorization + budget-decrement logic, not reimplemented here -- can make
# the actual retry decision. `failure_taxonomy.classify()` itself expects a
# `VerificationResultV1`, a shape the live cycle doesn't produce for every blocked
# reason; this is the deliberately narrow adapter from "real reason code we actually
# have" to "the taxonomy's own vocabulary".
_RETRYABLE_TERMINAL_REASONS = frozenset(
    {"STALE_FILE_VERSION", "WORKHUB_LOAD_FAILED", "WORKHUB_AUTH_MISSING", "EXECUTOR_MODEL_MISMATCH"}
)
_REPAIRABLE_TERMINAL_REASONS = frozenset(
    {
        "PATCH_VALIDATION_FAILED", "PATCH_CONTEXT_MISMATCH", "NO_OP_PATCH",
        "EXPECTED_CHANGE_NOT_FOUND", "COMPLETION_GATES_FAILED", "TESTS_FAILED",
        "UNINTENDED_FILES_CHANGED",
        # AIX Closed-Loop Execution -- Sprint CL2-P2: the semantic verifier's REPAIR
        # verdict (verify_step()) is retry-eligible through this same existing
        # decide_retry path, reused rather than duplicated.
        "VERIFICATION_REPAIR_REQUIRED",
        # AIX Closed-Loop Execution -- Sprint 003 (CL3-P3): a bounded repair budget
        # exhausted because the diff never covered the objective's named work (see
        # objective_coverage.py) is repairable, not a structural give-up -- a human-
        # initiated retry_step with clearer guidance can still succeed.
        "OBJECTIVE_COVERAGE_FAILED",
        "UNSUPPORTED_CAPABILITY",
        "STRUCTURAL_EFFECTS_FAILED",
        "STRATEGY_INSUFFICIENT",
        "NO_OP_PATCH",
        # AIX-GAEF-001: codegen failures are repairable — the reason catalog already
        # marks them retryable=True, and the auto-repair path at line ~5003 explicitly
        # handles them. Classifying them as TERMINAL while the auto-repair path treats
        # them as repair-eligible is an inconsistency that causes the human-initiated
        # retry path to reject what the system path accepts.
        "MODEL_RETURNED_NO_EDIT",
        "EDIT_PARSE_FAILED",
        "EDIT_UNCHANGED",
    }
)
# Structural/config blocks a bare retry cannot fix without new information (a
# different repository registration, a different objective, an approved resource
# scope). Never authorized for retry -- `decide_retry` never even sees these; they
# are classified TERMINAL up front so a caller gets a clear, immediate reason instead
# of a budget decrement that could never have helped.
_TERMINAL_ONLY_REASONS = frozenset(
    {
        "CAPABILITY_NOT_REGISTERED", "WORKSPACE_NOT_BOUND", "NO_EXECUTABLE_BRANCH_MATCHED",
        "INTENT_UNCLEAR", "EXPLORATION_REQUIRED", "NO_APPLICABLE_SOURCE_FILE",
        "SYMBOL_NOT_FOUND", "SYMBOL_AMBIGUOUS", "FILE_NOT_IN_APPROVED_SCOPE",
        "PLAN_CHANGED_SINCE_APPROVAL", "EXECUTION_LOOP_DETECTED", "UNCAUGHT_CONTROL_ERROR",
        "VERIFICATION_HALT",
        "AUTHORIZATION_REQUIRED",
    }
)
# cycle_outcome.py's own copy of this set (it cannot import this module -- this module
# is its only caller, importing back would be circular) must never silently drift from
# the real one above; test_al11_cycle_outcome.py also asserts this at the contract level.
from backend.aix_core.runtime.objective.cycle_outcome import TERMINAL_ONLY_REASONS as _CYCLE_OUTCOME_TERMINAL_REASONS  # noqa: E402

assert _TERMINAL_ONLY_REASONS == _CYCLE_OUTCOME_TERMINAL_REASONS, (
    "durable_api_backend._TERMINAL_ONLY_REASONS and cycle_outcome.TERMINAL_ONLY_REASONS "
    "have drifted apart -- update cycle_outcome.py's copy to match"
)


def _classify_terminal_reason_for_retry(reason_code: str | None) -> FailureClassificationV1:
    code = str(reason_code or "").strip().upper()
    if not code or code in _TERMINAL_ONLY_REASONS or code in MARK_DONE_FAIL_CLOSE_REASONS:
        return FailureClassificationV1("TERMINAL", (code or "NO_TERMINAL_REASON",))
    if code in _RETRYABLE_TERMINAL_REASONS:
        return FailureClassificationV1("RETRYABLE", (code,))
    if code in _REPAIRABLE_TERMINAL_REASONS:
        return FailureClassificationV1("REPAIRABLE", (code,))
    # An unrecognized reason code fails closed toward TERMINAL rather than guessing
    # it's safe to retry -- same discipline `failure_taxonomy.classify` uses for its
    # own unclassified-reason-code fallback.
    return FailureClassificationV1("TERMINAL", (code, "UNCLASSIFIED_REASON_CODE"))


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# Only match identifier-shaped names (contain underscore or mixedCase), not plain
# English words like "add", "satisfies", "remains" that happen to follow "function".
_SYMBOL_NAME_RE = re.compile(
    r"\b(?:function|symbol|method|class|def)\s+"
    r"([a-zA-Z_][a-zA-Z0-9]*(?:_[a-zA-Z0-9_]+)+|[a-z]+[A-Z][a-zA-Z0-9_]*)"
)


def _extract_symbol_name(objective: str) -> str | None:
    match = _SYMBOL_NAME_RE.search(objective or "")
    return match.group(1) if match else None


def _context_from_dict(objective_id: str, data: dict[str, Any] | None) -> ExecutionContext:
    """Rehydrate `ExecutionContext` from its own `to_dict()` projection -- the existing
    `_ObjectiveRecord`/ledger persistence, not a new store. Nested dataclass fields
    (`asdict()`-flattened by `to_dict()`) are reconstructed explicitly since dataclasses
    do not round-trip through plain dicts on their own."""
    if not data:
        return ExecutionContext(objective_id=objective_id)
    return ExecutionContext(
        objective_id=str(data.get("objective_id") or objective_id),
        current_workspace=data.get("current_workspace"),
        execution_root=data.get("execution_root"),
        current_branch=data.get("current_branch"),
        repositories_explored=list(data.get("repositories_explored") or []),
        directories_listed=[DirectoryRecord(**d) for d in (data.get("directories_listed") or [])],
        files_read=[FileReadRecord(**d) for d in (data.get("files_read") or [])],
        files_modified=list(data.get("files_modified") or []),
        symbols_resolved=[SymbolRecord(**d) for d in (data.get("symbols_resolved") or [])],
        searches_executed=[SearchRecord(**d) for d in (data.get("searches_executed") or [])],
        commands_executed=list(data.get("commands_executed") or []),
        tests_executed=list(data.get("tests_executed") or []),
        generated_patches=list(data.get("generated_patches") or []),
        understanding_complete=bool(data.get("understanding_complete", False)),
        last_tool_call_id=data.get("last_tool_call_id"),
        evidence_roots=tuple(data.get("evidence_roots") or ()),
        search_fingerprint_counts=dict(data.get("search_fingerprint_counts") or {}),
        read_fingerprint_counts=dict(data.get("read_fingerprint_counts") or {}),
        duplicate_attempt_counts=dict(data.get("duplicate_attempt_counts") or {}),
    )


def _repository_root_is_empty(repository_root: object) -> bool:
    """True when the registered root exists and contains no regular files.

    Bounded scan (first 32 entries): any file — including dotfiles — defeats
    vacuity, so a checked-out repo (even `.git` only) never qualifies. Used to
    complete understanding for greenfield CREATE, where exploration of an
    empty root could never produce evidence.
    """
    try:
        root = Path(str(repository_root or ""))
    except (TypeError, ValueError):
        return False
    try:
        if not root.is_dir():
            return False
        seen = 0
        for entry in root.rglob("*"):
            seen += 1
            if seen > 32:
                return False
            try:
                if entry.is_file():
                    return False
            except OSError:
                return False
        return True
    except OSError:
        return False


def _understanding_complete(
    *,
    task_family: str,
    context: ExecutionContext,
    target_path: str | None,
    symbol_name: str | None,
) -> bool:
    """Deterministic explore-before-plan gate: family-specific ExecutionContext evidence.

    Provenance Understanding v2 (MISSING_INFO cleared by ExplorationEvidence) is a
    separate gate checked later in ``_run_exploration_gate``. This function only
    certifies that the adaptive exploration slice recorded the census/search/read
    artifacts ``build_exploration_plan`` requires before mutation planning.
    """
    if not context.execution_root:
        return False
    family = str(task_family or "UNKNOWN").strip().upper()
    if family in {"EXPLORE", "SEARCH", "ANALYZE", "EXPLAIN", "REVIEW"}:
        if not context.directories_listed:
            return False
        if target_path not in (None, ".") and not context.files_read:
            return False
        return True
    if family in (MUTATION_FAMILIES | {"DEBUG"}):
        if not (context.directories_listed and context.files_read):
            return False
        if symbol_name:
            return any(
                record.name.casefold() == symbol_name.casefold()
                for record in context.symbols_resolved
            )
        return True
    if family == "READ" and target_path:
        return bool(context.files_read)
    return False


def _stage(name: str, status: str, *, reason_code: str | None = None) -> dict[str, Any]:
    return {"stage": name, "status": status, "reason_code": reason_code}


def _execution_stage_projection(
    *, obj: "_ObjectiveRecord", cycle: "_CycleRecord | None"
) -> list[dict[str, Any]]:
    """AIX-REAL-EXECUTION-005 canonical stage API: `Understanding -> Exploring ->
    Reading -> Searching -> Planning -> Executing -> Verifying -> Completed|Blocked|
    Failed`, derived entirely from already-persisted `execution_context` and
    `real_execute` truth -- backend-authored, Studio only renders it, never computes
    its own progress. Status only (`pending`/`running`/`completed`/`blocked`/`failed`/
    `skipped`); no per-stage timestamps are fabricated -- this backend does not yet
    instrument per-stage `started_at`/`ended_at` and this projection does not invent
    them."""
    context = obj.execution_context or {}
    target_bound = bool(context.get("execution_root"))
    needs_exploration = requires_exploration(obj.task_family, target_bound=target_bound)

    understanding_status = "pending"
    if needs_exploration:
        understanding_status = "completed" if context.get("understanding_complete") else "running"
    elif obj.task_family != "UNKNOWN":
        understanding_status = "completed"

    def _explore_substage(count_key: str) -> str:
        if not needs_exploration:
            return "skipped"
        return "completed" if context.get(count_key) else "pending"

    real_execute = cycle.real_execute if cycle is not None else None
    planning_status = "pending"
    executing_status = "pending"
    verifying_status = "pending"
    final_status = "pending"
    reason_code = None
    if real_execute:
        planning_status = "completed" if real_execute.get("planning_status") == "COMPLETED" else "blocked"
        exec_state = str(real_execute.get("execution_status") or "NOT_STARTED")
        executing_status = {
            "COMPLETED": "completed",
            "AWAITING_APPLICATION": "completed",
            "BLOCKED": "blocked",
            "FAILED": "failed",
        }.get(exec_state, "pending" if exec_state == "NOT_STARTED" else "running")
        verify_state = str(real_execute.get("verification_status") or "NOT_RUN")
        verifying_status = {"PASSED": "completed", "FAILED": "failed", "BLOCKED": "blocked"}.get(
            verify_state, "pending"
        )
        objective_state = str(real_execute.get("objective_status") or "INCOMPLETE")
        reason_code = real_execute.get("reason_code")
        final_status = {
            "COMPLETED": "completed",
            "COMPLETED_WITH_WARNINGS": "completed",
            "BLOCKED": "blocked",
            "FAILED": "failed",
            "AWAITING_REVIEW": "blocked",
        }.get(objective_state, "pending")

    return [
        _stage("Understanding", understanding_status),
        _stage("Exploring", _explore_substage("directories_listed")),
        _stage("Reading", _explore_substage("files_read")),
        _stage("Searching", _explore_substage("searches_executed")),
        _stage("Planning", planning_status),
        _stage("Executing", executing_status, reason_code=reason_code if executing_status == "blocked" else None),
        _stage("Verifying", verifying_status),
        _stage(
            "Completed|Blocked|Failed",
            final_status,
            reason_code=reason_code if final_status in {"blocked", "failed"} else None,
        ),
    ]


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _workspace_base(settings: object | None = None) -> Path:
    base = default_workspace_base()
    if settings is not None:
        configured = str(getattr(settings, "OBJECTIVE_CYCLE_WORKSPACE_BASE", "") or "").strip()
        if configured:
            base = Path(configured)
    return base


def _cycle_roots_on_disk(
    *, tenant_id: str, cycle_id: str, settings: object | None = None
) -> list[Path]:
    base = _workspace_base(settings)
    if not base.is_dir() or not cycle_id:
        return []

    cycle_roots: list[Path] = []
    if tenant_id:
        dated = list((base / tenant_id).glob(f"*/{cycle_id}"))
        dated_session = list((base / tenant_id).glob(f"*/*/{cycle_id}"))
        legacy = base / tenant_id / cycle_id
        if legacy.is_dir():
            cycle_roots.append(legacy)
        cycle_roots.extend(p for p in dated if p.is_dir())
        cycle_roots.extend(p for p in dated_session if p.is_dir())
    if not cycle_roots:
        cycle_roots.extend(p for p in base.glob(f"*/{cycle_id}") if p.is_dir())
        cycle_roots.extend(p for p in base.glob(f"*/*/{cycle_id}") if p.is_dir())
        cycle_roots.extend(p for p in base.glob(f"*/*/*/{cycle_id}") if p.is_dir())
        if tenant_id:
            cycle_roots = [p for p in cycle_roots if tenant_id in p.parts]

    seen: set[str] = set()
    unique_roots: list[Path] = []
    for root in cycle_roots:
        key = str(root.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)
    return unique_roots


def _deliverable_root_from_pointer(
    *, tenant_id: str, cycle_id: str, settings: object | None = None
) -> Path | None:
    """Read DELIVERABLE_ROOT.json when present (canonical verification workspace)."""
    for cycle_root in _cycle_roots_on_disk(
        tenant_id=tenant_id, cycle_id=cycle_id, settings=settings
    ):
        pointer = _read_json_file(cycle_root / "DELIVERABLE_ROOT.json")
        if not pointer:
            continue
        if tenant_id and str(pointer.get("tenant_id") or "") not in ("", tenant_id):
            continue
        if str(pointer.get("cycle_id") or "") not in ("", cycle_id):
            continue
        raw = str(pointer.get("deliverable_root") or "").strip()
        if raw:
            return Path(raw)
    return None


def _latest_attempt_workspace(*, tenant_id: str, cycle_id: str, settings: object | None = None) -> Path | None:
    """Find the newest step attempt folder for a cycle under the on-disk workspace tree.

    Layouts supported:
    - dated: ``<base>/<tenant>/<YYYY-MM-DD>/<cycle_id>/step-*/attempt-*`` or ``step-*/att_*``
    - legacy: ``<base>/<tenant>/<cycle_id>/step-*/attempt-*`` or ``step-*/att_*``
    """
    if not cycle_id:
        return None

    attempts: list[Path] = []
    for cycle_root in _cycle_roots_on_disk(
        tenant_id=tenant_id, cycle_id=cycle_id, settings=settings
    ):
        attempts.extend(p for p in cycle_root.glob("step-*/attempt-*") if p.is_dir())
        attempts.extend(p for p in cycle_root.glob("step-*/att_*") if p.is_dir())
    if not attempts:
        return None
    attempts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return attempts[0]


def _resolve_cycle_workspace(
    *, tenant_id: str, cycle_id: str, settings: object | None = None
) -> Path | None:
    """Prefer deliverable root (verification target), else newest step attempt folder."""
    deliverable = _deliverable_root_from_pointer(
        tenant_id=tenant_id, cycle_id=cycle_id, settings=settings
    )
    if deliverable is not None:
        return deliverable
    return _latest_attempt_workspace(
        tenant_id=tenant_id, cycle_id=cycle_id, settings=settings
    )


def _workspace_snapshot(*, tenant_id: str, cycle_id: str, settings: object | None = None) -> dict[str, Any] | None:
    """Build a get_cycle-compatible payload from durable workspace files (post-restart)."""
    workspace = _latest_attempt_workspace(tenant_id=tenant_id, cycle_id=cycle_id, settings=settings)
    if workspace is None:
        return None
    objective = _read_json_file(workspace / "evidence" / "objective.json") or {}
    report = _read_json_file(workspace / "evidence" / "execution_report.json") or {}
    plan = _read_json_file(workspace / "evidence" / "plan.json") or {}
    token_usage = _read_json_file(workspace / "evidence" / "token_usage.json")
    positive = _read_json_file(workspace / "proposed" / "tests" / "positive_cases.json")
    negative = _read_json_file(workspace / "proposed" / "tests" / "negative_cases.json")
    html_path = workspace / "proposed" / "index.html"
    css_path = workspace / "proposed" / "styles.css"
    artifacts: dict[str, str] = {}
    if isinstance(report.get("artifacts"), dict):
        artifacts = {str(k): str(v) for k, v in report["artifacts"].items()}
    if html_path.is_file():
        artifacts.setdefault("proposed_html", str(html_path))
    if css_path.is_file():
        artifacts.setdefault("proposed_css", str(css_path))
    obj_tenant = str(objective.get("tenant_id") or "").strip()
    if obj_tenant and obj_tenant != tenant_id:
        return None
    has_html = html_path.is_file() or bool(artifacts.get("proposed_html"))
    ok = bool(report.get("ok")) if report else has_html
    phase = "COMPLETED" if ok else "FAILED"
    source_diff_path = workspace / "evidence" / "source_diff.txt"
    diff_text = ""
    if source_diff_path.is_file():
        diff_text = source_diff_path.read_text(encoding="utf-8", errors="replace")
    changed_raw = report.get("changed_source_files") or []
    changed_source_files: list[Any] = []
    if isinstance(changed_raw, list):
        for entry in changed_raw:
            if isinstance(entry, dict):
                changed_source_files.append(entry)
            elif str(entry or "").strip():
                changed_source_files.append({"path": str(entry).strip()})
    real_execute = {
        "ok": ok,
        "workspace": str(workspace),
        "artifacts": artifacts,
        "token_usage": token_usage or report.get("token_usage"),
        "executor": report.get("executor") or "objective_cycle.real_execute.v1",
        "workhub_loaded": report.get("workhub_loaded"),
        "recorded_at": report.get("recorded_at"),
        "repository_root": report.get("repository_root"),
        "changed_source_files": changed_source_files,
        "diff": diff_text or None,
        "source": "workspace_disk",
        "verification_status": "PASSED" if ok else "FAILED",
    }
    real_execute["mutation_summary"] = build_mutation_summary(real_execute=real_execute)
    proposed_files: list[dict[str, Any]] = []
    for name, path in (("index.html", html_path), ("styles.css", css_path)):
        if path.is_file():
            proposed_files.append(
                {
                    "name": name,
                    "bytes": path.stat().st_size,
                    "preview_url": f"/api/aix/cycles/{cycle_id}/preview/{name}",
                }
            )
    return {
        "cycle_id": cycle_id,
        "objective_id": str(objective.get("objective_id") or ""),
        "tenant_id": tenant_id,
        "candidate_sha": "",
        "budgets": {},
        "phase": phase,
        "version": 1,
        "terminal_reason": None if ok else "REAL_EXECUTE_FAILED",
        "approval_reference": None,
        "status": {"phase": phase, "source": "workspace_disk"},
        "real_execute": real_execute,
        "mutation_summary": real_execute.get("mutation_summary"),
        "preview_url": f"/api/aix/cycles/{cycle_id}/preview/index.html" if has_html else None,
        "created_at": str(objective.get("recorded_at") or report.get("recorded_at") or ""),
        "objective": str(objective.get("objective") or ""),
        "workhub_task_id": str(objective.get("workhub_task_id") or ""),
        "task_number": objective.get("task_number"),
        "project_id": objective.get("project_id"),
        "repository_id": objective.get("repository_id"),
        "plan": plan,
        "proposed_files": proposed_files,
        "tests": {
            "positive": (positive or {}).get("cases") if positive else [],
            "negative": (negative or {}).get("cases") if negative else [],
        },
        "source": "workspace_disk",
        "workhub_lifecycle_authority": False,
        "aix_creates_workhub_tasks": False,
    }


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _aggregate_mutation_summary(
    *,
    history: list[dict[str, Any]],
    current: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge per-step mutation summaries into one evidence-accurate cycle summary."""
    pieces: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        summary = entry.get("mutation_summary")
        if isinstance(summary, dict):
            pieces.append(summary)
        else:
            built = build_mutation_summary(real_execute=entry)
            if built:
                pieces.append(built)
    if isinstance(current, dict):
        summary = current.get("mutation_summary")
        if isinstance(summary, dict):
            pieces.append(summary)
        else:
            built = build_mutation_summary(real_execute=current)
            if built:
                pieces.append(built)
    if not pieces:
        return None
    files: list[str] = []
    seen: set[str] = set()
    commands: list[str] = []
    cmd_seen: set[str] = set()
    repair_count = 0
    replan_count = 0
    executor = ""
    verdict = "NOT_RUN"
    for piece in pieces:
        for path in piece.get("files_changed") or []:
            p = str(path).replace("\\", "/")
            if p and p not in seen:
                seen.add(p)
                files.append(p)
        for cmd in piece.get("validation_commands") or []:
            c = str(cmd).strip()
            if c and c not in cmd_seen:
                cmd_seen.add(c)
                commands.append(c)
        repair_count = max(repair_count, int(piece.get("repair_count") or 0))
        replan_count = max(replan_count, int(piece.get("replan_count") or 0))
        executor = str(piece.get("executor") or executor or "")
        v = str(piece.get("verifier_verdict") or "").upper()
        if v and v not in {"NOT_RUN", ""}:
            verdict = v
    return {
        "files_changed": files,
        "files_changed_count": len(files),
        "executor": executor or "objective_cycle.real_execute.v1",
        "validation_commands": commands,
        "verifier_verdict": verdict,
        "repair_count": repair_count,
        "replan_count": replan_count,
    }


def _remember_mutation_evidence(cycle: _CycleRecord) -> None:
    """Snapshot current real_execute before a multi-step advance clears it."""
    re = cycle.real_execute
    if not isinstance(re, dict) or not re:
        return
    if not (re.get("changed_source_files") or re.get("mutation_summary")):
        return
    cycle.mutation_evidence_history.append(dict(re))


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _workhub_http_error_payload(exc: BaseException) -> dict[str, Any]:
    """Map WorkHub HTTP failures into admit error codes the UI can show."""
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = int(exc.response.status_code) if exc.response is not None else 0
        detail = str(exc)[:800]
        if status_code == 403:
            return {
                "code": "WORKHUB_TASK_CREATE_FORBIDDEN",
                "detail": (
                    f"{detail}. WorkHub task create requires manager+ roles "
                    "(admin/manager/platform_admin). Refresh HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN "
                    "with an admin JWT, or sign in as a manager+ user."
                ),
                "http_status": 403,
            }
        if status_code == 401:
            return {
                "code": "WORKHUB_TASK_CREATE_UNAUTHORIZED",
                "detail": (
                    f"{detail}. Session or HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN was rejected. "
                    "Re-login and/or refresh the WorkHub API token in .env."
                ),
                "http_status": 401,
            }
        return {"code": "WORKHUB_TASK_CREATE_FAILED", "detail": detail, "http_status": status_code or None}
    detail = f"{type(exc).__name__}:{exc}"
    if "WORKHUB_TOKEN_EXPIRED" in detail or "expired" in detail.lower():
        return {
            "code": "WORKHUB_TASK_CREATE_UNAUTHORIZED",
            "detail": (
                f"{detail}. Sign out and sign in again so Create & run can use a fresh session JWT."
            ),
            "http_status": 401,
        }
    return {"code": "WORKHUB_TASK_CREATE_FAILED", "detail": detail}


_SIG_OUTLINE_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static|final|async|export)\s+)*"
    r"(?:function|def|class|interface|trait|enum)\s+\w+",
    re.IGNORECASE,
)


def _file_signature_outline(text: str, budget: int) -> str:
    """Imports + signatures only — enough for grounded planning under the prompt cap."""
    uses: list[str] = []
    sigs: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("use ") and stripped.endswith(";") and len(uses) < 12:
            uses.append(stripped[:160])
        elif _SIG_OUTLINE_RE.match(stripped):
            sigs.append(stripped[:160])
    out: list[str] = []
    used = 0
    for line in uses + sigs:
        if used + len(line) + 1 > budget:
            break
        out.append(line)
        used += len(line) + 1
    if not out:
        return text[: min(400, max(0, budget))]
    return "\n".join(out)


def _jwt_expired(raw: str | None) -> bool:
    """Best-effort JWT exp check (no signature verify) so we skip dead env tokens."""
    token = str(raw or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or token.count(".") < 2:
        return False
    try:
        import base64
        import json
        from datetime import UTC, datetime

        payload_b64 = token.split(".")[1] + "=="
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
        exp = int(payload.get("exp") or 0)
        if exp <= 0:
            return False
        return exp <= int(datetime.now(UTC).timestamp())
    except Exception:  # noqa: BLE001
        return False


async def _create_workhub_task_with_auth_fallback(
    *,
    settings: object | None,
    caller_auth: str | None,
    project_id: str,
    title: str,
    description: str,
    tenant_id: str,
    idempotency_key: str,
    metadata_json: dict[str, Any],
    tags_json: list[str],
) -> dict[str, Any]:
    """Create a WorkHub task; retry with env service token on caller 401/403.

    WorkHub POST /api/v1/tasks requires manager+ roles. Browser session JWTs often
    authenticate but lack those roles (403). The env service token usually has
    admin/platform_admin but may be expired (401). Try caller first, then service.
    Never retry with an already-expired env JWT — that only masks the real session issue.
    """
    attempts: list[str | None] = []
    if caller_auth:
        attempts.append(caller_auth)
    env_token = None
    if settings is not None:
        env_token = str(getattr(settings, "WORKHUB_API_TOKEN", "") or "").strip() or None
    else:
        import os

        env_token = str(os.getenv("HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN", "") or "").strip() or None
    if env_token and _jwt_expired(env_token):
        logger.warning("workhub_env_token_expired_skipped")
    else:
        attempts.append(None)  # build_workhub_client resolves the env token when bearer is None

    if not attempts:
        raise RuntimeError(
            "WORKHUB_TOKEN_EXPIRED: no usable caller session Bearer and "
            "HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN is missing or expired. Sign in again."
        )

    last_exc: BaseException | None = None
    seen_tokens: set[str] = set()
    for bearer in attempts:
        key = (bearer or "").strip() or "__env__"
        if key in seen_tokens:
            continue
        seen_tokens.add(key)
        client = build_workhub_client(settings, bearer_authorization=bearer)
        try:
            return await client.create_task(
                project_id=project_id,
                title=title,
                description=description,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                metadata_json=metadata_json,
                tags_json=tags_json,
            )
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            status_code = int(exc.response.status_code) if exc.response is not None else 0
            if status_code in {401, 403} and bearer is not None and None in attempts:
                logger.warning(
                    "workhub_task_create_caller_rejected_retry_service_token",
                    extra={"http_status": status_code},
                )
                continue
            raise
        except Exception:
            # A failing env-token fallback (e.g. no token configured) is less actionable
            # than the caller rejection that sent us here.
            if bearer is None and last_exc is not None:
                raise last_exc
            raise
    assert last_exc is not None
    raise last_exc


@dataclass
class _ObjectiveRecord:
    objective_id: str
    tenant_id: str
    workhub_task_id: str
    objective: str
    task_class: str
    definition_of_done: tuple[str, ...]
    repository_id: str
    allowed_files: tuple[str, ...]
    project_id: str | None = None
    task_number: int | None = None
    workhub_task_created: bool = False
    attachments: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1
    created_at: str = field(default_factory=_utc)
    # Immutable user ask at admission. Clarify/planner may enrich `objective` but
    # must never erase this snapshot of WHAT the human originally requested.
    original_objective: str = ""
    # AIX-REAL-EXECUTION-002: conversation -> objective message binding. Populated
    # only when the caller supplied `messages` (chat-origin admission); a legacy
    # non-chat admission leaves these `None` unless it explicitly supplied
    # `objective_source_message_id` itself.
    conversation_id: str | None = None
    message_id: str | None = None
    parent_message_id: str | None = None
    selected_message_id: str | None = None
    objective_source_message_id: str | None = None
    task_family: str = "UNKNOWN"
    classification_method: str = "deterministic"
    classification_confidence: float = 0.0
    # AIX-REAL-EXECUTION-004: per-objective execution memory (`ExecutionContext`,
    # serialized via its own `to_dict()`). One context per objective, not per cycle --
    # keyed here so it survives resume/clarify/retry/approval-continuation across
    # cycles for the same objective, reusing this record as the existing persistence
    # point rather than adding a new store.
    execution_context: dict[str, Any] | None = None


@dataclass
class _CycleRecord:
    cycle_id: str
    objective_id: str
    tenant_id: str
    candidate_sha: str
    budgets: dict[str, int]
    version: int = 1
    phase: str = "STARTING"
    terminal_reason: str | None = None
    last_error_detail: str | None = None
    approval_reference: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    engine: AixObjectiveCycleWorkflowEngine | None = None
    real_execute: dict[str, Any] | None = None
    # Completed-step real_execute snapshots retained when a multi-step plan advances
    # and clears `real_execute` for the next step. Status clients read an aggregate
    # mutation_summary from this history so a successful step-1 edit is not erased.
    mutation_evidence_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_utc)
    model_routes: dict[str, Any] = field(default_factory=dict)
    event_sequence: int = 0
    trace_id: str = field(default_factory=lambda: f"trc_{new_ulid()[:16]}")
    # Approval idempotency (see `_control`'s approval_reference/resume handling):
    # `resume_in_progress` makes "one active resume operation per cycle" real — a
    # second call arriving while the first is still mutating state returns the
    # current cycle state instead of racing it. `last_approval_request_id` makes a
    # *repeat* call with the same id (or the same phase, when the caller supplied no
    # id) a no-op replay instead of a second `_maybe_real_execute` invocation.
    resume_in_progress: bool = False
    last_approval_request_id: str | None = None
    completion_gates: dict[str, Any] | None = None
    # AIX-REAL-EXECUTION-007 Workstream H: counts genuine (non-duplicate-id) resume/
    # approval attempts against this cycle. `resume_count > 2` with zero real tool
    # calls (`real_execute is None`) is the deterministic stuck-cycle guard -- a
    # different approval_request_id each time still trips it, unlike the pure
    # duplicate-id dedup above which only catches an exact repeat.
    resume_count: int = 0
    # Approval identity (gap-closure sprint 3, Priority 3): `plan_id` is stable for
    # the cycle's lifetime; `plan_revision`/`current_gate_id` change whenever the plan
    # actually changes (currently: a successful `clarify`, which rewrites `obj.
    # objective` and thus what will actually be dispatched). `approved_*` is a
    # snapshot taken at the moment approval is granted -- never mutated afterward --
    # so a later mismatch against `current_gate_id`/live `obj` state is detectable
    # rather than silently trusted.
    plan_id: str = ""
    plan_revision: int = 1
    current_gate_id: str | None = None
    approved_gate_id: str | None = None
    approved_plan_revision: int | None = None
    approved_operations: tuple[str, ...] = ()
    approved_resources: tuple[str, ...] = ()
    # Real retry lifecycle (gap-closure sprint 3, Priority 4).
    retry_budget_remaining: int = 3
    resolution_note: str | None = None
    # AIX Closed-Loop Execution Plan, Sprint W10: separate budget from
    # `retry_budget_remaining` -- a replan is a plan-level revision (BoundedReplanPolicy
    # + ReplanDecisionV1), not a bare re-attempt (retry_repair.decide_retry). Conflating
    # the two budgets would let an exhausted retry budget silently block replans that
    # are still legitimately authorized, or vice versa.
    replan_budget_remaining: int = 2
    # Real cancellation (gap-closure sprint 3, Priority 5): a plain in-memory flag on
    # the SAME `_CycleRecord` both the in-flight request and a concurrent `cancel`
    # request see (single in-process backend, shared `self._cycles` dict). The
    # in-flight test/command runner (`real_execute._run_test_command_cancellable`)
    # polls this between bounded waits using real `asyncio.create_subprocess_exec` (not
    # a blocking `subprocess.run`), so the event loop stays free to actually handle a
    # concurrent cancel request while a long-running command is executing.
    cancel_requested: bool = False
    # AIX-MPE-003 Packet MPE-06: the currently bound READY/RUNNING step id.
    current_step_cursor: str | None = None
    active_step_started_at: str | None = None
    last_progress_at: str = field(default_factory=_utc)
    step_lease_expires_at: str | None = None
    # AIX-DSD-001: durable dispatcher state is separate from the legacy workflow
    # engine phase.  It is the sole authority for deciding whether a step may start.
    dispatch_state: str = "IDLE"
    dispatch_required: bool = False
    execution_claim_id: str | None = None
    execution_claim_step_id: str | None = None
    execution_claim_attempt: int = 0
    execution_claim_expired: bool = False
    previous_step_id: str | None = None


class DurableObjectiveCycleApiBackend:
    """In-process durable command/query backend that drives the deterministic cycle engine."""

    def __init__(
        self,
        *,
        mode: str = "inprocess",
        repository: ObjectiveCycleRepository | None = None,
        real_execute_enabled: bool = False,
        require_workhub_load: bool = True,
        create_workhub_tasks: bool = False,
        mark_workhub_done: bool = False,
        settings: object | None = None,
        projection_store: ProjectionStore | None = None,
        session_lock_store: InMemorySessionLockStore | None = None,
        attempt_journal: InMemoryAttemptJournal | None = None,
        evidence_store: InMemoryEvidenceStore | None = None,
        workspace_store: WorkspaceLeaseStore | None = None,
        execution_port: ExecutionPort | None = None,
        muse_session_port: MuseSessionPort | None = None,
        muse_binding_lookup: ActivationBindingLookup | None = None,
        # Lane-ops #23 M3: credentialed owner-side assignment/lease
        # preflight handle, threaded into the Muse adapter. Deployment
        # binds the credentialed WorkHub transport; until then Muse
        # routes refuse MUSE_PREFLIGHT_UNBOUND instead of inferring
        # entitlement from the stored binding alone.
        muse_preflight_verifier: MusePreflightVerifier | None = None,
        framework_flags: FrameworkFlagsSnapshot | None = None,
        gap_store_path: Path | str | None = None,
        gap_store_pg_conn: Any | None = None,
        gap_store_database_url: str | None = None,
        autonomous_repair_enabled: bool = False,
    ) -> None:
        if mode not in {"inprocess", "postgres"}:
            raise ValueError(f"unsupported durable backend mode: {mode!r}")
        if mode == "postgres" and repository is None:
            raise ValueError("postgres mode requires ObjectiveCycleRepository")
        self.mode = mode
        self._repository = repository
        self.real_execute_enabled = bool(real_execute_enabled)
        self.require_workhub_load = bool(require_workhub_load)
        self.create_workhub_tasks = bool(create_workhub_tasks)
        self.mark_workhub_done = bool(mark_workhub_done)
        self._settings = settings
        self._execution_port = execution_port if execution_port is not None else RealExecuteAdapter()
        # Lane-ops #23 worker slice: the Muse adapter is only reachable when
        # the deployment binds BOTH a session transport and the canonical
        # activation binding lookup. Otherwise a Muse-routed step refuses
        # explicitly instead of falling back to the foreground executor.
        self._muse_adapter = (
            MuseExecuteAdapter(
                session_port=muse_session_port, binding_lookup=muse_binding_lookup,
                preflight_verifier=muse_preflight_verifier,
            )
            if muse_session_port is not None and muse_binding_lookup is not None
            else None
        )
        self._framework_flags = framework_flags or FrameworkFlagsSnapshot.from_settings(
            settings if settings is not None else object()
        )
        # C011: shared projection store lets a fresh backend rehydrate decision state
        # after process death (tests pass the same store; production uses postgres rows).
        self._projections = projection_store if projection_store is not None else InMemoryProjectionStore()
        # C012: exclusive per-run lease for mutating commands.
        self._session_locks = (
            session_lock_store if session_lock_store is not None else InMemorySessionLockStore()
        )
        # C013: write-ahead attempt journal (share across backends for restart sims).
        self._attempt_journal = attempt_journal if attempt_journal is not None else InMemoryAttemptJournal()
        # C015: content-addressed evidence.
        self._evidence = evidence_store if evidence_store is not None else InMemoryEvidenceStore()
        # C016: per-run workspace lease store.
        self._workspaces = workspace_store if workspace_store is not None else WorkspaceLeaseStore()
        # C017/C018/C019 policy surfaces (immutable defaults; tests may replace).
        self._env_fingerprint = discover_environment()
        self._capabilities = default_registry()
        self._boundary = default_boundary_policy()
        # GATE-D: P19-P22 lifecycle integration (single instance per backend).
        self._lifecycle = ObjectiveLifecycleIntegration()
        # Test-only fault injection: crash after BEGIN, before effects/COMMIT.
        self._c013_fault_after_begin = False
        # Prefer longer timeouts for local live certification under Windows.
        if self._repository is not None and getattr(self._repository, "_command_timeout_seconds", 5.0) < 15.0:
            self._repository._command_timeout_seconds = 15.0  # noqa: SLF001
        self._objectives: dict[str, _ObjectiveRecord] = {}
        self._works: dict[str, Any] = {}
        self._framework_checkpoints: list[Any] = []
        self._f10_compiles: dict[str, Any] = {}
        self._f10_sessions: dict[str, Any] = {}
        self._work_graph_store: Any = None
        self._scale_sessions: dict[str, Any] = {}
        self._provenance = ProvenanceHistory()
        self._durable_provenance = None
        self._last_context_bundle = None
        if bool(getattr(self._framework_flags, "scale", False)):
            from backend.aix_core.framework.scale.work_graph_store import WorkGraphStore

            self._work_graph_store = WorkGraphStore()
        if bool(getattr(self._framework_flags, "durable_provenance", False)):
            from backend.aix_core.framework.context.provenance_store import DurableProvenanceStore

            self._durable_provenance = DurableProvenanceStore()
        self._approvals: dict[str, ApprovalBinding] = {}
        self._cycles: dict[str, _CycleRecord] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        # AIX Closed-Loop Execution Plan, Sprint W2: ObjectiveEnvelopeV1 (contracts.py,
        # the reused WorkOrder layer) constructed best-effort at admit time. Keyed by
        # objective_id so it shares identity with `self._objectives`, not a second
        # object-identity scheme. `_envelope_construction_errors` records *why* an
        # objective has no envelope (e.g. an unsupported task_class) instead of
        # silently leaving that unexplained -- `run_real_execute`'s gate reads this
        # for its error detail.
        self._envelopes: dict[str, ObjectiveEnvelopeV1] = {}
        self._envelope_construction_errors: dict[str, tuple[str, ...]] = {}
        # C020: ObjectiveContractV1 from mandatory understanding (keyed by objective_id).
        self._objective_contracts: dict[str, ObjectiveContractV1] = {}
        # F12.2 Packet 01: CanonicalObjectiveTruthV1 and ObjectiveVerificationContractV1 (OVC)
        self._canonical_truths: dict[str, CanonicalObjectiveTruthV1] = {}
        self._objective_verification_contracts: dict[str, ObjectiveVerificationContractV1] = {}
        # F12.3 Packet 02: TerminalizationDecisionV1
        self._terminalization_decisions: dict[str, TerminalizationDecisionV1] = {}
        self._verification_decisions: dict[str, VerificationDecisionV1] = {}
        # F12.4 Packet 04: Durable GapStore
        self._gap_store: GapStore = GapStore(
            persist_path=gap_store_path,
            pg_conn=gap_store_pg_conn,
            database_url=gap_store_database_url,
        )
        # Phase 1 controlled refactor: verification + terminalization authority delegated
        # to focused services; engine remains sole phase writer via TerminalizationService.
        self._terminalization_service = TerminalizationService(gap_store=self._gap_store)
        self._verification_service = VerificationService(gap_store=self._gap_store)
        # F12.6 Packet 06: Autonomous repair coordinator (wired to backend commands)
        self._repair_coordinator = AutonomousRepairCoordinator(max_repair_attempts=3)
        self._cost_ledger = CostLedger(max_objective_cost_usd=5.0, max_repair_cost_usd=5.0)
        self._autonomous_repair_enabled = bool(autonomous_repair_enabled)
        # DoD architecture: sealed OutcomeContractV1 (facet ops/entities/mappings).
        # Must exist before decomposition is authoritative when require_outcome_contract.
        self._outcome_contracts: dict[str, Any] = {}
        self._integration_proofs: dict[str, Any] = {}
        self._outcome_contract_proposals: dict[str, Any] = {}
        self._outcome_contract_obligations: dict[str, tuple[OutcomeContractObligationV1, ...]] = {}
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2: the durable ObjectiveObligationV1
        # set derived at admission (see obligations.derive_obligations), keyed to
        # self._objectives by objective_id. Every admitted actionable objective (a
        # non-empty objective/definition_of_done) gets at least one entry here -- this
        # is the binding record of what was actually asked for, consulted by Packet P3's
        # coverage gate and Packet P13's ObjectiveLevelVerifier.
        self._obligations: dict[str, tuple[ObjectiveObligationV1, ...]] = {}
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P7: one ExecutorPackageV1 per real
        # dispatch attempt (a retry appends, never replaces -- mirrors
        # `self._step_attempts`' own immutability discipline), the authoritative
        # record of what the executor actually received for that attempt.
        self._executor_packages: dict[str, list[ExecutorPackageV1]] = {}
        # AIX Closed-Loop Execution Plan, Sprint W4/W6: the last UnderstandingEvidenceV1
        # snapshot collected for an objective, cached so _maybe_real_execute (a later,
        # separate call -- possibly after resume/retry) can hand it to the Sprint W5
        # planner without re-exploring. Sprint W6: the StepContractV1 that planner
        # proposes, plus a refusal reason when it declines (mirrors the envelope pair
        # above).
        self._understanding_evidence: dict[str, UnderstandingEvidenceV1] = {}
        self._step_contracts: dict[str, StepContractV1] = {}
        self._step_contract_errors: dict[str, str] = {}
        # AIX-MPE-003 Packet MPE-01: the durable, versioned multi-step plan container.
        # Mirrors `self._step_contracts` today -- every successful proposal above is
        # also wrapped into a single-step `PlanRevisionV1` and appended here, keyed by
        # objective_id, oldest revision first. `self._step_contracts` remains the
        # hot-path single-step read used by the rest of `_maybe_real_execute` until
        # Packet MPE-02 upgrades the planner to emit real multi-step plans and later
        # packets migrate the hot path onto this store; this field is the new canonical
        # record, not a second competing owner (see `_admit_plan_revision`/
        # `_active_plan_revision` below).
        self._plan_revisions: dict[str, list[PlanRevisionV1]] = {}
        self._step_runtime: dict[str, dict[str, Any]] = {}
        # AIX-PD-001: structured NEEDS_MORE_EVIDENCE history. Fingerprints block
        # repeated identical requests; revisions are the exploration replies.
        self._planner_evidence_fingerprints: dict[str, list[str]] = {}
        self._evidence_revisions: dict[str, list[dict[str, Any]]] = {}
        self._planner_trace: dict[str, list[dict[str, Any]]] = {}
        # AIX-PA-002: immutable planner-call artifacts (raw model JSON + budget).
        self._planner_calls: dict[str, list[dict[str, Any]]] = {}
        self._loop_trackers: dict[str, AutonomousLoopTracker] = {}
        self._repair_directives: dict[str, list[Any]] = {}
        self._failure_evidence: dict[str, list[FailureEvidenceV1]] = {}
        self._normalized_failures: dict[str, list[NormalizedFailureRecord]] = {}
        self._repair_hypotheses: dict[str, list[RepairHypothesisV1]] = {}
        self._failure_reports: dict[str, list[FailureReportV1]] = {}
        self._feedback_collector = AgentFeedbackCollector()
        # AIX Closed-Loop Execution Plan, Sprint W8: one or more StepAttemptV1 per
        # objective (a retry appends, per the architecture's "later attempts append"
        # immutability rule -- never replaces an earlier attempt's record).
        self._step_attempts: dict[str, list[StepAttemptV1]] = {}
        # Parallel to _step_attempts: unified diffs for cumulative semantic judgment
        # (avoids fragile file:// artifact path parsing across repair retries).
        self._attempt_diffs: dict[str, list[str]] = {}
        # AIX Closed-Loop Execution Plan, Sprint W9: one SemanticVerificationOutcome per
        # attempt (mirrors _step_attempts' list-per-objective shape).
        self._verification_verdicts: dict[str, list[SemanticVerificationOutcome]] = {}
        # AIX Closed-Loop Execution Plan, Sprint W10: one ReplanDecisionV1 per replan
        # actually authorized (list -- multiple replans across a cycle's lifetime are
        # legitimate, each is its own immutable decision record, never overwritten).
        self._replan_decisions: dict[str, list[ReplanDecisionV1]] = {}
        # Autonomous Closed-Loop Lane, Packet AL-11: one CycleOutcomeV1 per cycle, recorded
        # exactly once the cycle reaches a genuinely terminal phase (see cycle_outcome.py's
        # is_genuinely_terminal_phase()). Keyed by cycle_id, not objective_id, since a
        # `clarify`-driven re-admission can in principle span more than one cycle_id per
        # objective; presence of a key here is this cache's own idempotency guard.
        self._cycle_outcomes: dict[str, CycleOutcomeV1] = {}
        # Durable execution authorization (restart-safe, sealed to workspace
        # disk). Human JWTs arrive only at the HTTP edge; Temporal activities
        # resolve by (tenant_id, cycle_id). No in-memory auth fallback exists
        # by design: a fresh process resolves the same records.
        self._execution_auth = ExecutionAuthStore(_workspace_base(settings))
        self._last_strategy: dict[str, str] = {}
        # One claim critical section per cycle. The claim is persisted before the
        # lock is released; duplicate dispatcher calls observe EXECUTING and cannot
        # enter the executor. The attempt journal remains the second, effect-level
        # idempotency boundary.
        self._dispatch_locks: dict[str, asyncio.Lock] = {}
        # In-flight background execute tasks (keyed by cycle_id). When
        # OBJECTIVE_CYCLE_AWAIT_EXECUTION=0, dispatch returns EXECUTING immediately
        # so /health and FE polls stay responsive during planner/codegen.
        self._execute_tasks: dict[str, asyncio.Task[Any]] = {}
        await_setting = getattr(settings, "OBJECTIVE_CYCLE_AWAIT_EXECUTION", None) if settings is not None else None
        if await_setting is not None:
            self._await_execution = bool(await_setting)
        else:
            await_raw = str(
                os.getenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_AWAIT_EXECUTION", "0")
                or "0"
            ).strip().lower()
            self._await_execution = await_raw not in {"0", "false", "no", "off"}
        refactored_raw = str(
            os.getenv("AIX_OBJECTIVE_REFACTORED_RUNTIME", "0") or "0"
        ).strip().lower()
        self.refactored_runtime_enabled = refactored_raw in {"1", "true", "yes", "on"}
        # PLAT-D2 single dispatch ownership: when Temporal mode is on, the
        # Temporal workflow's dispatch_step activity is the ONLY executor of
        # dispatch_ready_step. Backend background self-drive is parked at
        # STEP_READY so it can never race the worker. Operator-solicited
        # inline calls (resume/clarify/repair/retry, sync tests) are
        # unaffected; only the unsolicited background schedulers yield.
        temporal_setting = getattr(settings, "TEMPORAL_ENABLED", None) if settings is not None else None
        if temporal_setting is not None:
            self._temporal_owns_dispatch = bool(temporal_setting)
        else:
            temporal_raw = str(
                os.getenv("HYEVE_AIX_SERVICE_TEMPORAL_ENABLED", "0") or "0"
            ).strip().lower()
            self._temporal_owns_dispatch = temporal_raw in {"1", "true", "yes", "on"}
        # Reserved for future shadow/differential mode only. Incremental extraction runs
        # inside this backend; do not swap to the partial RefactoredObjectiveCycleApiBackend.
        if self.refactored_runtime_enabled:
            logger.info(
                "AIX_OBJECTIVE_REFACTORED_RUNTIME=1: service extraction active in monolith "
                "(no alternate backend swap)"
            )

    async def command(
        self,
        *,
        operation: str,
        tenant_id: str,
        actor_id: str,
        resource_id: str | None,
        body: dict[str, Any],
        idempotency_key: str,
        expected_version: int | None,
    ) -> dict[str, Any]:
        try:
            claims_from_request(tenant_id=tenant_id, actor_id=actor_id, body=body)
            cache_key = bind_idempotency_key(
                tenant_id=tenant_id, operation=operation, idempotency_key=idempotency_key
            )
        except AuthzError as exc:
            return authz_error_payload(operation=operation, tenant_id=tenant_id, exc=exc)

        if cache_key in self._idempotency:
            return dict(self._idempotency[cache_key])

        _cycle_mutate_ops = {
            "clarify_cycle",
            "resume_cycle",
            "retry_step",
            "replan_cycle",
            "validate_approval_reference",
            "reject_cycle",
            "block_cycle",
            "dispatch_ready_step",
            "initiate_gap_repair",
            "replan_from_gaps",
            "repair_cycle",
            "reverify_and_terminalize",
        }
        # Cancellation is an out-of-band interrupt, not an ordinary serialized
        # mutation.  It must be able to set the cooperative cancellation flag while
        # the approval command owns the run lock and is awaiting a subprocess.
        lock_holder: str | None = None
        lock_run_id: str | None = None
        if operation in _cycle_mutate_ops:
            lock_run_id = str(resource_id or "")
            lock_holder = f"{actor_id}:{idempotency_key}"
            try:
                self._session_locks.acquire(
                    tenant_id=tenant_id,
                    run_id=lock_run_id,
                    holder=lock_holder,
                    version_at_acquire=expected_version,
                )
            except SessionLockError as exc:
                return lock_conflict_payload(operation=operation, tenant_id=tenant_id, exc=exc)

        try:
            if operation == "admit_objective":
                result = await self._admit(tenant_id=tenant_id, actor_id=actor_id, body=body, command_id=idempotency_key)
            elif operation == "start_cycle":
                result = await self._start_cycle(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    objective_id=str(resource_id or ""),
                    body=body,
                    command_id=idempotency_key,
                )
            elif operation == "cancel_cycle":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="cancel",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "clarify_cycle":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="clarify",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "resume_cycle":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="resume",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "retry_step":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="retry_step",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "dispatch_ready_step":
                # Temporal activities carry no credentials by design; resolve
                # the sealed execution record so dispatch survives restarts.
                # Lane-ops #23 M1: the activity command body carries the
                # already-owned opaque dispatch refs (never credentials);
                # they travel into the execution request for exact pre-spawn
                # comparison. Absent refs refuse at the adapter, never fall
                # back to another executor.
                activity_auth = str(body.get("caller_authorization") or "").strip() or None
                result = await self.dispatch_ready_step(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    command_id=idempotency_key,
                    caller_auth=self._resolve_execution_auth(
                        tenant_id=tenant_id,
                        cycle_id=str(resource_id or ""),
                        caller_auth=activity_auth,
                    ),
                    actor_id=actor_id,
                    owner_refs=muse_owner_refs_from_mapping(body),
                )
            elif operation == "replan_cycle":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="replan",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "validate_approval_reference":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="approval_reference",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "initiate_gap_repair":
                result = await self._initiate_gap_repair(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "replan_from_gaps":
                result = await self._replan_from_gaps(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "repair_cycle":
                result = await self._repair_cycle(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "reverify_and_terminalize":
                result = await self._reverify_and_terminalize(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "reject_cycle":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="reject",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            elif operation == "block_cycle":
                result = await self._control(
                    tenant_id=tenant_id,
                    cycle_id=str(resource_id or ""),
                    expected_version=expected_version,
                    action="block",
                    body=body,
                    command_id=idempotency_key,
                    actor_id=actor_id,
                )
            else:
                result = {
                    "ok": False,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "error": {"code": "UNKNOWN_OPERATION"},
                }
        finally:
            if lock_holder is not None and lock_run_id is not None:
                try:
                    self._session_locks.release(
                        tenant_id=tenant_id, run_id=lock_run_id, holder=lock_holder
                    )
                except SessionLockError:
                    pass

        self._idempotency[cache_key] = dict(result)
        return result

    async def query(
        self,
        *,
        operation: str,
        tenant_id: str,
        resource_id: str | None,
        child_id: str | None,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            require_tenant(tenant_id)
        except AuthzError as exc:
            return authz_error_payload(operation=operation, tenant_id=tenant_id, exc=exc)
        if operation == "list_objectives":
            items = [self._objective_payload(o) for o in self._objectives.values() if o.tenant_id == tenant_id]
            return {"ok": True, "operation": operation, "tenant_id": tenant_id, "items": items, "count": len(items)}
        if operation == "get_objective":
            obj = self._objectives.get(str(resource_id or ""))
            if obj is None or obj.tenant_id != tenant_id:
                await self.rehydrate_objective(tenant_id=tenant_id, objective_id=str(resource_id or ""))
                obj = self._objectives.get(str(resource_id or ""))
            if obj is None or obj.tenant_id != tenant_id:
                return {"ok": False, "operation": operation, "tenant_id": tenant_id, "error": {"code": "OBJECTIVE_NOT_FOUND"}}
            return {"ok": True, "operation": operation, "tenant_id": tenant_id, "item": self._objective_payload(obj)}
        if operation == "get_world_state":
            # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P6: one coherent, queryable
            # snapshot of what is currently true about this objective, assembled from
            # the already-existing per-objective caches -- never reconstructed from
            # chat history or prior agent prose.
            obj = self._objectives.get(str(resource_id or ""))
            if obj is None or obj.tenant_id != tenant_id:
                return {"ok": False, "operation": operation, "tenant_id": tenant_id, "error": {"code": "OBJECTIVE_NOT_FOUND"}}
            cycle_id = str(child_id or "").strip() or None
            cycle = self._cycles.get(cycle_id) if cycle_id else None
            if cycle is None:
                cycle = next(
                    (c for c in self._cycles.values() if c.objective_id == obj.objective_id), None
                )
            intent = self._provenance.get_intent(obj.objective_id)
            understanding_rev = self._provenance.latest_understanding(obj.objective_id)
            plan = self._provenance.latest_plan(obj.objective_id)
            evidence_items = self._provenance.evidence(obj.objective_id)
            attempt_items = self._provenance.attempts(obj.objective_id)
            cited_evidence_ids = (
                plan.evidence_ids if plan is not None else tuple(item.id for item in evidence_items)
            )
            cited_locations = tuple(
                item.location
                for item in evidence_items
                if item.id in set(cited_evidence_ids) and item.location
            )
            world_state = build_world_state(
                objective_id=obj.objective_id,
                cycle_id=cycle.cycle_id if cycle is not None else None,
                objective=self._intent_text(obj),
                obligations=self._obligations.get(obj.objective_id, ()),
                step_contracts=(
                    (self._step_contracts[obj.objective_id],) if obj.objective_id in self._step_contracts else ()
                ),
                verification_verdicts=tuple(self._verification_verdicts.get(obj.objective_id, ())),
                step_attempts=tuple(self._step_attempts.get(obj.objective_id, ())),
                plan_revision=(
                    plan.revision if plan is not None else (cycle.plan_revision if cycle is not None else 0)
                ),
                understanding=self._understanding_evidence.get(obj.objective_id),
                retries_remaining=cycle.retry_budget_remaining if cycle is not None else None,
                replans_remaining=cycle.replan_budget_remaining if cycle is not None else None,
                intent_id=intent.id if intent is not None else None,
                understanding_revision_id=understanding_rev.id if understanding_rev is not None else None,
                plan_id=plan.plan_id if plan is not None else None,
                evidence_ids=cited_evidence_ids,
                attempt_ids=tuple(item.id for item in attempt_items),
                provenance_known_facts=cited_locations if intent is not None else None,
            )
            return {
                "ok": True,
                "operation": operation,
                "tenant_id": tenant_id,
                "item": world_state.model_dump(mode="json"),
            }
        if operation == "get_executor_packages":
            # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P7: "what authoritative package
            # did the executor receive" for every real dispatch attempt on this
            # objective, oldest first.
            obj = self._objectives.get(str(resource_id or ""))
            if obj is None or obj.tenant_id != tenant_id:
                return {"ok": False, "operation": operation, "tenant_id": tenant_id, "error": {"code": "OBJECTIVE_NOT_FOUND"}}
            packages = self._executor_packages.get(obj.objective_id, [])
            return {
                "ok": True,
                "operation": operation,
                "tenant_id": tenant_id,
                "items": [p.model_dump(mode="json") for p in packages],
                "count": len(packages),
            }
        if operation == "get_failure_reports":
            obj = self._objectives.get(str(resource_id or ""))
            if obj is None or obj.tenant_id != tenant_id:
                return {"ok": False, "operation": operation, "tenant_id": tenant_id, "error": {"code": "OBJECTIVE_NOT_FOUND"}}
            reports = self._failure_reports.get(obj.objective_id, [])
            feedback = self._feedback_collector.to_dict(obj.objective_id)
            return {
                "ok": True,
                "operation": operation,
                "tenant_id": tenant_id,
                "items": [r.model_dump(mode="json") for r in reports],
                "count": len(reports),
                "feedback": feedback,
                "summary": summarize_failure_chain(
                    self._feedback_collector.get_reports(obj.objective_id)
                ),
            }
        if operation == "list_cycles":
            items = [self._cycle_payload(c) for c in self._cycles.values() if c.tenant_id == tenant_id]
            state = filters.get("state")
            if state:
                items = [item for item in items if item.get("phase") == state]
            limit = int(filters.get("limit") or 50)
            return {
                "ok": True,
                "operation": operation,
                "tenant_id": tenant_id,
                "items": items[:limit],
                "count": len(items[:limit]),
            }
        if operation == "get_cycle":
            cycle_id = str(resource_id or "")
            cycle = self._cycles.get(cycle_id)
            if cycle is None or cycle.tenant_id != tenant_id:
                await self.rehydrate_cycle(tenant_id=tenant_id, cycle_id=cycle_id)
                cycle = self._cycles.get(cycle_id)
            if cycle is not None and cycle.tenant_id == tenant_id:
                return {
                    "ok": True,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "item": self._cycle_payload(cycle),
                }
            # After AIX restart, engines are gone but workspaces remain — serve those.
            disk = _workspace_snapshot(tenant_id=tenant_id, cycle_id=cycle_id, settings=self._settings)
            if disk is not None:
                return {"ok": True, "operation": operation, "tenant_id": tenant_id, "item": disk}
            return {
                "ok": False,
                "operation": operation,
                "tenant_id": tenant_id,
                "error": {"code": "CYCLE_NOT_FOUND"},
            }
        if operation == "get_diff":
            cycle = self._cycles.get(str(resource_id or ""))
            if cycle is None or cycle.tenant_id != tenant_id:
                disk = _workspace_snapshot(tenant_id=tenant_id, cycle_id=str(resource_id or ""), settings=self._settings)
                re = (disk or {}).get("real_execute") if isinstance((disk or {}).get("real_execute"), dict) else {}
            else:
                re = cycle.real_execute or {}
            if not re:
                return {"ok": False, "operation": operation, "tenant_id": tenant_id, "error": {"code": "CYCLE_NOT_FOUND"}}
            return {
                "ok": True,
                "operation": operation,
                "tenant_id": tenant_id,
                "cycle_id": str(resource_id or ""),
                "diff": re.get("diff"),
                "changed_source_files": re.get("changed_source_files") or [],
                "run_artifacts": re.get("run_artifacts") or [],
                "repository_root": re.get("repository_root"),
                "workhub_lifecycle_authority": False,
            }
        if operation in {"get_plan", "get_steps"}:
            cycle = self._cycles.get(str(resource_id or ""))
            if cycle is None or cycle.tenant_id != tenant_id:
                await self.rehydrate_cycle(tenant_id=tenant_id, cycle_id=str(resource_id or ""))
                cycle = self._cycles.get(str(resource_id or ""))
            if cycle is None or cycle.tenant_id != tenant_id:
                return {
                    "ok": False,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "error": {"code": "CYCLE_NOT_FOUND"},
                }

            revisions = list(self._plan_revisions.get(cycle.objective_id, ()))
            plan = revisions[-1] if revisions else None
            planner_calls = list(self._planner_calls.get(cycle.objective_id, ()))
            if plan is None:
                return {
                    "ok": False,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "error": {"code": "PLAN_NOT_FOUND"},
                    "planner_calls": planner_calls,
                }

            plan_payload = plan.model_dump(mode="json")
            if operation == "get_plan":
                # Keep ``item`` for the generic API envelope while also exposing the
                # actual PlanRevision fields at the response root.  Older clients can
                # continue reading the envelope; closed-loop clients no longer have to
                # interpret a cycle status blob as a plan.
                return {
                    "ok": True,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "cycle_id": cycle.cycle_id,
                    **plan_payload,
                    "item": plan_payload,
                    "prior_revisions": [p.model_dump(mode="json") for p in revisions[:-1]],
                    "planner_calls": planner_calls,
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }

            runtime = self._step_runtime.get(cycle.objective_id, {})
            ledger: list[dict[str, Any]] = []
            for step in plan.steps:
                state = runtime.get(step.id)
                entry = step.model_dump(mode="json")
                entry["runtime"] = state.model_dump(mode="json") if state is not None else None
                entry["status"] = state.status if state is not None else "PENDING"
                ledger.append(entry)
            return {
                "ok": True,
                "operation": operation,
                "tenant_id": tenant_id,
                "cycle_id": cycle.cycle_id,
                "plan_id": plan.plan_id,
                "plan_revision": plan.revision,
                "active_step_id": cycle.current_step_cursor,
                "items": ledger,
                "steps": ledger,
                "count": len(ledger),
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }
        if operation in {
            "get_step",
            "get_attempts",
            "get_budgets",
            "get_events",
            "get_agents",
            "get_evidence",
            "get_review_packet",
            "replay_evidence",
        }:
            cycle = self._cycles.get(str(resource_id or ""))
            if cycle is None or cycle.tenant_id != tenant_id:
                # Post-restart: engines are gone; still expose disk evidence for panels.
                disk = _workspace_snapshot(tenant_id=tenant_id, cycle_id=str(resource_id or ""), settings=self._settings)
                if disk is None:
                    return {"ok": False, "operation": operation, "tenant_id": tenant_id, "error": {"code": "CYCLE_NOT_FOUND"}}
                recorded = str(disk.get("created_at") or "")
                re = disk.get("real_execute") if isinstance(disk.get("real_execute"), dict) else {}
                events = [
                    {
                        "event_type": "attempt.succeeded" if disk.get("phase") == "COMPLETED" else "attempt.failed",
                        "occurred_at": recorded,
                        "payload_refs": {
                            "attempt_id": "attempt-1",
                            "step_id": "step-1",
                            "executor_ref": (re or {}).get("executor") or "objective_cycle.real_execute.v1",
                        },
                    }
                ]
                return {
                    "ok": True,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "cycle_id": str(resource_id or ""),
                    "child_id": child_id,
                    "status": disk.get("status") or {"phase": disk.get("phase")},
                    "events": events,
                    "real_execute": re,
                    "item": disk,
                    "source": "workspace_disk",
                    "workhub_lifecycle_authority": False,
                }
            status = cycle.engine.query_status() if cycle.engine is not None else {"phase": cycle.phase}
            response = {
                "ok": True,
                "operation": operation,
                "tenant_id": tenant_id,
                "cycle_id": cycle.cycle_id,
                "child_id": child_id,
                "status": status,
                "events": list(cycle.events),
                "real_execute": cycle.real_execute,
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }
            if operation == "get_evidence":
                response["planner_calls"] = list(self._planner_calls.get(cycle.objective_id, ()))
                response["failure_evidence"] = [
                    item.model_dump(mode="json")
                    for item in self._failure_evidence.get(cycle.objective_id, ())
                ]
                response["normalized_failures"] = [
                    asdict(item)
                    for item in self._normalized_failures.get(cycle.objective_id, ())
                ]
                response["repair_hypotheses"] = [
                    asdict(item)
                    for item in self._repair_hypotheses.get(cycle.objective_id, ())
                ]
                failure_artifacts: list[dict[str, Any]] = []
                for failure in self._failure_evidence.get(cycle.objective_id, ()):
                    for ref in failure.artifact_refs:
                        if not str(ref).startswith("evidence://"):
                            continue
                        try:
                            record = self._evidence.verify(
                                tenant_id=tenant_id,
                                evidence_id=str(ref).removeprefix("evidence://"),
                            )
                        except Exception:
                            continue
                        failure_artifacts.append(record.to_dict())
                response["failure_artifacts"] = failure_artifacts
            return response
        return {"ok": False, "operation": operation, "tenant_id": tenant_id, "error": {"code": "UNKNOWN_OPERATION"}}

    def _ensure_frozen_intent(self, obj: _ObjectiveRecord) -> UserIntent:
        existing = self._provenance.get_intent(obj.objective_id)
        if existing is not None:
            return existing
        text = obj.original_objective or obj.objective
        intent = freeze_user_intent(
            objective_id=obj.objective_id,
            text=text,
            definition_of_done=tuple(obj.definition_of_done),
            source_message_id=obj.objective_source_message_id,
            conversation_id=obj.conversation_id,
        )
        stored = self._provenance.put_intent(intent)
        self._mirror_durable_intent(stored)
        if not obj.original_objective:
            obj.original_objective = original_objective_alias(stored)
        return stored

    def _mirror_durable_intent(self, intent: UserIntent) -> None:
        """When F3 flag is on, mirror intent into DurableProvenanceStore (additive)."""
        store = getattr(self, "_durable_provenance", None)
        if store is None:
            return
        store.record_intent_sync(
            objective_id=intent.objective_id,
            intent_text=intent.text,
            intent_id=intent.id,
        )

    def _build_execute_context_capsule(
        self,
        *,
        obj: _ObjectiveRecord,
        cycle: _CycleRecord,
        active_step_contract: StepContractV1 | None,
        execution_scope: tuple[str, ...],
        previous_failure: str | None,
        intent: Any,
        cited_claims: tuple[Any, ...],
        cited_items: tuple[Any, ...],
        prov_plan: Any,
    ):
        """Legacy capsule build, or F3 ContextAssemblyPipeline when flag on."""
        step_id = (
            active_step_contract.id
            if active_step_contract is not None
            else f"implicit-step:{cycle.cycle_id}"
        )
        kwargs = dict(
            step_id=step_id,
            understanding=self._understanding_evidence.get(obj.objective_id),
            target_scope=execution_scope,
            invariants=(
                active_step_contract.prohibited_actions if active_step_contract is not None else ()
            ),
            relevant_previous_failure=previous_failure,
            intent_id=intent.id if intent is not None else None,
            intent_text=intent.text if intent is not None else None,
            cited_claim_ids=tuple(c.id for c in cited_claims),
            cited_evidence_ids=tuple(e.id for e in cited_items),
            plan_target_paths=execution_scope,
            plan_id=prov_plan.plan_id if prov_plan is not None else None,
        )
        if not bool(getattr(self._framework_flags, "durable_provenance", False)):
            return build_step_context_capsule(**kwargs)

        from backend.aix_core.framework.context.assembly import ContextAssemblyPipeline
        from backend.aix_core.framework.context.compression import ContextCompressor

        bundle, capsule = ContextAssemblyPipeline().assemble(
            **kwargs,
            step_contract=active_step_contract,
        )
        compressed = ContextCompressor.compress(bundle)
        self._last_context_bundle = compressed
        return capsule

    def _append_user_turn_for_objective(
        self, obj: _ObjectiveRecord, text: str, *, source_message_id: str | None = None
    ) -> None:
        """Clarify/replan persist a UserTurn. Intent.text and original_objective stay frozen."""
        body = str(text or "").strip()
        if not body:
            return
        intent = self._ensure_frozen_intent(obj)
        turn = append_user_turn(intent=intent, text=body, source_message_id=source_message_id)
        self._provenance.append_turn(turn)
        self._provenance.assert_intent_unchanged(obj.objective_id, original_objective_alias(intent))
        family_result = classify_task_family(self._provenance.user_corpus(obj.objective_id))
        obj.task_family = family_result.task_family
        obj.classification_method = family_result.classification_method
        obj.classification_confidence = family_result.classification_confidence
        if obj.execution_context:
            obj.execution_context["understanding_complete"] = False
        self._refresh_understanding_v1(obj)

    def _refresh_understanding_v1(self, obj: _ObjectiveRecord) -> UnderstandingV1Result | None:
        intent = self._provenance.get_intent(obj.objective_id)
        if intent is None:
            return None
        result = build_understanding_v1(
            intent=intent,
            turns=self._provenance.turns(obj.objective_id),
            parent=self._provenance.latest_understanding(obj.objective_id),
            # Prefer persisted Governance so create_root / create_capability match U3 + greenfield CREATE.
            # Absent Governance still treats default create-root names (e.g. proposed/) as non-SCOPE_IN.
            governance=self._provenance.latest_governance(obj.objective_id),
        )
        self._provenance.put_understanding(result.revision)
        return result

    def _ensure_governance(
        self, obj: _ObjectiveRecord, envelope: object | None = None
    ) -> GovernanceRevision:
        existing = self._provenance.latest_governance(obj.objective_id)
        if existing is not None:
            return existing
        self._ensure_frozen_intent(obj)
        budgets = (
            envelope.budget_ceiling
            if envelope is not None and getattr(envelope, "budget_ceiling", None) is not None
            else BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)
        )
        forbidden = tuple(getattr(envelope, "forbidden_effects", ()) or ())
        policy = str(getattr(envelope, "approval_policy", None) or "INITIAL_PLAN_ONLY")
        gov = default_governance(
            objective_id=obj.objective_id,
            task_class=str(obj.task_class or "code_change"),
            budgets=budgets,
            forbidden_effects=forbidden,
            approval_policy=policy,
        )
        return self._provenance.put_governance(gov)

    def _planner_provenance_kwargs(self, obj: _ObjectiveRecord, cycle: _CycleRecord, envelope: object | None) -> dict[str, object]:
        self._ensure_frozen_intent(obj)
        if self._provenance.latest_understanding(obj.objective_id) is None:
            self._refresh_understanding_v1(obj)
        if self._provenance.latest_governance(obj.objective_id) is None:
            self._ensure_governance(obj, envelope)
        intent = self._ensure_frozen_intent(obj)
        understanding = self._provenance.latest_understanding(obj.objective_id)
        if understanding is None:
            self._refresh_understanding_v1(obj)
            understanding = self._provenance.latest_understanding(obj.objective_id)
        governance = self._ensure_governance(obj, envelope)
        evidence = self._provenance.evidence(obj.objective_id)
        if not evidence and obj.allowed_files:
            self._ensure_exploration_evidence_for_paths(
                obj=obj,
                paths=tuple(
                    str(p).strip().replace("\\", "/")
                    for p in obj.allowed_files
                    if str(p).strip()
                ),
            )
            self._reconcile_understanding_from_exploration_evidence(obj)
            evidence = self._provenance.evidence(obj.objective_id)
            understanding = self._provenance.latest_understanding(obj.objective_id)
        needs = self._provenance.needs(obj.objective_id)
        if understanding is None:
            return {}
        request = build_plan_request(
            cycle_id=cycle.cycle_id,
            intent=intent,
            turns=self._provenance.turns(obj.objective_id),
            understanding=understanding,
            evidence=evidence,
            governance=governance,
        )
        return {
            "objective": intent.text,
            "plan_request": request,
            "intent": intent,
            "understanding_revision": understanding,
            "needs": needs,
            "exploration_evidence": evidence,
            "governance": governance,
            "plan_revision": (
                (self._provenance.latest_plan(obj.objective_id).revision + 1)
                if self._provenance.latest_plan(obj.objective_id) is not None
                else 1
            ),
        }

    def _demo_capability_from_accepted_plan(self, obj: _ObjectiveRecord) -> bool:
        """K16: HTML/demo lane only if an accepted CREATE_FILE target sits in Governance create_root."""
        plan = self._provenance.latest_plan(obj.objective_id)
        gov = self._provenance.latest_governance(obj.objective_id)
        if plan is None or gov is None or plan.status != "ACCEPTED":
            return False
        if "CREATE_FILE" not in gov.create_capability:
            return False
        for target in plan.targets:
            if target.kind != "CREATE" or target.create_capability != "CREATE_FILE":
                continue
            if not target.create_root:
                continue
            if any(create_root_contains(target=target.path, root=root) for root in gov.create_root):
                return True
        return False

    def _intent_text(self, obj: _ObjectiveRecord) -> str:
        intent = self._provenance.get_intent(obj.objective_id)
        if intent is not None:
            return intent.text
        return obj.original_objective or obj.objective

    def _step_contract_planner_enabled(self) -> bool:
        """Prefer per-backend settings (tests / FakeSettings) over process-global config."""
        if self._settings is not None and hasattr(
            self._settings, "INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED"
        ):
            return bool(self._settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED)
        from backend.hyeve_aix_service.config import settings as _settings_module

        return bool(_settings_module.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED)

    def _accepted_plan_target_paths(self, obj: _ObjectiveRecord) -> tuple[str, ...] | None:
        """Sole execution target authority when a provenance Plan was accepted.

        None means no provenance plan (legacy/bootstrap). Empty tuple means accepted
        plan with zero targets — still not `obj.allowed_files`.
        """
        plan = self._provenance.latest_plan(obj.objective_id)
        if plan is None or plan.status != "ACCEPTED":
            return None
        return tuple(t.path for t in plan.targets)

    def _is_closed_seed_family(self, obj: _ObjectiveRecord) -> bool:
        """mkdir / rename / read-only summary bootstrap. Not leftover target_scope."""
        text = self._intent_text(obj)
        return (
            looks_like_create_empty_directory_objective(text)
            or looks_like_rename_path_objective(text)
            or looks_like_read_only_summary_objective(text)
            or looks_like_unbounded_codebase_audit(text)
        )

    def _execution_scope_paths(
        self, obj: _ObjectiveRecord, step_contract: StepContractV1 | None
    ) -> tuple[str, ...]:
        """Paths the executor may touch. Never `obj.allowed_files`."""
        step_paths = ()
        if step_contract is not None:
            step_paths = tuple(p for p in (step_contract.target_scope or ()) if str(p).strip())
        targets = self._accepted_plan_target_paths(obj)
        if step_paths:
            # When provenance PlanTargets exist, execution scope is the intersection
            # with this claim's target_scope. Heuristic / admission-only paths that
            # leak into step.target_scope must not authorize (ADV12 / P5).
            if targets is not None:
                return tuple(p for p in step_paths if p in set(targets))
            return step_paths
        if targets is not None:
            return targets
        if step_contract is not None and self._is_closed_seed_family(obj):
            return tuple(step_contract.target_scope)
        return ()

    def _bind_approval_to_plan(self, obj: _ObjectiveRecord, ref: str) -> None:
        plan = self._provenance.latest_plan(obj.objective_id)
        if plan is None or not str(ref or "").strip():
            return
        token = str(ref).strip()
        for existing in self._approvals.values():
            if existing.ref == token and existing.plan_id != plan.plan_id:
                return
        binding = ApprovalBinding(plan_id=plan.plan_id, ref=token)
        self._approvals[plan.plan_id] = binding
        self._provenance.put_plan(plan.model_copy(update={"approval_ref": binding.ref}))

    def _approval_for_execute(self, obj: _ObjectiveRecord, *, auto_replan: bool = False):
        plan = self._provenance.latest_plan(obj.objective_id)
        gov = self._provenance.latest_governance(obj.objective_id)
        if plan is None or gov is None:
            return None
        binding = self._approvals.get(plan.plan_id)
        if binding is None and plan.approval_ref:
            binding = ApprovalBinding(plan_id=plan.plan_id, ref=plan.approval_ref)
        return validate_approval_for_execute(
            plan=plan,
            governance=gov,
            approval=binding,
            auto_replan=auto_replan,
        )

    def _provenance_blocks_auto_replan(self, obj: _ObjectiveRecord) -> bool:
        """K15: v1 approval must not auto-dispatch Plan vN when policy is not NEVER."""
        plan = self._provenance.latest_plan(obj.objective_id)
        gov = self._provenance.latest_governance(obj.objective_id)
        if plan is None or gov is None:
            return False
        return str(gov.approval_policy) != "NEVER"

    def _changed_paths_from_execute_result(self, result: object) -> tuple[str, ...]:
        raw = getattr(result, "changed_source_files", None) or ()
        paths: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                path = str(item.get("path") or "").strip()
            else:
                path = str(item).strip()
            if path:
                paths.append(path)
        summary = getattr(result, "mutation_summary", None)
        if isinstance(summary, dict):
            for item in summary.get("files_changed") or ():
                path = (
                    str(item.get("path") or "").strip()
                    if isinstance(item, dict)
                    else str(item).strip()
                )
                if path:
                    paths.append(path)
        return tuple(dict.fromkeys(paths))

    def _enforce_execute_diff_and_record_attempt(
        self,
        *,
        obj: _ObjectiveRecord,
        result: object,
        verification_outcome: SemanticVerificationOutcome,
        step_id: str,
        attempt: int,
    ) -> tuple[SemanticVerificationOutcome, bool]:
        """E1: extra changed paths fail closed. AttemptEvidence stays off Understanding."""
        changed_paths = self._changed_paths_from_execute_result(result)
        plan = self._provenance.latest_plan(obj.objective_id)
        e1_failed = False
        if plan is not None and plan.status == "ACCEPTED" and plan.targets:
            e1 = validate_execute_diff(changed_files=changed_paths, plan=plan)
            if not e1.ok:
                e1_failed = True
                verification_outcome = SemanticVerificationOutcome(
                    verdict="HALT",
                    reasons=verification_outcome.reasons + (e1.detail or e1.rule_id,),
                    mechanical_passed=False,
                    failed_mandatory_checks=verification_outcome.failed_mandatory_checks + ("E1",),
                    semantic_checked=verification_outcome.semantic_checked,
                    criteria_results=verification_outcome.criteria_results,
                )
                self._verification_verdicts.setdefault(obj.objective_id, []).append(
                    verification_outcome
                )
        if plan is not None:
            self._provenance.put_attempt(
                obj.objective_id,
                AttemptEvidence(
                    plan_id=plan.plan_id,
                    step_id=step_id or "unknown-step",
                    attempt=max(1, int(attempt or 1)),
                    changed_files=changed_paths,
                    verifier_verdict=verification_outcome.verdict,
                    reasons=tuple(verification_outcome.reasons),
                ),
            )
        return verification_outcome, e1_failed

    async def _admit(self, *, tenant_id: str, actor_id: str, body: dict[str, Any], command_id: str) -> dict[str, Any]:
        # Never persist credentials into ledger/events.
        caller_auth = str(body.pop("caller_authorization", "") or "").strip() or None

        # AIX-REAL-EXECUTION-002: conversation -> objective message binding
        # (CURSOR-CLASS-EXTENSION.md S1, highest priority). `messages` marks this as a
        # chat-origin admission; the bound text always wins over whatever `objective`
        # string the caller also sent -- defense in depth, matching the same
        # "never trust the client redacted it" discipline `redact_objective_text`
        # already uses elsewhere in this module. A legacy non-chat admission (no
        # `messages`) keeps today's behavior unchanged and may still supply
        # `objective_source_message_id` directly.
        messages_raw = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        conversation_id = (
            str(body.get("conversation_id") or body.get("thread_id") or "").strip() or None
        )
        selected_message_id = str(body.get("selected_message_id") or "").strip() or None
        binding_message_id: str | None = None
        binding_parent_message_id: str | None = None
        binding_selected_message_id: str | None = None
        objective_source_message_id: str | None = None
        objective_text = str(body.get("objective") or "")
        try:
            reject_objective_install_text(objective_text)
        except EnvironmentError as exc:
            return {
                "ok": False,
                "operation": "admit_objective",
                "tenant_id": tenant_id,
                "error": {"code": exc.code, "detail": exc.detail},
            }

        if messages_raw:
            try:
                binding = bind_objective_source_message(
                    messages_raw, conversation_id=conversation_id, selected_message_id=selected_message_id
                )
            except MessageBindingError as exc:
                return {
                    "ok": False,
                    "operation": "admit_objective",
                    "tenant_id": tenant_id,
                    "error": {"code": exc.code, "detail": exc.detail},
                }
            objective_text = binding.objective_text
            conversation_id = binding.conversation_id
            binding_message_id = binding.message_id
            binding_parent_message_id = binding.parent_message_id
            binding_selected_message_id = binding.selected_message_id
            objective_source_message_id = binding.objective_source_message_id
        else:
            objective_source_message_id = str(body.get("objective_source_message_id") or "").strip() or None

        family_result = classify_task_family(objective_text)

        # Clarification-on-doubt (AIX-REAL-EXECUTION-002 / gap-closure Priority 2): a
        # resource-bearing task family (READ/EDIT/CREATE/DELETE) with no extractable
        # target and no explicit resources[] is under-specified -- e.g. "fix the
        # client". Never invent a resource or fall through to a proposal-only plan.
        # Previously this hard-rejected admission itself (no objective ever created),
        # which made "persist the answer and continue the same objective" impossible.
        # The objective is now admitted normally and the *existing*, cycle-scoped
        # exploration-gate clarification mechanism (`exploration_planner
        # ._clarification_question`, wired at `_run_exploration_gate`) owns the actual
        # block -- `clarify_cycle` can then answer it, reclassify, and resume the same
        # objective/cycle. See `requires_exploration`'s READ/CREATE `target_bound`
        # cases and `_clarification_question`'s family coverage for the equivalent
        # gate. Scoped to chat-origin admission only by construction (legacy callers
        # with no `messages` already resolve resources themselves).

        project_id = str(body.get("project_id") or "").strip() or None
        task_number_raw = body.get("task_number")
        task_number: int | None
        try:
            task_number = int(task_number_raw) if task_number_raw is not None and str(task_number_raw).strip() != "" else None
        except (TypeError, ValueError):
            return {
                "ok": False,
                "operation": "admit_objective",
                "tenant_id": tenant_id,
                "error": {"code": "TASK_NUMBER_INVALID"},
            }

        workhub_task_id = str(body.get("workhub_task_id") or "").strip()
        create_requested = _truthy(body.get("create_workhub_task"), default=self.create_workhub_tasks)
        # Create markers (auto/create/new) and empty id are create-intent only.
        # They never persist: either create authority resolves them to a real
        # WorkHub parent task id, or admit fails closed (WORKHUB_TASK_ID_UNRESOLVED).
        # A real task id is never overwritten even when create_workhub_task=true.
        needs_new_workhub_identity = (not workhub_task_id) or is_workhub_task_id_placeholder(
            workhub_task_id
        )
        create_intent = create_requested or is_workhub_task_id_placeholder(workhub_task_id)
        should_create = bool(
            self.create_workhub_tasks and needs_new_workhub_identity and create_intent
        )

        if should_create and not project_id:
            return {
                "ok": False,
                "operation": "admit_objective",
                "tenant_id": tenant_id,
                "error": {
                    "code": "WORKHUB_PROJECT_ID_REQUIRED",
                    "detail": "create_workhub_task requires project_id",
                },
            }

        if self.real_execute_enabled and self.require_workhub_load and not project_id and not should_create:
            return {
                "ok": False,
                "operation": "admit_objective",
                "tenant_id": tenant_id,
                "error": {
                    "code": "WORKHUB_PROJECT_ID_REQUIRED",
                    "detail": "real execute requires project_id to load an existing WorkHub task",
                },
            }

        if not workhub_task_id and not should_create:
            return {
                "ok": False,
                "operation": "admit_objective",
                "tenant_id": tenant_id,
                "error": {"code": "WORKHUB_TASK_ID_REQUIRED"},
            }

        # Fail closed: never persist a create placeholder when create is not taking
        # effect (flag off, or create not requested / not authorized).
        if not should_create and is_workhub_task_id_placeholder(workhub_task_id):
            return {
                "ok": False,
                "operation": "admit_objective",
                "tenant_id": tenant_id,
                "error": {
                    "code": "WORKHUB_TASK_ID_UNRESOLVED",
                    "detail": (
                        "workhub_task_id is a create placeholder (auto/create/new) but "
                        "WorkHub task create is not enabled for this service; provide a "
                        "real WorkHub task id, or enable OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK "
                        "deliberately when create-side-effect authority is approved"
                    ),
                },
                "aix_creates_workhub_tasks": bool(self.create_workhub_tasks),
                "workhub_lifecycle_authority": False,
            }

        workhub_task_created = False
        workhub_create_idempotency_key: str | None = None
        if should_create:
            if not str(tenant_id or "").strip():
                return {
                    "ok": False,
                    "operation": "admit_objective",
                    "tenant_id": tenant_id,
                    "error": {
                        "code": "WORKHUB_TASK_CREATE_TENANT_REQUIRED",
                        "detail": "WorkHub parent-task create requires a non-empty tenant_id",
                    },
                    "aix_creates_workhub_tasks": True,
                    "workhub_lifecycle_authority": False,
                }
            workhub_create_idempotency_key = f"aix-admit-create:{command_id}"
            try:
                created = await _create_workhub_task_with_auth_fallback(
                    settings=self._settings,
                    caller_auth=caller_auth,
                    project_id=str(project_id),
                    title=(objective_text or "AIX objective")[:200],
                    description=objective_text,
                    tenant_id=tenant_id,
                    idempotency_key=workhub_create_idempotency_key,
                    metadata_json={
                        "source": "aix.objective_cycle.admit",
                        "exception": "001P-W12",
                        "actor_id": actor_id,
                        "tenant_id": tenant_id,
                        "aix_creates_workhub_tasks": True,
                        "workhub_lifecycle_authority": False,
                        "create_intent": "parent_task",
                        "admit_command_id": command_id,
                    },
                    tags_json=["aix", "objective-cycle", "created-by-aix", "parent-task"],
                )
            except Exception as exc:  # noqa: BLE001 - surface WorkHub create failures fail-closed
                err = _workhub_http_error_payload(exc)
                logger.warning(
                    "workhub_parent_task_create_failed",
                    extra={
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "error_code": err.get("code"),
                        "exception": "001P-W12",
                    },
                )
                return {
                    "ok": False,
                    "operation": "admit_objective",
                    "tenant_id": tenant_id,
                    "error": err,
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": True,
                    "exception": "001P-W12",
                    "production_go": False,
                }
            workhub_task_id = str(
                created.get("id") or created.get("task_id") or created.get("uuid") or ""
            ).strip()
            if not workhub_task_id:
                return {
                    "ok": False,
                    "operation": "admit_objective",
                    "tenant_id": tenant_id,
                    "error": {"code": "WORKHUB_TASK_CREATE_MISSING_ID", "detail": str(created)[:500]},
                    "aix_creates_workhub_tasks": True,
                    "workhub_lifecycle_authority": False,
                    "exception": "001P-W12",
                    "production_go": False,
                }
            if is_workhub_task_id_placeholder(workhub_task_id):
                return {
                    "ok": False,
                    "operation": "admit_objective",
                    "tenant_id": tenant_id,
                    "error": {
                        "code": "WORKHUB_TASK_ID_UNRESOLVED",
                        "detail": (
                            "WorkHub create returned a placeholder task id "
                            f"{workhub_task_id!r}; refusing to admit"
                        ),
                    },
                    "aix_creates_workhub_tasks": True,
                    "workhub_lifecycle_authority": False,
                    "exception": "001P-W12",
                    "production_go": False,
                }
            created_number = created.get("task_number") or created.get("number")
            if created_number is not None:
                try:
                    task_number = int(created_number)
                except (TypeError, ValueError):
                    pass
            workhub_task_created = True
            logger.info(
                "workhub_parent_task_created",
                extra={
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "workhub_task_id": workhub_task_id,
                    "task_number": task_number,
                    "idempotency_key": workhub_create_idempotency_key,
                    "exception": "001P-W12",
                    "workhub_lifecycle_authority": False,
                    "production_go": False,
                },
            )

        objective_id = f"obj_{new_ulid()[:12]}"
        raw_attachments = body.get("attachments") or []
        attachments = [item for item in raw_attachments if isinstance(item, dict)]
        from backend.aix_core.runtime.executor.read_file_execute import sanitize_allowed_file_entry

        allowed_raw = tuple(body.get("allowed_files") or ())
        # PowerShell `-File` invocations often collapse `-AllowedFiles "a","b"` into one
        # comma-joined string. Expand those so multi-file scope stays multi-file
        # (S2 live: one joined path → full-repo discovery → EXPLORATION_BUDGET_EXCEEDED).
        allowed_clean_list: list[str] = []
        for raw_entry in allowed_raw:
            text = str(raw_entry or "").strip()
            parts = [p.strip() for p in text.split(",")] if "," in text else [text]
            for part in parts:
                cleaned = sanitize_allowed_file_entry(part)
                if cleaned and cleaned not in allowed_clean_list:
                    allowed_clean_list.append(cleaned)
        allowed_clean = tuple(allowed_clean_list)
        repository_id = str(body.get("repository_id") or "").strip()
        # Discuss/CLI sometimes send a filesystem path as repository_id (live
        # cyc_01M0FGPGDTQM → WORKSPACE_NOT_BOUND). Remap to a registered id only.
        if repository_id and (
            re.match(r"^[A-Za-z]:[\\/]", repository_id)
            or repository_id.startswith(("/", "\\\\"))
            or (not is_registered_repository(repository_id, settings=self._settings) and ("/" in repository_id or "\\" in repository_id))
        ):
            remapped = resolve_repository_for_path(repository_id, settings=self._settings)
            if remapped:
                repository_id = remapped
            else:
                return {
                    "ok": False,
                    "operation": "admit_objective",
                    "tenant_id": tenant_id,
                    "error": {
                        "code": "REPOSITORY_ROOT_UNKNOWN",
                        "detail": (
                            f"repository_id {repository_id!r} is a filesystem path that does not "
                            "map to OBJECTIVE_CYCLE_REPOSITORY_ROOTS"
                        ),
                    },
                }
        explicit_dod = body.get("dod_user_authored")
        dod_user_authored = infer_dod_user_authored(
            definition_of_done=tuple(body.get("definition_of_done") or ()),
            intent_text=objective_text,
            explicit=bool(explicit_dod) if explicit_dod is not None else None,
        )
        intent = freeze_user_intent(
            objective_id=objective_id,
            text=objective_text,
            definition_of_done=tuple(body.get("definition_of_done") or ()),
            dod_user_authored=dod_user_authored,
            source_message_id=objective_source_message_id,
            conversation_id=conversation_id,
        )
        self._provenance.put_intent(intent)
        self._mirror_durable_intent(intent)
        frozen_text = original_objective_alias(intent)
        record = _ObjectiveRecord(
            objective_id=objective_id,
            tenant_id=tenant_id,
            workhub_task_id=workhub_task_id,
            objective=frozen_text,
            original_objective=frozen_text,
            task_class=str(body["task_class"]),
            definition_of_done=tuple(body.get("definition_of_done") or ()),
            repository_id=repository_id,
            allowed_files=allowed_clean,
            project_id=project_id,
            task_number=task_number,
            workhub_task_created=workhub_task_created,
            attachments=attachments,
            conversation_id=conversation_id,
            message_id=binding_message_id,
            parent_message_id=binding_parent_message_id,
            selected_message_id=binding_selected_message_id,
            objective_source_message_id=objective_source_message_id,
            task_family=family_result.task_family,
            classification_method=family_result.classification_method,
            classification_confidence=family_result.classification_confidence,
        )
        self._objectives[objective_id] = record
        understanding_v1 = self._refresh_understanding_v1(record)
        try:
            contract = understand_objective(
                tenant_id=tenant_id,
                raw_objective=objective_text,
                definition_of_done=list(record.definition_of_done),
                objective_id=objective_id,
                allowed_files=(),  # P6: admission list is metadata, not mutation authority
            )
        except ObjectiveContractError as exc:
            del self._objectives[objective_id]
            return {
                "ok": False,
                "operation": "admit_objective",
                "tenant_id": tenant_id,
                "error": {"code": exc.code, "detail": exc.detail},
            }
        self._objective_contracts[objective_id] = contract
        await self._persist_event(
            tenant_id=tenant_id,
            aggregate_type="objective",
            aggregate_id=objective_id,
            expected_sequence_number=1,
            event_type="objective.admitted",
            command_id=command_id,
            payload={
                "actor_id": actor_id,
                "workhub_task_id": record.workhub_task_id,
                "project_id": record.project_id,
                "task_number": record.task_number,
                "workhub_task_created": record.workhub_task_created,
                "aix_creates_workhub_tasks": record.workhub_task_created,
                "workhub_lifecycle_authority": False,
                "exception": "001P-W12" if record.workhub_task_created else None,
                "workhub_create_idempotency_key": workhub_create_idempotency_key,
                "conversation_id": record.conversation_id,
                "objective_source_message_id": record.objective_source_message_id,
                "task_family": record.task_family,
                "classification_method": record.classification_method,
                "classification_confidence": record.classification_confidence,
                "contract_id": contract.contract_id,
                "contract_admission": contract.admission,
                "contract_hash": contract.content_hash,
            },
        )
        await self._project_objective(record)
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2: convert the objective/
        # definition_of_done into the durable ObjectiveObligationV1 set -- independent
        # of envelope construction below (obligations record what was asked for;
        # the envelope records governance/execution authorization -- an unsupported
        # task_class or malformed budget must not erase the obligation record of what
        # the user actually requested).
        obligations = derive_obligations(
            objective_id=objective_id, objective=objective_text, definition_of_done=record.definition_of_done
        )
        self._obligations[objective_id] = obligations
        # F12.2 Packet 01: Derive and seal CanonicalObjectiveTruthV1 + OVC
        truth = derive_canonical_truth(original_text=objective_text, objective_id=objective_id)
        ovc = derive_ovc(truth)
        self._canonical_truths[objective_id] = truth
        self._objective_verification_contracts[objective_id] = ovc
        await self._persist_event(
            tenant_id=tenant_id,
            aggregate_type="objective",
            aggregate_id=objective_id,
            expected_sequence_number=2,
            event_type="objective.obligations_created",
            command_id=f"{command_id}:obligations",
            payload={
                "obligation_ids": [o.id for o in obligations],
                "count": len(obligations),
                "sources": sorted({o.source for o in obligations}),
                "canonical_truth_hash": truth.hash,
                "ovc_hash": ovc.hash,
            },
        )

        envelope, envelope_reason = self._build_objective_envelope(
            record=record, actor_id=actor_id, command_id=command_id, body=body
        )
        if envelope is not None:
            self._envelopes[objective_id] = envelope
            self._ensure_governance(record, envelope)
            await self._persist_event(
                tenant_id=tenant_id,
                aggregate_type="objective",
                aggregate_id=objective_id,
                expected_sequence_number=3,
                event_type="objective.envelope_created",
                command_id=f"{command_id}:envelope",
                payload={
                    "envelope_id": envelope.id,
                    "task_class": envelope.task_class,
                    "repository_scope": list(envelope.repository_scope),
                    "approval_policy": envelope.approval_policy,
                    "budget_ceiling": envelope.budget_ceiling.model_dump(),
                },
            )
        else:
            self._envelope_construction_errors[objective_id] = envelope_reason
            await self._persist_event(
                tenant_id=tenant_id,
                aggregate_type="objective",
                aggregate_id=objective_id,
                expected_sequence_number=3,
                event_type="objective.envelope_construction_failed",
                command_id=f"{command_id}:envelope",
                payload={"reasons": list(envelope_reason)},
            )

        # GATE-D: register objective in control room at admission.
        self._lifecycle.register_objective(
            LifecycleContext(objective_id=objective_id, tenant_id=tenant_id),
            title=record.objective[:120] if record.objective else objective_id,
        )

        return {
            "ok": True,
            "operation": "admit_objective",
            "tenant_id": tenant_id,
            "objective_id": objective_id,
            "version": record.version,
            "project_id": record.project_id,
            "task_number": record.task_number,
            "workhub_task_id": record.workhub_task_id,
            "workhub_task_created": record.workhub_task_created,
            "workhub_create_idempotency_key": workhub_create_idempotency_key,
            "workhub_lifecycle_authority": False,
            "objective": record.objective,
            "original_objective": record.original_objective,
            "intent_id": (self._provenance.get_intent(objective_id).id if self._provenance.get_intent(objective_id) else None),
            "understanding_revision_id": understanding_v1.revision.id if understanding_v1 is not None else None,
            "scope_mode": understanding_v1.revision.scope_mode if understanding_v1 is not None else None,
            "understanding_decision": understanding_v1.decision if understanding_v1 is not None else None,
            "conversation_id": record.conversation_id,
            "message_id": record.message_id,
            "parent_message_id": record.parent_message_id,
            "selected_message_id": record.selected_message_id,
            "objective_source_message_id": record.objective_source_message_id,
            "task_family": record.task_family,
            "classification_method": record.classification_method,
            "classification_confidence": record.classification_confidence,
            "aix_creates_workhub_tasks": record.workhub_task_created,
            "real_execute_enabled": self.real_execute_enabled,
            "exception": "001P-W12" if record.workhub_task_created else None,
            "production_go": False,
            # AIX Closed-Loop Execution Plan, Sprint W2.
            "envelope_id": envelope.id if envelope is not None else None,
            "envelope_constructed": envelope is not None,
            "envelope_construction_reason": None if envelope is not None else list(envelope_reason),
            # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2.
            "obligations": [o.model_dump(mode="json") for o in obligations],
            "obligation_ids": [o.id for o in obligations],
        }

    def _build_objective_envelope(
        self, *, record: "_ObjectiveRecord", actor_id: str, command_id: str, body: dict[str, Any]
    ) -> tuple[ObjectiveEnvelopeV1 | None, tuple[str, ...]]:
        """AIX Closed-Loop Execution Plan, Sprint W2: best-effort construction of the
        reused WorkOrder contract (`ObjectiveEnvelopeV1`) at admit time. Never raises --
        returns `(None, reasons)` on any validation failure so the caller can persist an
        honest `objective.envelope_construction_failed` event instead of guessing or
        silently admitting without one. `run_real_execute`'s pre-dispatch gate (below)
        is what actually enforces "No Work Order -> No mutation"; this method only
        builds the envelope, it does not decide whether execution may proceed.
        """
        task_class = record.task_class if record.task_class in SUPPORTED_TASK_CLASSES else None
        if task_class is None:
            task_class = task_family_to_task_class(record.task_family)

        # `ObjectiveEnvelopeV1.project_id` requires a non-empty value; the admit path
        # already guarantees `workhub_task_id` is non-empty (checked earlier in
        # `_admit`) even when the caller supplied no explicit `project_id` -- reuse it
        # as the project identity rather than leaving envelope construction blocked on
        # a field this data model treats as optional today. This is a deliberate,
        # documented fallback (see AIX Closed-Loop Execution Plan Sprint W2), not a
        # silent guess: it is recorded on the envelope itself, inspectable later.
        project_id = record.project_id or record.workhub_task_id

        acceptance_criteria = record.definition_of_done or (record.objective or record.objective_id,)
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2 / P0 finding #1: this field used
        # to be set to the raw objective text unconditionally, even when the caller
        # supplied a real `definition_of_done` -- silently discarding it on this
        # envelope (the `acceptance_criteria` tuple above already preferred the real
        # DoD correctly; only this single-string field had the bug). `ObjectiveEnvelopeV1
        # .definition_of_done` is a single summary string (not a tuple), so a real
        # multi-item DoD is joined rather than truncated to its first entry.
        envelope_definition_of_done = (
            "; ".join(record.definition_of_done) if record.definition_of_done else (record.objective or record.objective_id)
        )

        budgets = body.get("budgets") if isinstance(body.get("budgets"), dict) else {}
        try:
            budget_ceiling = BudgetV1(
                time_seconds=int(budgets.get("time_seconds", 3600)),
                tokens=int(budgets.get("tokens", 200_000)),
                cost_usd=float(budgets.get("cost_usd", 10.0)),
                retries=int(budgets.get("retries", 3)),
                steps=int(budgets.get("steps", 20)),
                concurrency=1,
            )
        except (TypeError, ValueError) as exc:
            return None, (f"invalid_budget:{exc}",)

        try:
            envelope = ObjectiveEnvelopeV1(
                id=record.objective_id,
                tenant_id=record.tenant_id,
                actor_ref=actor_id,
                project_id=project_id,
                workhub_task_ref=record.workhub_task_id,
                task_class=task_class or "",
                definition_of_done=envelope_definition_of_done,
                acceptance_criteria=acceptance_criteria,
                repository_scope=(record.repository_id,),
                budget_ceiling=budget_ceiling,
                idempotency_key=command_id,
                correlation_id=command_id,
            )
        except _ContractValidationError as exc:
            return None, tuple(f"{err['loc']}:{err['msg']}" for err in exc.errors())
        return envelope, ()

    def _ensure_objective_envelope(
        self,
        *,
        obj: "_ObjectiveRecord",
        actor_id: str,
        command_id: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[ObjectiveEnvelopeV1 | None, tuple[str, ...]]:
        """Rebuild the admit-time work order into serving memory.

        `_envelopes` / `_envelope_construction_errors` are process-local. After
        kill/restart, C011 restores cycle/objective records but not the envelope,
        so the mutation gate saw `envelope_not_constructed` and returned
        `NO_WORK_ORDER` even when admit had already recorded a task_class-only
        construction failure (or a valid envelope). Restore from the objective
        record instead of treating a fresh process as an incomplete admission.
        """
        existing = self._envelopes.get(obj.objective_id)
        if existing is not None:
            return existing, ()
        envelope, reasons = self._build_objective_envelope(
            record=obj,
            actor_id=actor_id or "rehydrate",
            command_id=command_id,
            body=body if isinstance(body, dict) else {},
        )
        if envelope is not None:
            self._envelopes[obj.objective_id] = envelope
            self._envelope_construction_errors.pop(obj.objective_id, None)
            return envelope, ()
        self._envelope_construction_errors[obj.objective_id] = reasons
        return None, reasons

    async def _start_cycle(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        objective_id: str,
        body: dict[str, Any],
        command_id: str,
    ) -> dict[str, Any]:
        obj = self._objectives.get(objective_id)
        if obj is None or obj.tenant_id != tenant_id:
            await self.rehydrate_objective(tenant_id=tenant_id, objective_id=objective_id)
            obj = self._objectives.get(objective_id)
        if obj is None or obj.tenant_id != tenant_id:
            return {"ok": False, "operation": "start_cycle", "tenant_id": tenant_id, "error": {"code": "OBJECTIVE_NOT_FOUND"}}
        # C020: planning/start requires an ADMITTED ObjectiveContract.
        try:
            assert_planning_allowed(self._objective_contracts.get(objective_id))
        except ObjectiveContractError as exc:
            return {
                "ok": False,
                "operation": "start_cycle",
                "tenant_id": tenant_id,
                "error": {"code": exc.code, "detail": exc.detail},
            }
        cycle_id = f"cyc_{new_ulid()[:12]}"
        candidate_sha = str(body.get("candidate_sha") or "")
        plan_material = f"{objective_id}:{cycle_id}:{candidate_sha}:{obj.objective}"
        workflow_input = ObjectiveCycleWorkflowInput(
            workflow_version=1,
            tenant_id=tenant_id,
            objective_id=objective_id,
            cycle_id=cycle_id,
            candidate_sha=candidate_sha,
            plan_sha256=_sha256_hex(plan_material),
            start_request_id=command_id,
            correlation_id=command_id,
        )
        engine = AixObjectiveCycleWorkflowEngine(workflow_input)
        engine.run_until_wait_or_terminal()
        cycle = _CycleRecord(
            cycle_id=cycle_id,
            objective_id=objective_id,
            tenant_id=tenant_id,
            candidate_sha=candidate_sha,
            budgets=dict(body.get("budgets") or {}),
            version=engine.state.aggregate_version or 1,
            phase=str(engine.state.phase),
            engine=engine,
            model_routes=dict(body.get("model_routes") or {}),
        )
        if not cycle.model_routes:
            cycle.model_routes = self._default_model_routes(cycle)
        cycle.plan_id = f"plan_{cycle_id}"
        if cycle.phase == "WAITING_APPROVAL":
            cycle.current_gate_id = f"gate_{cycle_id}_r{cycle.plan_revision}"
        self._cycles[cycle_id] = cycle
        # Sprint 001 trace foundation.  These events describe stages the current
        # admission path actually performed; later sprints can enrich their safe
        # summaries without introducing a second event stream.
        self._emit_event(cycle=cycle, event_type="request.received", payload_refs={"actor_id": actor_id})
        self._emit_event(
            cycle=cycle,
            event_type="intent.classified",
            payload_refs={"classification": "EXECUTION_REQUIRED" if obj.repository_id else "INTENT_UNCLEAR"},
        )
        self._emit_event(cycle=cycle, event_type="objective.created", payload_refs={})
        self._emit_event(cycle=cycle, event_type="cycle.started", payload_refs={"actor_id": actor_id})
        await self._persist_event(
            tenant_id=tenant_id,
            aggregate_type="cycle",
            aggregate_id=cycle_id,
            expected_sequence_number=1,
            event_type="cycle.started",
            command_id=command_id,
            payload={"objective_id": objective_id, "candidate_sha": candidate_sha, "phase": cycle.phase},
        )
        await self._project_run(cycle)
        # Seed read-only summary plans during WAITING_APPROVAL so GET /plan is
        # ACCEPTED rather than PLAN_NOT_FOUND before the operator approves.
        self._seed_verification_summary_contract(obj=obj, cycle=cycle, envelope=None)
        self._seed_codebase_audit_contract(obj=obj, cycle=cycle, envelope=None)
        workspace_id = None
        try:
            lease = self._workspaces.create(
                tenant_id=tenant_id, run_id=cycle_id, holder=actor_id
            )
            workspace_id = lease.workspace_id
        except Exception as exc:
            logger.warning("C016 workspace create skipped: %s", exc)
        return {
            "ok": True,
            "operation": "start_cycle",
            "tenant_id": tenant_id,
            "objective_id": objective_id,
            "cycle_id": cycle_id,
            "phase": cycle.phase,
            "version": cycle.version,
            "status": engine.query_status(),
            "plan_id": cycle.plan_id,
            "plan_revision": cycle.plan_revision,
            "approval_gate_id": cycle.current_gate_id,
            "workhub_lifecycle_authority": False,
            "workspace_id": workspace_id,
            "environment_fingerprint_id": self._env_fingerprint.fingerprint_id,
        }

    async def _control(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        expected_version: int | None,
        action: str,
        body: dict[str, Any],
        command_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        cycle = self._cycles.get(cycle_id)
        if cycle is None or cycle.tenant_id != tenant_id:
            # C011: reconstruct serving state from durable decision snapshots.
            loaded = await self.rehydrate_cycle(tenant_id=tenant_id, cycle_id=cycle_id)
            cycle = self._cycles.get(cycle_id) if loaded else None
        if cycle is None or cycle.tenant_id != tenant_id:
            return {"ok": False, "operation": action, "tenant_id": tenant_id, "error": {"code": "CYCLE_NOT_FOUND"}}
        if expected_version is not None and expected_version != cycle.version:
            return {
                "ok": False,
                "operation": action,
                "tenant_id": tenant_id,
                "error": {
                    "code": "VERSION_CONFLICT",
                    "expected": expected_version,
                    "actual": cycle.version,
                    # Lets a caller distinguish "the cycle already advanced past what
                    # I asked for — stop retrying" from "genuinely stale" without a
                    # second round trip, so a resume-loop doesn't spin against
                    # WAITING_APPROVAL after the server has already moved on.
                    "current_phase": cycle.phase,
                    "current_real_execute_ok": (
                        bool(cycle.real_execute.get("ok")) if cycle.real_execute else None
                    ),
                },
            }
        engine = cycle.engine
        if engine is None:
            return {"ok": False, "operation": action, "tenant_id": tenant_id, "error": {"code": "CYCLE_ENGINE_MISSING"}}

        caller_auth = str(body.pop("caller_authorization", "") or "").strip() or None
        # Human-edge authorization is sealed into the durable execution store
        # so later Temporal activities resolve it after restarts. Raw JWTs
        # never enter workflow history; no process-memory auth exists.
        if caller_auth and action in ("approval_reference", "resume"):
            self._seal_execution_auth(
                tenant_id=tenant_id,
                cycle_id=cycle_id,
                caller_auth=caller_auth,
                actor_id=actor_id,
            )

        # Approval idempotency: an approval-request id (client-supplied, or derived
        # deterministically from cycle+phase when the caller sent none) makes a
        # *repeat* approve/resume call for the same logical approval a no-op replay
        # instead of re-running `engine.signal_approval`/`continue_after_approval` (and
        # therefore `_maybe_real_execute`) a second time — this is the actual
        # "approval repeatedly resumes the same cycle" bug: it does not depend on
        # `real_execute_enabled`, so the check must not either. `resume_in_progress`
        # separately makes "one active resume operation per cycle" real by rejecting a
        # second call that arrives while the first is still mutating state. Both apply
        # only to the two actions that can trigger real execution.
        approval_request_id: str | None = None
        if action in ("approval_reference", "resume"):
            approval_request_id = str(body.get("approval_request_id") or "").strip() or f"{cycle_id}:{cycle.phase}"
            if cycle.resume_in_progress:
                return self._idempotent_control_response(
                    cycle=cycle, action=action, tenant_id=tenant_id, note="resume_already_in_progress"
                )
            # The cycle has already moved past the phase this action exists to
            # unblock (WAITING_APPROVAL/WAITING_BLOCKER) — a phase-keyed derived id
            # can't catch this by itself since the phase it was derived from is gone
            # by the time a duplicate call arrives, which is exactly the "approval
            # repeatedly resumes the same cycle" bug: nothing left to resume, so
            # return the current state rather than re-entering the action dispatch.
            #
            # Exception: RUNNING_STEP with no real_execute yet means an earlier
            # `_maybe_real_execute` returned an error without parking the engine
            # (PLAN_CHANGED / step-contract refusal / etc.). Live CLI only calls
            # resume — refusing here stranded S2 as WAITING_APPROVAL vs RUNNING_STEP
            # desync. Allow resume to re-enter execution (same as retry_step).
            stuck_running_no_execute = (
                action == "resume"
                and str(engine.state.phase) == "RUNNING_STEP"
                and cycle.real_execute is None
            )
            waiting_for_auth_resume = (
                action == "resume"
                and cycle.dispatch_state == "WAITING_FOR_AUTH"
            )
            # Mark-done fail-close must win before mid-plan recovery re-arms STEP_PASSED.
            if str(cycle.terminal_reason or "").strip().upper() in MARK_DONE_FAIL_CLOSE_REASONS:
                return self._mark_done_fail_close_payload(
                    cycle=cycle,
                    operation=action,
                    code=str(cycle.terminal_reason),
                    detail=str(cycle.last_error_detail or cycle.terminal_reason),
                )
            # Mid-plan STEP_FAILED/STEP_PASSED must heal BEFORE the
            # stuck_running_no_execute path and EXECUTION_LOOP_DETECTED guard.
            # After step 1, real_execute is cleared for step 2 — that looks like
            # "zero progress" to the loop detector and previously skipped heal.
            if action == "resume" and cycle.dispatch_state in {
                "STEP_PASSED",
                "STEP_FAILED",
                "VERIFYING",
                "IDLE",
            } and str(engine.state.phase) != "WAITING_APPROVAL":
                # When an external caller re-parks the engine at WAITING_APPROVAL
                # (stuck-cycle probe), do not mid-plan heal past the loop guard.
                recovered = await self._maybe_advance_mid_plan_dispatch(
                    cycle=cycle, command_id=command_id
                )
                if isinstance(recovered, dict) and recovered.get("ok"):
                    if recovered.get("dispatch_required"):
                        if cycle.terminal_reason == "EXECUTION_LOOP_DETECTED":
                            cycle.terminal_reason = None
                            cycle.last_error_detail = None
                        # Only clear resume_count when recovery reflects real progress;
                        # zero-tool zombie re-arm must not erase the loop guard.
                        if (
                            bool(getattr(cycle, "mutation_evidence_history", None))
                            or (
                                isinstance(cycle.real_execute, dict)
                                and cycle.real_execute.get("ok")
                            )
                        ):
                            cycle.resume_count = 0
                        cycle.resume_in_progress = False
                        dispatched = await self._dispatch_after_ready(
                            cycle=cycle,
                            tenant_id=tenant_id,
                            cycle_id=cycle_id,
                            command_id=command_id,
                            caller_auth=caller_auth,
                            actor_id=actor_id,
                            ready=recovered,
                        )
                        if dispatched is not None:
                            return self._control_payload_with_synced_phase(cycle, dispatched)
                        return self._control_payload_with_synced_phase(cycle, recovered)
                    if (
                        recovered.get("operation") == "plan_complete_terminal"
                        or str(recovered.get("phase") or "") == "COMPLETED_FOR_REVIEW"
                    ):
                        cycle.resume_in_progress = False
                        return self._control_payload_with_synced_phase(cycle, recovered)
            approval_ref_still_required = (
                action == "approval_reference"
                and "approval_ref required" in str(cycle.last_error_detail or "").casefold()
            )
            if (
                str(engine.state.phase) not in ("WAITING_APPROVAL", "WAITING_BLOCKER")
                and not stuck_running_no_execute
                and not waiting_for_auth_resume
                and not approval_ref_still_required
            ):
                if (
                    str(engine.state.phase) == "BLOCKED"
                    and str(cycle.terminal_reason or "").strip().upper() in MARK_DONE_FAIL_CLOSE_REASONS
                ):
                    return self._mark_done_fail_close_payload(
                        cycle=cycle,
                        operation=action,
                        code=str(cycle.terminal_reason),
                        detail=str(cycle.last_error_detail or cycle.terminal_reason),
                    )
                recovered = await self._maybe_advance_mid_plan_dispatch(
                    cycle=cycle, command_id=command_id
                )
                if isinstance(recovered, dict) and recovered.get("ok"):
                    if recovered.get("dispatch_required"):
                        dispatched = await self._dispatch_after_ready(
                            cycle=cycle,
                            tenant_id=tenant_id,
                            cycle_id=cycle_id,
                            command_id=command_id,
                            caller_auth=caller_auth,
                            actor_id=actor_id,
                            ready=recovered,
                        )
                        if dispatched is not None:
                            return self._control_payload_with_synced_phase(cycle, dispatched)
                        return self._control_payload_with_synced_phase(cycle, recovered)
                    if (
                        recovered.get("operation") == "plan_complete_terminal"
                        or str(recovered.get("phase") or "") == "COMPLETED_FOR_REVIEW"
                    ):
                        return self._control_payload_with_synced_phase(cycle, recovered)
                return self._idempotent_control_response(
                    cycle=cycle, action=action, tenant_id=tenant_id, note="cycle_not_awaiting_approval"
                )
            if (
                not stuck_running_no_execute
                and cycle.last_approval_request_id is not None
                and cycle.last_approval_request_id == approval_request_id
            ):
                recovered = await self._maybe_advance_mid_plan_dispatch(
                    cycle=cycle, command_id=command_id
                )
                if isinstance(recovered, dict) and recovered.get("ok"):
                    if recovered.get("dispatch_required"):
                        dispatched = await self._dispatch_after_ready(
                            cycle=cycle,
                            tenant_id=tenant_id,
                            cycle_id=cycle_id,
                            command_id=command_id,
                            caller_auth=caller_auth,
                            actor_id=actor_id,
                            ready=recovered,
                        )
                        if dispatched is not None:
                            return self._control_payload_with_synced_phase(cycle, dispatched)
                        return self._control_payload_with_synced_phase(cycle, recovered)
                    if (
                        recovered.get("operation") == "plan_complete_terminal"
                        or str(recovered.get("phase") or "") == "COMPLETED_FOR_REVIEW"
                    ):
                        return self._control_payload_with_synced_phase(cycle, recovered)
                redispatched = await self._maybe_redispatch_stuck_after_approval(
                    cycle=cycle,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    command_id=command_id,
                    caller_auth=caller_auth,
                    actor_id=actor_id,
                    reason="approval_already_applied_redispatch",
                )
                if redispatched is not None:
                    return self._control_payload_with_synced_phase(cycle, redispatched)
                return self._idempotent_control_response(
                    cycle=cycle, action=action, tenant_id=tenant_id, note="approval_already_applied"
                )
            cycle.resume_in_progress = True
            cycle.last_approval_request_id = approval_request_id
            cycle.resume_count += 1
            # Prior-step mutations (multi-step plans) clear real_execute before the
            # next claim — that is progress, not a no-tool loop.
            prior_step_progress = bool(getattr(cycle, "mutation_evidence_history", None))
            remaining_plan_work = False
            plan_for_loop = self._active_plan_revision(cycle.objective_id)
            if plan_for_loop is not None and str(plan_for_loop.status) == "ACCEPTED":
                runtime_for_loop = self._step_runtime.get(cycle.objective_id) or {}
                remaining_plan_work = any(
                    (runtime_for_loop.get(s.id) is None
                     or str(runtime_for_loop[s.id].status) not in {"PASS", "SUPERSEDED"})
                    for s in plan_for_loop.steps
                )
            if (
                cycle.resume_count > 2
                and cycle.real_execute is None
                and not prior_step_progress
                and (not remaining_plan_work or not self.real_execute_enabled)
            ):
                cycle.resume_in_progress = False
                cycle.terminal_reason = "EXECUTION_LOOP_DETECTED"
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.stuck_detected",
                    payload_refs={
                        "reason_code": "EXECUTION_LOOP_DETECTED",
                        "resume_count": cycle.resume_count,
                        "real_tool_calls": 0,
                        "target_resources_accessed": 0,
                    },
                )
                return {
                    "ok": False,
                    "operation": action,
                    "tenant_id": tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "terminal_reason": cycle.terminal_reason,
                    "error": {
                        "code": "EXECUTION_LOOP_DETECTED",
                        "detail": (
                            f"{cycle.resume_count} resume/approval attempts with zero real "
                            "tool calls -- this cycle is not making progress"
                        ),
                    },
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }

        try:
            if action == "cancel":
                # Real cancellation (gap-closure sprint 3, Priority 5): flip the flag
                # BEFORE touching the engine so a concurrently in-flight test/command
                # run (see `real_execute._run_test_command_cancellable`) observes it
                # on its next poll even if this request completes and returns before
                # that poll happens.
                cycle.cancel_requested = True
                engine.signal_cancel(reason=str(body.get("reason") or "OPERATOR_CANCEL"))
                # Clear dispatch claim so GET / `_sync_cycle_phase_from_engine` cannot
                # resurrect EXECUTING over CANCELLED after AIX hangs or the worker dies.
                cycle.dispatch_required = False
                cycle.dispatch_state = "IDLE"
                cycle.execution_claim_id = None
                cycle.execution_claim_step_id = None
                cycle.execution_claim_attempt = 0
                cycle.execution_claim_expired = False
                cycle.active_step_started_at = None
                cycle.step_lease_expires_at = None
                cycle.terminal_reason = "CANCELLED"
                cycle.last_error_detail = str(body.get("reason") or "OPERATOR_CANCEL")
                self._emit_event(
                    cycle=cycle, event_type="cycle.cancel_requested",
                    payload_refs={"reason": str(body.get("reason") or "OPERATOR_CANCEL"), "phase_at_request": cycle.phase},
                )
            elif action == "block":
                engine.wait_blocker()
                engine.signal_blocker_resolved(blocker_ref="", resolved=False)
            elif action == "reject":
                engine.signal_cancel(reason=str(body.get("reason") or "OPERATOR_REJECT"))
                cycle.terminal_reason = "REJECTED"
            elif action == "approval_reference":
                gate_error = self._verify_approval_gate(cycle=cycle, body=body)
                if gate_error is not None:
                    return gate_error
                ref = str(body.get("approval_reference") or "")
                engine.signal_approval(approval_ref=ref, valid=bool(ref.strip()))
                cycle.approval_reference = ref or None
                obj_for_approval = self._objectives.get(cycle.objective_id)
                if obj_for_approval is not None and ref.strip():
                    self._bind_approval_to_plan(obj_for_approval, ref)
                if engine.state.phase in ("WAITING_APPROVAL", "RUNNING_STEP"):
                    self._snapshot_approval(cycle=cycle)
                    return await self._plan_then_dispatch_after_approval(
                        cycle=cycle,
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        command_id=command_id,
                        caller_auth=caller_auth,
                        actor_id=actor_id,
                        reason="approval_received",
                    )
                if (
                    str(cycle.phase) == "GAPS_FOUND"
                    and "approval_ref required" in str(cycle.last_error_detail or "").casefold()
                ):
                    cycle.last_error_detail = None
                    cycle.terminal_reason = None
                    recovered = await self._maybe_advance_mid_plan_dispatch(
                        cycle=cycle, command_id=command_id
                    )
                    if isinstance(recovered, dict) and recovered.get("ok"):
                        if recovered.get("dispatch_required"):
                            dispatched = await self._dispatch_after_ready(
                                cycle=cycle,
                                tenant_id=tenant_id,
                                cycle_id=cycle_id,
                                command_id=command_id,
                                caller_auth=caller_auth,
                                actor_id=actor_id,
                                ready=recovered,
                            )
                            if dispatched is not None:
                                return self._control_payload_with_synced_phase(cycle, dispatched)
                        return self._control_payload_with_synced_phase(cycle, recovered)
                    return self._control_payload_with_synced_phase(
                        cycle,
                        {
                            "ok": True,
                            "operation": action,
                            "tenant_id": tenant_id,
                            "cycle_id": cycle.cycle_id,
                            "phase": cycle.phase,
                            "note": "approval_bound_gaps_found",
                        },
                    )
            elif action == "resume":
                if cycle.dispatch_state == "WAITING_FOR_AUTH":
                    new_auth = caller_auth or str(body.get("caller_authorization") or body.get("authorization") or "")
                    if not new_auth:
                        return {
                            "ok": False,
                            "operation": action,
                            "tenant_id": tenant_id,
                            "cycle_id": cycle_id,
                            "phase": cycle.phase,
                            "dispatch_state": "WAITING_FOR_AUTH",
                            "error": {
                                "code": "AUTHORIZATION_STILL_MISSING",
                                "detail": "Resume requires caller_auth or authorization in body.",
                            },
                        }
                    self._seal_execution_auth(
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        caller_auth=new_auth,
                        actor_id=actor_id,
                    )
                    cycle.terminal_reason = None
                    cycle.dispatch_state = "IDLE"
                    cycle.real_execute = None
                    if engine.state.phase == "BLOCKED":
                        engine.enter_resolution_pending(reason="AUTH_RESTORED", note="authorization provided")
                        engine.mark_ready_to_retry()
                        engine.begin_retry(
                            attempt_number=engine.state.attempt_number + 1
                        )
                    self._emit_event(
                        cycle=cycle,
                        event_type="cycle.authorization_restored",
                        payload_refs={"dispatch_state": "resuming"},
                    )
                    ready = await self._plan_then_mark_dispatch_ready(
                        cycle=cycle,
                        state="STEP_READY",
                        command_id=command_id,
                        reason="authorization_restored",
                        caller_auth=new_auth,
                        actor_id=actor_id,
                    )
                    if not ready.get("ok", False):
                        return self._control_payload_with_synced_phase(cycle, ready)
                    dispatched = await self._dispatch_after_ready(
                        cycle=cycle,
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        command_id=command_id,
                        caller_auth=new_auth,
                        actor_id=actor_id,
                        ready=ready,
                    )
                    if dispatched is not None:
                        return self._control_payload_with_synced_phase(cycle, dispatched)
                    if engine.state.phase == "RUNNING_STEP":
                        self._apply_terminalization_to_engine(cycle)
                else:
                    # Multi-step recovery: STEP_PASSED / VERIFYING with PENDING plan steps
                    # must advance before re-signaling WAITING_APPROVAL (ERP continue loop).
                    recovered = await self._maybe_advance_mid_plan_dispatch(
                        cycle=cycle, command_id=command_id
                    )
                    if isinstance(recovered, dict) and recovered.get("ok"):
                        if recovered.get("dispatch_required"):
                            dispatched = await self._dispatch_after_ready(
                                cycle=cycle,
                                tenant_id=tenant_id,
                                cycle_id=cycle_id,
                                command_id=command_id,
                                caller_auth=caller_auth,
                                actor_id=actor_id,
                                ready=recovered,
                            )
                            if dispatched is not None:
                                return self._control_payload_with_synced_phase(cycle, dispatched)
                            return self._control_payload_with_synced_phase(cycle, recovered)
                        if (
                            recovered.get("operation") == "plan_complete_terminal"
                            or str(recovered.get("phase") or "") == "COMPLETED_FOR_REVIEW"
                        ):
                            return self._control_payload_with_synced_phase(cycle, recovered)
                if engine.state.phase == "WAITING_BLOCKER":
                    engine.signal_blocker_resolved(blocker_ref=str(body.get("reason") or "blocker-resolved"), resolved=True)
                elif engine.state.phase == "WAITING_APPROVAL":
                    terminalized = await self._try_terminalize_all_steps_passed(cycle=cycle)
                    if terminalized is not None:
                        return self._control_payload_with_synced_phase(cycle, terminalized)
                    gate_error = self._verify_approval_gate(cycle=cycle, body=body)
                    if gate_error is not None:
                        return gate_error
                    # Same binding as action=approval_reference. Resume used to signal the
                    # engine only — cycle.approval_reference stayed empty, so after the plan
                    # was ACCEPTED execute failed with NEED_APPROVAL / approval_ref required.
                    ref = str(
                        body.get("approval_reference")
                        or cycle.approval_reference
                        or "workhub://approvals/local"
                    ).strip()
                    engine.signal_approval(approval_ref=ref, valid=bool(ref))
                    cycle.approval_reference = ref or None
                    obj_for_approval = self._objectives.get(cycle.objective_id)
                    if obj_for_approval is not None and ref:
                        self._bind_approval_to_plan(obj_for_approval, ref)
                    if engine.state.phase in ("WAITING_APPROVAL", "RUNNING_STEP"):
                        self._snapshot_approval(cycle=cycle)
                        return await self._plan_then_dispatch_after_approval(
                            cycle=cycle,
                            tenant_id=tenant_id,
                            cycle_id=cycle_id,
                            command_id=command_id,
                            caller_auth=caller_auth,
                            actor_id=actor_id,
                            reason="resume_approved",
                        )
                elif engine.state.phase == "RUNNING_STEP" and cycle.real_execute is None:
                    if not cycle.dispatch_required:
                        return await self._plan_then_dispatch_after_approval(
                            cycle=cycle,
                            tenant_id=tenant_id,
                            cycle_id=cycle_id,
                            command_id=command_id,
                            caller_auth=caller_auth,
                            actor_id=actor_id,
                            reason="explicit_resume",
                        )
                    ready = {
                        "ok": True,
                        "phase": cycle.phase,
                        "dispatch_state": cycle.dispatch_state,
                        "dispatch_deferred": True,
                    }
                    dispatched = await self._dispatch_after_ready(
                        cycle=cycle,
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        command_id=command_id,
                        caller_auth=caller_auth,
                        actor_id=actor_id,
                        ready=ready,
                    )
                    if dispatched is not None:
                        return self._control_payload_with_synced_phase(cycle, dispatched)
                    if engine.state.phase == "RUNNING_STEP":
                        self._apply_terminalization_to_engine(cycle)
            elif action == "retry_step":
                if engine.state.phase == "RUNNING_STEP":
                    # AIX-CLOSED-LOOP-004 P1: this branch is reached when the cycle is
                    # stuck at RUNNING_STEP because an earlier `_maybe_real_execute`
                    # call returned an error WITHOUT moving the engine off RUNNING_STEP
                    # (e.g. NO_WORK_ORDER, PLAN_CHANGED_SINCE_APPROVAL, a step-contract
                    # refusal) -- `cycle.real_execute` was never set in that case. The
                    # old code jumped straight to `engine.continue_after_approval()`
                    # here, which bypasses `_maybe_real_execute` entirely and always
                    # reaches COMPLETED_FOR_REVIEW via the stub `dispatch_step`/
                    # `verify_step`/`build_review_packet` activities -- a known false-
                    # completion route (AL-12 leftover). Route through
                    # `_maybe_real_execute` the same way "approval_reference"/"resume"
                    # do, so the retry actually re-attempts real execution (or surfaces
                    # the same blocking error again) instead of rubber-stamping completion.
                    if not cycle.dispatch_required:
                        ready = await self._plan_then_mark_dispatch_ready(
                            cycle=cycle,
                            state="STEP_READY",
                            command_id=command_id,
                            reason="explicit_retry",
                            caller_auth=caller_auth,
                            actor_id=actor_id,
                        )
                        if not ready.get("ok", False):
                            return self._control_payload_with_synced_phase(cycle, ready)
                    real_err = await self.dispatch_ready_step(
                        tenant_id=tenant_id, cycle_id=cycle_id, command_id=command_id,
                        caller_auth=caller_auth, actor_id=actor_id,
                    )
                    if real_err is not None:
                        return self._control_payload_with_synced_phase(cycle, real_err)
                    if engine.state.phase == "RUNNING_STEP":
                        self._apply_terminalization_to_engine(
                            cycle, step_id=str(body.get("step_id") or "step-1")
                        )
                elif engine.state.phase == "BLOCKED":
                    # Real retry lifecycle (gap-closure sprint 3, Priority 4):
                    # BLOCKED -> RESOLUTION_PENDING -> READY_TO_RETRY -> RETRYING ->
                    # RUNNING_STEP, authorized by `retry_repair.decide_retry`'s real
                    # budget/classification logic -- never a new objective/cycle;
                    # `obj` (execution_context, repository_id) and `cycle.trace_id`/
                    # evidence already persist unchanged because nothing is recreated.
                    classification = _classify_terminal_reason_for_retry(cycle.terminal_reason)
                    decision = decide_retry_routed(
                        classification,
                        current_attempt_number=engine.state.attempt_number,
                        remaining_budget=BudgetV1(
                            time_seconds=3600, tokens=1_000_000, cost_usd=100.0,
                            retries=cycle.retry_budget_remaining, steps=100, concurrency=1,
                        ),
                    )
                    if decision.disposition != "AUTHORIZED":
                        self._emit_event(
                            cycle=cycle, event_type="cycle.retry_denied",
                            payload_refs={"reason_codes": list(decision.reason_codes), "terminal_reason": cycle.terminal_reason},
                        )
                        return {
                            "ok": False,
                            "operation": action,
                            "tenant_id": tenant_id,
                            "cycle_id": cycle_id,
                            "phase": cycle.phase,
                            "terminal_reason": cycle.terminal_reason,
                            "error": {"code": "RETRY_NOT_AUTHORIZED", "reason_codes": list(decision.reason_codes)},
                            "workhub_lifecycle_authority": False,
                            "aix_creates_workhub_tasks": False,
                        }
                    cycle.retry_budget_remaining = decision.remaining_budget.retries if decision.remaining_budget else 0
                    reason_before = cycle.terminal_reason
                    engine.enter_resolution_pending(reason="RETRY_REQUESTED", note=str(body.get("reason") or reason_before or ""))
                    engine.mark_ready_to_retry()
                    engine.begin_retry(attempt_number=decision.next_attempt_number or (engine.state.attempt_number + 1))
                    cycle.terminal_reason = None
                    cycle.real_execute = None
                    self._emit_event(
                        cycle=cycle, event_type="cycle.retrying",
                        payload_refs={
                            "attempt_number": engine.state.attempt_number,
                            "prior_reason": reason_before,
                            "retry_budget_remaining": cycle.retry_budget_remaining,
                        },
                    )
                    ready = await self._plan_then_mark_dispatch_ready(
                        cycle=cycle,
                        state="STEP_READY",
                        command_id=command_id,
                        reason="retry_authorized",
                        caller_auth=caller_auth,
                        actor_id=actor_id,
                    )
                    if not ready.get("ok", False):
                        return self._control_payload_with_synced_phase(cycle, ready)
                    real_err = await self.dispatch_ready_step(
                        tenant_id=tenant_id, cycle_id=cycle_id, command_id=command_id,
                        caller_auth=caller_auth, actor_id=actor_id,
                    )
                    if real_err is not None:
                        return real_err
                    if engine.state.phase == "RUNNING_STEP":
                        self._apply_terminalization_to_engine(cycle)
            elif action == "replan":
                # Approval identity (gap-closure sprint 3, Priority 3): a replan is,
                # by definition, "the plan changed" -- bump plan_revision and mint a
                # fresh gate so any approval already granted against the old plan is
                # provably stale (`_verify_approval_gate`/`PLAN_CHANGED_SINCE_
                # APPROVAL`), and require the cycle to pass through WAITING_APPROVAL
                # again before it can execute. Distinct from `clarify`, which answers
                # a pre-plan question and never touches an already-granted approval.
                replan_input = body.get("replan_input") if isinstance(body.get("replan_input"), dict) else {}
                obj = self._objectives.get(cycle.objective_id)
                new_objective_text = str(replan_input.get("objective") or "").strip()
                if obj is not None and new_objective_text:
                    # K12: do not rewrite obj.objective / original_objective. Replan is a UserTurn.
                    self._append_user_turn_for_objective(obj, new_objective_text)
                cycle.plan_revision += 1
                cycle.current_gate_id = f"gate_{cycle_id}_r{cycle.plan_revision}"
                cycle.approved_gate_id = None
                cycle.approved_operations = ()
                cycle.approved_resources = ()
                # AIX-MPE-003 Packet MPE-12: a replan must not reuse the cached R1
                # StepContract. Clear the hot-path cache so the next dispatch
                # re-enters the planner and admits PlanRevision N+1.
                self._clear_plan_cache_for_replan(cycle.objective_id)
                self._step_contract_errors.pop(cycle.objective_id, None)
                # Drop outcome contracts that are not required for this objective class
                # (e.g. a prior LLM seal with UI/API facets on a source_edit refactor).
                if obj is not None and not requires_outcome_contract(
                    objective=self._objective_text(obj),
                    task_class=str(obj.task_class or ""),
                    settings=self._settings,
                    contract_already_present=False,
                ):
                    self._outcome_contracts.pop(obj.objective_id, None)
                    self._outcome_contract_obligations.pop(obj.objective_id, None)
                cycle.terminal_reason = None
                cycle.last_error_detail = None
                cycle.resume_count = 0
                cycle.resume_in_progress = False
                if engine.replan_from_blocked(reason="REPLAN"):
                    pass
                elif engine.force_waiting_approval_for_replan(reason="REPLAN"):
                    cycle.dispatch_state = "IDLE"
                    cycle.dispatch_required = False
                    cycle.execution_claim_id = None
                    cycle.execution_claim_step_id = None
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.replan",
                    payload_refs={"plan_revision": cycle.plan_revision, "new_gate_id": cycle.current_gate_id},
                )
            elif action == "clarify":
                # AIX-REAL-EXECUTION-002/005 gap-closure Priority 2: wires the existing
                # clarification-on-doubt gate (exploration_planner._clarification_
                # question, raised via `_run_exploration_gate`'s
                # `plan.clarification_required` branch, which parks the cycle in
                # WAITING_BLOCKER) to a real persist-answer -> reclassify -> continue
                # loop for the SAME objective/cycle -- never a parallel clarification
                # system. `body["clarification"]["answer"]` is the free-text answer
                # (`CycleControlRequest.clarification` already carries it).
                answer_text = str((body.get("clarification") or {}).get("answer") or "").strip()
                if not answer_text:
                    return {
                        "ok": False,
                        "operation": action,
                        "tenant_id": tenant_id,
                        "cycle_id": cycle_id,
                        "error": {"code": "CLARIFICATION_ANSWER_REQUIRED"},
                    }
                obj = self._objectives.get(cycle.objective_id)
                if obj is not None:
                    # P1: the answer is a UserTurn. Intent.text stays the admit freeze.
                    # Vague-phrase replacement in obj.objective is Intent rewrite (K12 class) and is not done here.
                    self._append_user_turn_for_objective(obj, answer_text)
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.clarify",
                    payload_refs={"answer": answer_text[:300], "objective_id": cycle.objective_id},
                )
                # Only revive a cycle the clarification gate itself parked in BLOCKED
                # (`_blocked_exploration_payload`'s `plan.clarification_required`
                # branch sets `cycle.terminal_reason` to one of these two codes) --
                # never a genuine failure like `CAPABILITY_NOT_REGISTERED` or
                # `WORKSPACE_NOT_BOUND`, which `clarify` must not be usable to bypass.
                if cycle.terminal_reason in _CLARIFICATION_REASON_CODES and engine.resume_from_clarification():
                    cycle.terminal_reason = None
                    ready = await self._plan_then_mark_dispatch_ready(
                        cycle=cycle,
                        state="STEP_READY",
                        command_id=command_id,
                        reason="clarification_resolved",
                        caller_auth=caller_auth,
                        actor_id=actor_id,
                    )
                    if not ready.get("ok", False):
                        return self._control_payload_with_synced_phase(cycle, ready)
                    real_err = await self.dispatch_ready_step(
                        tenant_id=tenant_id, cycle_id=cycle_id, command_id=command_id,
                        caller_auth=caller_auth, actor_id=actor_id,
                    )
                    if real_err is not None:
                        return self._control_payload_with_synced_phase(cycle, real_err)
                    if engine.state.phase == "RUNNING_STEP":
                        self._apply_terminalization_to_engine(cycle)

            self._sync_cycle_phase_from_engine(cycle)
            await self._maybe_record_cycle_outcome(cycle=cycle)
            cycle.version = max(cycle.version + 1, int(engine.state.aggregate_version or cycle.version + 1))
            self._emit_event(
                cycle=cycle,
                event_type=f"cycle.{action}",
                payload_refs={"actor_id": actor_id, "phase": cycle.phase},
            )
            await self._persist_event(
                tenant_id=tenant_id,
                aggregate_type="cycle",
                aggregate_id=cycle_id,
                expected_sequence_number=cycle.version,
                event_type=f"cycle.{action}",
                command_id=command_id,
                payload={"phase": cycle.phase, "actor_id": actor_id, "reason": body.get("reason")},
            )
            if action == "cancel":
                await self._project_run(cycle)
            return {
                "ok": True,
                "operation": action,
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "status": engine.query_status(),
                "terminal_reason": cycle.terminal_reason,
                "real_execute": cycle.real_execute,
                "plan_id": cycle.plan_id,
                "plan_revision": cycle.plan_revision,
                "approval_gate_id": cycle.current_gate_id,
                "approved_gate_id": cycle.approved_gate_id,
                "approved_operations": list(cycle.approved_operations),
                "approved_resources": list(cycle.approved_resources),
                "attempt_number": engine.state.attempt_number,
                "canonical_truth_hash": self._canonical_truths.get(cycle.objective_id).hash if self._canonical_truths.get(cycle.objective_id) else None,
                "ovc_hash": self._objective_verification_contracts.get(cycle.objective_id).hash if self._objective_verification_contracts.get(cycle.objective_id) else None,
                "open_gaps_count": self._gap_store.count_blocking_gaps(cycle.objective_id),
                "terminalization_decision": self._terminalization_decisions.get(cycle.cycle_id).decision if self._terminalization_decisions.get(cycle.cycle_id) else None,
                "workhub_lifecycle_authority": bool(self.mark_workhub_done),
                "aix_marks_workhub_done": bool(self.mark_workhub_done),
                "exception": "001P-W13" if self.mark_workhub_done else ("001P-W12" if self.create_workhub_tasks else None),
                "production_go": False,
            }
        except Exception as exc:  # noqa: BLE001 - resume/retry must not 500 the CLI
            detail = f"{type(exc).__name__}:{exc}"[:800]
            logger.exception(
                "uncaught_cycle_control_error action=%s cycle=%s",
                action,
                cycle_id,
            )
            cycle.last_error_detail = detail
            try:
                log_path = Path(r"D:\v2.1\aix\.tmp\start-logs\aix-uncaught.log")
                log_path.parent.mkdir(parents=True, exist_ok=True)
                # Was write_text() (overwrite-only, one line, no traceback) --
                # each new uncaught error erased the previous one and only the
                # exception's str() survived, rarely enough to locate the
                # raising frame. Append the full traceback instead.
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"{_utc()} {action} {cycle_id} {detail}\n")
                    f.write(traceback.format_exc())
                    f.write("\n")
            except Exception:
                pass
            try:
                if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                    cycle.engine.wait_blocker()
                    cycle.engine.signal_blocker_resolved(
                        blocker_ref="UNCAUGHT_CONTROL_ERROR", resolved=False
                    )
                if cycle.engine is not None:
                    self._sync_cycle_phase_from_engine(cycle)
                if not cycle.terminal_reason:
                    cycle.terminal_reason = "UNCAUGHT_CONTROL_ERROR"
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.uncaught_error",
                    payload_refs={"detail": detail, "action": action},
                )
            except Exception:
                logger.exception("failed to park cycle after uncaught control error")
            return {
                "ok": False,
                "operation": action,
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
                "last_error_detail": detail,
                "error": {"code": cycle.terminal_reason or "UNCAUGHT_CONTROL_ERROR", "detail": detail},
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
                "production_go": False,
            }
        finally:
            if action in ("approval_reference", "resume"):
                cycle.resume_in_progress = False

    def _verify_approval_gate(self, *, cycle: "_CycleRecord", body: dict[str, Any]) -> dict[str, Any] | None:
        """AIX gap-closure sprint 3, Priority 3: when the caller supplies an explicit
        `approval_gate_id` (and/or `plan_revision`), it must name the cycle's CURRENT
        gate/revision -- a stale value naming an earlier plan is rejected outright
        rather than silently accepted just because the cycle happens to be
        `WAITING_APPROVAL` again. Legacy callers that supply neither are unaffected
        (today's behavior: approval implicitly targets whatever gate is current).
        Returns an error payload to return verbatim, or `None` when the check passes.
        """
        supplied_gate = str(body.get("approval_gate_id") or "").strip() or None
        supplied_revision = body.get("plan_revision")
        if supplied_gate is not None and supplied_gate != cycle.current_gate_id:
            return {
                "ok": False,
                "operation": "approval_reference",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "error": {
                    "code": "STALE_APPROVAL_GATE",
                    "detail": (
                        f"approval_gate_id {supplied_gate!r} does not match the cycle's "
                        f"current gate {cycle.current_gate_id!r} -- the plan changed "
                        "since this approval was issued"
                    ),
                },
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }
        if supplied_revision is not None:
            try:
                if int(supplied_revision) != cycle.plan_revision:
                    return {
                        "ok": False,
                        "operation": "approval_reference",
                        "tenant_id": cycle.tenant_id,
                        "cycle_id": cycle.cycle_id,
                        "phase": cycle.phase,
                        "error": {
                            "code": "WRONG_PLAN_REVISION",
                            "detail": (
                                f"approval targets plan_revision {supplied_revision!r} but the "
                                f"cycle's current plan_revision is {cycle.plan_revision}"
                            ),
                        },
                        "workhub_lifecycle_authority": False,
                        "aix_creates_workhub_tasks": False,
                    }
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "operation": "approval_reference",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "error": {"code": "PLAN_REVISION_INVALID"},
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }
        return None

    def _snapshot_approval(self, *, cycle: "_CycleRecord") -> None:
        """Record exactly what this approval authorized -- never mutated afterward, so
        a later mismatch against the cycle's live state (plan changed, repository
        rebound) is detectable at dispatch time rather than silently trusted."""
        obj = self._objectives.get(cycle.objective_id)
        cycle.approved_gate_id = cycle.current_gate_id
        cycle.approved_plan_revision = cycle.plan_revision
        cycle.approved_operations = (obj.task_family,) if obj is not None else ()
        cycle.approved_resources = (obj.repository_id,) if obj is not None else ()

    def _sync_cycle_phase_from_engine(self, cycle: _CycleRecord) -> None:
        """Keep `cycle.phase` aligned with the engine. Early `_maybe_real_execute`
        returns historically left `cycle.phase` stuck at WAITING_APPROVAL after
        `signal_approval` moved the engine to RUNNING_STEP — GET then lied and
        resume refused recovery via `cycle_not_awaiting_approval`.

        Cancel / terminal outcomes win over dispatch overlays: an operator cancel
        may clear the engine to CANCELLED while a zombie EXECUTING claim still
        sits in memory after an AIX hang; never let that claim rewrite the phase.
        """
        engine_phase = str(cycle.engine.state.phase) if cycle.engine is not None else ""
        if (
            cycle.cancel_requested
            or engine_phase == "CANCELLED"
            or is_genuinely_terminal_phase(
                phase=cycle.phase, terminal_reason=cycle.terminal_reason
            )
            or cycle.terminal_reason == "CANCELLED"
        ):
            if engine_phase == "CANCELLED" or cycle.cancel_requested or cycle.terminal_reason == "CANCELLED":
                cycle.phase = "CANCELLED"
            return
        # Engine gap-repair phases win over stale dispatch overlays left from a
        # prior failed repair attempt (dispatch_state STEP_FAILED vs engine GAPS_FOUND).
        if engine_phase in {"GAPS_FOUND", "REPAIRING"}:
            cycle.phase = engine_phase
            if cycle.dispatch_state in {"STEP_FAILED", "VERIFYING", "EXECUTING"}:
                cycle.dispatch_state = "IDLE"
                cycle.dispatch_required = False
                cycle.execution_claim_id = None
                cycle.execution_claim_step_id = None
                cycle.execution_claim_attempt = 0
            return
        if cycle.dispatch_state in {
            "STEP_READY",
            "NEXT_STEP_READY",
            "EXECUTING",
            "VERIFYING",
            "STEP_FAILED",
            "BLOCKED",
            "CANCELLED",
        }:
            cycle.phase = cycle.dispatch_state
        elif cycle.engine is not None:
            cycle.phase = engine_phase

    def _cycle_eligible_for_gap_repair(self, cycle: _CycleRecord) -> bool:
        """True when engine or durable gaps indicate the cycle is in gap-repair state."""
        self._sync_cycle_phase_from_engine(cycle)
        if cycle.phase == "GAPS_FOUND":
            return True
        engine_phase = str(cycle.engine.state.phase) if cycle.engine is not None else ""
        if engine_phase == "GAPS_FOUND":
            cycle.phase = "GAPS_FOUND"
            if cycle.dispatch_state in {"STEP_FAILED", "VERIFYING", "EXECUTING"}:
                cycle.dispatch_state = "IDLE"
                cycle.dispatch_required = False
            return True
        return self._gap_store.count_blocking_gaps(cycle.objective_id) > 0

    def _control_payload_with_synced_phase(
        self, cycle: _CycleRecord, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._sync_cycle_phase_from_engine(cycle)
        out = dict(payload)
        out["phase"] = cycle.phase
        if cycle.engine is not None:
            out["status"] = cycle.engine.query_status()
        out["canonical_truth_hash"] = self._canonical_truths.get(cycle.objective_id).hash if self._canonical_truths.get(cycle.objective_id) else None
        out["ovc_hash"] = self._objective_verification_contracts.get(cycle.objective_id).hash if self._objective_verification_contracts.get(cycle.objective_id) else None
        out["open_gaps_count"] = self._gap_store.count_blocking_gaps(cycle.objective_id)
        out["terminalization_decision"] = self._terminalization_decisions.get(cycle.cycle_id).decision if self._terminalization_decisions.get(cycle.cycle_id) else None
        return out

    def _idempotent_control_response(
        self, *, cycle: _CycleRecord, action: str, tenant_id: str, note: str
    ) -> dict[str, Any]:
        """Returned instead of re-running approve/resume mutation logic when a repeat
        call for the same approval (or one arriving mid-flight) is detected — the
        current cycle state, not an error, and never a second `_maybe_real_execute`."""
        self._sync_cycle_phase_from_engine(cycle)
        engine = cycle.engine
        return {
            "ok": True,
            "operation": action,
            "tenant_id": tenant_id,
            "cycle_id": cycle.cycle_id,
            "phase": cycle.phase,
            "version": cycle.version,
            "status": engine.query_status() if engine is not None else {"phase": cycle.phase},
            "terminal_reason": cycle.terminal_reason,
            "dispatch_state": cycle.dispatch_state,
            "dispatch_required": cycle.dispatch_required,
            "active_step_id": cycle.current_step_cursor,
            "real_execute": cycle.real_execute,
            "workhub_lifecycle_authority": bool(self.mark_workhub_done),
            "aix_marks_workhub_done": bool(self.mark_workhub_done),
            "already_processed": True,
            "idempotent_replay_reason": note,
            "note": note,
            "production_go": False,
        }

    def _emit_event(
        self, *, cycle: _CycleRecord, event_type: str, payload_refs: dict[str, Any] | None = None
    ) -> None:
        """Append one real event with the Sprint 001 trace envelope.

        Events carry a stable trace/objective/cycle identity, version, monotonic
        sequence, component and status. Deduped: an event that exactly repeats the immediately
        preceding event's type+refs, and whose refs describe a zero-change file
        (`additions == 0 and deletions == 0`), is dropped instead of appended again —
        the client must never see the same no-op file event twice."""
        refs = {k: v for k, v in dict(payload_refs or {}).items()}
        if cycle.events:
            last = cycle.events[-1]
            if last.get("event_type") == event_type and last.get("payload_refs") == refs:
                additions, deletions = refs.get("additions"), refs.get("deletions")
                if additions is not None and deletions is not None:
                    try:
                        if int(additions) == 0 and int(deletions) == 0:
                            return
                    except (TypeError, ValueError):
                        pass
        cycle.event_sequence += 1
        try:
            family = family_for(event_type)
        except EventSchemaError:
            family = "cycle"
        occurred_at = _utc()
        status = str(
            refs.get("objective_status")
            or refs.get("status")
            or event_type.rsplit(".", 1)[-1]
        ).upper()
        reason_code = refs.get("reason_code") or refs.get("code")
        # C006: attach versioned EventEnvelopeV1 metadata (fail-closed only for
        # contract_version; unknown event labels still append for migration).
        envelope: dict[str, Any] | None = None
        try:
            from backend.aix_core.runtime.governance.command_event_contract import build_event

            env = build_event(
                event_type=event_type,
                tenant_id=cycle.tenant_id,
                actor_id=str(refs.get("actor_id") or "system"),
                sequence_number=cycle.event_sequence,
                command_id=str(refs.get("command_id") or f"emit:{cycle.cycle_id}:{cycle.event_sequence}"),
                correlation_id=cycle.trace_id,
                objective_id=cycle.objective_id,
                run_id=cycle.cycle_id,
                payload=refs,
                allow_unknown_event=True,
            )
            envelope = env.to_dict()
        except Exception:
            envelope = {
                "contract_version": "aix.command_event.v1",
                "event_type": event_type,
                "tenant_id": cycle.tenant_id,
                "sequence_number": cycle.event_sequence,
                "envelope_error": True,
            }
        cycle.events.append(
            {
                "event_id": new_ulid(),
                "event_type": event_type,
                "event": event_type,  # back-compat key some existing callers still read
                "event_version": "1.0",
                "contract_version": "aix.command_event.v1",
                "envelope": envelope,
                "family": family,
                "trace_id": cycle.trace_id,
                "objective_id": cycle.objective_id,
                "cycle_id": cycle.cycle_id,
                "sequence": cycle.event_sequence,
                "sequence_number": cycle.event_sequence,
                "component": "objective-cycle",
                "status": status,
                "occurred_at": occurred_at,
                "at": occurred_at,  # back-compat key some existing callers still read
                "reason_code": reason_code,
                "step_id": refs.get("step_id"),
                "attempt_id": refs.get("attempt_id"),
                "tool_call_id": refs.get("tool_call_id"),
                "resource_id": refs.get("resource_id") or refs.get("path"),
                "input_summary": {},
                "output_summary": {},
                "payload_refs": refs,
            }
        )
        # C011: keep decision projection current on aggregate lifecycle events.
        # Never do sync disk upsert on the event loop — that freezes /health.
        # Skip tool.* entirely; other events project via a worker thread.
        if isinstance(cycle, _CycleRecord) and not str(event_type).startswith("tool."):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(asyncio.to_thread(self._project_run_sync, cycle))
            except RuntimeError:
                self._project_run_sync(cycle)

    async def _maybe_record_cycle_outcome(self, *, cycle: "_CycleRecord") -> None:
        """Autonomous Closed-Loop Lane, Packet AL-11: construct and record a
        `CycleOutcomeV1` the first time (and only the first time -- `self._cycle_outcomes`
        is this method's own idempotency guard) a cycle reaches a genuinely terminal phase.
        Safe to call after every `cycle.phase` mutation regardless of what phase resulted --
        a no-op for every non-terminal phase and for a cycle whose outcome is already
        recorded, so callers never need to duplicate the terminal-phase check themselves."""
        if cycle.cycle_id in self._cycle_outcomes:
            return
        if not is_genuinely_terminal_phase(phase=cycle.phase, terminal_reason=cycle.terminal_reason):
            return
        attempts = tuple(self._step_attempts.get(cycle.objective_id, ()))
        evidence_bundle_ref = f"trace://{cycle.trace_id}"
        outcome = build_cycle_outcome(
            cycle_id=cycle.cycle_id,
            tenant_id=cycle.tenant_id,
            phase=cycle.phase,
            terminal_reason=cycle.terminal_reason,
            step_attempts=attempts,
            evidence_bundle_ref=evidence_bundle_ref,
        )
        self._cycle_outcomes[cycle.cycle_id] = outcome
        # C003: sole canonical CompletionDecision emitter for objective_cycle terminals.
        completion_decision_id: str | None = None
        try:
            from backend.aix_core.runtime.governance.completion_authority import decision_from_cycle_outcome

            completion = decision_from_cycle_outcome(
                tenant_id=cycle.tenant_id,
                objective_id=cycle.objective_id,
                cycle_id=cycle.cycle_id,
                terminal_status=outcome.terminal_status,
                evidence_refs=(evidence_bundle_ref,),
                reasons=(cycle.terminal_reason,) if cycle.terminal_reason else (f"cycle_outcome:{outcome.terminal_status}",),
                terminalization_decision=self._terminalization_decisions.get(cycle.cycle_id),
            )
            completion_decision_id = completion.decision_id
        except Exception:
            logger.exception(
                "completion_authority decision failed cycle=%s status=%s",
                cycle.cycle_id,
                outcome.terminal_status,
            )
        self._emit_event(
            cycle=cycle,
            event_type="cycle.outcome_recorded",
            payload_refs={
                "outcome_id": outcome.id,
                "terminal_status": outcome.terminal_status,
                "completed_step_ids": list(outcome.completed_step_ids),
                "failed_step_ids": list(outcome.failed_step_ids),
                "blocked_step_ids": list(outcome.blocked_step_ids),
                "completion_decision_id": completion_decision_id,
            },
        )
        try:
            await self._persist_event(
                tenant_id=cycle.tenant_id,
                aggregate_type="outcome",
                aggregate_id=outcome.id,
                expected_sequence_number=1,
                event_type="cycle.outcome_recorded",
                command_id=f"outcome:{cycle.cycle_id}",
                payload={
                    "cycle_id": outcome.cycle_id,
                    "terminal_status": outcome.terminal_status,
                    "completed_step_ids": list(outcome.completed_step_ids),
                    "failed_step_ids": list(outcome.failed_step_ids),
                    "blocked_step_ids": list(outcome.blocked_step_ids),
                    "unresolved_risks": list(outcome.unresolved_risks),
                    "review_recommendation": outcome.review_recommendation,
                },
            )
        except ObjectiveCycleRepositoryError:
            logger.exception("cycle_outcome persist failed cycle=%s", cycle.cycle_id)

    def _record_real_execute_in_context(self, *, obj: "_ObjectiveRecord", result: Any) -> None:
        """AIX-REAL-EXECUTION-006 Cursor-class extension: 'on apply_patch/validate,
        update context files_modified, generated_patches, hashes before/after'. The
        actual patch validate/apply happens inside `real_execute.py`'s own edit
        branch (not brokered through `ContextToolExecutor` -- that file is a
        different agent's ownership this session does not restructure), so this is a
        truthful post-hoc reconciliation of its real, already-computed result into
        the same per-objective `ExecutionContext` the exploration gate maintains --
        never invented, only reflected."""
        changed = list(getattr(result, "changed_source_files", None) or [])
        proposed = list(getattr(result, "proposed_edits", None) or [])
        if not changed and not proposed:
            return
        context = _context_from_dict(obj.objective_id, obj.execution_context)
        for entry in changed:
            if isinstance(entry, dict):
                context.files_modified.append(dict(entry))
        for edit in proposed:
            if isinstance(edit, dict):
                context.generated_patches.append(
                    {
                        "path": edit.get("path"),
                        "before_hash": edit.get("before_hash"),
                        "after_hash": edit.get("after_hash"),
                        "lines_added": edit.get("lines_added"),
                        "lines_removed": edit.get("lines_removed"),
                    }
                )
        obj.execution_context = context.to_dict()

    async def _blocked_exploration_payload(
        self,
        *,
        cycle: _CycleRecord,
        code: str,
        detail: str,
        agent_role: str = "lifecycle",
        agent_component: str = "_blocked_exploration_payload",
        suggested_recovery: str = "",
        upstream_dependency: str = "",
        evidence_missing: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Shared truthful-block response shape (matches `_maybe_real_execute`'s own
        `RealExecuteError` handler) for a gate that never reached `run_real_execute`."""
        report = build_failure_report(
            agent_role=agent_role,
            agent_component=agent_component,
            objective_id=cycle.objective_id,
            cycle_id=cycle.cycle_id,
            failure_code=code,
            failure_detail=detail[:500],
            phase=cycle.phase or "BLOCKED",
            attempted_action=f"gate check ({code})",
            suggested_recovery=suggested_recovery,
            upstream_dependency=upstream_dependency,
            evidence_missing=evidence_missing,
        )
        self._feedback_collector.record(report)
        self._failure_reports.setdefault(cycle.objective_id, []).append(report)

        if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
            cycle.engine.wait_blocker()
            cycle.engine.signal_blocker_resolved(blocker_ref=code, resolved=False)
            self._sync_cycle_phase_from_engine(cycle)
        cycle.terminal_reason = code
        # Was never set here -- GET /cycles/{id} (`_cycle_payload`) surfaces
        # `last_error_detail`, but every caller only ever saw this function's
        # `detail` in the one HTTP response of the failing resume/approve call.
        # Once that response was discarded, `terminal_reason` (a bare code like
        # NEEDS_MORE_EVIDENCE) was all a later poll or page reload could show --
        # the planner's actual explanation of what evidence it needs was lost.
        cycle.last_error_detail = detail[:800]
        if cycle.engine is not None and str(cycle.engine.state.phase) == "BLOCKED":
            await self._maybe_record_cycle_outcome(cycle=cycle)
        cycle.last_progress_at = _utc()
        engine_version = (
            int(cycle.engine.state.aggregate_version or 0)
            if cycle.engine is not None
            else 0
        )
        prior_version = int(getattr(cycle, "version", 0) or 0)
        cycle.version = max(prior_version + 1, engine_version)
        self._emit_event(cycle=cycle, event_type="plan.rejected", payload_refs={"reason_code": code})
        self._emit_event(cycle=cycle, event_type="execution.blocked", payload_refs={"reason_code": code})
        self._emit_event(
            cycle=cycle,
            event_type="objective.blocked",
            payload_refs={"reason_code": code, "objective_status": "BLOCKED"},
        )
        # This helper is commonly returned directly from _control. Persist here so
        # the early return cannot leave PostgreSQL at WAITING_APPROVAL while memory is
        # already BLOCKED (the execution UI then appears permanently stale).
        await self._project_run(cycle)
        failure_chain = self._feedback_collector.failure_chain(cycle.objective_id)
        return {
            "ok": False,
            "operation": "real_execute",
            "tenant_id": cycle.tenant_id,
            "cycle_id": cycle.cycle_id,
            "phase": cycle.phase,
            "terminal_reason": cycle.terminal_reason,
            "error": {"code": code, "detail": detail},
            "failure_report_id": report.id,
            "failure_chain": failure_chain,
            "failure_summary": summarize_failure_chain(
                self._feedback_collector.get_reports(cycle.objective_id)
            ),
            "workhub_lifecycle_authority": False,
            "aix_creates_workhub_tasks": False,
        }

    def _resolve_exploration_step_path(
        self,
        step: Any,
        context: ExecutionContext,
        named_sources: tuple[str, ...] = (),
    ) -> str | None:
        """Resolve the real target of an adaptive exploration step. Never invents a
        path: `path_from_step`/`selector` steps only resolve against a path a prior
        real search or symbol-resolution step in this same context actually returned."""
        explicit = str(step.arguments.get("path") or "").strip()
        if explicit:
            return explicit
        selector = str(step.arguments.get("selector") or "")
        if selector == "first_source_hit" and context.searches_executed:
            # Multiple search terms may have been tried in the same exploration round
            # (see `_append_adaptive_steps`, which searches every derived term before
            # this read step runs) -- a later term producing zero hits must not shadow
            # an earlier one that actually found the real, relevant file. Scan from
            # most recent backward for the first search that returned a real hit,
            # rather than blindly trusting whichever search happened to run last.
            ordered: list[str] = []
            for record in reversed(context.searches_executed):
                for path in record.hit_summary.get("paths") or []:
                    text = str(path or "").strip()
                    if text and text not in ordered:
                        ordered.append(text)
            if not ordered:
                return None
            # AIX-EDIT planner grounding priority (generic): a file explicitly
            # named in the objective outranks hit order; among discovered hits
            # an implementation file outranks adjacent tests (tests join via
            # validation, they must not become the implementation locus unless
            # explicitly named). Never invents: only recorded hits qualify.
            named_set = {str(p or "").strip().replace("\\", "/") for p in named_sources}
            for path in ordered:
                if path.replace("\\", "/") in named_set:
                    return path
            for path in ordered:
                if not is_test_like_path(path):
                    return path
            return str(ordered[0])
        if selector == "resolved_file" and context.symbols_resolved:
            locations = context.symbols_resolved[-1].locations
            if locations:
                return str(locations[0]).split(":", 1)[0]
        return None

    def _execute_exploration_steps(
        self,
        *,
        plan: ExplorationPlan,
        context: ExecutionContext,
        repository_root: Path,
        discovery_config_files: dict[str, str],
        objective: str = "",
    ) -> None:
        """Execute one bounded exploration slice for real through `ContextToolExecutor`
        -- every step is a genuine `RepositoryToolbox`/filesystem/`SymbolResolver` call,
        path-confined to `repository_root` exactly like the production execution
        branches. Context is updated only on success (`ContextToolExecutor`'s own
        contract); a duplicate fingerprint is skipped, not re-issued; any other failure
        is skipped for this step (best-effort exploration) rather than aborting the
        whole slice, since an exploration read/search failing is informative, not fatal,
        and the deterministic understanding gate will simply stay unsatisfied."""
        executor = ContextToolExecutor(context=context)

        def _noop_event(_event_type: str, _payload_refs: dict[str, Any]) -> None:
            return None

        toolbox = RepositoryToolbox(repository_root=repository_root, on_event=_noop_event)

        # AIX-EDIT planner grounding priority (generic): resolve explicitly
        # named objective files once per slice; the hit selector prefers them
        # over hit order, then implementation files over adjacent tests.
        _named_for_slice: tuple[str, ...] = ()
        try:
            _named_for_slice = split_named_objective_files(
                str(objective or ""), repository_root
            )[0]
        except (OSError, ValueError, AttributeError, TypeError):
            _named_for_slice = ()

        for step in plan.steps:
            try:
                if step.capability == "filesystem.exists":
                    path = self._resolve_exploration_step_path(
                        step, context, named_sources=_named_for_slice
                    )
                    if not path:
                        continue
                    relative = resolve_relative_path(path, repository_root)
                    exists = bool(relative is not None and (repository_root / relative).exists())
                    result = {"path": relative or path, "exists": exists}
                    executor.call("filesystem.exists", lambda **_: result, arguments={"path": relative or path})
                elif step.capability == "filesystem.stat":
                    path = self._resolve_exploration_step_path(
                        step, context, named_sources=_named_for_slice
                    )
                    if not path:
                        continue
                    relative = resolve_relative_path(path, repository_root)
                    full = (repository_root / relative) if relative else None
                    if full is not None and full.exists():
                        st = full.stat()
                        result = {"path": relative, "size_bytes": st.st_size, "is_dir": full.is_dir()}
                    else:
                        result = {"path": relative or path, "size_bytes": None, "is_dir": False}
                    executor.call("filesystem.stat", lambda **_: result, arguments={"path": relative or path})
                elif step.capability == "filesystem.read_file":
                    path = self._resolve_exploration_step_path(
                        step, context, named_sources=_named_for_slice
                    )
                    if not path:
                        continue
                    relative = resolve_relative_path(path, repository_root)
                    if relative is None:
                        continue
                    result = toolbox.read_file(relative)
                    executor.call("filesystem.read_file", lambda **_: result, arguments={"path": relative})
                elif step.capability == "filesystem.list_directory":
                    raw_path = str(step.arguments.get("path") or ".")
                    relative = "." if raw_path in (".", "") else (resolve_relative_path(raw_path, repository_root) or ".")
                    result = toolbox.list_directory(relative)
                    executor.call("filesystem.list_directory", lambda **_: result, arguments={"path": relative})
                elif step.capability == "filesystem.search":
                    query = str(step.arguments.get("query") or "")
                    if not query:
                        continue
                    raw_scope = str(step.arguments.get("scope") or ".")
                    relative_scope = "." if raw_scope in (".", "") else (resolve_relative_path(raw_scope, repository_root) or ".")
                    result = toolbox.search_files(query, relative_path=relative_scope)
                    executor.call(
                        "filesystem.search",
                        lambda **_: result,
                        arguments={"query": query, "scope": relative_scope},
                    )
                elif step.capability == "repository.resolve_symbol":
                    symbol_name = str(step.arguments.get("symbol_name") or "")
                    if not symbol_name:
                        continue
                    resolver = SymbolResolver()
                    sym_res = resolver.resolve_symbol(
                        symbol_name=symbol_name,
                        target_file=None,
                        workspace_root=repository_root,
                        content_by_file=discovery_config_files,
                    )
                    if sym_res.status != "RESOLVED":
                        continue
                    result = {
                        "symbol_name": symbol_name,
                        "locations": [
                            {"file_path": loc.file_path, "start_line": loc.start_line} for loc in sym_res.locations
                        ],
                    }
                    executor.call(
                        "repository.resolve_symbol",
                        lambda **_: result,
                        arguments={"symbol_name": symbol_name},
                    )
            except DuplicateToolCallError:
                continue
            except (RepositoryToolError, ReadFileError, OSError):
                continue

    def _record_search_hits_as_evidence(
        self,
        *,
        obj: "_ObjectiveRecord",
        query: str,
        paths: tuple[str, ...],
    ) -> None:
        """Attach search hits to an existing Need. Never invent a Need from the repo."""
        understanding = self._provenance.latest_understanding(obj.objective_id)
        intent = self._provenance.get_intent(obj.objective_id)
        if understanding is None or intent is None or not paths:
            return
        need = next((item for item in reversed(self._provenance.needs(obj.objective_id))), None)
        if need is None:
            minted = needs_from_understanding(understanding, intent=intent)
            if not minted:
                return
            need = minted[0]
            self._provenance.put_need(need)
        hits = tuple(
            ExploreHit(
                location=path,
                observation=f"Search for {query!r} matched {path}",
                exists=True,
                tool="search_files",
                query=query,
            )
            for path in paths
        )
        evidence = execute_need(need, understanding=understanding, collect=lambda _need: hits)
        for item in evidence:
            try:
                self._provenance.put_evidence(item)
            except IntentFrozenError:
                continue

    async def _run_provenance_explore(
        self,
        *,
        cycle: _CycleRecord,
        obj: "_ObjectiveRecord",
        repository_root: Path | None,
        context: ExecutionContext,
    ) -> dict[str, Any] | None:
        """Execute typed ExplorationNeeds only. Search hits become Evidence, not scope."""
        understanding = self._provenance.latest_understanding(obj.objective_id)
        intent = self._provenance.get_intent(obj.objective_id)
        if understanding is None or intent is None:
            return None
        sources = ("search_files",)
        n5 = validate_explore_gate_sources(sources)
        if not n5.ok:
            return await self._blocked_exploration_payload(
                cycle=cycle,
                code="N5",
                detail=n5.detail or "forbidden explore source",
            )

        workspace_root = Path(context.current_workspace) if context.current_workspace else None

        def collect(need: ExplorationNeed) -> tuple[ExploreHit, ...]:
            root = repository_root
            if need.source == "cycle_workspace":
                root = workspace_root
            if root is None:
                return ()
            toolbox = RepositoryToolbox(repository_root=root, on_event=lambda *_a, **_k: None)
            try:
                result = toolbox.search_files(need.question)
            except (RepositoryToolError, OSError):
                return ()
            hits: list[ExploreHit] = []
            seen: set[str] = set()
            for raw in result.get("hits") or []:
                path = str(raw.get("path") or "").replace("\\", "/")
                if not path or path in seen:
                    continue
                seen.add(path)
                snippet = str(raw.get("line") or raw.get("text") or "").strip()[:160]
                observation = f"Search for {need.question!r} matched {path}"
                if snippet:
                    observation = f"{observation}: {snippet}"
                hits.append(
                    ExploreHit(
                        location=path,
                        observation=observation,
                        exists=True,
                        tool="search_files",
                        query=need.question,
                    )
                )
            return tuple(hits)

        run = await asyncio.to_thread(
            run_explore,
            intent=intent,
            understanding=understanding,
            collect=collect,
            turns=self._provenance.turns(obj.objective_id),
            sources=sources,
        )
        for need in run.needs:
            self._provenance.put_need(need)
        admission_paths = {
            str(p or "").strip().replace("\\", "/")
            for p in obj.allowed_files
            if str(p or "").strip()
        }
        for item in run.evidence:
            loc = str(item.location or "").replace("\\", "/")
            if admission_paths and loc not in admission_paths:
                continue
            try:
                self._provenance.put_evidence(item)
            except IntentFrozenError:
                continue
        if run.understanding.id != understanding.id:
            self._provenance.put_understanding(run.understanding)
        if run.evidence:
            locations = [item.location for item in run.evidence if item.location]
            snapshot = build_understanding_evidence(
                objective_id=obj.objective_id,
                cycle_id=cycle.cycle_id,
                context=context,
                observed_problem=(
                    f"{intent.text} (exploration evidence: {len(run.evidence)} hit(s)"
                    + (f" at {', '.join(str(p) for p in locations[:8])}" if locations else "")
                    + ")"
                ),
            )
            if locations and not snapshot.files_read:
                # The planner's v1 evidence contract exposes repository locations as
                # files_read. Provenance Explore has already produced typed, persisted
                # Evidence for these search hits, so carry those grounded locations
                # across the compatibility boundary instead of presenting an empty
                # evidence set and forcing a redundant planner request.
                snapshot = snapshot.model_copy(
                    update={
                        "files_read": tuple(dict.fromkeys(str(p) for p in locations)),
                        "evidence_refs": tuple(f"evidence://{item.id}" for item in run.evidence),
                    }
                )
            self._understanding_evidence[obj.objective_id] = snapshot
        return None

    async def _run_adaptive_exploration_slice(
        self,
        *,
        cycle: _CycleRecord,
        obj: "_ObjectiveRecord",
        context: ExecutionContext,
        repository_root: Path,
        target_path: str | None,
        symbol_name: str | None,
        target_bound: bool,
    ) -> dict[str, Any] | None:
        """Execute one bounded adaptive exploration slice through ContextToolExecutor."""
        plan = build_exploration_plan(
            objective=obj.objective,
            task_family=obj.task_family,
            context=context,
            target_path=target_path,
            symbol_name=symbol_name,
        )
        if plan.clarification_required or plan.understanding_complete or not plan.steps:
            return None

        steps_payload = [
            {
                "capability": step.capability,
                "action": step.action,
                "arguments": dict(step.arguments),
            }
            for step in plan.steps
        ]
        cap = validate_plan_capabilities(steps_payload)
        if not cap.valid:
            return await self._blocked_exploration_payload(
                cycle=cycle,
                code=cap.reason_code or "CAPABILITY_NOT_REGISTERED",
                detail=cap.detail or "plan step references unregistered capability",
            )

        discovery_config_files = await asyncio.to_thread(
            build_discovery_config_for_exploration,
            objective=obj.objective,
            repository_root=repository_root,
        )
        await asyncio.to_thread(
            self._execute_exploration_steps,
            plan=plan,
            context=context,
            repository_root=repository_root,
            discovery_config_files=discovery_config_files,
            objective=str(obj.objective or ""),
        )
        self._supplement_exploration_census_read(
            context=context,
            repository_root=repository_root,
            task_family=obj.task_family,
            target_bound=target_bound,
        )
        obj.execution_context = context.to_dict()
        return None

    def _supplement_exploration_census_read(
        self,
        *,
        context: ExecutionContext,
        repository_root: Path,
        task_family: str,
        target_bound: bool,
    ) -> None:
        """When adaptive exploration listed the repo but no search needle produced a read."""
        if not requires_exploration(task_family, target_bound=target_bound):
            return
        if context.files_read:
            return
        if not context.directories_listed and not context.execution_root:
            return

        executor = ContextToolExecutor(context=context)
        toolbox = RepositoryToolbox(repository_root=repository_root, on_event=lambda *_a, **_k: None)

        def _try_read(rel: str) -> bool:
            normalized = str(rel or "").strip().replace("\\", "/")
            if not normalized:
                return False
            if not context.check_read(normalized).should_issue:
                return False
            try:
                result = toolbox.read_file(normalized)
            except (RepositoryToolError, ReadFileError, OSError):
                return False
            try:
                executor.call(
                    "filesystem.read_file",
                    lambda **_: result,
                    arguments={"path": normalized},
                )
            except DuplicateToolCallError:
                return bool(context.files_read)
            return bool(context.files_read)

        for name in ("README.md", "readme.md", "README", "readme.txt"):
            if _try_read(name):
                return

        try:
            listing = toolbox.list_directory(".")
        except (RepositoryToolError, OSError):
            return
        for entry in listing.get("entries") or ():
            if not isinstance(entry, dict):
                continue
            rel = str(entry.get("path") or entry.get("name") or "").replace("\\", "/")
            if not rel or entry.get("is_dir"):
                continue
            if rel.endswith((".py", ".md", ".txt", ".yaml", ".yml", ".json")):
                if _try_read(rel):
                    return

    async def _run_exploration_gate(
        self,
        *,
        cycle: _CycleRecord,
        obj: "_ObjectiveRecord",
    ) -> dict[str, Any] | None:
        """AIX-REAL-EXECUTION-003/004 production flow: admitted objective -> task
        family -> bound resources -> capability requirements -> exploration plan when
        required -> deterministic plan validation -> deterministic understanding gate.

        Returns `None` when the caller may proceed to `run_real_execute` (either no
        exploration was required, or it completed and the gate passed). Returns a
        truthful blocked/clarification-required payload otherwise -- never lets
        `run_real_execute` run for a task family that still requires understanding.
        """
        context = _context_from_dict(obj.objective_id, obj.execution_context)
        target_path = extract_target_path(obj.objective)
        symbol_name = _extract_symbol_name(obj.objective)

        # Same canonical resolver `run_real_execute` uses (repository_location.
        # rebind_repository_for_objective) so exploration and execution can never bind
        # to different repositories for the same objective, and a service-relative
        # path's redundant repository-name prefix is stripped even when
        # `obj.repository_id` was already valid. Persisted onto the objective record
        # so the correction survives resume/clarify and shows up in reporting -- never
        # a silent, per-call guess.
        resolved_repository_id, resolved_target_path = rebind_repository_for_objective(
            obj.repository_id, obj.objective, target_path=target_path, settings=self._settings
        )
        if resolved_repository_id is not None:
            obj.repository_id = resolved_repository_id
            target_path = resolved_target_path or target_path

        registered = is_registered_repository(obj.repository_id, settings=self._settings)
        repository_root: Path | None = None
        if registered:
            if context.execution_root:
                repository_root = Path(context.execution_root)
            else:
                try:
                    repository_root = resolve_repository_root(obj.repository_id, settings=self._settings)
                except RepositoryLocationError:
                    repository_root = None
                if repository_root is not None:
                    context.execution_root = str(repository_root)
                    context.current_workspace = str(repository_root)

        from backend.aix_core.runtime.executor.read_file_execute import (
            _looks_like_a_file,
        )

        # P6: obj.allowed_files is admission metadata, not bound mutation scope.
        # User-named paths come from Intent text via extract_target_path above.
        path_bound = bool(target_path) and (
            _looks_like_a_file(str(target_path))
            or ("/" in str(target_path).replace("\\", "/"))
            or (":" in str(target_path))
        )
        target_bound = registered and path_bound
        # A vague chat objective such as "fix the client" must remain a
        # clarification request. Do this before class/topic heuristics search the
        # repository: otherwise a coincidentally named ``client.py`` silently turns an
        # ambiguous instruction into authorized mutation scope.
        preliminary_target = (
            target_path
            if target_path
            and (
                _looks_like_a_file(str(target_path))
                or "/" in str(target_path).replace("\\", "/")
            )
            else None
        )
        preliminary_plan = build_exploration_plan(
            objective=obj.objective,
            task_family=obj.task_family,
            context=context,
            target_path=preliminary_target,
            symbol_name=symbol_name,
        )
        if preliminary_plan.clarification_required:
            # Admission allowed_files are enough to pass vague-intent clarification;
            # registration only gates whether repository exploration can run later.
            admission_grounded = any(str(p or "").strip() for p in obj.allowed_files)
            if not admission_grounded:
                obj.execution_context = context.to_dict()
                return await self._blocked_exploration_payload(
                    cycle=cycle,
                    code=preliminary_plan.reason_code or "INTENT_UNCLEAR",
                    detail=(
                        preliminary_plan.clarification_question
                        or "clarification required before planning"
                    ),
                )

        # P3: class/topic/route/URL heuristics must not write allowed_files.
        # They may only suggest ExplorationNeed queries (see provenance/explore.py).
        # New markdown/docs CREATE must not walk the default repository (node_modules).
        if looks_like_markdown_doc_objective(obj.objective) or (
            obj.task_class == "docs_only_change"
            and str(obj.task_family or "").upper() == "CREATE"
            and not target_bound
        ):
            obj.execution_context = context.to_dict()
            return None
        if not requires_exploration(obj.task_family, target_bound=target_bound):
            # User-named files skip repository census. Provenance Explore still
            # runs only if ACTIVE MISSING_INFO exists; it does not invent a Need.
            pass
        elif context.understanding_complete:
            return None
        if repository_root is None and self._provenance.latest_understanding(obj.objective_id) is None:
            obj.execution_context = context.to_dict()
            return None
        if repository_root is None:
            # Bound Intent/admission without a resolvable root: cannot Explore.
            if any(str(p or "").strip() for p in obj.allowed_files):
                context.mark_understanding_complete()
                obj.execution_context = context.to_dict()
                return None
            preseeded_no_root = self._understanding_evidence.get(obj.objective_id)
            if preseeded_no_root is not None and preseeded_no_root.understanding_complete:
                # W8/W9-style preseeded UnderstandingEvidence: do not re-block on
                # admit-time MISSING_INFO when the cycle already carries complete evidence.
                context.mark_understanding_complete()
                obj.execution_context = context.to_dict()
                return None
            understanding_no_root = self._provenance.latest_understanding(obj.objective_id)
            if understanding_no_root is not None and any(
                claim.kind == "MISSING_INFO" and claim.status == "ACTIVE"
                for claim in understanding_no_root.claims
            ):
                # Bound absolute/path probes (retry structural) should reach the
                # executor and surface WORKSPACE_NOT_BOUND / REPOSITORY_ROOT_UNKNOWN.
                # Vague empty-admission intents (page-generation, etc.) stay NEED_EVIDENCE.
                if self.real_execute_enabled:
                    intent_text = str(obj.objective or "")
                    if extract_target_path(intent_text):
                        obj.execution_context = context.to_dict()
                        return None
                obj.execution_context = context.to_dict()
                return await self._blocked_exploration_payload(
                    cycle=cycle,
                    code="NEED_EVIDENCE",
                    detail="ACTIVE MISSING_INFO is cleared only by Evidence-grounded Understanding, not by files_read",
                )
            obj.execution_context = context.to_dict()
            return None

        understanding = self._provenance.latest_understanding(obj.objective_id)
        if understanding is not None and any(
            claim.kind == "AMBIGUITY" and claim.status == "ACTIVE" for claim in understanding.claims
        ):
            obj.execution_context = context.to_dict()
            return await self._blocked_exploration_payload(
                cycle=cycle,
                code="NEED_CLARIFICATION",
                detail="ACTIVE scope AMBIGUITY blocks exploration",
            )

        if repository_root is not None and not context.understanding_complete:
            needs_adaptive = requires_exploration(
                obj.task_family, target_bound=target_bound
            ) or (
                str(obj.task_family or "").upper() == "READ"
                and preliminary_target is not None
            )
            if needs_adaptive:
                blocked = await self._run_adaptive_exploration_slice(
                    cycle=cycle,
                    obj=obj,
                    context=context,
                    repository_root=repository_root,
                    target_path=preliminary_target or target_path,
                    symbol_name=symbol_name,
                    target_bound=target_bound,
                )
                if blocked is not None:
                    return blocked
                context = _context_from_dict(obj.objective_id, obj.execution_context)
                # Adaptive reads must become typed ExplorationEvidence so MISSING_INFO
                # can clear (REVIEW/DEBUG/EDIT). Previously only READ / non-exploration
                # families persisted paths, leaving NEED_EVIDENCE after a successful census.
                read_paths = tuple(
                    str(record.path or "").replace("\\", "/")
                    for record in context.files_read
                    if str(record.path or "").strip()
                )
                if read_paths:
                    self._ensure_exploration_evidence_for_paths(obj=obj, paths=read_paths)

        blocked = await self._run_provenance_explore(
            cycle=cycle,
            obj=obj,
            repository_root=repository_root,
            context=context,
        )
        if blocked is not None:
            return blocked

        understanding_before_bootstrap = self._provenance.latest_understanding(obj.objective_id)
        missing_info_blocks = understanding_before_bootstrap is not None and any(
            claim.kind == "MISSING_INFO" and claim.status == "ACTIVE"
            for claim in understanding_before_bootstrap.claims
        )
        admission_paths = {
            str(p or "").strip().replace("\\", "/")
            for p in obj.allowed_files
            if str(p or "").strip()
        }
        provenance_paths = {
            str(item.location or "").replace("\\", "/")
            for item in self._provenance.evidence(obj.objective_id)
        }
        context_paths = {
            str(record.path or "").replace("\\", "/")
            for record in context.files_read
            if str(record.path or "").strip()
        }
        # Ground provenance even when adaptive/bootstrap already filled context.files_read.
        needs_provenance_grounding = bool(
            (admission_paths and admission_paths - provenance_paths)
            or (context_paths and not provenance_paths and missing_info_blocks)
        )
        bootstrap_needed = (
            repository_root is not None
            and (
                (
                    requires_exploration(obj.task_family, target_bound=target_bound)
                    and (
                        not context.files_read
                        or any(
                            str(p).replace("\\", "/") not in {r.path for r in context.files_read}
                            for p in obj.allowed_files
                            if str(p or "").strip()
                        )
                    )
                )
                or (missing_info_blocks and not context.files_read)
                or needs_provenance_grounding
            )
        )
        if bootstrap_needed:
            context_needs_reads = not context.files_read or any(
                str(p).replace("\\", "/") not in {r.path for r in context.files_read}
                for p in obj.allowed_files
                if str(p or "").strip()
            )
            if context_needs_reads:
                und = self._understanding_evidence.get(obj.objective_id)
                has_admission_evidence = bool(admission_paths & provenance_paths)
                if (
                    und is not None
                    and und.files_read
                    and has_admission_evidence
                    and not missing_info_blocks
                ):
                    await self._sync_admission_reads_into_context(
                        cycle=cycle,
                        obj=obj,
                        context=context,
                        repository_root=repository_root,
                    )
                else:
                    await self._bootstrap_repository_grounding_for_planner(
                        cycle=cycle,
                        obj=obj,
                        context=context,
                        repository_root=repository_root,
                    )
                context = _context_from_dict(obj.objective_id, obj.execution_context)
            if needs_provenance_grounding:
                read_paths = tuple(
                    dict.fromkeys(
                        [
                            *(str(p).replace("\\", "/") for p in obj.allowed_files if str(p or "").strip()),
                            *(
                                str(record.path or "").replace("\\", "/")
                                for record in context.files_read
                                if str(record.path or "").strip()
                            ),
                        ]
                    )
                )
                if read_paths:
                    self._ensure_exploration_evidence_for_paths(obj=obj, paths=read_paths)
                    context = _context_from_dict(obj.objective_id, obj.execution_context)

        self._reconcile_understanding_from_exploration_evidence(obj)

        latest = self._provenance.latest_understanding(obj.objective_id)
        preseeded = self._understanding_evidence.get(obj.objective_id)
        if latest is not None and any(
            claim.kind == "MISSING_INFO" and claim.status == "ACTIVE" for claim in latest.claims
        ):
            # Re-derive Understanding from Intent/UserTurns so greenfield CREATE under
            # create_root (user-named, never SCOPE_IN) clears stale MISSING_INFO after
            # the Understanding fix without requiring a new admit. Evidence-grounded
            # SITUATION/CAUSE claims are preserved by reconcile.
            refreshed = self._refresh_understanding_v1(obj)
            if refreshed is not None:
                latest = refreshed.revision
        family_now = str(obj.task_family or "").upper()
        # Repo-bound TEST/VERIFY runs the suite against the workspace; admit-time
        # "which files?" MISSING_INFO is not an execute blocker for that lane.
        test_verify_lane = (
            family_now in {"TEST", "VERIFY"}
            and registered
            and repository_root is not None
        )
        if test_verify_lane:
            context.mark_understanding_complete()

        def _file_like_seed(path: str) -> bool:
            raw = str(path or "").strip().replace("\\", "/")
            if not raw or raw in {"cycle-workspace", ".", ".."}:
                return False
            name = Path(raw).name
            return "/" in raw or ("." in name and not name.startswith("."))

        # Seeded ACCEPTED step targets / admission paths are typed evidence for
        # clearing MISSING_INFO before the hard block (mutation / bound edit).
        if (
            not test_verify_lane
            and latest is not None
            and any(
                claim.kind == "MISSING_INFO" and claim.status == "ACTIVE"
                for claim in latest.claims
            )
            and repository_root is not None
        ):
            seed_paths: list[str] = [
                str(p).replace("\\", "/")
                for p in (obj.allowed_files or ())
                if _file_like_seed(str(p or ""))
            ]
            contract = self._step_contracts.get(obj.objective_id)
            if contract is not None:
                seed_paths.extend(
                    str(p).replace("\\", "/")
                    for p in (contract.target_scope or ())
                    if _file_like_seed(str(p or ""))
                )
            plan_now = self._active_plan_revision(obj.objective_id)
            if plan_now is not None:
                for step in plan_now.steps:
                    seed_paths.extend(
                        str(p).replace("\\", "/")
                        for p in (step.target_scope or ())
                        if _file_like_seed(str(p or ""))
                    )
            seed_paths = list(dict.fromkeys(seed_paths))
            if seed_paths:
                self._ensure_exploration_evidence_for_paths(obj=obj, paths=tuple(seed_paths))
                self._reconcile_understanding_from_exploration_evidence(obj)
                latest = self._provenance.latest_understanding(obj.objective_id)

        if (
            not test_verify_lane
            and not (preseeded is not None and preseeded.understanding_complete)
            and latest is not None
            and any(
                claim.kind == "MISSING_INFO" and claim.status == "ACTIVE"
                for claim in latest.claims
            )
        ):
            obj.execution_context = context.to_dict()
            if registered and repository_root is not None:
                return await self._blocked_exploration_payload(
                    cycle=cycle,
                    code="NEED_EVIDENCE",
                    detail="ACTIVE MISSING_INFO is cleared only by Evidence-grounded Understanding, not by files_read",
                )
        if _understanding_complete(
            task_family=obj.task_family,
            context=context,
            target_path=preliminary_target or target_path,
            symbol_name=symbol_name,
        ):
            context.mark_understanding_complete()
        elif preseeded is not None and preseeded.understanding_complete:
            context.mark_understanding_complete()
        elif not requires_exploration(obj.task_family, target_bound=target_bound):
            context.mark_understanding_complete()
        elif (
            str(obj.task_family or "").upper() == "CREATE"
            and repository_root is not None
            and _repository_root_is_empty(repository_root)
        ):
            # Greenfield CREATE against an empty registered root: exploration
            # is vacuous — there is nothing to census, read, or resolve, so no
            # exploration evidence can ever complete understanding. The concrete
            # deliverable still comes only from the accepted plan scope and
            # must still clear codegen + completion gates + verification; this
            # completes understanding, never scope. Restricted to CREATE (an
            # EDIT/DELETE against an empty root is a genuine
            # NO_APPLICABLE_SOURCE_FILE situation, not vacuity).
            context.mark_understanding_complete()
        elif (
            requires_exploration(obj.task_family, target_bound=target_bound)
            and context.files_read
            and latest is not None
            and not any(
                claim.kind in {"MISSING_INFO", "AMBIGUITY"} and claim.status == "ACTIVE"
                for claim in latest.claims
            )
        ):
            # Mutation families may proceed once repository grounding reads exist
            # and provenance Understanding is clear — pd001 bootstrap path — even
            # when adaptive census did not record directories_listed.
            family = str(obj.task_family or "").upper()
            if family in (MUTATION_FAMILIES | {"DEBUG"}):
                context.mark_understanding_complete()

        obj.execution_context = context.to_dict()
        # Keep the planner-facing UnderstandingEvidence snapshot coherent with the
        # ExecutionContext completeness flag (P5 / W4 bound-target assertions).
        if context.understanding_complete:
            und_snap = self._understanding_evidence.get(obj.objective_id)
            if und_snap is not None and not und_snap.understanding_complete:
                self._understanding_evidence[obj.objective_id] = und_snap.model_copy(
                    update={"understanding_complete": True}
                )
        self._emit_event(
            cycle=cycle,
            event_type="execution_context.updated",
            payload_refs={
                "understanding_complete": context.understanding_complete,
                "files_read": len(context.files_read),
                "searches_executed": len(context.searches_executed),
                "directories_listed": len(context.directories_listed),
                "symbols_resolved": len(context.symbols_resolved),
            },
        )
        gate = gate_execution_planning(
            task_family=obj.task_family,
            context=context,
            target_bound=target_bound,
            clarification_required=False,
        )
        if not gate.allowed:
            return await self._blocked_exploration_payload(
                cycle=cycle,
                code=gate.reason_code or "EXPLORATION_REQUIRED",
                detail=gate.detail,
            )
        return None

    def _terminalization_workspace_for_cycle(self, cycle: _CycleRecord) -> Path | None:
        ws_path = _resolve_cycle_workspace(
            tenant_id=cycle.tenant_id,
            cycle_id=cycle.cycle_id,
            settings=self._settings,
        )
        re = cycle.real_execute if isinstance(cycle.real_execute, dict) else {}
        if ws_path is None and re.get("workspace"):
            ws_path = Path(str(re["workspace"]))
        return ws_path

    def _terminalization_evaluation_for_cycle(
        self,
        cycle: _CycleRecord,
    ) -> TerminalizationEvaluationResult:
        plan = self._active_plan_revision(cycle.objective_id)
        runtime = self._step_runtime.get(cycle.objective_id) or {}
        ovc = self._objective_verification_contracts.get(cycle.objective_id)
        ws_path = self._terminalization_workspace_for_cycle(cycle)
        re = cycle.real_execute if isinstance(cycle.real_execute, dict) else {}
        eval_res = self._terminalization_service.evaluate_guards(
            objective_id=cycle.objective_id,
            cycle_id=cycle.cycle_id,
            version=cycle.version,
            current_phase=str(cycle.engine.state.phase) if cycle.engine is not None else cycle.phase,
            plan=plan,
            step_runtime=runtime,
            ovc=ovc,
            real_execute=re if re else None,
            workspace_path=ws_path,
        )
        self._terminalization_decisions[cycle.cycle_id] = eval_res.terminalization_decision
        self._verification_decisions[cycle.cycle_id] = eval_res.verification_decision
        return eval_res

    def _evaluate_cycle_terminalization(
        self,
        cycle: _CycleRecord,
    ) -> TerminalizationDecisionV1:
        return self._terminalization_evaluation_for_cycle(cycle).terminalization_decision

    def _apply_terminalization_to_engine(
        self,
        cycle: _CycleRecord,
        *,
        step_id: str | None = None,
        evaluation: TerminalizationEvaluationResult | None = None,
    ) -> TerminalizationDecisionV1:
        """Build VerificationResult/Decision + TerminalizationDecision, then let engine write phase."""
        if cycle.engine is None:
            raise RuntimeError("ENGINE_REQUIRED_FOR_TERMINALIZATION")
        eval_res = evaluation or self._terminalization_evaluation_for_cycle(cycle)
        self._terminalization_service.apply_terminalization_to_engine(
            engine=cycle.engine,
            step_id=step_id
            or cycle.execution_claim_step_id
            or cycle.current_step_cursor
            or "step-1",
            evaluation=eval_res,
        )
        self._sync_cycle_phase_from_engine(cycle)
        return eval_res.terminalization_decision

    async def _persist_gap(self, gap: GapV1) -> None:
        """Flush gap to durable JSON and/or Postgres when configured."""
        self._gap_store.put_gap(gap)
        if self._gap_store._pg_conn is not None:
            await self._gap_store.save_pg(gap)

    def _default_model_routes(self, cycle: _CycleRecord) -> dict[str, Any]:
        """Populate canonical role routes from ModelRegistry when caller omits overrides."""
        registry = get_model_registry()
        repair_count = max(
            (g.repair_attempt_count for g in self._gap_store.get_gaps_for_objective(cycle.objective_id)),
            default=0,
        )
        escalated = repair_count >= 2
        routes: dict[str, Any] = {}
        for role in (
            "objective_understanding",
            "planner",
            "executor",
            "bulk_executor",
            "tester",
            "verifier",
            "critical_reviewer",
        ):
            route = registry.get_route(
                role,
                repair_attempt_count=repair_count,
                escalated=escalated,
            )
            routes[role] = {
                "model_id": route.model_id,
                "family": route.family,
                "model_ref": route.model_ref,
                "attempt": route.attempt,
                "fallback_used": route.fallback_used,
            }
        return routes

    def _gap_repair_budget_ok(
        self,
        cycle: _CycleRecord,
        *,
        repair_attempt: int,
    ) -> tuple[bool, str, str]:
        blocking = self._gap_store.get_open_blocking_gaps(cycle.objective_id)
        primary_gap_id = blocking[0].gap_id if blocking else None
        allowed, verdict, reason = self._cost_ledger.has_budget_for_repair(
            objective_id=cycle.objective_id,
            repair_attempt_count=repair_attempt,
            gap_id=primary_gap_id,
        )
        return allowed, verdict, reason

    async def _apply_gap_repair_plan(
        self,
        cycle: _CycleRecord,
        *,
        repair_result: Any,
        repair_attempt: int,
    ) -> dict[str, Any] | None:
        """Admit bounded repair plan and mark cycle dispatch-ready. Returns error payload or None."""
        if repair_result.status != "REPAIR_PLANNED":
            return {
                "ok": False,
                "repair_status": repair_result.status,
                "error": {
                    "code": repair_result.status,
                    "detail": repair_result.escalation_reason or "Repair not planned",
                },
            }

        for gap_id in repair_result.affected_gaps:
            gap = self._gap_store.get_gap(gap_id)
            if gap is not None:
                await self._persist_gap(gap)

        plan = self._active_plan_revision(cycle.objective_id)
        existing_steps = plan.steps if plan is not None else ()
        failed_step_id = existing_steps[-1].id if existing_steps else None
        replan_result = perform_incremental_replan(
            failed_step_id=failed_step_id,
            existing_steps=existing_steps,
            replacement_steps=repair_result.repair_steps,
        )
        active_steps = tuple(
            s for s in replan_result.updated_steps if str(s.status) != "FAILED"
        )
        if not active_steps:
            active_steps = repair_result.repair_steps
        new_plan = PlanRevisionV1(
            objective_id=cycle.objective_id,
            cycle_id=cycle.cycle_id,
            revision=(plan.revision + 1) if plan is not None else 1,
            created_from_plan_revision=plan.plan_id if plan is not None else None,
            steps=active_steps,
            planner_reasoning_ref="gap_repair_incremental_replan",
            status="ACCEPTED",
        )
        self._admit_plan_revision(new_plan)
        runtime = init_step_runtime(new_plan)
        for step in replan_result.updated_steps:
            if str(step.status) == "VERIFIED" and step.id in runtime:
                runtime[step.id] = replace_runtime_status(runtime[step.id], status="PASS")
        self._step_runtime[cycle.objective_id] = runtime
        repair_step_id = repair_result.repair_steps[0].id if repair_result.repair_steps else None
        if repair_step_id:
            cycle.current_step_cursor = repair_step_id

        cycle.plan_revision = new_plan.revision
        cycle.current_gate_id = f"gate_{cycle.cycle_id}_repair_a{repair_attempt}"
        cycle.approved_gate_id = cycle.current_gate_id
        cycle.approved_plan_revision = new_plan.revision
        obj = self._objectives.get(cycle.objective_id)
        if obj is not None:
            cycle.approved_operations = (obj.task_family,)
            cycle.approved_resources = (obj.repository_id,)
        if repair_result.repair_steps:
            self._step_contracts[cycle.objective_id] = repair_result.repair_steps[0]

        if cycle.engine is not None:
            if not cycle.engine.begin_gap_repair(reason="AUTONOMOUS_GAP_REPAIR"):
                return {
                    "ok": False,
                    "error": {"code": "ENGINE_GAP_REPAIR_REFUSED"},
                }

        cycle.real_execute = None
        cycle.dispatch_state = "STEP_READY"
        cycle.dispatch_required = True
        cycle.version += 1
        self._sync_cycle_phase_from_engine(cycle)
        self._emit_event(
            cycle=cycle,
            event_type="cycle.gap_repair_planned",
            payload_refs={
                "repair_step_ids": [s.id for s in repair_result.repair_steps],
                "affected_gaps": list(repair_result.affected_gaps),
                "repair_attempt": repair_attempt,
            },
        )
        return None

    async def _handle_gaps_found(
        self,
        cycle: _CycleRecord,
        *,
        tenant_id: str,
        actor_id: str,
        command_id: str,
        body: dict[str, Any],
        auto_dispatch: bool = True,
        auto_reverify: bool = True,
        operation: str = "repair_cycle",
    ) -> dict[str, Any]:
        """Orchestrate gap report → bounded replan → dispatch → reverify → terminalize."""
        self._sync_cycle_phase_from_engine(cycle)
        blocking_gaps = self._gap_store.get_open_blocking_gaps(cycle.objective_id)
        if not blocking_gaps:
            return {
                "ok": False,
                "operation": operation,
                "tenant_id": tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "error": {"code": "NO_OPEN_GAPS"},
            }

        gap_report = GapStore.build_report(cycle.objective_id, blocking_gaps)
        repair_attempt = int(
            body.get("repair_attempt")
            or max((g.repair_attempt_count for g in blocking_gaps), default=0) + 1
        )
        budget_ok, budget_verdict, budget_reason = self._gap_repair_budget_ok(
            cycle, repair_attempt=repair_attempt
        )
        if not budget_ok:
            cycle.terminal_reason = budget_verdict
            cycle.version += 1
            return self._control_payload_with_synced_phase(
                cycle,
                {
                    "ok": False,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "version": cycle.version,
                    "budget_verdict": budget_verdict,
                    "gap_report_count": len(gap_report),
                    "error": {"code": budget_verdict, "detail": budget_reason},
                },
            )

        plan = self._active_plan_revision(cycle.objective_id)
        existing_steps = plan.steps if plan is not None else ()
        repair_result = self._repair_coordinator.plan_repair_for_open_gaps(
            cycle_id=cycle.cycle_id,
            tenant_id=tenant_id,
            objective_id=cycle.objective_id,
            gap_store=self._gap_store,
            existing_steps=existing_steps,
            current_attempt=repair_attempt,
        )
        if repair_result.status == "ESCALATED":
            for gap_id in repair_result.affected_gaps:
                gap = self._gap_store.get_gap(gap_id)
                if gap is not None:
                    await self._persist_gap(gap)
            routes = self._default_model_routes(cycle)
            cycle.version += 1
            return self._control_payload_with_synced_phase(
                cycle,
                {
                    "ok": False,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "version": cycle.version,
                    "repair_status": repair_result.status,
                    "escalation_reason": repair_result.escalation_reason,
                    "critical_reviewer_route": routes.get("critical_reviewer"),
                    "error": {
                        "code": "ESCALATED",
                        "detail": repair_result.escalation_reason,
                    },
                },
            )

        plan_err = await self._apply_gap_repair_plan(
            cycle,
            repair_result=repair_result,
            repair_attempt=repair_attempt,
        )
        if plan_err is not None:
            return self._control_payload_with_synced_phase(
                cycle,
                {
                    **plan_err,
                    "operation": operation,
                    "tenant_id": tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "version": cycle.version,
                },
            )

        obj = self._objectives.get(cycle.objective_id)
        if obj is not None:
            self._ensure_ovc_for_objective(obj)
        self._ensure_workspace_for_cycle(cycle)

        result: dict[str, Any] = {
            "ok": True,
            "operation": operation,
            "tenant_id": tenant_id,
            "cycle_id": cycle.cycle_id,
            "phase": cycle.phase,
            "version": cycle.version,
            "repair_status": repair_result.status,
            "repair_step_ids": [s.id for s in repair_result.repair_steps],
            "affected_gaps": list(repair_result.affected_gaps),
            "gap_report_count": len(gap_report),
            "dispatch_state": cycle.dispatch_state,
            "dispatch_required": cycle.dispatch_required,
            "workhub_lifecycle_authority": False,
            "aix_creates_workhub_tasks": False,
        }

        if auto_dispatch and cycle.dispatch_required:
            dispatch_res = await self.dispatch_ready_step(
                tenant_id=tenant_id,
                cycle_id=cycle.cycle_id,
                command_id=f"{command_id}-dispatch",
                caller_auth=str(body.get("caller_authorization") or "").strip() or None,
                actor_id=actor_id,
            )
            result["dispatch"] = dispatch_res
            if isinstance(dispatch_res, dict) and not dispatch_res.get("ok", True):
                result["ok"] = False
                return self._control_payload_with_synced_phase(cycle, result)
            cycle.version = int(dispatch_res.get("version") or cycle.version)
            # Do not reverify inline while repair execute is still async in background.
            if isinstance(dispatch_res, dict) and (
                dispatch_res.get("async_execution")
                or dispatch_res.get("dispatch_state") == "EXECUTING"
            ):
                auto_reverify = False

        if auto_reverify and result.get("ok", False):
            reverify_res = await self._reverify_and_terminalize(
                tenant_id=tenant_id,
                cycle_id=cycle.cycle_id,
                expected_version=cycle.version,
                body=body,
                command_id=f"{command_id}-reverify",
                actor_id=actor_id,
            )
            result["reverify"] = reverify_res
            result["ok"] = bool(reverify_res.get("ok"))
            result["phase"] = cycle.phase
            result["version"] = cycle.version
            if reverify_res.get("terminalization_decision"):
                result["terminalization_decision"] = reverify_res["terminalization_decision"]
            return self._control_payload_with_synced_phase(cycle, result)

        return self._control_payload_with_synced_phase(cycle, result)

    async def _replan_from_gaps(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        expected_version: int | None,
        body: dict[str, Any],
        command_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Gap → planner feedback + bounded replan without dispatch."""
        cycle = self._cycles.get(cycle_id)
        if cycle is None or cycle.tenant_id != tenant_id:
            return {
                "ok": False,
                "operation": "replan_from_gaps",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "CYCLE_NOT_FOUND"},
            }
        if expected_version is not None and cycle.version != expected_version:
            return {
                "ok": False,
                "operation": "replan_from_gaps",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "VERSION_CONFLICT", "expected": expected_version, "actual": cycle.version},
            }
        return await self._handle_gaps_found(
            cycle,
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=command_id,
            body=body,
            auto_dispatch=False,
            auto_reverify=False,
            operation="replan_from_gaps",
        )

    async def _repair_cycle(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        expected_version: int | None,
        body: dict[str, Any],
        command_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Full autonomous repair loop via backend.command()."""
        cycle = self._cycles.get(cycle_id)
        if cycle is None or cycle.tenant_id != tenant_id:
            return {
                "ok": False,
                "operation": "repair_cycle",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "CYCLE_NOT_FOUND"},
            }
        if expected_version is not None and cycle.version != expected_version:
            return {
                "ok": False,
                "operation": "repair_cycle",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "VERSION_CONFLICT", "expected": expected_version, "actual": cycle.version},
            }
        self._sync_cycle_phase_from_engine(cycle)
        if not self._cycle_eligible_for_gap_repair(cycle):
            return {
                "ok": False,
                "operation": "repair_cycle",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "error": {"code": "NOT_IN_GAPS_FOUND"},
            }
        return await self._handle_gaps_found(
            cycle,
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=command_id,
            body=body,
            auto_dispatch=bool(body.get("auto_dispatch", True)),
            auto_reverify=bool(body.get("auto_reverify", True)),
            operation="repair_cycle",
        )

    async def _maybe_autonomous_repair_after_terminalization(
        self,
        cycle: _CycleRecord,
        term_dec: TerminalizationDecisionV1,
        *,
        tenant_id: str,
        actor_id: str,
        command_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """When terminalization denies certification, enter live repair path (not COMPLETED_FOR_REVIEW)."""
        if not self._autonomous_repair_enabled:
            return None
        self._sync_cycle_phase_from_engine(cycle)
        if term_dec.decision != "DENY_NEEDS_REPAIR" and cycle.phase != "GAPS_FOUND":
            return None
        return await self._handle_gaps_found(
            cycle,
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=command_id or f"auto-repair-{cycle.cycle_id}",
            body=body or {},
            auto_dispatch=bool((body or {}).get("auto_dispatch", True)),
            auto_reverify=bool((body or {}).get("auto_reverify", False)),
            operation="repair_cycle",
        )

    async def _initiate_gap_repair(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        expected_version: int | None,
        body: dict[str, Any],
        command_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Plan bounded repair steps for open gaps and re-enter execution via engine."""
        cycle = self._cycles.get(cycle_id)
        if cycle is None or cycle.tenant_id != tenant_id:
            return {
                "ok": False,
                "operation": "initiate_gap_repair",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "CYCLE_NOT_FOUND"},
            }
        if expected_version is not None and cycle.version != expected_version:
            return {
                "ok": False,
                "operation": "initiate_gap_repair",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "VERSION_CONFLICT", "expected": expected_version, "actual": cycle.version},
            }
        self._sync_cycle_phase_from_engine(cycle)
        if not self._cycle_eligible_for_gap_repair(cycle):
            return {
                "ok": False,
                "operation": "initiate_gap_repair",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "error": {"code": "NOT_IN_GAPS_FOUND"},
            }

        plan = self._active_plan_revision(cycle.objective_id)
        existing_steps = plan.steps if plan is not None else ()
        repair_attempt = int(body.get("repair_attempt") or 1)
        budget_ok, budget_verdict, budget_reason = self._gap_repair_budget_ok(
            cycle, repair_attempt=repair_attempt
        )
        if not budget_ok:
            return {
                "ok": False,
                "operation": "initiate_gap_repair",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "budget_verdict": budget_verdict,
                "error": {"code": budget_verdict, "detail": budget_reason},
            }

        repair_result = self._repair_coordinator.plan_repair_for_open_gaps(
            cycle_id=cycle_id,
            tenant_id=tenant_id,
            objective_id=cycle.objective_id,
            gap_store=self._gap_store,
            existing_steps=existing_steps,
            current_attempt=repair_attempt,
        )
        if repair_result.status != "REPAIR_PLANNED":
            return {
                "ok": False,
                "operation": "initiate_gap_repair",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "repair_status": repair_result.status,
                "error": {
                    "code": repair_result.status,
                    "detail": repair_result.escalation_reason or "Repair not planned",
                },
            }

        plan_err = await self._apply_gap_repair_plan(
            cycle,
            repair_result=repair_result,
            repair_attempt=repair_attempt,
        )
        if plan_err is not None:
            return self._control_payload_with_synced_phase(
                cycle,
                {
                    **plan_err,
                    "operation": "initiate_gap_repair",
                    "tenant_id": tenant_id,
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "version": cycle.version,
                },
            )

        return self._control_payload_with_synced_phase(
            cycle,
            {
                "ok": True,
                "operation": "initiate_gap_repair",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "repair_status": repair_result.status,
                "repair_step_ids": [s.id for s in repair_result.repair_steps],
                "affected_gaps": list(repair_result.affected_gaps),
                "dispatch_state": cycle.dispatch_state,
                "dispatch_required": cycle.dispatch_required,
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            },
        )

    async def _reverify_and_terminalize(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        expected_version: int | None,
        body: dict[str, Any],
        command_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Re-verify workspace after repair, close gaps, and terminalize via guard."""
        cycle = self._cycles.get(cycle_id)
        if cycle is None or cycle.tenant_id != tenant_id:
            # Worker-process activities carry no memory: rehydrate from durable
            # projections like dispatch does, or verify can never run on the
            # Temporal worker's backend instance (PLAT-D2 single ownership).
            loaded = await self.rehydrate_cycle(tenant_id=tenant_id, cycle_id=cycle_id)
            cycle = self._cycles.get(cycle_id) if loaded else None
        if cycle is None or cycle.tenant_id != tenant_id:
            return {
                "ok": False,
                "operation": "reverify_and_terminalize",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "CYCLE_NOT_FOUND"},
            }
        if expected_version is not None and cycle.version != expected_version:
            return {
                "ok": False,
                "operation": "reverify_and_terminalize",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "VERSION_CONFLICT", "expected": expected_version, "actual": cycle.version},
            }
        if cycle.engine is None:
            return {
                "ok": False,
                "operation": "reverify_and_terminalize",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "ENGINE_REQUIRED"},
            }

        ovc = self._objective_verification_contracts.get(cycle.objective_id)
        if ovc is None:
            obj = self._objectives.get(cycle.objective_id)
            if obj is not None:
                self._ensure_ovc_for_objective(obj)
                ovc = self._objective_verification_contracts.get(cycle.objective_id)
        if ovc is None:
            return {
                "ok": False,
                "operation": "reverify_and_terminalize",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "OVC_NOT_FOUND"},
            }

        ws_path = _resolve_cycle_workspace(
            tenant_id=cycle.tenant_id,
            cycle_id=cycle.cycle_id,
            settings=self._settings,
        )
        re = cycle.real_execute if isinstance(cycle.real_execute, dict) else {}
        if ws_path is None and re.get("workspace"):
            ws_path = Path(str(re["workspace"]))
        if ws_path is None or not ws_path.is_dir():
            ws_path = self._ensure_workspace_for_cycle(cycle)
        if ws_path is None or not ws_path.is_dir():
            ws_path = await self._rehydrate_cycle_workspace(cycle_id)
        if ws_path is None:
            return {
                "ok": False,
                "operation": "reverify_and_terminalize",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "WORKSPACE_NOT_FOUND"},
            }

        obj_outcome = self._verification_service.verify_objective_at_workspace(
            ovc=ovc,
            workspace_path=ws_path,
            gap_store=self._gap_store,
        )
        v_res = obj_outcome.verification_result
        _v_dec = obj_outcome.verification_decision
        new_gaps = obj_outcome.new_gaps
        reverified_ids = {r.ov_id for r in v_res.ov_item_results if r.status == "PASS"}
        closed, remaining = self._repair_coordinator.evaluate_reverification(
            verification_id=v_res.verification_id,
            gap_store=self._gap_store,
            objective_id=cycle.objective_id,
            reverified_ov_ids=reverified_ids,
        )
        for gap in closed:
            await self._persist_gap(gap)
        for gap in new_gaps:
            await self._persist_gap(gap)

        if self._gap_store.count_blocking_gaps(cycle.objective_id) == 0:
            plan = self._active_plan_revision(cycle.objective_id)
            if plan is not None:
                runtime = dict(
                    self._step_runtime.get(cycle.objective_id) or init_step_runtime(plan)
                )
                for step in plan.steps:
                    st = runtime.get(step.id)
                    if st is not None and str(st.status) != "PASS":
                        runtime[step.id] = replace_runtime_status(
                            st, status="PASS", verification_verdict="PASS"
                        )
                self._step_runtime[cycle.objective_id] = runtime
            cycle.dispatch_state = "STEP_PASSED"
            cycle.dispatch_required = False
            cycle.execution_claim_id = None
            cycle.execution_claim_step_id = None
            cycle.execution_claim_attempt = 0
            cycle.terminal_reason = None

        if self._gap_store.count_blocking_gaps(cycle.objective_id) > 0:
            blocking_count = self._gap_store.count_blocking_gaps(cycle.objective_id)
            if cycle.engine is not None and str(cycle.engine.state.phase) != "GAPS_FOUND":
                if cycle.engine.force_running_step_for_mid_plan(reason="REVERIFY_GAPS_FOUND"):
                    eval_res = self._terminalization_service.evaluate_from_reverify_outcome(
                        verification_decision=_v_dec,
                        runtime_proof_status=v_res.runtime_proof_status,
                        blocking_gaps=blocking_count,
                        current_phase=str(cycle.engine.state.phase),
                    )
                    self._terminalization_service.apply_terminalization_to_engine(
                        engine=cycle.engine,
                        step_id=cycle.execution_claim_step_id or "step-1",
                        evaluation=eval_res,
                    )
                    term_dec = eval_res.terminalization_decision
                    if str(cycle.engine.state.phase) == "GAPS_FOUND":
                        cycle.dispatch_state = "IDLE"
                        cycle.dispatch_required = False
                        cycle.execution_claim_id = None
                        cycle.execution_claim_step_id = None
                        cycle.execution_claim_attempt = 0
                        cycle.terminal_reason = None
                    self._sync_cycle_phase_from_engine(cycle)
                    self._terminalization_decisions[cycle.cycle_id] = term_dec
                    cycle.version += 1
            return self._control_payload_with_synced_phase(
                cycle,
                {
                    "ok": False,
                    "operation": "reverify_and_terminalize",
                    "tenant_id": tenant_id,
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "runtime_proof_status": v_res.runtime_proof_status,
                    "open_gaps_count": blocking_count,
                    "terminalization_decision": self._terminalization_decisions.get(cycle.cycle_id).decision
                    if self._terminalization_decisions.get(cycle.cycle_id)
                    else None,
                    "error": {"code": "GAPS_REMAIN", "remaining": [g.gap_id for g in remaining]},
                },
            )

        if not cycle.engine.force_running_step_for_mid_plan(reason="POST_REPAIR_REVERIFICATION"):
            return {
                "ok": False,
                "operation": "reverify_and_terminalize",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "error": {"code": "ENGINE_REOPEN_REFUSED", "phase": cycle.engine.state.phase},
            }

        eval_res = self._terminalization_service.evaluate_from_reverify_outcome(
            verification_decision=_v_dec,
            runtime_proof_status=v_res.runtime_proof_status,
            blocking_gaps=0,
            current_phase=str(cycle.engine.state.phase),
        )
        self._terminalization_service.apply_terminalization_to_engine(
            engine=cycle.engine,
            step_id=cycle.execution_claim_step_id or "step-1",
            evaluation=eval_res,
        )
        term_dec = eval_res.terminalization_decision
        self._sync_cycle_phase_from_engine(cycle)
        self._terminalization_decisions[cycle.cycle_id] = term_dec
        self._verification_decisions[cycle.cycle_id] = _v_dec
        cycle.version += 1
        await self._maybe_record_cycle_outcome(cycle=cycle)
        return self._control_payload_with_synced_phase(
            cycle,
            {
                "ok": True,
                "operation": "reverify_and_terminalize",
                "tenant_id": tenant_id,
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "runtime_proof_status": v_res.runtime_proof_status,
                "terminalization_decision": term_dec.decision,
                "closed_gaps": [g.gap_id for g in closed],
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            },
        )

    async def _try_terminalize_all_steps_passed(
        self,
        *,
        cycle: _CycleRecord,
    ) -> dict[str, Any] | None:
        """When every ACCEPTED plan step is PASS and no next step remains, land
        ``COMPLETED_FOR_REVIEW`` even if the engine was parked in WAITING_APPROVAL.

        Live ERP hang: final step returned ok:True while forcing REPLAN → engine
        WAITING_APPROVAL; finalize stamped STEP_PASSED without calling
        ``continue_after_approval`` (requires RUNNING_STEP); resume then returned
        ok via ``approval_already_applied`` forever. This is the missing terminal
        handoff — not a second approval gate.
        """
        if cycle.engine is None:
            return None
        eng_phase = str(cycle.engine.state.phase)
        if eng_phase in {"COMPLETED_FOR_REVIEW", "CERTIFIED", "GAPS_FOUND", "FAILED", "CANCELLED", "REJECTED"}:
            return None
        plan = self._active_plan_revision(cycle.objective_id)
        if plan is None or str(plan.status) != "ACCEPTED":
            return None
        runtime = self._step_runtime.get(cycle.objective_id) or {}
        if not runtime or not all_required_steps_passed(plan, runtime):
            return None
        if select_next_step(plan, runtime) is not None:
            return None
        re = cycle.real_execute if isinstance(cycle.real_execute, dict) else {}
        obj_status = str(re.get("objective_status") or "").upper()
        if obj_status in {"BLOCKED", "FAILED"}:
            return None
        if re.get("ok") is False:
            return None
        if eng_phase != "RUNNING_STEP":
            if not cycle.engine.force_running_step_for_mid_plan(
                reason="ALL_STEPS_PASSED_TERMINAL"
            ):
                return None
        term_decision = self._apply_terminalization_to_engine(cycle)
        cycle.dispatch_state = "STEP_PASSED"
        cycle.dispatch_required = False
        cycle.step_lease_expires_at = None
        cycle.last_progress_at = _utc()
        await self._maybe_record_cycle_outcome(cycle=cycle)
        cycle.version += 1
        await self._project_run(cycle)
        self._emit_event(
            cycle=cycle,
            event_type="cycle.plan_complete_terminal",
            payload_refs={
                "phase": cycle.phase,
                "dispatch_state": cycle.dispatch_state,
                "reason": "all_required_steps_passed",
                "terminalization_decision": term_decision.decision,
            },
        )
        return {
            "ok": True,
            "operation": "plan_complete_terminal",
            "tenant_id": cycle.tenant_id,
            "cycle_id": cycle.cycle_id,
            "phase": cycle.phase,
            "version": cycle.version,
            "dispatch_state": cycle.dispatch_state,
            "dispatch_required": False,
            "terminal_reason": cycle.terminal_reason,
            "status": cycle.engine.query_status() if cycle.engine is not None else {},
            "workhub_lifecycle_authority": False,
            "aix_creates_workhub_tasks": False,
        }

    async def _maybe_advance_mid_plan_dispatch(
        self,
        *,
        cycle: _CycleRecord,
        command_id: str,
    ) -> dict[str, Any] | None:
        """Advance a multi-step plan stuck after STEP_PASSED without NEXT_STEP_READY.

        Happens when a prior trailing-steps skip or finalize left PENDING plan steps
        while the cycle sat in WAITING_APPROVAL / STEP_PASSED (CLI approval spam).

        Never promote RUNNING steps and never interrupt an in-flight claim — that
        falsely PASSed ERP steps 2–5 without writing files.
        """
        if cycle.dispatch_required and cycle.dispatch_state in {
            "STEP_READY",
            "NEXT_STEP_READY",
            "EXECUTING",
        }:
            return None
        if cycle.dispatch_state in {"EXECUTING", "NEXT_STEP_READY", "STEP_READY"}:
            return None
        if cycle.execution_claim_id and cycle.dispatch_state not in {
            "STEP_PASSED",
            "STEP_FAILED",
            "IDLE",
            "VERIFYING",
        }:
            return None
        plan = self._active_plan_revision(cycle.objective_id)
        if plan is None or str(plan.status) != "ACCEPTED":
            return None

        runtime = dict(self._step_runtime.get(cycle.objective_id) or {})
        if not runtime:
            runtime = dict(init_step_runtime(plan))
            self._step_runtime[cycle.objective_id] = runtime

        # Zombie: dispatch finished (STEP_PASSED/STEP_FAILED) or replan parked IDLE,
        # but a step is still RUNNING. A hung background execute task can look "live"
        # forever and block heal — cancel and retry the step.
        if cycle.dispatch_state in {"STEP_PASSED", "STEP_FAILED", "IDLE"}:
            exec_task = self._execute_tasks.pop(cycle.cycle_id, None)
            if exec_task is not None and not exec_task.done():
                exec_task.cancel()
                logger.warning(
                    "mid_plan_dispatch_recovery: cancelled hung execute task cycle=%s",
                    cycle.cycle_id,
                )
            changed = {
                str(p).replace("\\", "/")
                for p in (
                    list((cycle.real_execute or {}).get("changed_source_files") or [])
                    + [
                        str(e.get("path") or e.get("file") or "")
                        for e in (getattr(cycle, "mutation_evidence_history", None) or [])
                        if isinstance(e, dict)
                    ]
                )
                if str(p).strip()
            }
            for step in sorted(plan.steps, key=lambda s: s.step_index):
                st = runtime.get(step.id)
                if st is None or st.status != "RUNNING":
                    continue
                targets = {str(t).replace("\\", "/") for t in (step.target_scope or ())}
                # Only count mutations that match THIS step's targets (not prior steps).
                # Never invent PASS while dispatch is parked IDLE — stale evidence from
                # a failed OBJECTIVE_COVERAGE attempt must not block re-arm → STEP_READY.
                if (
                    cycle.dispatch_state != "IDLE"
                    and targets
                    and (targets & changed)
                ):
                    runtime[step.id] = replace_runtime_status(
                        st, status="PASS", verification_verdict="PASS"
                    )
                    self._step_runtime[cycle.objective_id] = runtime
                    continue
                runtime[step.id] = replace_runtime_status(st, status="READY")
                self._step_runtime[cycle.objective_id] = runtime
                self._step_contracts[cycle.objective_id] = step
                cycle.execution_claim_id = None
                cycle.execution_claim_step_id = None
                cycle.execution_claim_attempt = 0
                cycle.execution_claim_expired = False
                if cycle.engine is not None:
                    cycle.engine.force_running_step_for_mid_plan(
                        reason="zombie_running_retry"
                    )
                logger.warning(
                    "mid_plan_dispatch_recovery: zombie RUNNING reset cycle=%s step=%s",
                    cycle.cycle_id,
                    step.id,
                )
                return await self._mark_dispatch_ready(
                    cycle=cycle,
                    state="NEXT_STEP_READY",
                    command_id=command_id,
                    step=step,
                    previous_step_id=cycle.previous_step_id,
                    reason="zombie_running_retry",
                )

        if any(st.status == "RUNNING" for st in runtime.values()):
            # Live execute in progress — do not recover/promote. IDLE dispatch means
            # no worker owns the RUNNING row; allow heal paths below.
            if cycle.dispatch_state != "IDLE":
                return None
        if all_required_steps_passed(plan, runtime):
            # Plan work is done — terminalize instead of silent None (ERP hang:
            # STEP_PASSED + WAITING_APPROVAL with resume ok:True forever).
            terminalized = await self._try_terminalize_all_steps_passed(cycle=cycle)
            if terminalized is not None:
                return terminalized
            # Stale PASS (e.g. zombie mutation promotion) with dispatch parked must
            # not block STEP_READY forever when terminalization correctly refused.
            if cycle.dispatch_state in {"IDLE", "STEP_FAILED"}:
                for step in reversed(sorted(plan.steps, key=lambda s: s.step_index)):
                    st = runtime.get(step.id)
                    if st is None or st.status != "PASS":
                        continue
                    runtime[step.id] = replace_runtime_status(
                        st, status="READY", verification_verdict=None
                    )
                    self._step_runtime[cycle.objective_id] = runtime
                    self._step_contracts[cycle.objective_id] = step
                    cycle.real_execute = None
                    logger.warning(
                        "mid_plan_dispatch_recovery: stale PASS->READY cycle=%s step=%s",
                        cycle.cycle_id,
                        step.id,
                    )
                    break

        # FAILED is excluded from select_next_step forever. A STEP_FAILED finalize
        # (gates / false VERIFICATION_HALT) must re-arm the step so a fixed verifier
        # or presence check can retry (ERP step 2 after PHP/SQL already on disk).
        # After replan/approval the durable snapshot may park dispatch=IDLE while the
        # step runtime still carries FAILED — without re-arm, observe/select_next_step
        # returns None and approve/resume plan_prep never reaches STEP_READY.
        has_failed_step = any(
            (runtime.get(s.id) is not None and runtime[s.id].status == "FAILED")
            for s in plan.steps
        )
        if has_failed_step and cycle.dispatch_state in {"STEP_FAILED", "IDLE"}:
            rearmed = False
            for step in sorted(plan.steps, key=lambda s: s.step_index):
                st = runtime.get(step.id)
                if st is None or st.status != "FAILED":
                    continue
                runtime[step.id] = replace_runtime_status(
                    st, status="READY", verification_verdict=None
                )
                self._step_runtime[cycle.objective_id] = runtime
                self._step_contracts[cycle.objective_id] = step
                rearmed = True
                logger.warning(
                    "mid_plan_dispatch_recovery: FAILED->READY cycle=%s step=%s reason=%s",
                    cycle.cycle_id,
                    step.id,
                    cycle.terminal_reason,
                )
                break
            if rearmed:
                cycle.execution_claim_id = None
                cycle.execution_claim_step_id = None
                cycle.execution_claim_attempt = 0
                cycle.execution_claim_expired = False
                cycle.terminal_reason = None
                cycle.last_error_detail = None
                if cycle.engine is not None:
                    cycle.engine.force_running_step_for_mid_plan(
                        reason="failed_step_retry"
                    )

        # VERIFYING with dispatch parked IDLE is always stale (no verifier owns it).
        if cycle.dispatch_state == "IDLE":
            rearmed_verify = False
            for step in sorted(plan.steps, key=lambda s: s.step_index):
                st = runtime.get(step.id)
                if st is None or st.status != "VERIFYING":
                    continue
                runtime[step.id] = replace_runtime_status(
                    st, status="READY", verification_verdict=None
                )
                self._step_runtime[cycle.objective_id] = runtime
                self._step_contracts[cycle.objective_id] = step
                rearmed_verify = True
                logger.warning(
                    "mid_plan_dispatch_recovery: VERIFYING->READY cycle=%s step=%s",
                    cycle.cycle_id,
                    step.id,
                )
                break
            if rearmed_verify:
                cycle.execution_claim_id = None
                cycle.execution_claim_step_id = None
                cycle.execution_claim_attempt = 0
                cycle.execution_claim_expired = False
                cycle.terminal_reason = None
                cycle.last_error_detail = None
                if cycle.engine is not None:
                    cycle.engine.force_running_step_for_mid_plan(
                        reason="stale_verifying_retry"
                    )

        re = cycle.real_execute if isinstance(cycle.real_execute, dict) else {}
        # Only heal VERIFYING after a completed step (STEP_PASSED), not while
        # inventing PASS from older mutation evidence.
        can_heal_verifying = cycle.dispatch_state in {"STEP_PASSED", "VERIFYING"} and bool(
            re.get("ok")
            or re.get("verification_status") == "PASSED"
            or bool(re.get("changed_source_files"))
            or bool(getattr(cycle, "mutation_evidence_history", None))
            or re.get("objective_status")
            in {"COMPLETED", "COMPLETED_FOR_REVIEW", "AWAITING_REVIEW", "COMPLETED_WITH_WARNINGS"}
            or cycle.dispatch_state == "STEP_PASSED"
        )
        if can_heal_verifying:
            cursor = str(
                cycle.current_step_cursor or cycle.execution_claim_step_id or ""
            ).strip()
            if not cursor and plan.steps:
                for step in sorted(plan.steps, key=lambda s: s.step_index):
                    st = runtime.get(step.id)
                    if st is not None and st.status == "VERIFYING":
                        cursor = step.id
                        break
                if not cursor:
                    cursor = plan.steps[0].id
            # Promote only VERIFYING (never RUNNING) for the active cursor.
            if cursor:
                st = runtime.get(cursor)
                if st is not None and st.status == "VERIFYING":
                    runtime[cursor] = replace_runtime_status(
                        st,
                        status="PASS",
                        verification_verdict="PASS",
                    )
                    self._step_runtime[cycle.objective_id] = runtime

        if cycle.dispatch_state == "IDLE" and select_next_step(plan, runtime) is None:
            exec_task = self._execute_tasks.get(cycle.cycle_id)
            if exec_task is None or exec_task.done():
                for step in sorted(plan.steps, key=lambda s: s.step_index):
                    st = runtime.get(step.id)
                    if st is None or st.status in {"PENDING", "READY"}:
                        continue
                    runtime[step.id] = replace_runtime_status(
                        st, status="READY", verification_verdict=None
                    )
                    self._step_runtime[cycle.objective_id] = runtime
                    self._step_contracts[cycle.objective_id] = step
                    cycle.execution_claim_id = None
                    cycle.execution_claim_step_id = None
                    cycle.real_execute = None
                    if cycle.engine is not None:
                        cycle.engine.force_running_step_for_mid_plan(
                            reason="idle_stale_step_rearm"
                        )
                    logger.warning(
                        "mid_plan_dispatch_recovery: idle stale %s->READY cycle=%s step=%s",
                        st.status,
                        cycle.cycle_id,
                        step.id,
                    )
                    break

        nxt = self._select_next_step_governed(plan, runtime)
        if nxt is None:
            logger.info(
                "mid_plan_dispatch_recovery: no next step cycle=%s dispatch=%s "
                "runtime=%s",
                cycle.cycle_id,
                cycle.dispatch_state,
                {sid: st.status for sid, st in runtime.items()},
            )
            return None
        previous = cycle.current_step_cursor
        self._step_contracts[cycle.objective_id] = nxt
        _remember_mutation_evidence(cycle)
        self._emit_event(
            cycle=cycle,
            event_type="cycle.mid_plan_dispatch_recovery",
            payload_refs={
                "from_step_id": previous,
                "to_step_id": nxt.id,
                "prior_dispatch_state": cycle.dispatch_state,
            },
        )
        logger.info(
            "mid_plan_dispatch_recovery: advancing cycle=%s %s -> %s",
            cycle.cycle_id,
            previous,
            nxt.id,
        )
        return await self._mark_dispatch_ready(
            cycle=cycle,
            state="NEXT_STEP_READY",
            command_id=command_id,
            step=nxt,
            previous_step_id=previous,
            reason="mid_plan_dispatch_recovery",
        )

    async def _mark_dispatch_ready(
        self,
        *,
        cycle: _CycleRecord,
        state: str,
        command_id: str,
        step: StepContractV1 | None = None,
        previous_step_id: str | None = None,
        reason: str,
    ) -> dict[str, Any]:
        """Persist executable readiness without starting execution.

        This is the only producer of STEP_READY/NEXT_STEP_READY. It deliberately
        clears the prior claim before the durable snapshot is written, making the
        next dispatcher invocation a separate, observable boundary.

        Invariants (refuse — never emit null ids):
        - active PlanRevision must exist and be ACCEPTED
        - executable step_id must be non-null and must not be a planning: placeholder
        """
        if state not in {"STEP_READY", "NEXT_STEP_READY"}:
            raise ValueError(f"unsupported dispatcher ready state: {state}")

        plan = self._active_plan_revision(cycle.objective_id)
        if plan is None or str(plan.status) != "ACCEPTED":
            return {
                "ok": False,
                "operation": "dispatch_deferred",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "dispatch_state": cycle.dispatch_state,
                "dispatch_required": cycle.dispatch_required,
                "error": {
                    "code": "STEP_READY_REQUIRES_ACCEPTED_PLAN",
                    "detail": (
                        "refusing STEP_READY/NEXT_STEP_READY without an active ACCEPTED "
                        "PlanRevision; run understanding/planner first"
                    ),
                },
            }

        selected = step
        if selected is None:
            selected = self._dispatcher_observe_and_bind_step(cycle=cycle, plan=plan)
        if selected is None:
            selected = self._step_contracts.get(cycle.objective_id)
        step_id = str(getattr(selected, "id", "") or "").strip() if selected is not None else ""
        if selected is None or not step_id or step_id.startswith("planning:"):
            return {
                "ok": False,
                "operation": "dispatch_deferred",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "dispatch_state": cycle.dispatch_state,
                "dispatch_required": cycle.dispatch_required,
                "plan_revision_id": plan.plan_id,
                "error": {
                    "code": "STEP_READY_REQUIRES_EXECUTABLE_STEP",
                    "detail": (
                        "refusing STEP_READY/NEXT_STEP_READY without a concrete executable "
                        "step_id (planning: placeholders are not allowed)"
                    ),
                },
            }

        cycle.current_step_cursor = step_id
        runtime = self._step_runtime.get(cycle.objective_id) or {}
        current = runtime.get(step_id)
        # READY re-arm must clear stale RUNNING left by a failed/hung attempt;
        # otherwise STEP_READY + RUNNING blocks dispatch forever.
        if current is not None and current.status != "PASS":
            runtime[step_id] = replace_runtime_status(current, status="READY")
            self._step_runtime[cycle.objective_id] = runtime
        cycle.previous_step_id = previous_step_id
        cycle.dispatch_state = state
        cycle.dispatch_required = True
        # A valid READY/PENDING step must not keep a prior attempt's last_error
        # (empty-diff REPAIR, PATH_OUTSIDE_SCOPE after rebind, etc.).
        # Exception: zero-progress zombie re-arm must keep the prior refusal code so
        # a subsequent execute None maps to NO_ACCEPTED_PLAN (resume recovery), not
        # a silent STEP_PASSED / generic NOOP.
        prior_re = cycle.real_execute if isinstance(cycle.real_execute, dict) else {}
        zero_progress_zombie = reason == "zombie_running_retry" and not (
            bool(prior_re.get("ok"))
            or bool(getattr(cycle, "mutation_evidence_history", None))
        )
        if not zero_progress_zombie:
            cycle.last_error_detail = None
            cycle.terminal_reason = None
        cycle.execution_claim_id = None
        cycle.execution_claim_step_id = None
        cycle.execution_claim_attempt = 0
        cycle.execution_claim_expired = False
        cycle.active_step_started_at = None
        cycle.step_lease_expires_at = None
        cycle.last_progress_at = _utc()
        # Mid-plan re-arm clears false loop detection only when prior resumes made
        # real progress. Zombie RUNNING retry with zero real_execute / mutations is
        # not progress — resetting resume_count there let NEED_EVIDENCE loops spin
        # forever past EXECUTION_LOOP_DETECTED.
        prior_step_progress = bool(getattr(cycle, "mutation_evidence_history", None))
        real_progress = bool(prior_re.get("ok")) or prior_step_progress
        cycle.real_execute = None
        if reason == "previous_step_verified_pass" or (
            reason == "zombie_running_retry" and real_progress
        ):
            cycle.resume_count = 0
            # Keep approval bound to the live gate so step N+1 is not refused as
            # PLAN_CHANGED_SINCE_APPROVAL after a bookkeeping gate bump.
            if cycle.current_gate_id:
                cycle.approved_gate_id = cycle.current_gate_id
                cycle.approved_plan_revision = cycle.plan_revision
        cycle.dispatch_state = state
        self._sync_cycle_phase_from_engine(cycle)
        cycle.version += 1
        payload = {
            "objective_id": cycle.objective_id,
            "cycle_id": cycle.cycle_id,
            "plan_revision_id": plan.plan_id,
            "step_id": step_id,
            "step_index": selected.step_index,
            "previous_step_id": previous_step_id,
            "state": state,
            "attempt": 0,
            "lease": None,
            "active_step_started_at": None,
            "last_progress_at": cycle.last_progress_at,
            "dispatch_required": True,
            "reason": reason,
        }
        self._emit_event(cycle=cycle, event_type="cycle.next_step_ready", payload_refs=payload)
        await self._persist_event(
            tenant_id=cycle.tenant_id,
            aggregate_type="cycle",
            aggregate_id=cycle.cycle_id,
            expected_sequence_number=cycle.version,
            event_type="cycle.next_step_ready",
            command_id=f"{command_id}:ready:{step_id}:{cycle.version}",
            payload=payload,
        )
        await self._project_run(cycle)
        return {
            "ok": True,
            "operation": "dispatch_deferred",
            "tenant_id": cycle.tenant_id,
            "cycle_id": cycle.cycle_id,
            "phase": state,
            "version": cycle.version,
            "dispatch_state": state,
            "dispatch_required": True,
            "active_step_id": cycle.current_step_cursor,
            "plan_revision_id": plan.plan_id,
            "execution_claim_id": None,
            "dispatch_deferred": True,
            "aix_creates_workhub_tasks": False,
            "approval_gate_id": cycle.current_gate_id,
            "approved_gate_id": cycle.approved_gate_id,
            "approved_operations": list(cycle.approved_operations),
            "approved_resources": list(cycle.approved_resources),
            "attempt_number": cycle.engine.state.attempt_number if cycle.engine is not None else 0,
            "workhub_lifecycle_authority": bool(self.mark_workhub_done),
            "aix_marks_workhub_done": bool(self.mark_workhub_done),
            "exception": "001P-W13" if self.mark_workhub_done else ("001P-W12" if self.create_workhub_tasks else None),
        }

    async def _plan_then_mark_dispatch_ready(
        self,
        *,
        cycle: _CycleRecord,
        command_id: str,
        reason: str,
        caller_auth: str | None,
        actor_id: str,
        state: str = "STEP_READY",
        step: StepContractV1 | None = None,
        previous_step_id: str | None = None,
    ) -> dict[str, Any]:
        """Understanding/planner → ACCEPTED plan → bind step → STEP_READY.

        Never emits STEP_READY before an ACCEPTED plan and executable step exist.
        Does not retry or mask hangs inside plan preparation / dispatch.
        """
        prepare_err = await self._ensure_plan_accepted_before_claim(
            cycle=cycle,
            command_id=command_id,
            caller_auth=caller_auth,
            actor_id=actor_id,
        )
        if prepare_err is not None:
            return prepare_err
        selected = step or self._step_contracts.get(cycle.objective_id)
        return await self._mark_dispatch_ready(
            cycle=cycle,
            state=state,
            command_id=command_id,
            step=selected,
            previous_step_id=previous_step_id,
            reason=reason,
        )

    async def _dispatch_after_ready(
        self,
        *,
        cycle: _CycleRecord,
        tenant_id: str,
        cycle_id: str,
        command_id: str,
        caller_auth: str | None,
        actor_id: str,
        ready: dict[str, Any],
    ) -> dict[str, Any] | None:
        """After STEP_READY: either await dispatch (tests) or schedule it (live API).

        When ``OBJECTIVE_CYCLE_AWAIT_EXECUTION=0``, return the ready payload immediately
        so approve/resume HTTP finishes and /health stays responsive. Dispatch continues
        in the background; hang/failure surfaces on the cycle, not by wedging uvicorn.
        """
        if not self._await_execution:
            existing = self._execute_tasks.get(cycle_id)
            if existing is not None and not existing.done():
                out = dict(ready)
                out["async_execution"] = True
                out["note"] = "dispatch_already_in_flight"
                return out

            # PLAT-D2: Temporal owns dispatch — do not schedule inline
            # self-drive; the workflow's dispatch_step activity executes the
            # parked STEP_READY. The ready payload below carries the signal.
            if self._temporal_owns_dispatch:
                logger.info(
                    "dispatch_parked_temporal_owned cycle=%s",
                    cycle_id,
                )
                out = dict(ready)
                out["async_execution"] = False
                out["note"] = "dispatch_parked_temporal_owned"
                return out

            async def _run() -> None:
                try:
                    await asyncio.sleep(0.05)
                    await self.dispatch_ready_step(
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        command_id=f"{command_id}:bg-dispatch",
                        caller_auth=caller_auth,
                        actor_id=actor_id,
                    )
                except Exception:
                    logger.exception(
                        "background_dispatch_after_ready_failed cycle=%s", cycle_id
                    )
                finally:
                    self._execute_tasks.pop(cycle_id, None)

            self._execute_tasks[cycle_id] = asyncio.create_task(_run())
            await asyncio.sleep(0)
            out = dict(ready)
            out["async_execution"] = True
            out["note"] = "dispatch_scheduled_after_step_ready"
            return out

        return await self.dispatch_ready_step(
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            command_id=command_id,
            caller_auth=caller_auth,
            actor_id=actor_id,
        )

    async def _maybe_redispatch_stuck_after_approval(
        self,
        *,
        cycle: _CycleRecord,
        tenant_id: str,
        cycle_id: str,
        command_id: str,
        caller_auth: str | None,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Re-arm plan+dispatch when approval was recorded but execute never started."""
        engine = cycle.engine
        if engine is None:
            return None
        if str(engine.state.phase) not in ("WAITING_APPROVAL", "RUNNING_STEP"):
            return None
        plan = self._active_plan_revision(cycle.objective_id)
        if plan is None or str(plan.status) != "ACCEPTED":
            return None
        if cycle.dispatch_state in {"EXECUTING", "STEP_PASSED"}:
            return None
        existing = self._execute_tasks.get(cycle_id)
        if existing is not None and not existing.done():
            return None
        return await self._plan_then_dispatch_after_approval(
            cycle=cycle,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            command_id=command_id,
            caller_auth=caller_auth,
            actor_id=actor_id,
            reason=reason,
        )

    async def _plan_then_dispatch_after_approval(
        self,
        *,
        cycle: _CycleRecord,
        tenant_id: str,
        cycle_id: str,
        command_id: str,
        caller_auth: str | None,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Plan then dispatch. Live (`AWAIT_EXECUTION=0`) returns immediately so /health
        and CLI stay responsive while planner runs in the background.
        """
        if not self._await_execution:
            existing = self._execute_tasks.get(cycle_id)
            if existing is not None and not existing.done():
                return self._control_payload_with_synced_phase(
                    cycle,
                    {
                        "ok": True,
                        "operation": "plan_prep_scheduled",
                        "note": "plan_prep_already_in_flight",
                        "async_execution": True,
                        "phase": cycle.phase,
                        "dispatch_state": cycle.dispatch_state,
                    },
                )

            async def _run() -> None:
                try:
                    # Python 3.12+ may start tasks eagerly; defer plan prep so approve/resume
                    # HTTP can flush before CPU-bound planner/repo work blocks the loop.
                    await asyncio.sleep(0.05)
                    ready: dict[str, Any] | None = None
                    plan = self._active_plan_revision(cycle.objective_id)
                    if plan is not None and str(plan.status) == "ACCEPTED":
                        advanced = await self._maybe_advance_mid_plan_dispatch(
                            cycle=cycle, command_id=f"{command_id}:bg-advance"
                        )
                        if isinstance(advanced, dict) and advanced.get("ok"):
                            ready = advanced
                    if ready is None or not ready.get("ok"):
                        ready = await self._plan_then_mark_dispatch_ready(
                            cycle=cycle,
                            state="STEP_READY",
                            command_id=f"{command_id}:bg-plan",
                            reason=reason,
                            caller_auth=caller_auth,
                            actor_id=actor_id,
                        )
                    if not ready.get("ok", False):
                        err = ready.get("error") if isinstance(ready.get("error"), dict) else {}
                        logger.warning(
                            "background_plan_then_dispatch_not_ready cycle=%s reason=%s code=%s",
                            cycle_id,
                            reason,
                            err.get("code") if isinstance(err, dict) else ready.get("error"),
                        )
                        return
                    # Await dispatch in this task. Do not call `_dispatch_after_ready`
                    # here — with AWAIT_EXECUTION=0 it would see this task in
                    # `_execute_tasks` and skip the actual claim (self-collision).
                    # PLAT-D2: Temporal owns dispatch — park at STEP_READY for
                    # the workflow's dispatch_step activity; never self-drive.
                    if self._temporal_owns_dispatch:
                        logger.info(
                            "dispatch_parked_temporal_owned cycle=%s",
                            cycle_id,
                        )
                        return
                    dispatched = await self.dispatch_ready_step(
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        command_id=f"{command_id}:bg-dispatch",
                        caller_auth=caller_auth,
                        actor_id=actor_id,
                    )
                    if (
                        dispatched is None
                        and cycle.engine is not None
                        and str(cycle.engine.state.phase) == "RUNNING_STEP"
                    ):
                        self._apply_terminalization_to_engine(cycle)
                except Exception:
                    logger.exception("background_plan_then_dispatch_failed cycle=%s", cycle_id)
                finally:
                    self._execute_tasks.pop(cycle_id, None)

            self._execute_tasks[cycle_id] = asyncio.create_task(_run())
            # Yield so ASGI can send this response before the deferred task runs.
            await asyncio.sleep(0)
            return self._control_payload_with_synced_phase(
                cycle,
                {
                    "ok": True,
                    "operation": "plan_prep_scheduled",
                    "note": "plan_prep_scheduled",
                    "async_execution": True,
                    "phase": cycle.phase,
                    "dispatch_state": cycle.dispatch_state,
                },
            )

        ready = await self._plan_then_mark_dispatch_ready(
            cycle=cycle,
            state="STEP_READY",
            command_id=command_id,
            reason=reason,
            caller_auth=caller_auth,
            actor_id=actor_id,
        )
        if not ready.get("ok", False):
            return self._control_payload_with_synced_phase(cycle, ready)
        dispatched = await self._dispatch_after_ready(
            cycle=cycle,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            command_id=command_id,
            caller_auth=caller_auth,
            actor_id=actor_id,
            ready=ready,
        )
        if dispatched is not None:
            return self._control_payload_with_synced_phase(cycle, dispatched)
        if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
            self._apply_terminalization_to_engine(cycle)
        return self._control_payload_with_synced_phase(cycle, ready)

    def _dispatcher_observe_and_bind_step(
        self,
        *,
        cycle: _CycleRecord,
        plan: PlanRevisionV1,
    ) -> StepContractV1 | None:
        """Dispatcher boundary: read ACCEPTED plan, select ready step, bind claim.

        This is the only planner → executor bridge. Planner must never call
        ``run_real_execute``; the executor only receives the step returned here.
        """
        runtime = self._step_runtime.get(cycle.objective_id)
        preferred = cycle.current_step_cursor or cycle.execution_claim_step_id
        if preferred and str(preferred).startswith("planning:"):
            preferred = None
        observed = observe_accepted_plan(plan, runtime, preferred_step_id=preferred)
        if observed is None:
            return None
        self._step_runtime[cycle.objective_id] = observed.runtime
        self._step_contracts[cycle.objective_id] = observed.step
        cycle.current_step_cursor = observed.step.id
        attempt = int(cycle.execution_claim_attempt or 0) or 1
        log_plan_observed(
            objective_id=cycle.objective_id,
            plan_id=observed.plan.plan_id,
            step_id=observed.step.id,
            attempt=attempt,
            reason="accepted_plan_ready",
        )
        log_step_ready(
            objective_id=cycle.objective_id,
            plan_id=observed.plan.plan_id,
            step_id=observed.step.id,
            attempt=attempt,
            reason="scheduler_selected",
        )
        prior_claim_step = cycle.execution_claim_step_id
        cycle.execution_claim_step_id = observed.step.id
        if cycle.execution_claim_id:
            log_step_claimed(
                objective_id=cycle.objective_id,
                plan_id=observed.plan.plan_id,
                step_id=observed.step.id,
                attempt=attempt,
                reason=(
                    "rebound_from_planning_claim"
                    if prior_claim_step and str(prior_claim_step).startswith("planning:")
                    else "ready_step_claimed"
                ),
            )
            self._emit_event(
                cycle=cycle,
                event_type="step.execution_claim_bound",
                payload_refs={
                    "execution_claim_id": cycle.execution_claim_id,
                    "step_id": observed.step.id,
                    "attempt": attempt,
                    "prior_claim_step_id": prior_claim_step,
                },
            )
        return observed.step

    async def _ensure_plan_accepted_before_claim(
        self,
        *,
        cycle: _CycleRecord,
        command_id: str,
        caller_auth: str | None,
        actor_id: str,
    ) -> dict[str, Any] | None:
        """Intent + planner only. No execution claim. Returns None when ready to claim."""
        plan = self._active_plan_revision(cycle.objective_id)
        if plan is not None and str(plan.status) == "ACCEPTED":
            ovc = self._objective_verification_contracts.get(cycle.objective_id)
            if ovc is not None:
                disp_coverage = evaluate_ovc_disposition_coverage(ovc, plan.steps)
                if not disp_coverage.ok:
                    return {
                        "ok": False,
                        "operation": "dispatch_ready_step",
                        "tenant_id": cycle.tenant_id,
                        "cycle_id": cycle.cycle_id,
                        "phase": cycle.phase,
                        "error": {
                            "code": "OVC_DISPOSITION_INCOMPLETE",
                            "detail": f"Plan lacks disposition for OVC items: missing={disp_coverage.missing_ov_ids}, invalid={disp_coverage.invalid_dispositions}",
                        },
                    }
            bound = self._dispatcher_observe_and_bind_step(cycle=cycle, plan=plan)
            if bound is not None:
                return None
        from backend.hyeve_aix_service.config import settings as _plan_settings

        chat_timeout = float(
            max(30, int(getattr(_plan_settings, "INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS", 90)))
        )
        plan_prep_timeout = float(
            max(
                chat_timeout + 30.0,
                int(getattr(_plan_settings, "INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS", 90)) * 3,
            )
        )
        try:
            prepared = await asyncio.wait_for(
                self._execute_claimed_step(
                    cycle=cycle,
                    command_id=command_id,
                    caller_auth=caller_auth,
                    actor_id=actor_id,
                    plan_only=True,
                ),
                timeout=plan_prep_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "plan_prep timed out after %ss; trying existing-evidence bootstrap cycle=%s",
                int(plan_prep_timeout),
                cycle.cycle_id,
            )
            bootstrap: dict[str, Any] | None = None
            try:
                bootstrap = await asyncio.wait_for(
                    self._execute_claimed_step(
                        cycle=cycle,
                        command_id=f"{command_id}:bootstrap",
                        caller_auth=caller_auth,
                        actor_id=actor_id,
                        plan_only=True,
                        planner_force_existing_bootstrap=True,
                    ),
                    timeout=90.0,
                )
            except asyncio.TimeoutError:
                bootstrap = None
            if isinstance(bootstrap, dict) and bootstrap.get("plan_prepared"):
                return None
            detail = (
                f"plan preparation exceeded {int(plan_prep_timeout)}s "
                "(planner/outcome-contract/exploration budget exhausted)"
            )
            self._step_contract_errors[cycle.objective_id] = f"PLANNER_PHASE_TIMEOUT:{detail}"
            cycle.terminal_reason = "PLANNER_PHASE_TIMEOUT"
            cycle.last_error_detail = detail[:800]
            cycle.last_progress_at = _utc()
            cycle.version += 1
            await self._project_run(cycle)
            return {
                "ok": False,
                "operation": "dispatch_ready_step",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "terminal_reason": "PLANNER_PHASE_TIMEOUT",
                "error": {"code": "PLANNER_PHASE_TIMEOUT", "detail": detail},
            }
        if isinstance(prepared, dict) and prepared.get("plan_prepared"):
            return None
        if isinstance(prepared, dict):
            return prepared
        return {
            "ok": False,
            "operation": "dispatch_ready_step",
            "tenant_id": cycle.tenant_id,
            "cycle_id": cycle.cycle_id,
            "phase": cycle.phase,
            "error": {
                "code": "NO_ACCEPTED_PLAN",
                "detail": "intent/planner did not produce an ACCEPTED PlanRevision before claim",
            },
        }

    def _seal_execution_auth(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        caller_auth: str | None,
        actor_id: str = "",
    ) -> None:
        """Seal human-edge authorization into the durable execution store."""
        if caller_auth:
            self._execution_auth.seal(
                tenant_id=tenant_id,
                cycle_id=cycle_id,
                bearer=caller_auth,
                actor_id=actor_id,
            )

    def _resolve_execution_auth(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        caller_auth: str | None,
    ) -> str | None:
        """Execution bearer: fresh human auth wins, else the durable record.

        Temporal activities arrive with empty auth by design; they resolve
        via the sealed record. Never falls back to process memory.
        """
        if caller_auth:
            return caller_auth
        return self._execution_auth.resolve(tenant_id=tenant_id, cycle_id=cycle_id)

    async def dispatch_ready_step(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        command_id: str,
        caller_auth: str | None = None,
        actor_id: str = "dispatcher",
        owner_refs: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Single step-start authority: claim persisted readiness, then execute once.

        ``owner_refs`` carries the already-owned opaque dispatch refs from
        the Temporal activity command body (lane-ops #23 M1) into the
        execution request. It is request-scoped plumbing, not durable
        state; absent refs fail closed at the executor, never fall back.
        """
        cycle = self._cycles.get(cycle_id)
        if cycle is None or cycle.tenant_id != tenant_id:
            loaded = await self.rehydrate_cycle(tenant_id=tenant_id, cycle_id=cycle_id)
            cycle = self._cycles.get(cycle_id) if loaded else None
        if cycle is None or cycle.tenant_id != tenant_id:
            return {"ok": False, "operation": "dispatch_ready_step", "error": {"code": "CYCLE_NOT_FOUND"}}

        lock = self._dispatch_locks.setdefault(cycle_id, asyncio.Lock())
        async with lock:
            if is_genuinely_terminal_phase(
                phase=cycle.phase, terminal_reason=cycle.terminal_reason
            ):
                return {
                    "ok": True,
                    "operation": "dispatch_ready_step",
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "dispatch_required": False,
                    "idempotent_dispatch": True,
                }
            now = datetime.now(UTC)
            if cycle.dispatch_state == "EXECUTING" and cycle.execution_claim_id:
                if cycle.cancel_requested or (
                    cycle.engine is not None and str(cycle.engine.state.phase) == "CANCELLED"
                ):
                    cycle.dispatch_state = "IDLE"
                    cycle.dispatch_required = False
                    cycle.execution_claim_id = None
                    cycle.execution_claim_step_id = None
                    cycle.execution_claim_attempt = 0
                    cycle.execution_claim_expired = False
                    cycle.step_lease_expires_at = None
                    cycle.cancel_requested = True
                    if cycle.engine is not None and str(cycle.engine.state.phase) not in {'CANCELLED', 'FAILED'}:
                        try:
                            cycle.engine.request_cancel(reason='BACKEND_CANCEL')
                        except Exception:
                            pass
                    cycle.dispatch_state = 'CANCELLED'
                    self._sync_cycle_phase_from_engine(cycle)
                    cycle.terminal_reason = cycle.terminal_reason or "CANCELLED"
                    return {
                        "ok": True,
                        "operation": "dispatch_ready_step",
                        "cycle_id": cycle_id,
                        "phase": "CANCELLED",
                        "dispatch_required": False,
                        "idempotent_dispatch": True,
                        "note": "cancel_requested",
                    }
                lease_valid = False
                if cycle.step_lease_expires_at:
                    try:
                        expiry = datetime.fromisoformat(cycle.step_lease_expires_at)
                        if expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=UTC)
                        lease_valid = now < expiry
                    except (TypeError, ValueError):
                        lease_valid = False
                if lease_valid:
                    return {
                        "ok": True,
                        "operation": "dispatch_ready_step",
                        "cycle_id": cycle_id,
                        "phase": "EXECUTING",
                        "dispatch_required": False,
                        "execution_claim_id": cycle.execution_claim_id,
                        "idempotent_dispatch": True,
                        "note": "valid_execution_claim_already_active",
                    }
                cycle.execution_claim_expired = True
                cycle.dispatch_required = False
                cycle.last_progress_at = _utc()
                if cycle.real_execute or cycle.mutation_evidence_history:
                    cycle.dispatch_state = "VERIFYING"
                    cycle.dispatch_state = "VERIFYING"
                    self._sync_cycle_phase_from_engine(cycle)
                    cycle.terminal_reason = "EXPIRED_CLAIM_REQUIRES_EVIDENCE_RECOVERY"
                else:
                    cycle.dispatch_state = "STEP_FAILED"
                    cycle.dispatch_state = "STEP_FAILED"
                    self._sync_cycle_phase_from_engine(cycle)
                    cycle.terminal_reason = "STEP_EXECUTION_TIMED_OUT"
                self._emit_event(
                    cycle=cycle,
                    event_type="step.execution_claim_expired",
                    payload_refs={
                        "execution_claim_id": cycle.execution_claim_id,
                        "step_id": cycle.execution_claim_step_id,
                        "recovery_state": cycle.dispatch_state,
                    },
                )
                cycle.version += 1
                await self._project_run(cycle)
                return {
                    "ok": False,
                    "operation": "dispatch_ready_step",
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "dispatch_required": False,
                    "execution_claim_id": cycle.execution_claim_id,
                    "error": {"code": cycle.terminal_reason, "detail": "expired claim was not re-executed"},
                }
            if not cycle.dispatch_required or cycle.dispatch_state not in {"STEP_READY", "NEXT_STEP_READY"}:
                return {
                    "ok": True,
                    "operation": "dispatch_ready_step",
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "dispatch_required": cycle.dispatch_required,
                    "execution_claim_id": cycle.execution_claim_id,
                    "idempotent_dispatch": True,
                    "note": "no_ready_step",
                }

            # Final boundary ordering: ACCEPTED plan + real StepContract before any
            # execution claim. Never claim planning:{cycle_id} on the normal path.
            prepare_err = await self._ensure_plan_accepted_before_claim(
                cycle=cycle,
                command_id=command_id,
                caller_auth=caller_auth,
                actor_id=actor_id,
            )
            if prepare_err is not None:
                return prepare_err

            active = self._step_contracts.get(cycle.objective_id)
            if active is None or not str(getattr(active, "id", "") or "").strip():
                return {
                    "ok": False,
                    "operation": "dispatch_ready_step",
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "error": {
                        "code": "NO_READY_STEP",
                        "detail": "accepted plan observed but no concrete StepContractV1 to claim",
                    },
                }
            if str(active.id).startswith("planning:"):
                return {
                    "ok": False,
                    "operation": "dispatch_ready_step",
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "error": {
                        "code": "INVALID_CLAIM_STEP",
                        "detail": "refusing planning: placeholder claim; require real StepContractV1.id",
                    },
                }
            cycle.current_step_cursor = active.id
            runtime = self._step_runtime.get(cycle.objective_id) or {}
            runtime_state = runtime.get(active.id)
            if runtime_state is not None and runtime_state.status == "PASS":
                cycle.dispatch_required = False
                return {
                    "ok": True,
                    "operation": "dispatch_ready_step",
                    "cycle_id": cycle_id,
                    "idempotent_dispatch": True,
                    "note": "step_already_passed",
                }
            attempt = int(runtime_state.attempt_count if runtime_state is not None else 0) + 1
            claim_id = f"claim_{new_ulid()[:16]}"
            timeout_seconds = max(1, int(active.step_budget.time_seconds))
            cycle.dispatch_state = "EXECUTING"
            cycle.dispatch_state = "EXECUTING"
            self._sync_cycle_phase_from_engine(cycle)
            cycle.dispatch_required = False
            cycle.execution_claim_id = claim_id
            cycle.execution_claim_step_id = active.id
            cycle.execution_claim_attempt = attempt
            cycle.execution_claim_expired = False
            cycle.active_step_started_at = now.isoformat()
            cycle.last_progress_at = cycle.active_step_started_at
            cycle.step_lease_expires_at = (now + timedelta(seconds=timeout_seconds)).isoformat()
            if runtime_state is not None:
                runtime[active.id] = replace_runtime_status(
                    runtime_state, status="RUNNING", attempt_count=attempt
                )
            cycle.version += 1
            plan_for_claim = self._active_plan_revision(cycle.objective_id)
            log_step_claimed(
                objective_id=cycle.objective_id,
                plan_id=plan_for_claim.plan_id if plan_for_claim is not None else None,
                step_id=active.id,
                attempt=attempt,
                reason="ready_step_claimed",
            )
            claim_payload = {
                "execution_claim_id": claim_id,
                "step_id": cycle.execution_claim_step_id,
                "attempt": attempt,
                "active_step_started_at": cycle.active_step_started_at,
                "step_lease_expires_at": cycle.step_lease_expires_at,
                "state": "EXECUTING",
                "plan_id": plan_for_claim.plan_id if plan_for_claim is not None else None,
            }
            self._emit_event(cycle=cycle, event_type="step.execution_claimed", payload_refs=claim_payload)
            await self._persist_event(
                tenant_id=tenant_id,
                aggregate_type="cycle",
                aggregate_id=cycle_id,
                expected_sequence_number=cycle.version,
                event_type="step.execution_claimed",
                command_id=f"{command_id}:claim:{claim_id}",
                payload=claim_payload,
            )
            await self._project_run(cycle)

        # Durable dispatch owns step start; keep the workflow engine on RUNNING_STEP
        # even after rehydrate seeded EXECUTING/STEP_PASSED or a premature finalize.
        if cycle.engine is not None:
            forced = cycle.engine.force_running_step_for_mid_plan(
                reason="dispatch_ready_step_claim"
            )
            if forced:
                cycle.dispatch_state = "EXECUTING"
                self._sync_cycle_phase_from_engine(cycle)
            elif str(cycle.engine.state.phase) != "RUNNING_STEP":
                return {
                    "ok": False,
                    "operation": "dispatch_ready_step",
                    "cycle_id": cycle_id,
                    "phase": cycle.phase,
                    "error": {
                        "code": "ENGINE_NOT_RUNNING_STEP",
                        "detail": (
                            f"cannot dispatch with engine phase={cycle.engine.state.phase}; "
                            "mid-plan reopen refused"
                        ),
                    },
                }
        # Prior step's real_execute must not block this claim (multi-step plans).
        if cycle.real_execute is not None:
            _remember_mutation_evidence(cycle)
            cycle.real_execute = None

        async def _finalize_execution(result: dict[str, Any] | None) -> dict[str, Any]:
            if isinstance(result, dict) and result.get("plan_prepared"):
                return result
            if isinstance(result, dict) and result.get("dispatch_deferred"):
                return result
            if isinstance(result, dict) and result.get("close_path_blocked"):
                cycle.dispatch_required = False
                if cycle.dispatch_state in {"STEP_READY", "NEXT_STEP_READY", "EXECUTING", "VERIFYING"}:
                    cycle.dispatch_state = "STEP_PASSED"
                self._sync_cycle_phase_from_engine(cycle)
                result = dict(result)
                result["phase"] = cycle.phase
                result["terminal_reason"] = cycle.terminal_reason
                cycle.last_progress_at = _utc()
                cycle.version += 1
                await self._project_run(cycle)
                return result
            if isinstance(result, dict) and result.get("dispatch_state") == "WAITING_FOR_AUTH":
                return result
            if result is None:
                if cycle.real_execute is not None:
                    # _maybe_real_execute records truth on cycle.real_execute and returns
                    # None as the canonical success signal for _control paths; only an
                    # actual no-op (engine phase / stale guard) is EXECUTE_CLAIMED_STEP_NOOP.
                    pass
                elif not self.real_execute_enabled:
                    # Disabled executor: exploration/planner gates already ran above.
                    # Returning None is intentional (no mutation) — mark the claimed
                    # step PASS so dry-run can reach COMPLETED_FOR_REVIEW. Do not
                    # confuse this with EXECUTE_CLAIMED_STEP_NOOP (engine/stale guard).
                    claim_step_id = str(cycle.execution_claim_step_id or "").strip()
                    runtime = dict(self._step_runtime.get(cycle.objective_id) or {})
                    st = runtime.get(claim_step_id) if claim_step_id else None
                    if st is not None and str(st.status) not in {"PASS", "SUPERSEDED"}:
                        runtime[claim_step_id] = replace_runtime_status(
                            st, status="PASS", verification_verdict="PASS"
                        )
                        self._step_runtime[cycle.objective_id] = runtime
                else:
                    # Enabled executor returned None without recording real_execute.
                    # Never fall through as STEP_PASSED. Planner/contract refusals
                    # surface as NO_ACCEPTED_PLAN; other engine/stale no-ops keep
                    # EXECUTE_CLAIMED_STEP_NOOP.
                    prior = str(
                        cycle.terminal_reason
                        or (cycle.last_error_detail or "")
                        or ""
                    ).upper()
                    plan_now = self._active_plan_revision(cycle.objective_id)
                    planner_refused = any(
                        token in prior
                        for token in (
                            "STEP_CONTRACT_NOT_PROPOSED",
                            "NO_ACCEPTED_PLAN",
                            "PLANNER",
                            "CONTRACT_NOT",
                        )
                    )
                    no_accepted = plan_now is None or str(plan_now.status) != "ACCEPTED"
                    noop_code = (
                        "NO_ACCEPTED_PLAN"
                        if planner_refused or no_accepted
                        else "EXECUTE_CLAIMED_STEP_NOOP"
                    )
                    cycle.dispatch_state = "STEP_FAILED"
                    cycle.dispatch_required = False
                    cycle.terminal_reason = noop_code
                    cycle.last_error_detail = (
                        "claimed step did not run (engine not RUNNING_STEP or prior "
                        "real_execute still set); refusing false STEP_PASSED"
                        if noop_code == "EXECUTE_CLAIMED_STEP_NOOP"
                        else (
                            "execute returned no result and no ACCEPTED plan is available "
                            "for this claim; refusing false STEP_PASSED"
                        )
                    )
                    cycle.last_progress_at = _utc()
                    cycle.version += 1
                    await self._project_run(cycle)
                    return {
                        "ok": False,
                        "operation": "dispatch_ready_step",
                        "tenant_id": cycle.tenant_id,
                        "cycle_id": cycle.cycle_id,
                        "phase": cycle.phase,
                        "dispatch_state": cycle.dispatch_state,
                        "dispatch_required": False,
                        "error": {
                            "code": noop_code,
                            "detail": cycle.last_error_detail,
                        },
                    }
            if isinstance(result, dict) and not result.get("ok", False):
                cycle.dispatch_state = "STEP_FAILED"
                cycle.dispatch_required = False
                err = result.get("error") if isinstance(result.get("error"), dict) else {}
                detail = str(err.get("detail") or err.get("code") or "").strip()
                if detail:
                    cycle.last_error_detail = detail[:800]
                code = str(err.get("code") or "").strip()
                if code and not cycle.terminal_reason:
                    cycle.terminal_reason = code
                cycle.last_progress_at = _utc()
                cycle.version += 1
                await self._project_run(cycle)
                return result
            # Never advance the stub workflow to COMPLETED while ACCEPTED plan steps
            # remain — that stranded mid-plan recovery with engine != RUNNING_STEP.
            plan_for_finalize = self._active_plan_revision(cycle.objective_id)
            runtime_for_finalize = self._step_runtime.get(cycle.objective_id) or {}
            has_remaining = False
            if plan_for_finalize is not None and str(plan_for_finalize.status) == "ACCEPTED":
                has_remaining = select_next_step(plan_for_finalize, runtime_for_finalize) is not None
            cycle.dispatch_state = "STEP_PASSED"
            cycle.dispatch_required = False
            cycle.step_lease_expires_at = None
            cycle.last_progress_at = _utc()
            if not has_remaining and cycle.engine is not None:
                eng_phase = str(cycle.engine.state.phase)
                term_dec: TerminalizationDecisionV1 | None = None
                if eng_phase == "RUNNING_STEP":
                    term_dec = self._apply_terminalization_to_engine(
                        cycle, step_id=cycle.execution_claim_step_id or "step-1"
                    )
                    self._sync_cycle_phase_from_engine(cycle)
                elif eng_phase == "WAITING_APPROVAL":
                    # Final-step ok:True REPLAN parks (or equivalent) left the engine
                    # off RUNNING_STEP; still terminalize when all plan steps PASS.
                    terminalized = await self._try_terminalize_all_steps_passed(
                        cycle=cycle
                    )
                    if terminalized is not None:
                        return terminalized
                if term_dec is not None and (
                    term_dec.decision == "DENY_NEEDS_REPAIR" or cycle.phase == "GAPS_FOUND"
                ):
                    repair_res = await self._maybe_autonomous_repair_after_terminalization(
                        cycle,
                        term_dec,
                        tenant_id=cycle.tenant_id,
                        actor_id=actor_id,
                        command_id=command_id,
                        body={"auto_dispatch": False, "auto_reverify": False},
                    )
                    cycle.version += 1
                    await self._project_run(cycle)
                    payload = {
                        "ok": True,
                        "operation": "dispatch_ready_step",
                        "tenant_id": cycle.tenant_id,
                        "cycle_id": cycle.cycle_id,
                        "phase": cycle.phase,
                        "version": cycle.version,
                        "dispatch_state": cycle.dispatch_state,
                        "dispatch_required": cycle.dispatch_required,
                        "terminalization_decision": term_dec.decision,
                        "repair_required": True,
                        "open_gaps_count": self._gap_store.count_blocking_gaps(cycle.objective_id),
                        "workhub_lifecycle_authority": bool(self.mark_workhub_done),
                        "aix_creates_workhub_tasks": False,
                    }
                    if repair_res is not None:
                        payload["gap_repair"] = repair_res
                        payload["dispatch_required"] = cycle.dispatch_required
                    if isinstance(result, dict):
                        payload["executor_result"] = result
                    return payload
            await self._maybe_record_cycle_outcome(cycle=cycle)
            cycle.version += 1
            await self._project_run(cycle)
            completed = {
                "ok": True,
                "operation": "dispatch_ready_step",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "dispatch_state": cycle.dispatch_state,
                "dispatch_required": False,
                "execution_claim_id": cycle.execution_claim_id,
                "real_execute": cycle.real_execute,
                "terminal_reason": cycle.terminal_reason,
                "status": cycle.engine.query_status() if cycle.engine is not None else {},
                "plan_id": cycle.plan_id,
                "plan_revision": cycle.plan_revision,
                "approval_gate_id": cycle.current_gate_id,
                "approved_gate_id": cycle.approved_gate_id,
                "approved_operations": list(cycle.approved_operations),
                "approved_resources": list(cycle.approved_resources),
                "attempt_number": cycle.engine.state.attempt_number if cycle.engine is not None else 0,
                "workhub_lifecycle_authority": bool(self.mark_workhub_done),
                "aix_marks_workhub_done": bool(self.mark_workhub_done),
                "aix_creates_workhub_tasks": False,
                "exception": "001P-W13" if self.mark_workhub_done else ("001P-W12" if self.create_workhub_tasks else None),
            }
            if isinstance(result, dict):
                completed["executor_result"] = result
            return completed

        async def _run_execution_background() -> None:
            try:
                await asyncio.sleep(0.05)
                result = await self._execute_claimed_step(
                    cycle=cycle, command_id=command_id, caller_auth=caller_auth, actor_id=actor_id,
                    owner_refs=owner_refs,
                )
                await _finalize_execution(result)
            except Exception:
                logger.exception(
                    "background_execute_failed cycle=%s claim=%s",
                    cycle.cycle_id,
                    cycle.execution_claim_id,
                )
                cycle.dispatch_state = "STEP_FAILED"
                cycle.dispatch_required = False
                cycle.last_error_detail = "background execute failed"
                if not cycle.terminal_reason:
                    cycle.terminal_reason = "UNCAUGHT_CONTROL_ERROR"
                cycle.last_progress_at = _utc()
                cycle.version += 1
                try:
                    await self._project_run(cycle)
                except Exception:
                    logger.exception("background_execute_project_failed cycle=%s", cycle.cycle_id)
            finally:
                self._execute_tasks.pop(cycle_id, None)

        if not self._await_execution:
            existing = self._execute_tasks.get(cycle_id)
            current = asyncio.current_task()
            if existing is not None and not existing.done() and existing is not current:
                return {
                    "ok": True,
                    "operation": "dispatch_ready_step",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": "EXECUTING",
                    "version": cycle.version,
                    "dispatch_state": "EXECUTING",
                    "dispatch_required": False,
                    "execution_claim_id": cycle.execution_claim_id,
                    "async_execution": True,
                    "note": "execution_already_in_flight",
                }
            # _dispatch_after_ready owns this task entry while it calls back into
            # dispatch_ready_step. Treating the current task as a duplicate leaves a
            # persisted EXECUTING claim with no executor behind it.
            if existing is current:
                result = await self._execute_claimed_step(
                    cycle=cycle,
                    command_id=command_id,
                    caller_auth=caller_auth,
                    actor_id=actor_id,
                )
                return await _finalize_execution(result)
            self._execute_tasks[cycle_id] = asyncio.create_task(_run_execution_background())
            return {
                "ok": True,
                "operation": "dispatch_ready_step",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": "EXECUTING",
                "version": cycle.version,
                "dispatch_state": "EXECUTING",
                "dispatch_required": False,
                "execution_claim_id": cycle.execution_claim_id,
                "async_execution": True,
                "note": "execution_started_in_background",
                "workhub_lifecycle_authority": bool(self.mark_workhub_done),
                "aix_marks_workhub_done": bool(self.mark_workhub_done),
                "aix_creates_workhub_tasks": False,
            }

        result = await self._execute_claimed_step(
            cycle=cycle, command_id=command_id, caller_auth=caller_auth, actor_id=actor_id,
            owner_refs=owner_refs,
        )
        return await _finalize_execution(result)

    async def _execute_claimed_step(
        self,
        *,
        cycle: _CycleRecord,
        command_id: str,
        caller_auth: str | None = None,
        actor_id: str = "",
        plan_only: bool = False,
        planner_force_existing_bootstrap: bool = False,
        owner_refs: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Run intent/planner and optionally execute one claimed step.

        ``plan_only=True`` stops after ACCEPTED plan + dispatcher observe/bind.
        No execution claim is required or created in that mode.
        """
        # Fresh human auth seals durably; activities resolve the sealed record.
        # No process-memory fallback: post-restart processes resolve from disk.
        self._seal_execution_auth(
            tenant_id=cycle.tenant_id,
            cycle_id=cycle.cycle_id,
            caller_auth=caller_auth,
            actor_id=actor_id,
        )
        caller_auth = self._resolve_execution_auth(
            tenant_id=cycle.tenant_id,
            cycle_id=cycle.cycle_id,
            caller_auth=caller_auth,
        )
        # Do not early-return on real_execute_enabled here — exploration / planner
        # gates must still run (NEED_EVIDENCE, PLANNER_DISABLED, etc.). The disabled
        # executor short-circuit lives after those gates.
        if cycle.real_execute is not None and not plan_only:
            return None
        if cycle.engine is None or str(cycle.engine.state.phase) != "RUNNING_STEP":
            return None
        obj = self._objectives.get(cycle.objective_id)
        if obj is None:
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "error": {"code": "OBJECTIVE_NOT_FOUND"},
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }
        persist_cmd = f"{command_id}:a{int(cycle.engine.state.attempt_number or 1)}"
        # Approval identity (gap-closure sprint 3, Priority 3): the plan this dispatch
        # is about to execute must still be the one that was actually approved. In the
        # normal synchronous admit->start->approve->dispatch flow this can never
        # mismatch (nothing changes `obj` in between); it matters for retry/resume-
        # later paths where a `clarify` could have changed the objective/repository
        # between approval and this dispatch attempt.
        if cycle.approved_gate_id is not None and cycle.approved_gate_id != cycle.current_gate_id:
            # Mid-plan: cycle.plan_revision/gate can bump (auto-replan bookkeeping)
            # while the same ACCEPTED PlanRevision is still authoritative. Do not
            # refuse remaining steps — rebind approval to the current gate.
            plan_now = self._active_plan_revision(cycle.objective_id)
            mid_plan_same_accepted = (
                plan_now is not None
                and str(plan_now.status) == "ACCEPTED"
                and (
                    cycle.approved_plan_revision is None
                    or int(getattr(plan_now, "revision", 0) or 0)
                    == int(cycle.approved_plan_revision or 0)
                    or bool(getattr(cycle, "mutation_evidence_history", None))
                )
            )
            if mid_plan_same_accepted:
                logger.warning(
                    "execute_claimed_step: rebinding stale approval gate cycle=%s "
                    "approved=%s current=%s (mid-plan same ACCEPTED plan)",
                    cycle.cycle_id,
                    cycle.approved_gate_id,
                    cycle.current_gate_id,
                )
                cycle.approved_gate_id = cycle.current_gate_id
                cycle.approved_plan_revision = cycle.plan_revision
            else:
                return {
                    "ok": False,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "error": {
                        "code": "PLAN_CHANGED_SINCE_APPROVAL",
                        "detail": (
                            f"approved gate {cycle.approved_gate_id!r} no longer matches current "
                            f"gate {cycle.current_gate_id!r} -- the plan changed after approval "
                            "was granted; re-approve before executing"
                        ),
                    },
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }
        if cycle.approved_resources and obj.repository_id not in cycle.approved_resources:
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "error": {
                    "code": "PLAN_CHANGED_SINCE_APPROVAL",
                    "detail": (
                        f"repository {obj.repository_id!r} was not in the approved resource "
                        f"set {cycle.approved_resources!r}"
                    ),
                },
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }

        # AIX Closed-Loop Execution Plan, Sprint W6: re-check the dispatch-time
        # repository against the envelope's authorized `repository_scope`. Distinct
        # from the `approved_resources` check above (a `_control`-level snapshot) --
        # this specifically catches the envelope (Sprint W2, frozen at admit time)
        # going stale relative to `obj.repository_id`, which `rebind_repository_for_
        # objective` inside `_run_exploration_gate` can change after admission (e.g.
        # via `clarify`). `REPLAN_REQUIRED`, not silent scope expansion, matching the
        # plan's own Phase 4 rule.
        envelope = self._envelopes.get(obj.objective_id)
        if envelope is not None and obj.repository_id not in envelope.repository_scope:
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "error": {
                    "code": "REPLAN_REQUIRED",
                    "detail": (
                        f"repository {obj.repository_id!r} is outside the envelope's authorized "
                        f"repository_scope {envelope.repository_scope!r} -- the repository was "
                        "rebound after admission; re-admit or replan before executing"
                    ),
                },
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }

        # AIX-REAL-EXECUTION-003/004: explore-before-plan gate. Must run and pass (or
        # return a truthful blocked/clarification result) before `run_real_execute` --
        # and, as of Packet P4, before the planner block immediately below -- is ever
        # reached for a task family that requires understanding first.
        #
        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P4: this call moved here from its
        # original position (right before `run_real_execute`, after the planner block)
        # -- a genuine, previously-undiscovered ordering bug found while implementing
        # "move the planner onto the canonical runtime": the planner block below only
        # calls `propose_step_contract` when `self._understanding_evidence.get(obj.
        # objective_id)` is already populated, but that cache is only ever populated
        # BY this exploration gate. With the old ordering, a fresh cycle's first
        # `_maybe_real_execute` call always found no understanding yet (exploration
        # hadn't run), so the planner silently no-op'd on the very first pass -- every
        # existing flag-on test only ever passed because it pre-seeds `self.
        # _understanding_evidence` directly, bypassing this ordering entirely (see
        # `test_w6_governance_scope.py`/`test_al09_auto_repair.py`/etc.'s own `_seed_
        # understanding`/`_seed_contract_and_understanding` helpers). A real,
        # non-test-seeded single-approval flow would never have reached the planner at
        # all. Moving exploration first also avoids spending a live planner model call
        # before knowing whether exploration itself blocks the cycle.
        from backend.hyeve_aix_service.config import settings as _settings_module

        self._ensure_frozen_intent(obj)
        if self._provenance.latest_understanding(obj.objective_id) is None:
            self._refresh_understanding_v1(obj)
        if self._provenance.latest_governance(obj.objective_id) is None:
            self._ensure_governance(obj, envelope)

        self._seed_codebase_audit_contract(obj=obj, cycle=cycle, envelope=envelope)
        self._ensure_accepted_plan_for_seeded_contract(
            obj=obj, cycle=cycle, allow_test_bootstrap=True
        )
        if not self._provenance.evidence(obj.objective_id) and obj.allowed_files:
            self._ensure_exploration_evidence_for_paths(
                obj=obj,
                paths=tuple(
                    str(p).strip().replace("\\", "/")
                    for p in obj.allowed_files
                    if str(p).strip()
                ),
            )
            self._reconcile_understanding_from_exploration_evidence(obj)
        plan_pre = self._active_plan_revision(obj.objective_id)
        planner_on = self._step_contract_planner_enabled()
        skip_exploration = (
            (
                not planner_on
                and (plan_pre is None or str(plan_pre.status) != "ACCEPTED")
            )
            or (
                plan_pre is not None
                and str(plan_pre.status) == "ACCEPTED"
                and self._is_closed_seed_family(obj)
            )
        )
        if planner_force_existing_bootstrap and self._provenance.evidence(obj.objective_id):
            skip_exploration = True
        elif self._provenance.evidence(obj.objective_id):
            # Replan/resume: grounded evidence already sealed — do not re-run exploration LLM.
            skip_exploration = True
        elif obj.allowed_files:
            # Admission-scoped objectives: allowed_files are authoritative exploration hints.
            skip_exploration = True
        if skip_exploration:
            gate_result = None
        else:
            gate_result = await self._run_exploration_gate(cycle=cycle, obj=obj)
        if gate_result is not None:
            return gate_result

        # AIX Closed-Loop Execution Plan, Sprint W6: propose a StepContractV1 (Sprint
        # W5's planner) once real UnderstandingEvidenceV1 exists, opt-in behind
        # settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED (default False --
        # this makes a live model call, and no existing caller/test that doesn't stub
        # it should suddenly need to). A proposal already cached from an earlier
        # attempt on the same objective is reused, not re-requested.

        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P4: "Prior failure evidence when
        # applicable" is one of the planner's own documented inputs -- computed here
        # (rather than only later, for the AL-05 context capsule) so a re-invoked
        # planner call sees the same real evidence the executor's capsule does. Same
        # source, same "most recent verdict only" narrowing, never a second competing
        # computation -- the context-capsule block below reuses this exact value.
        prior_verdicts = self._verification_verdicts.get(obj.objective_id) or []
        previous_failure = None
        if prior_verdicts and prior_verdicts[-1].verdict in ("REPAIR", "HALT", "REUNDERSTAND"):
            previous_failure = "; ".join(prior_verdicts[-1].reasons) or prior_verdicts[-1].verdict

        self._seed_create_directory_contract(obj=obj, cycle=cycle, envelope=envelope)
        self._seed_rename_path_contract(obj=obj, cycle=cycle, envelope=envelope)
        self._seed_verification_summary_contract(obj=obj, cycle=cycle, envelope=envelope)
        self._seed_codebase_audit_contract(obj=obj, cycle=cycle, envelope=envelope)

        if planner_force_existing_bootstrap:
            self._step_contract_errors.pop(obj.objective_id, None)
            if str(cycle.terminal_reason or "") in {
                "PLANNER_PHASE_TIMEOUT",
                "EXISTING_EVIDENCE_BOOTSTRAP_UNAVAILABLE",
                "CONTRACT_FACET_MISSING",
                "INVALID_STEP_CONTRACT",
            }:
                cycle.terminal_reason = None
                cycle.last_error_detail = None
            if not requires_outcome_contract(
                objective=self._objective_text(obj),
                task_class=str(obj.task_class or ""),
                settings=self._settings,
                contract_already_present=False,
            ):
                self._outcome_contracts.pop(obj.objective_id, None)
                self._outcome_contract_obligations.pop(obj.objective_id, None)

        if (
            self._step_contract_planner_enabled()
            and obj.objective_id not in self._step_contracts
            and (
                planner_force_existing_bootstrap
                or obj.objective_id not in self._step_contract_errors
            )
        ):
            log_planning_started(
                objective_id=obj.objective_id,
                cycle_id=cycle.cycle_id,
                reason="propose_step_contract",
            )
            understanding = self._understanding_evidence.get(obj.objective_id)
            # K6: do not overwrite Understanding with GREENFIELD_PAGE_GENERATION.
            # CREATE is proposed via Governance capability + create_root on PlanTargets.
            if understanding is None:
                understanding = build_understanding_evidence(
                    objective_id=obj.objective_id,
                    cycle_id=cycle.cycle_id,
                    context=_context_from_dict(obj.objective_id, obj.execution_context),
                    observed_problem=obj.original_objective or obj.objective or "plan requested",
                )
                self._understanding_evidence[obj.objective_id] = understanding
            provenance_kwargs = self._planner_provenance_kwargs(obj, cycle, envelope)
            intent_text = str(provenance_kwargs.get("objective") or obj.original_objective or obj.objective)
            if understanding is not None:
                # DoD-002C: seal+compile before planner when product-class requires contract,
                # so Evidence.outcome_contract_obligations is authoritative input.
                if self._require_outcome_contract_for(obj):
                    ensure_before = await self._ensure_outcome_contract(
                        obj=obj,
                        cycle=cycle,
                        actor_id=actor_id,
                        caller_auth=caller_auth or "",
                        use_llm=(
                            not planner_force_existing_bootstrap
                            and not bool(self._provenance.evidence(obj.objective_id))
                            and not bool(obj.allowed_files)
                        ),
                    )
                    sealed = self._outcome_contracts.get(obj.objective_id)
                    if (
                        sealed is None
                        or ensure_before is None
                        or not ensure_before.ok
                        or not self._compile_and_store_outcome_obligations(obj.objective_id, sealed)
                    ):
                        code = (
                            ensure_before.code
                            if ensure_before is not None
                            else "OUTCOME_CONTRACT_REQUIRED"
                        )
                        detail = (
                            ensure_before.detail
                            if ensure_before is not None
                            else "OutcomeContractV1 required before planner"
                        )
                        if sealed is not None and obj.objective_id not in self._outcome_contract_obligations:
                            detail = "sealed OutcomeContract produced no compileable obligations"
                            code = "OBLIGATIONS_COMPILE_FAILED"
                        self._step_contract_errors[obj.objective_id] = f"{code}:{detail}"
                        self._record_planner_trace(
                            obj.objective_id,
                            {
                                "quality_ok": False,
                                "code": code,
                                "detail": detail,
                                "before_propose": True,
                            },
                        )
                        understanding = None  # skip propose below
                oc_kwargs = self._planner_outcome_contract_kwargs(obj) if understanding is not None else {}
            if understanding is not None:
                proposal = await propose_step_contract(
                    objective=intent_text,
                    definition_of_done=list(obj.definition_of_done),
                    understanding=understanding,
                    constraints=self._planner_constraints(obj, envelope),
                    tenant_id=cycle.tenant_id,
                    cycle_id=cycle.cycle_id,
                    step_index=0,
                    assigned_role="executor",
                    step_budget=(envelope.budget_ceiling if envelope is not None else BudgetV1(
                        time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1,
                    )),
                    actor_id=actor_id,
                    authorization_header=caller_auth or "",
                    timeout_seconds=float(_settings_module.INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS),
                    obligations=self._obligations.get(obj.objective_id, ()),
                    prior_failure_evidence=previous_failure,
                    objective_id=obj.objective_id,
                    file_excerpts=self._planner_file_excerpts(obj, understanding),
                    evidence_revision_ref=self._planner_evidence_revision_ref(obj, understanding),
                    **{k: v for k, v in provenance_kwargs.items() if k != "objective"},
                    **oc_kwargs,
                    force_existing_evidence_bootstrap=planner_force_existing_bootstrap,
                    skip_llm_when_evidence_grounded=bool(
                        obj.allowed_files or self._provenance.evidence(obj.objective_id)
                    ),
                )
                await self._capture_planner_proposal(
                    cycle=cycle, obj=obj, proposal=proposal, persist_cmd=persist_cmd,
                )
                self._record_planner_trace(
                    obj.objective_id,
                    {
                        "reason": proposal.reason,
                        "reply": proposal.reply,
                        "evidence_requests": [dict(item) for item in proposal.evidence_requests],
                        "used_legacy_one_step": proposal.used_legacy_one_step,
                        "has_contract": proposal.contract is not None,
                        "step_count": len(proposal.plan.steps) if proposal.plan is not None else 0,
                    },
                )
                if (
                    proposal.contract is None
                    and (
                        str(proposal.reason or "").startswith(
                            ("NEEDS_MORE_EVIDENCE", "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE")
                        )
                        or proposal.provenance_decision == "NEED_EVIDENCE"
                    )
                ):
                    requests = normalize_evidence_requests(proposal.evidence_requests) or default_evidence_requests(
                        reason=str(proposal.reason or ""),
                        reply=proposal.reply,
                        objective=obj.objective,
                    )
                    fingerprint = evidence_request_fingerprint(requests)
                    seen = self._planner_evidence_fingerprints.setdefault(obj.objective_id, [])
                    halt_u = self._loop_trackers.setdefault(
                        obj.objective_id, AutonomousLoopTracker()
                    ).record_reunderstand()
                    if fingerprint in seen or halt_u:
                        self._step_contract_errors[obj.objective_id] = (
                            halt_u or "REPEATED_IDENTICAL_EVIDENCE_REQUEST"
                        )
                    elif evidence_requests_are_unbounded(requests):
                        # Repo-wide asks (all files / whole codebase) hang plan-prep
                        # on search + a second LLM call. Fail closed; docs-only
                        # audits belong in Cursor, not /create.
                        self._step_contract_errors[obj.objective_id] = (
                            "NEEDS_MORE_EVIDENCE:unbounded repo-wide evidence request; "
                            "narrow allowed_files or the objective (do not search the whole tree)"
                        )
                    else:
                        seen.append(fingerprint)
                        self._emit_event(
                            cycle=cycle,
                            event_type="planner.needs_more_evidence",
                            payload_refs={
                                "reason": proposal.reason,
                                "requests": [dict(item) for item in requests],
                                "fingerprint": fingerprint,
                            },
                        )
                        await self._persist_event(
                            tenant_id=cycle.tenant_id,
                            aggregate_type="objective",
                            aggregate_id=obj.objective_id,
                            expected_sequence_number=4,
                            event_type="planner.needs_more_evidence",
                            command_id=f"{persist_cmd}:nme:{fingerprint[:12]}",
                            payload={
                                "reason": proposal.reason,
                                "reply": proposal.reply,
                                "requests": [dict(item) for item in requests],
                            },
                        )
                        await self._fulfill_planner_evidence_requests(
                            cycle=cycle, obj=obj, requests=requests, persist_cmd=persist_cmd,
                        )
                        understanding = self._understanding_evidence.get(obj.objective_id) or understanding
                        provenance_kwargs = self._planner_provenance_kwargs(obj, cycle, envelope)
                        proposal = await propose_step_contract(
                            objective=str(provenance_kwargs.get("objective") or obj.original_objective or obj.objective),
                            definition_of_done=list(obj.definition_of_done),
                            understanding=understanding,
                            constraints=self._planner_constraints(obj, envelope),
                            tenant_id=cycle.tenant_id,
                            cycle_id=cycle.cycle_id,
                            step_index=0,
                            assigned_role="executor",
                            step_budget=(envelope.budget_ceiling if envelope is not None else BudgetV1(
                                time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1,
                            )),
                            actor_id=actor_id,
                            authorization_header=caller_auth or "",
                            timeout_seconds=float(_settings_module.INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS),
                            obligations=self._obligations.get(obj.objective_id, ()),
                            prior_failure_evidence=proposal.reply or proposal.reason,
                            objective_id=obj.objective_id,
                            file_excerpts=self._planner_file_excerpts(obj, understanding),
                            evidence_revision_ref=self._planner_evidence_revision_ref(obj, understanding),
                            **{k: v for k, v in provenance_kwargs.items() if k != "objective"},
                            **oc_kwargs,
                        )
                        await self._capture_planner_proposal(
                            cycle=cycle, obj=obj, proposal=proposal, persist_cmd=persist_cmd,
                        )
                        self._record_planner_trace(
                            obj.objective_id,
                            {
                                "reason": proposal.reason,
                                "reply": proposal.reply,
                                "evidence_requests": [dict(item) for item in proposal.evidence_requests],
                                "used_legacy_one_step": proposal.used_legacy_one_step,
                                "has_contract": proposal.contract is not None,
                                "step_count": len(proposal.plan.steps) if proposal.plan is not None else 0,
                                "after_evidence_revision": True,
                            },
                        )
                if proposal.contract is not None:
                    _contract = proposal.contract
                    # Do not auto-stamp every obligation id onto a one-step plan —
                    # that falsely proves semantic coverage. Steps must reference
                    # only obligations they actually address; plan quality + scope
                    # validation catch incomplete coverage.
                    plan = proposal.plan
                    prev = self._active_plan_revision(obj.objective_id)
                    rev_n = len(self._plan_revisions.get(obj.objective_id, ())) + 1
                    if plan is None:
                        plan = PlanRevisionV1(
                            objective_id=obj.objective_id,
                            cycle_id=cycle.cycle_id,
                            revision=rev_n,
                            created_from_evidence=understanding.id,
                            created_from_plan_revision=prev.plan_id if prev is not None else None,
                            steps=(_contract,),
                            obligation_coverage=tuple(dict.fromkeys(_contract.obligation_refs)),
                            planner_reasoning_ref=proposal.reply or None,
                            status="DRAFT",
                        )
                    else:
                        plan = plan.model_copy(
                            update={
                                "objective_id": obj.objective_id,
                                "cycle_id": cycle.cycle_id,
                                "revision": rev_n,
                                "created_from_plan_revision": prev.plan_id if prev is not None else None,
                            }
                        )
                    # DoD: WorkHub/objective → propose OutcomeContract → seal → bind → CoverageGate
                    # before Plan ACCEPTED. Proposal is never authoritative until sealed.
                    require_oc = self._require_outcome_contract_for(obj)
                    ensure_oc = await self._ensure_outcome_contract(
                        obj=obj,
                        cycle=cycle,
                        actor_id=actor_id,
                        caller_auth=caller_auth or "",
                        use_llm=True,
                    )
                    sealed_contract = self._outcome_contracts.get(obj.objective_id)
                    quality: PlanQualityResult | None = None
                    if require_oc and (
                        sealed_contract is None
                        or ensure_oc is None
                        or not ensure_oc.ok
                    ):
                        quality = PlanQualityResult(
                            ok=False,
                            code=(
                                ensure_oc.code
                                if ensure_oc is not None
                                else "OUTCOME_CONTRACT_REQUIRED"
                            ),
                            detail=(
                                ensure_oc.detail
                                if ensure_oc is not None
                                else "OutcomeContractV1 required for product-class objective"
                            ),
                        )
                    else:
                        if sealed_contract is not None:
                            plan = plan.model_copy(
                                update={
                                    "steps": bind_steps_to_contract(
                                        steps=plan.steps,
                                        contract=sealed_contract,
                                    )
                                }
                            )
                            # Contract present → always enforce coverage.
                            require_oc = True
                        provenance_accept = (
                            proposal.provenance_plan is not None
                            and proposal.provenance_decision == "ACCEPT"
                            and not proposal.used_legacy_one_step
                        )
                        if provenance_accept:
                            # Validator is the accept gate. Parser must not have repaired the plan.
                            intent_ref = self._provenance.get_intent(obj.objective_id)
                            und_ref = self._provenance.latest_understanding(obj.objective_id)
                            gov_ref = self._provenance.latest_governance(obj.objective_id)
                            if (
                                intent_ref is None
                                or und_ref is None
                                or gov_ref is None
                                or not isinstance(proposal.provenance_plan, PlanRevision)
                            ):
                                quality = PlanQualityResult(
                                    ok=False,
                                    code="P1",
                                    detail="missing provenance refs for plan accept",
                                )
                            else:
                                checked = validate_plan_revision(
                                    plan=proposal.provenance_plan,
                                    intent=intent_ref,
                                    understanding=und_ref,
                                    needs=self._provenance.needs(obj.objective_id),
                                    evidence=self._provenance.evidence(obj.objective_id),
                                    governance=gov_ref,
                                    transformations=tuple(proposal.transformations or ()),
                                )
                                if checked.ok and checked.decision == "ACCEPT":
                                    quality = PlanQualityResult(
                                        ok=True,
                                        code="PROVENANCE_ACCEPT",
                                        detail=checked.rule_id,
                                    )
                                else:
                                    quality = PlanQualityResult(
                                        ok=False,
                                        code=checked.rule_id,
                                        detail=checked.detail,
                                    )
                            if quality.ok and require_oc:
                                cov = evaluate_contract_coverage(
                                    outcome_contract=sealed_contract,
                                    steps=plan.steps,
                                    require_outcome_contract=True,
                                )
                                if not cov.ok:
                                    quality = PlanQualityResult(
                                        ok=False,
                                        code=cov.code,
                                        detail=cov.detail,
                                    )
                        else:
                            if proposal.used_legacy_one_step:
                                quality = evaluate_plan_quality(
                                    plan=plan,
                                    obligations=self._obligations.get(obj.objective_id, ()),
                                    allowed_files=(),
                                    objective=obj.original_objective or obj.objective,
                                    used_legacy_one_step=True,
                                    outcome_contract=sealed_contract,
                                    require_outcome_contract=require_oc,
                                )
                            elif (
                                proposal.provenance_plan is None
                                and proposal.provenance_decision is None
                            ):
                                quality = evaluate_plan_quality(
                                    plan=plan,
                                    obligations=self._obligations.get(obj.objective_id, ()),
                                    allowed_files=(),
                                    objective=obj.original_objective or obj.objective,
                                    used_legacy_one_step=proposal.used_legacy_one_step,
                                    outcome_contract=sealed_contract,
                                    require_outcome_contract=require_oc,
                                )
                            elif self._provenance.get_intent(obj.objective_id) is not None:
                                seed_hint = ""
                                for item in getattr(proposal, "transformations", ()) or ():
                                    op = str(item.get("op") or "") if isinstance(item, dict) else ""
                                    if "obligation_seed" in op:
                                        seed_hint = f"; transform={op}"
                                        break
                                quality = PlanQualityResult(
                                    ok=False,
                                    code="LEGACY_PLAN_FORBIDDEN",
                                    detail=(
                                        "live planner must emit provenance PlanTargets; "
                                        "mixed state cannot accept a plan"
                                        f" (decision={proposal.provenance_decision!r}"
                                        f"{seed_hint})"
                                    ),
                                )
                            else:
                                quality = evaluate_plan_quality(
                                    plan=plan,
                                    obligations=self._obligations.get(obj.objective_id, ()),
                                    allowed_files=(),
                                    objective=obj.original_objective or obj.objective,
                                    used_legacy_one_step=proposal.used_legacy_one_step,
                                    outcome_contract=sealed_contract,
                                    require_outcome_contract=require_oc,
                                )
                    assert quality is not None
                    if not quality.ok and quality.code not in {
                        "OUTCOME_CONTRACT_REQUIRED",
                        "CONTRACT_INVALID",
                    }:
                        provenance_kwargs = self._planner_provenance_kwargs(obj, cycle, envelope)
                        retry = await propose_step_contract(
                            objective=str(
                                provenance_kwargs.get("objective")
                                or obj.original_objective
                                or obj.objective
                            ),
                            definition_of_done=list(obj.definition_of_done),
                            understanding=understanding,
                            constraints=self._planner_constraints(obj, envelope),
                            tenant_id=cycle.tenant_id,
                            cycle_id=cycle.cycle_id,
                            step_index=0,
                            assigned_role="executor",
                            step_budget=(envelope.budget_ceiling if envelope is not None else BudgetV1(
                                time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1,
                            )),
                            actor_id=actor_id,
                            authorization_header=caller_auth or "",
                            timeout_seconds=float(_settings_module.INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS),
                            obligations=self._obligations.get(obj.objective_id, ()),
                            prior_failure_evidence=f"{quality.code}:{quality.detail}",
                            objective_id=obj.objective_id,
                            file_excerpts=self._planner_file_excerpts(obj, understanding),
                            evidence_revision_ref=self._planner_evidence_revision_ref(obj, understanding),
                            **{k: v for k, v in provenance_kwargs.items() if k != "objective"},
                            **oc_kwargs,
                        )
                        await self._capture_planner_proposal(
                            cycle=cycle, obj=obj, proposal=retry, persist_cmd=persist_cmd,
                        )
                        if retry.contract is not None:
                            proposal = retry
                            _retry_contract = retry.contract
                            plan = retry.plan or PlanRevisionV1(
                                objective_id=obj.objective_id,
                                cycle_id=cycle.cycle_id,
                                revision=rev_n,
                                created_from_evidence=understanding.id,
                                created_from_plan_revision=prev.plan_id if prev is not None else None,
                                steps=(_retry_contract,),
                                obligation_coverage=tuple(dict.fromkeys(_retry_contract.obligation_refs)),
                                planner_reasoning_ref=retry.reply or None,
                                status="DRAFT",
                            )
                            plan = plan.model_copy(
                                update={
                                    "objective_id": obj.objective_id,
                                    "cycle_id": cycle.cycle_id,
                                    "revision": rev_n,
                                }
                            )
                            sealed_contract = self._outcome_contracts.get(obj.objective_id)
                            if sealed_contract is not None:
                                plan = plan.model_copy(
                                    update={
                                        "steps": bind_steps_to_contract(
                                            steps=plan.steps,
                                            contract=sealed_contract,
                                        )
                                    }
                                )
                                require_oc = True
                            if (
                                retry.provenance_plan is not None
                                and retry.provenance_decision == "ACCEPT"
                                and not retry.used_legacy_one_step
                            ):
                                quality = PlanQualityResult(
                                    ok=True,
                                    code="PROVENANCE_ACCEPT",
                                    detail="validate_plan_revision ACCEPT",
                                )
                                if quality.ok and require_oc:
                                    cov = evaluate_contract_coverage(
                                        outcome_contract=sealed_contract,
                                        steps=plan.steps,
                                        require_outcome_contract=True,
                                    )
                                    if not cov.ok:
                                        quality = PlanQualityResult(
                                            ok=False,
                                            code=cov.code,
                                            detail=cov.detail,
                                        )
                            else:
                                if retry.used_legacy_one_step:
                                    quality = evaluate_plan_quality(
                                        plan=plan,
                                        obligations=self._obligations.get(obj.objective_id, ()),
                                        allowed_files=(),
                                        objective=obj.original_objective or obj.objective,
                                        used_legacy_one_step=True,
                                        outcome_contract=sealed_contract,
                                        require_outcome_contract=require_oc,
                                        ovc=self._objective_verification_contracts.get(obj.objective_id),
                                    )
                                elif (
                                    retry.provenance_plan is None
                                    and retry.provenance_decision is None
                                ):
                                    quality = evaluate_plan_quality(
                                        plan=plan,
                                        obligations=self._obligations.get(obj.objective_id, ()),
                                        allowed_files=(),
                                        objective=obj.original_objective or obj.objective,
                                        used_legacy_one_step=retry.used_legacy_one_step,
                                        outcome_contract=sealed_contract,
                                        require_outcome_contract=require_oc,
                                        ovc=self._objective_verification_contracts.get(obj.objective_id),
                                    )
                                elif self._provenance.get_intent(obj.objective_id) is not None:
                                    seed_hint = ""
                                    for item in getattr(retry, "transformations", ()) or ():
                                        op = str(item.get("op") or "") if isinstance(item, dict) else ""
                                        if "obligation_seed" in op:
                                            seed_hint = f"; transform={op}"
                                            break
                                    quality = PlanQualityResult(
                                        ok=False,
                                        code="LEGACY_PLAN_FORBIDDEN",
                                        detail=(
                                            "retry must emit provenance PlanTargets; "
                                            "mixed state cannot accept a plan"
                                            f" (decision={retry.provenance_decision!r}"
                                            f"{seed_hint})"
                                        ),
                                    )
                                else:
                                    quality = evaluate_plan_quality(
                                        plan=plan,
                                        obligations=self._obligations.get(obj.objective_id, ()),
                                        allowed_files=(),
                                        objective=obj.original_objective or obj.objective,
                                        used_legacy_one_step=retry.used_legacy_one_step,
                                        outcome_contract=sealed_contract,
                                        require_outcome_contract=require_oc,
                                        ovc=self._objective_verification_contracts.get(obj.objective_id),
                                    )
                    if not quality.ok:
                        self._step_contract_errors[obj.objective_id] = (
                            f"{quality.code}:{quality.detail}"
                        )
                        quality_report = build_failure_report(
                            agent_role="planner",
                            agent_component="evaluate_plan_quality",
                            objective_id=obj.objective_id,
                            cycle_id=cycle.cycle_id,
                            failure_code=quality.code,
                            failure_detail=quality.detail[:500],
                            phase=cycle.phase or "EXECUTING",
                            attempted_action="evaluate_plan_quality",
                            suggested_recovery=(
                                "Replan with obligation_refs covering all required obligations."
                                if quality.code == "OBLIGATION_COVERAGE_INCOMPLETE"
                                else "Revise plan to address quality gate failure."
                            ),
                            evidence_missing=tuple(
                                getattr(quality, "uncovered_obligation_ids", ()) or ()
                            ),
                        )
                        self._feedback_collector.record(quality_report)
                        self._failure_reports.setdefault(obj.objective_id, []).append(quality_report)
                        self._record_planner_trace(
                            obj.objective_id,
                            {"quality_ok": False, "code": quality.code, "detail": quality.detail},
                        )
                    else:
                        self._record_planner_trace(
                            obj.objective_id,
                            {"quality_ok": True, "code": quality.code, "detail": quality.detail},
                        )
                        accepted = plan.model_copy(update={"status": "ACCEPTED"})
                        self._admit_plan_revision(accepted)
                        if isinstance(proposal.provenance_plan, PlanRevision):
                            self._provenance.put_plan(
                                proposal.provenance_plan.model_copy(update={"status": "ACCEPTED"})
                            )
                        # Bind pre-plan CLI/FE approval to the ACCEPTED provenance plan
                        # even when the accept path is step-contract-only.
                        if cycle.approval_reference:
                            self._bind_approval_to_plan(obj, cycle.approval_reference)
                        self._link_planner_call_to_plan(obj.objective_id, accepted.plan_id)
                        self._step_runtime[obj.objective_id] = init_step_runtime(accepted)
                        # Planner boundary ends at ACCEPTED + persist. Selecting the
                        # ready step and claiming it is the dispatcher's job — never
                        # call the executor from this block.
                        log_plan_accepted(accepted, reason="plan_quality_ok")
                        self._persist_accepted_plan_observability(
                            cycle=cycle,
                            obj=obj,
                            accepted=accepted,
                            proposal=proposal,
                            understanding=understanding,
                        )
                        # objective_cycle_events CHECK only allows
                        # objective|cycle|step|attempt|replan|outcome. Plan
                        # durability is the cycle event + run snapshot envelope,
                        # not a new ledger aggregate named plan_revision.
                        await self._persist_event(
                            tenant_id=cycle.tenant_id,
                            aggregate_type="cycle",
                            aggregate_id=cycle.cycle_id,
                            expected_sequence_number=1,
                            event_type="plan_revision.accepted",
                            command_id=f"{persist_cmd}:plan:{accepted.plan_id}",
                            payload=accepted.model_dump(mode="json"),
                        )
                        await asyncio.to_thread(self._project_run_sync, cycle)
                        for planned_step in accepted.steps:
                            await self._persist_event(
                                tenant_id=cycle.tenant_id,
                                aggregate_type="step",
                                aggregate_id=planned_step.id,
                                expected_sequence_number=1,
                                event_type="step.planned",
                                command_id=f"{persist_cmd}:plan:{accepted.plan_id}:step:{planned_step.id}",
                                payload={
                                    "plan_id": accepted.plan_id,
                                    "plan_revision": accepted.revision,
                                    "contract": planned_step.model_dump(mode="json"),
                                    "status": "PENDING",
                                },
                            )
                        self._loop_trackers.setdefault(obj.objective_id, AutonomousLoopTracker())
                else:
                    reply = (proposal.reply or "").strip()
                    reason = proposal.reason or "STEP_CONTRACT_NOT_PROPOSED"
                    # AIX-PD-001 P0: planner refusal never falls through to mutation,
                    # even when allowed_files is already bound. NEEDS_MORE_EVIDENCE was
                    # already converted to exploration above; remaining refusals BLOCK.
                    if obj.objective_id not in self._step_contract_errors:
                        self._step_contract_errors[obj.objective_id] = (
                            reason if not reply else f"{reason}:{reply[:800]}"
                        )
                    self._emit_event(
                        cycle=cycle,
                        event_type="step_contract_proposed",
                        payload_refs={
                            "proposed": False,
                            "soft_skip": False,
                            "reason": self._step_contract_errors[obj.objective_id],
                        },
                    )
                # AIX Closed-Loop Execution Plan, Sprint W11: this event was a real,
                # named gap -- the plan's own Phase 11 event-type list names
                # `step_contract_proposed` explicitly, but Sprint W6's wiring only ever
                # cached the proposal in memory, never persisted it. Fixed here rather
                # than left for a future pass.
                if proposal.contract is not None or obj.objective_id in self._step_contract_errors:
                    await self._persist_event(
                        tenant_id=cycle.tenant_id,
                        aggregate_type="objective",
                        aggregate_id=obj.objective_id,
                        expected_sequence_number=4,
                        event_type="step_contract_proposed",
                        command_id=f"{persist_cmd}:step_contract",
                        payload=(
                            {
                                "step_contract_id": proposal.contract.id,
                                "purpose": proposal.contract.purpose,
                                "target_scope": list(proposal.contract.target_scope),
                                "risk_tier": proposal.contract.risk_tier,
                            }
                            if proposal.contract is not None
                            else {"proposed": False, "reason": self._step_contract_errors.get(obj.objective_id)}
                        ),
                    )

        # Autonomous Closed-Loop Lane, Packet AL-12 (Transport Failure Tests --
        # "Contradictory contract" scenario): a genuine, previously-unchecked gap found
        # while wiring this -- `self._step_contract_errors` was populated above on any
        # planner refusal (INVALID_STEP_CONTRACT, NEEDS_MORE_EVIDENCE,
        # TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE, ...) but nothing ever read it before
        # falling through to `run_real_execute` below -- a refused/contradictory
        # proposal silently executed anyway with no contract at all. Fixed: a cached
        # refusal now blocks dispatch every time it applies, not just on the pass that
        # produced it (mirrors the scope check just below, which also blocks on every
        # subsequent call, not only the first).
        if obj.objective_id in self._step_contract_errors:
            reason = self._step_contract_errors[obj.objective_id]
            code = reason.split(":", 1)[0] if reason else "STEP_CONTRACT_NOT_PROPOSED"
            reason_lower = (reason or "").lower()
            if code == "PLANNER_MODEL_ERROR" and any(
                kw in reason_lower
                for kw in ("unauthorized", "403", "auth", "token", "credential")
            ):
                failure_report = build_failure_report(
                    agent_role="planner",
                    agent_component="propose_step_contract",
                    objective_id=obj.objective_id,
                    cycle_id=cycle.cycle_id,
                    failure_code="AUTHORIZATION_REQUIRED",
                    failure_detail=reason[:500],
                    phase=cycle.phase or "EXECUTING",
                    attempted_action="propose_step_contract",
                    evidence_missing=("valid_auth_token",),
                    suggested_recovery="Provide valid authorization credentials, then resume the cycle.",
                    upstream_dependency="hyeve_integration_llm_gateway",
                    retryable=False,
                    retry_after_condition="valid_authorization_token_available",
                )
                self._feedback_collector.record(failure_report)
                self._failure_reports.setdefault(obj.objective_id, []).append(failure_report)
                cycle.dispatch_state = "WAITING_FOR_AUTH"
                cycle.dispatch_required = False
                cycle.terminal_reason = "AUTHORIZATION_REQUIRED"
                cycle.last_error_detail = f"AUTHORIZATION_REQUIRED:{reason}"[:800]
                cycle.last_progress_at = _utc()
                cycle.version += 1
                if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                    cycle.engine.wait_blocker()
                    cycle.engine.signal_blocker_resolved(blocker_ref="AUTHORIZATION_REQUIRED", resolved=False)
                    self._sync_cycle_phase_from_engine(cycle)
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.authorization_required",
                    payload_refs={
                        "failure_report_id": failure_report.id,
                        "failure_code": "AUTHORIZATION_REQUIRED",
                        "source": "planner",
                        "detail": reason[:500],
                    },
                )
                await self._project_run(cycle)
                return {
                    "ok": False,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "dispatch_state": "WAITING_FOR_AUTH",
                    "terminal_reason": "AUTHORIZATION_REQUIRED",
                    "failure_report_id": failure_report.id,
                    "failure_chain": self._feedback_collector.failure_chain(obj.objective_id),
                    "failure_summary": summarize_failure_chain(
                        self._feedback_collector.get_reports(obj.objective_id)
                    ),
                    "error": {
                        "code": "AUTHORIZATION_REQUIRED",
                        "detail": reason[:500],
                        "non_retryable": True,
                    },
                }
            return await self._blocked_exploration_payload(
                cycle=cycle, code=code, detail=reason,
                agent_role="planner",
                agent_component="step_contract_errors_gate",
                suggested_recovery="Fix planner error or provide additional evidence.",
            )

        # AIX-PA-002 P0: no ACCEPTED PlanRevision => no executor, independent of
        # INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED. Planner-off without an
        # accepted plan is PLANNER_DISABLED, never silent direct execution. Seeded
        # empty-directory / test contracts still wrap into an ACCEPTED PlanRevision
        # via `_ensure_accepted_plan_for_seeded_contract`.
        #
        # Dispatcher (not planner) independently observes the ACCEPTED plan and
        # selects the ready step before any executor package is built.
        self._ensure_accepted_plan_for_seeded_contract(obj=obj, cycle=cycle)
        plan_now = self._active_plan_revision(obj.objective_id)
        if plan_now is None or str(plan_now.status) != "ACCEPTED":
            planner_on = self._step_contract_planner_enabled()
            code = "NO_ACCEPTED_PLAN" if planner_on else "PLANNER_DISABLED"
            detail = (
                "run_real_execute refused: no ACCEPTED PlanRevisionV1 exists for this objective"
                if planner_on
                else (
                    "planner disabled: no ACCEPTED PlanRevisionV1; executor not invoked "
                    "(NO PLAN = NO MUTATION)"
                )
            )
            return await self._blocked_exploration_payload(
                cycle=cycle, code=code, detail=detail,
                agent_role="planner",
                agent_component="plan_acceptance_gate",
                suggested_recovery="Enable planner and ensure LLM credentials are available."
                if not planner_on else "Re-run planner with valid understanding evidence.",
                evidence_missing=("accepted_plan_revision",),
            )

        bound_step = self._dispatcher_observe_and_bind_step(cycle=cycle, plan=plan_now)
        if bound_step is None:
            return await self._blocked_exploration_payload(
                cycle=cycle,
                code="NO_READY_STEP",
                detail=(
                    "dispatcher observed ACCEPTED PlanRevisionV1 but found no ready "
                    "PENDING step (dependencies unmet or all steps terminal)"
                ),
                agent_role="dispatcher",
                agent_component="observe_accepted_plan",
                suggested_recovery="Ensure plan steps are PENDING with satisfied depends_on.",
                evidence_missing=("ready_step_contract",),
            )

        if plan_only:
            # Planner boundary ends here. Claim + executor happen only after
            # dispatch_ready_step observes this ACCEPTED plan and claims bound_step.id.
            return {
                "ok": True,
                "operation": "plan_prepared",
                "plan_prepared": True,
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "objective_id": obj.objective_id,
                "plan_id": plan_now.plan_id,
                "plan_revision": plan_now.revision,
                "step_id": bound_step.id,
                "execution_claim_id": cycle.execution_claim_id,
            }

        if not self.real_execute_enabled and not plan_only:
            # Gates above already ran. Disabled executor: no mutation.
            return None

        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: Objective Obligations ->
        # StepContracts -> Coverage Validation, run before any other governance check
        # on the proposal (scope, dispatch) gets a chance to let it through. Only
        # reachable when an active StepContract exists. Planner-off without a seeded
        # contract is already blocked above (PLANNER_DISABLED).
        step_contract = self._step_contracts.get(obj.objective_id)
        if step_contract is not None:
            plan_for_coverage = self._active_plan_revision(obj.objective_id)
            seeded_bootstrap = (
                plan_for_coverage is not None
                and str(plan_for_coverage.planner_reasoning_ref or "") == "seeded-step-contract"
            )
            gap_repair_plan = (
                plan_for_coverage is not None
                and str(plan_for_coverage.planner_reasoning_ref or "")
                == "gap_repair_incremental_replan"
            )
            # K13/P6: facet obligations are not execution authority. Provenance plans
            # and seeded bootstrap execute from PlanTargets / Intent marker only.
            # Gap repair plans are bounded OVC-driven fixes verified post-repair.
            if (
                self._provenance.latest_plan(obj.objective_id) is None
                and not seeded_bootstrap
                and not gap_repair_plan
            ):
                coverage_steps = (
                    plan_for_coverage.steps if plan_for_coverage is not None else (step_contract,)
                )
                coverage = validate_obligation_coverage(
                    obligations=self._obligations.get(obj.objective_id, ()),
                    step_contracts=coverage_steps,
                )
                if not coverage.ok:
                    self._emit_event(
                        cycle=cycle,
                        event_type="plan.coverage_incomplete",
                        payload_refs={
                            "step_contract_id": step_contract.id,
                            "uncovered_obligation_ids": [o.id for o in coverage.uncovered],
                            "uncovered_descriptions": [o.description for o in coverage.uncovered],
                        },
                    )
                    return await self._blocked_exploration_payload(
                        cycle=cycle,
                        code="PLAN_INCOMPLETE",
                        detail=(
                            "proposed step_contract does not cover every required obligation: "
                            f"{[o.description for o in coverage.uncovered]!r} "
                            f"(ids={[o.id for o in coverage.uncovered]!r})"
                        ),
                        agent_role="obligation_coverage",
                        agent_component="validate_obligation_coverage",
                        suggested_recovery="Planner must produce step contracts with obligation_refs "
                        "covering all required obligations, or use single-step legacy plan.",
                        evidence_missing=tuple(o.id for o in coverage.uncovered),
                    )

        # Execution target authority is accepted PlanTargets. obj.allowed_files
        # is admission metadata only and cannot authorize a path.
        if step_contract is not None:
            authorized = self._accepted_plan_target_paths(obj)
            if authorized is None:
                plan_now = self._active_plan_revision(obj.objective_id)
                is_seeded_bootstrap = (
                    plan_now is not None
                    and str(getattr(plan_now, "planner_reasoning_ref", "") or "")
                    == "seeded-step-contract"
                )
                # Seeded bootstrap contracts may use stem/workspace placeholders that are
                # not literal admission paths; do not REPLAN them against allowed_files.
                # Live planner proposals without provenance PlanTargets still fall back.
                if not is_seeded_bootstrap:
                    allowed_fallback = tuple(
                        str(p or "").strip().replace("\\", "/")
                        for p in obj.allowed_files
                        if str(p or "").strip()
                    )
                    if allowed_fallback:
                        authorized = allowed_fallback
            if authorized is not None:
                out_of_scope = [p for p in step_contract.target_scope if p not in set(authorized)]
                if out_of_scope:
                    return await self._blocked_exploration_payload(
                        cycle=cycle,
                        code="REPLAN_REQUIRED",
                        detail=(
                            f"proposed step_contract target_scope {step_contract.target_scope!r} "
                            f"names files outside accepted PlanTargets {authorized!r}: "
                            f"{out_of_scope!r}"
                        ),
                        agent_role="scope_checker",
                        agent_component="plan_target_gate",
                        suggested_recovery="Replan with target_scope ⊆ accepted PlanTarget.path.",
                    )
            await self._persist_event(
                tenant_id=cycle.tenant_id, aggregate_type="objective", aggregate_id=obj.objective_id,
                expected_sequence_number=5, event_type="step_authorized",
                command_id=f"{persist_cmd}:step_authorized",
                payload={"step_contract_id": step_contract.id, "target_scope": list(step_contract.target_scope)},
            )

        executor_route = cycle.model_routes.get("executor") if isinstance(cycle.model_routes, dict) else None
        model_override = None
        if isinstance(executor_route, dict):
            model_override = str(
                executor_route.get("selected_model_id") or executor_route.get("model_id") or ""
            ).strip() or None

        # AIX Closed-Loop Execution Plan, Sprint W2: "No Work Order -> No mutation."
        # `run_real_execute` refuses when no `ObjectiveEnvelopeV1` was constructed for
        # this objective at admit time (see `_build_objective_envelope`) -- UNLESS the
        # *only* construction failure is an unsupported `task_class`.
        #
        # Empirically confirmed while wiring this gate (full-suite run against this
        # repo's actual fixtures, not a guess): `"bounded_repair"` is the dominant
        # real-world `task_class` value used across `test_mark_workhub_done.py`,
        # `test_exploration_gate_integration.py`, `test_cursor_class_acceptance_matrix.py`,
        # `test_ex6_s2_model_enforcement.py` -- it is not in `policy.SUPPORTED_TASK_CLASSES`
        # (5 restricted-MVP classes) and does not map via `task_class_mapping.py` (a
        # `task_family.py` family, which `bounded_repair` also isn't). Hard-blocking on
        # this would refuse the majority of today's real, working admissions -- that is
        # not "wiring in a Work Order", it is silently breaking production behavior on an
        # unreviewed policy boundary. `SUPPORTED_TASK_CLASSES` needs a deliberate, human-
        # reviewed widening (flagged already in Sprint W1's plan text) before this specific
        # failure reason can safely become a hard gate. Every *other* construction failure
        # (missing repository scope, malformed budget, etc.) still blocks -- those indicate
        # a genuinely incomplete admission, not a policy-narrowness gap.
        if obj.objective_id not in self._envelopes:
            recorded = self._envelope_construction_errors.get(obj.objective_id)
            recorded_is_hard_failure = bool(recorded) and not all("task_class" in r for r in recorded)
            if recorded_is_hard_failure:
                return {
                    "ok": False,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "error": {
                        "code": "NO_WORK_ORDER",
                        "detail": (
                            "no ObjectiveEnvelopeV1 exists for this objective; real execution is "
                            f"refused until one can be constructed. reasons={list(recorded)}"
                        ),
                    },
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }
            envelope, reasons = self._ensure_objective_envelope(
                obj=obj,
                actor_id=actor_id,
                command_id=f"{persist_cmd}:envelope-restore",
                body={},
            )
            if envelope is None:
                reasons = reasons or recorded or ("envelope_not_constructed",)
                only_task_class_unsupported = bool(reasons) and all("task_class" in r for r in reasons)
                if not only_task_class_unsupported:
                    return {
                        "ok": False,
                        "operation": "real_execute",
                        "tenant_id": cycle.tenant_id,
                        "cycle_id": cycle.cycle_id,
                        "phase": cycle.phase,
                        "error": {
                            "code": "NO_WORK_ORDER",
                            "detail": (
                                "no ObjectiveEnvelopeV1 exists for this objective; real execution is "
                                f"refused until one can be constructed. reasons={list(reasons)}"
                            ),
                        },
                        "workhub_lifecycle_authority": False,
                        "aix_creates_workhub_tasks": False,
                    }

        # Autonomous Closed-Loop Lane, Packet AL-05: build the bounded, step-specific
        # context capsule (never the complete exploration history) right before
        # dispatch. `relevant_previous_failure` deliberately carries only the most
        # recent verdict's reasons -- Packet AL-09's structured repair context -- not
        # every prior attempt's history. `previous_failure` was already computed above
        # (Packet P4) for the planner block -- reused here, not recomputed.
        # Dispatcher already bound the accepted-plan step via
        # `_dispatcher_observe_and_bind_step`; reuse that concrete contract.
        active_step_contract = self._step_contracts.get(obj.objective_id)
        active_plan = self._active_plan_revision(obj.objective_id)
        active_profile = default_executor_profile()
        runtime_now = self._step_runtime.get(obj.objective_id) or {}
        if active_step_contract is not None and active_step_contract.id in runtime_now:
            st = runtime_now[active_step_contract.id]
            if st.status != "RUNNING":
                runtime_now[active_step_contract.id] = replace_runtime_status(
                    st,
                    status="RUNNING",
                    attempt_count=int(st.attempt_count or 0) + 1,
                )
            cycle.current_step_cursor = active_step_contract.id
            cycle.execution_claim_step_id = active_step_contract.id
            cycle.execution_claim_attempt = int(
                runtime_now[active_step_contract.id].attempt_count or 1
            )
        # Safety net: resume/approval may have raced ahead of plan accept. Bind now.
        if cycle.approval_reference:
            self._bind_approval_to_plan(obj, cycle.approval_reference)
        approval_check = self._approval_for_execute(obj)
        if approval_check is not None and not approval_check.ok:
            return await self._blocked_exploration_payload(
                cycle=cycle,
                code=str(approval_check.decision or "NEED_APPROVAL"),
                detail=approval_check.detail or approval_check.rule_id,
                agent_role="approval",
                agent_component="validate_approval_for_execute",
                suggested_recovery="Approve this PlanRevision (approval binds plan_id, not objective_id).",
            )
        prov_plan = self._provenance.latest_plan(obj.objective_id)
        intent = self._provenance.get_intent(obj.objective_id)
        understanding_rev = self._provenance.latest_understanding(obj.objective_id)
        cited_evidence = self._provenance.evidence(obj.objective_id) if prov_plan is not None else ()
        cited_ids = set(prov_plan.evidence_ids) if prov_plan is not None else set()
        cited_items = tuple(e for e in cited_evidence if e.id in cited_ids)
        cited_claims = ()
        if understanding_rev is not None and prov_plan is not None:
            target_claim_ids = {
                cid for target in prov_plan.targets for cid in target.outcome_claim_ids
            }
            cited_claims = tuple(c for c in understanding_rev.claims if c.id in target_claim_ids)
        execution_scope = self._execution_scope_paths(obj, active_step_contract)
        context_capsule = self._build_execute_context_capsule(
            obj=obj,
            cycle=cycle,
            active_step_contract=active_step_contract,
            execution_scope=execution_scope,
            previous_failure=previous_failure,
            intent=intent,
            cited_claims=cited_claims,
            cited_items=cited_items,
            prov_plan=prov_plan,
        )

        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P7: record the complete,
        # authoritative package about to be handed to the executor -- so "what
        # authoritative package did the executor receive" (the sprint doc's own exit
        # question) is answerable by reading this record afterward, not by
        # reconstructing it from scattered caches. Bundles references to the
        # already-built `active_step_contract`/`context_capsule` -- never a second
        # computation of their content. Appends (never replaces) -- one record per
        # dispatch attempt, mirroring `self._step_attempts`' own list-per-objective
        # immutability discipline.
        executor_package = build_executor_package(
            cycle_id=cycle.cycle_id,
            objective_id=obj.objective_id,
            objective=self._intent_text(obj),
            envelope=envelope,
            step_contract=active_step_contract,
            context_capsule=context_capsule,
            repair_context=previous_failure,
            plan_id=(
                prov_plan.plan_id if prov_plan is not None
                else (active_plan.plan_id if active_plan is not None else None)
            ),
            plan_revision=(
                prov_plan.revision if prov_plan is not None
                else (active_plan.revision if active_plan is not None else None)
            ),
            executor_profile_id=active_profile.executor_id,
            approval_ref=(
                (self._approvals.get(prov_plan.plan_id).ref if prov_plan is not None and self._approvals.get(prov_plan.plan_id) else None)
                or (prov_plan.approval_ref if prov_plan is not None else None)
            ),
            target_paths=execution_scope,
        )
        self._executor_packages.setdefault(obj.objective_id, []).append(executor_package)
        await self._persist_event(
            tenant_id=cycle.tenant_id,
            aggregate_type="attempt",
            aggregate_id=executor_package.id,
            expected_sequence_number=1,
            event_type="executor_package_issued",
            command_id=f"{persist_cmd}:executor_package",
            payload={
                "executor_package_id": executor_package.id,
                "step_contract_id": executor_package.step_contract_id,
                "obligation_ids": list(executor_package.obligation_ids),
                "context_capsule_id": executor_package.context_capsule_id,
                "repair_context": executor_package.repair_context,
            },
        )

        journal_step_id = (
            active_step_contract.id
            if active_step_contract is not None
            else f"implicit-step:{cycle.cycle_id}"
        )
        journal_attempt_number = (
            int(runtime_now[active_step_contract.id].attempt_count)
            if active_step_contract is not None and active_step_contract.id in runtime_now
            else (
                1
                + sum(
                    1
                    for prior_attempt in self._step_attempts.get(obj.objective_id, ())
                    if prior_attempt.step_id == journal_step_id
                )
            )
        )
        # A command can advance more than one plan step in the bounded autonomous
        # loop.  The attempt journal idempotency key therefore has to identify the
        # step as well as the originating control command.  Reusing only
        # ``persist_cmd`` made Step N+1 look like a replay of Step N after the latter
        # committed, so the dispatcher returned the prior journal outcome without
        # ever invoking the executor for the next contract.
        journal_command_id = (
            f"{persist_cmd}:{journal_step_id}:attempt:{journal_attempt_number}:real_execute"
        )
        prior_journal = self._attempt_journal.get_by_command(
            tenant_id=cycle.tenant_id, command_id=journal_command_id
        )
        if prior_journal is not None and prior_journal.status == "COMMITTED":
            # C013: identical command cannot re-apply effects.
            return {
                "ok": prior_journal.outcome == "SUCCEEDED",
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "idempotent_replay": True,
                "attempt_id": prior_journal.attempt_id,
                "error": None
                if prior_journal.outcome == "SUCCEEDED"
                else {"code": "DUPLICATE_ATTEMPT_APPLY", "detail": prior_journal.attempt_id},
            }
        try:
            journal_entry = self._attempt_journal.begin(
                tenant_id=cycle.tenant_id,
                run_id=cycle.cycle_id,
                objective_id=obj.objective_id,
                step_id=journal_step_id,
                attempt_number=journal_attempt_number,
                command_id=journal_command_id,
                payload={"persist_cmd": persist_cmd},
            )
        except AttemptJournalError as exc:
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "error": {"code": exc.code, "detail": exc.detail},
            }
        if self._c013_fault_after_begin:
            raise RuntimeError("C013_FAULT_INJECTION_AFTER_BEGIN")

        # GATE-D: checkpoint before execution + update control room state.
        _lc_ctx = LifecycleContext(
            objective_id=obj.objective_id, tenant_id=cycle.tenant_id,
            cycle_id=cycle.cycle_id,
            step_id=getattr(active_step_contract, "id", ""),
            attempt_id=getattr(journal_entry, "attempt_id", ""),
        )
        self._lifecycle.checkpoint(
            _lc_ctx,
            state_snapshot={"phase": cycle.phase, "step": getattr(active_step_contract, "id", "")},
        )
        if bool(getattr(self._framework_flags, "durable_checkpoint", False)):
            self._append_framework_checkpoint(
                cycle=cycle,
                obj=obj,
                step_id=str(getattr(active_step_contract, "id", "") or ""),
                attempt_id=str(getattr(journal_entry, "attempt_id", "") or ""),
                reason="PRE_ATTEMPT_BEGIN",
            )
        self._lifecycle.update_objective_state(_lc_ctx, state="executing")

        log_execution_started(
            objective_id=obj.objective_id,
            plan_id=active_plan.plan_id if active_plan is not None else None,
            step_id=getattr(active_step_contract, "id", None),
            attempt=journal_attempt_number,
        )
        execute_step_contract = active_step_contract
        if (
            execute_step_contract is not None
            and str(obj.task_family or "").upper() == "READ"
            and execute_step_contract.expected_output_schema_ref
            in {"aix.step_contract.v1.proposal", "aix.step_contract.v1.generic_edit"}
            and tuple(execute_step_contract.target_scope or ()) == ("cycle-workspace",)
        ):
            from backend.aix_core.runtime.executor.read_file_execute import (
                extract_target_path,
                looks_like_read_file_objective,
            )

            intent_text = self._intent_text(obj)
            read_target = extract_target_path(intent_text)
            if read_target and looks_like_read_file_objective(intent_text, read_target):
                execute_step_contract = None
        try:
            # One request surface for every executor below. A Muse-routed
            # step never falls back to the legacy executor, whatever the
            # rollout flag says: without a bound Muse runtime that is an
            # explicit refusal, not a silent substitution.
            # Lane-ops #23 M1: owner refs arrive from the Temporal
            # activity command body (already-owned opaque values, never
            # credentials). Blank means absent; the Muse adapter refuses
            # missing/mismatched refs before spawn.
            owned = owner_refs or {}

            def _owned_ref(key: str) -> str | None:
                return str(owned.get(key) or "").strip() or None

            request = ExecuteStepRequest(
                tenant_id=cycle.tenant_id,
                cycle_id=cycle.cycle_id,
                objective_id=obj.objective_id,
                objective=self._intent_text(obj),
                workhub_task_id=obj.workhub_task_id,
                project_id=obj.project_id,
                task_number=obj.task_number,
                repository_id=obj.repository_id,
                attachments=tuple(obj.attachments or ()),
                settings=self._settings,
                require_workhub_load=self.require_workhub_load,
                model_override=model_override,
                caller_authorization=caller_auth,
                actor_id=actor_id,
                task_family=obj.task_family,
                execution_context=obj.execution_context,
                allowed_files=execution_scope,
                admission_allowed_files=tuple(
                    str(p).replace("\\", "/").strip()
                    for p in (obj.allowed_files or ())
                    if str(p).strip()
                ),
                is_cancelled=lambda: cycle.cancel_requested,
                demo_capability=self._demo_capability_from_accepted_plan(obj),
                original_objective=self._intent_text(obj),
                step_contract=execute_step_contract,
                plan_steps=tuple(active_plan.steps) if active_plan is not None else (),
                context_capsule=context_capsule,
                workspace_step_id=(
                    f"step-{active_step_contract.step_index + 1}"
                    if active_step_contract is not None
                    else "step-1"
                ),
                workspace_attempt_id=journal_entry.attempt_id,
                repair_directive=(
                    (self._repair_directives.get(obj.objective_id) or [None])[-1]
                ),
                session_id=obj.conversation_id,
                activation_id=_owned_ref("activation_id"),
                workhub_attempt_id=_owned_ref("workhub_attempt_id"),
                selection_id=_owned_ref("selection_id"),
                candidate_sha=_owned_ref("candidate_sha"),
                plan_sha256=_owned_ref("plan_sha256"),
                worker_id=_owned_ref("worker_id"),
                accepted_model_id=_owned_ref("accepted_model_id"),
            )
            if is_muse_model_id(model_override):
                # Owner refs arrive from the Temporal activity command body
                # (lane-ops #23 M1); when the dispatch path did not carry
                # them they stay empty and the adapter refuses unbound
                # execution rather than executing without authority.
                if self._muse_adapter is None:
                    raise RealExecuteError(
                        "MUSE_RUNTIME_NOT_BOUND",
                        "accepted model route selects Muse but no Muse "
                        "session transport/binding lookup is bound",
                    )
                # Credential boundary (lane-ops #23 M3): the shared
                # request above may carry caller auth for the legacy
                # executor, but no credential may cross into the Muse
                # adapter/session. Hand over a sanitized copy with the
                # credential cleared; non-Muse routes keep the shared
                # request unchanged.
                muse_request = replace(request, caller_authorization=None)
                result = await self._muse_adapter.execute_step(muse_request)
            elif self._framework_flags.execution_port:
                result = await self._execution_port.execute_step(request)
            else:
                # Keep kwargs identical to ExecuteStepRequest.to_legacy_kwargs()
                # (F4 equivalence: same surface whether execution_port is on or off).
                result = await run_real_execute(
                tenant_id=cycle.tenant_id,
                cycle_id=cycle.cycle_id,
                objective_id=obj.objective_id,
                objective=self._intent_text(obj),
                workhub_task_id=obj.workhub_task_id,
                project_id=obj.project_id,
                task_number=obj.task_number,
                repository_id=obj.repository_id,
                attachments=list(obj.attachments or []),
                settings=self._settings,
                require_workhub_load=self.require_workhub_load,
                workspace_base=None,
                model_override=model_override,
                caller_authorization=caller_auth,
                actor_id=actor_id,
                budget_config=None,
                apply_edits=True,
                task_family=obj.task_family,
                execution_context=obj.execution_context,
                allowed_files=execution_scope,
                admission_allowed_files=tuple(
                    str(p).replace("\\", "/").strip()
                    for p in (obj.allowed_files or ())
                    if str(p).strip()
                ),
                is_cancelled=lambda: cycle.cancel_requested,
                # Only explicit page-generation wording AND no file scope may open the
                # HTML/CSS proposal branch. File-bound CLI objectives are real executions.
                demo_capability=self._demo_capability_from_accepted_plan(obj),
                original_objective=self._intent_text(obj),
                # AIX Closed-Loop Execution Plan, Sprint W7: hand the Sprint W5/W6
                # proposal (if any -- opt-in behind INTELLIGENCE_LAYER_STEP_CONTRACT_
                # PLANNER_ENABLED, None otherwise) to the executor.
                step_contract=execute_step_contract,
                plan_steps=tuple(active_plan.steps) if active_plan is not None else (),
                context_capsule=context_capsule,
                workspace_step_id=(
                    f"step-{active_step_contract.step_index + 1}"
                    if active_step_contract is not None
                    else "step-1"
                ),
                workspace_attempt_id=journal_entry.attempt_id,
                repair_directive=(
                    (self._repair_directives.get(obj.objective_id) or [None])[-1]
                ),
                session_id=obj.conversation_id,
                )
            log_execution_completed(
                objective_id=obj.objective_id,
                plan_id=active_plan.plan_id if active_plan is not None else None,
                step_id=getattr(active_step_contract, "id", None),
                attempt=journal_attempt_number,
                status="COMPLETED" if result.ok else "FAILED",
                reason=getattr(result, "code", None),
                ok=bool(result.ok),
            )
        except RealExecuteError as exc:
            log_execution_completed(
                objective_id=obj.objective_id,
                plan_id=active_plan.plan_id if active_plan is not None else None,
                step_id=getattr(active_step_contract, "id", None),
                attempt=journal_attempt_number,
                status="FAILED",
                reason=exc.code,
                ok=False,
            )
            try:
                self._attempt_journal.abort(
                    tenant_id=cycle.tenant_id,
                    attempt_id=journal_entry.attempt_id,
                    reason=exc.code,
                    payload={"detail": str(exc.detail)[:500]},
                )
            except AttemptJournalError:
                pass
            normalized_failure = normalize_failure(exc.code, str(exc.detail))
            self._normalized_failures.setdefault(obj.objective_id, []).append(normalized_failure)
            changed_files = tuple(
                dict.fromkeys(
                    str(value)
                    for event in exc.events
                    for key, value in (event.get("payload_refs") or {}).items()
                    if key in {"file", "path", "target_file", "changed_file"} and value
                )
            )
            artifact_refs = tuple(
                dict.fromkeys(
                    str(value)
                    for event in exc.events
                    for key, value in (event.get("payload_refs") or {}).items()
                    if key in {"artifact_ref", "diff_artifact", "evidence_ref"} and value
                )
            )
            if exc.raw_model_payload is not None:
                raw_record = self._evidence.put(
                    tenant_id=cycle.tenant_id,
                    run_id=cycle.cycle_id,
                    payload={
                        "kind": "raw_model_failure_payload",
                        "failure_code": exc.code,
                        "raw_model_payload": exc.raw_model_payload,
                    },
                )
                artifact_refs = tuple(dict.fromkeys((*artifact_refs, raw_record.uri)))
            failure_evidence = FailureEvidenceV1(
                step_id=journal_step_id,
                attempt=journal_attempt_number,
                failure_signature=normalized_failure.failure_id,
                failure_domain=normalized_failure.domain,
                failure_type=exc.code,
                executor_reason=coerce_executor_reason(code=exc.code, detail=str(exc.detail)),
                verifier_reason=(
                    coerce_executor_reason(code=exc.code, detail=str(exc.detail))
                    if "VERIF" in exc.code or "TEST" in exc.code
                    else None
                ),
                test_failures=(
                    (coerce_executor_reason(code=exc.code, detail=str(exc.detail)),)
                    if "TEST" in exc.code
                    else ()
                ),
                rejected_edits=(
                    (coerce_executor_reason(code=exc.code, detail=str(exc.detail)),)
                    if "EDIT" in exc.code or "PATCH" in exc.code
                    else ()
                ),
                changed_files=changed_files,
                previous_step_contract=(
                    active_step_contract.model_dump(mode="json")
                    if active_step_contract is not None
                    else None
                ),
                artifact_refs=artifact_refs,
            )
            self._failure_evidence.setdefault(obj.objective_id, []).append(failure_evidence)
            self._emit_event(
                cycle=cycle,
                event_type="evidence.failure_recorded",
                payload_refs={
                    "failure_evidence_id": failure_evidence.id,
                    "failure_signature": failure_evidence.failure_signature,
                    "failure_domain": failure_evidence.failure_domain,
                    "reason_code": exc.code,
                    "attempt": failure_evidence.attempt,
                },
            )
            repair_hypothesis = RepairHypothesisV1(
                failure_signature=normalized_failure.failure_id,
                root_cause_evidence_refs=(failure_evidence.id,),
                hypothesis=f"The typed {normalized_failure.domain.lower()} failure {exc.code} must be corrected before redispatch.",
                blast_radius_files=changed_files,
                repair_budget_allocated={"remaining_retries": cycle.retry_budget_remaining},
                changed_strategy=f"typed_repair:{exc.code}",
            )
            prior_hypothesis = (self._repair_hypotheses.get(obj.objective_id) or [None])[-1]
            identical_retry_rejected = False
            try:
                validate_repair_hypothesis(repair_hypothesis, prior_hypothesis)
            except IdenticalRetryError:
                identical_retry_rejected = True
                cycle.terminal_reason = "IDENTICAL_RETRY_WITHOUT_NEW_HYPOTHESIS_REJECTED"
                cycle.last_error_detail = (
                    "IDENTICAL_RETRY_WITHOUT_NEW_HYPOTHESIS_REJECTED: "
                    f"failure signature {normalized_failure.failure_id} repeated"
                )
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.repair_rejected",
                    payload_refs={
                        "reason_code": "IDENTICAL_RETRY_WITHOUT_NEW_HYPOTHESIS_REJECTED",
                        "failure_evidence_id": failure_evidence.id,
                    },
                )
            else:
                self._repair_hypotheses.setdefault(obj.objective_id, []).append(repair_hypothesis)
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.repair_hypothesis_recorded",
                    payload_refs={
                        "failure_evidence_id": failure_evidence.id,
                        "failure_signature": repair_hypothesis.failure_signature,
                        "changed_strategy": repair_hypothesis.changed_strategy,
                    },
                )
            if exc.code == "AUTHORIZATION_REQUIRED":
                failure_report = build_failure_report(
                    agent_role="executor",
                    agent_component="run_real_execute",
                    objective_id=obj.objective_id,
                    cycle_id=cycle.cycle_id,
                    failure_code="AUTHORIZATION_REQUIRED",
                    failure_detail=str(exc.detail)[:500],
                    phase=cycle.phase or "EXECUTING",
                    attempted_action="run_real_execute",
                    evidence_available=tuple(
                        fe.id for fe in (self._failure_evidence.get(obj.objective_id) or [])
                    ),
                    evidence_missing=("valid_auth_token",),
                    suggested_recovery="Provide valid authorization credentials, then resume the cycle.",
                    upstream_dependency="hyeve_integration_llm_gateway",
                    retryable=False,
                    retry_after_condition="valid_authorization_token_available",
                )
                self._feedback_collector.record(failure_report)
                self._failure_reports.setdefault(obj.objective_id, []).append(failure_report)
                self._evidence.put(
                    tenant_id=cycle.tenant_id,
                    run_id=cycle.cycle_id,
                    payload=failure_report.model_dump(mode="json"),
                )
                cycle.dispatch_state = "WAITING_FOR_AUTH"
                cycle.dispatch_required = False
                if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                    cycle.engine.wait_blocker()
                    cycle.engine.signal_blocker_resolved(blocker_ref="AUTHORIZATION_REQUIRED", resolved=False)
                    self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = "AUTHORIZATION_REQUIRED"
                cycle.last_error_detail = f"AUTHORIZATION_REQUIRED:{exc.detail}"[:800]
                cycle.last_progress_at = _utc()
                cycle.version += 1
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.authorization_required",
                    payload_refs={
                        "failure_report_id": failure_report.id,
                        "failure_code": "AUTHORIZATION_REQUIRED",
                        "detail": str(exc.detail)[:500],
                        "dispatch_state": "WAITING_FOR_AUTH",
                    },
                )
                await self._project_run(cycle)
                return {
                    "ok": False,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "dispatch_state": "WAITING_FOR_AUTH",
                    "terminal_reason": "AUTHORIZATION_REQUIRED",
                    "failure_report_id": failure_report.id,
                    "failure_chain": self._feedback_collector.failure_chain(obj.objective_id),
                    "failure_summary": summarize_failure_chain(
                        self._feedback_collector.get_reports(obj.objective_id)
                    ),
                    "error": {
                        "code": "AUTHORIZATION_REQUIRED",
                        "detail": str(exc.detail)[:500],
                        "non_retryable": True,
                    },
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }
            if cycle.cancel_requested or exc.code == "CANCELLED":
                if cycle.engine is not None and str(cycle.engine.state.phase) != "CANCELLED":
                    cycle.engine.signal_cancel(reason="EXECUTION_CANCELLED_MID_RUN")
                if cycle.engine is not None:
                    self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = "CANCELLED"
            elif cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                cycle.engine.wait_blocker()
                cycle.engine.signal_blocker_resolved(blocker_ref=exc.code, resolved=False)
                self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = exc.code
            cycle.last_error_detail = f"{exc.code}:{exc.detail}"[:800]
            await self._maybe_record_cycle_outcome(cycle=cycle)
            for real_event in exc.events:
                self._emit_event(
                    cycle=cycle,
                    event_type=str(real_event.get("event_type") or "tool.command_run"),
                    payload_refs=real_event.get("payload_refs") or {},
                )
            self._emit_event(
                cycle=cycle,
                event_type="cycle.real_execute_failed",
                payload_refs={"code": exc.code, "detail": exc.detail[:500]},
            )
            if cycle.cancel_requested or exc.code == "CANCELLED":
                self._emit_event(
                    cycle=cycle, event_type="cycle.cancelled",
                    payload_refs={"reason_code": "CANCELLED", "objective_status": "CANCELLED"},
                )
            else:
                self._emit_event(cycle=cycle, event_type="plan.rejected", payload_refs={"reason_code": exc.code})
                self._emit_event(cycle=cycle, event_type="execution.blocked", payload_refs={"reason_code": exc.code})
                self._emit_event(
                    cycle=cycle, event_type="objective.blocked",
                    payload_refs={"reason_code": exc.code, "objective_status": "BLOCKED"},
                )
            if (
                exc.code in (
                    "OBJECTIVE_COVERAGE_FAILED",
                    "COMPLETION_GATES_FAILED",
                    "MODEL_RETURNED_NO_EDIT",
                    "EDIT_PARSE_FAILED",
                    "EDIT_UNCHANGED",
                    "NO_APPLICABLE_SOURCE_FILE",
                )
                # Soft-skip / single-step paths often have no PlanRevisionV1; still
                # auto-repair when the flag is on (S3 live needs fail→repair without a
                # multi-step plan artifact).
                and bool(getattr(_settings_module, "INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", False))
                and cycle.engine is not None
                and str(cycle.engine.state.phase) == "BLOCKED"
                and not identical_retry_rejected
            ):
                classification = _classify_terminal_reason_for_retry(exc.code)
                decision = decide_retry_routed(
                    classification,
                    current_attempt_number=cycle.engine.state.attempt_number,
                    remaining_budget=BudgetV1(
                        time_seconds=3600, tokens=1_000_000, cost_usd=100.0,
                        retries=cycle.retry_budget_remaining, steps=100, concurrency=1,
                    ),
                )
                if decision.disposition == "AUTHORIZED":
                    cycle.retry_budget_remaining = (
                        decision.remaining_budget.retries if decision.remaining_budget else 0
                    )
                    cycle.engine.enter_resolution_pending(reason="AUTO_REPAIR", note=exc.code)
                    cycle.engine.mark_ready_to_retry()
                    cycle.engine.begin_retry(
                        attempt_number=decision.next_attempt_number or (cycle.engine.state.attempt_number + 1)
                    )
                    cycle.terminal_reason = None
                    cycle.real_execute = None
                    # Capture mutations from the failed attempt before wipe — otherwise
                    # repair's successful step-only summary drops files changed on the
                    # first attempt (live S3: feature_flag.py then test_feature_flag.py).
                    if exc.events:
                        partial = {
                            "ok": False,
                            "executor": "objective_cycle.real_execute.edit_file.multistep.v1",
                            "changed_source_files": [],
                            "events": list(exc.events),
                            "verification_status": "FAILED",
                            "reason_code": exc.code,
                        }
                        partial["mutation_summary"] = build_mutation_summary(
                            real_execute=partial,
                            events=list(exc.events),
                            verifier_verdict="FAIL",
                        )
                        if partial.get("mutation_summary") and (
                            partial["mutation_summary"].get("files_changed")
                        ):
                            cycle.real_execute = partial
                            _remember_mutation_evidence(cycle)
                        cycle.real_execute = None
                    active = self._step_contracts.get(obj.objective_id)
                    if active is not None:
                        self._repair_directives.setdefault(obj.objective_id, []).append(
                            build_repair_directive(
                                step=active,
                                reasons=(exc.detail or exc.code,),
                                last_strategy_id=self._last_strategy.get(obj.objective_id),
                                remaining_asked_work=True,
                            )
                        )
                    self._loop_trackers.setdefault(obj.objective_id, AutonomousLoopTracker()).record_repair()
                    return await self._mark_dispatch_ready(
                        cycle=cycle,
                        state="STEP_READY",
                        command_id=command_id,
                        step=active,
                        previous_step_id=active.id if active is not None else None,
                        reason=f"executor_auto_repair:{exc.code}",
                    )
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
                "error": {
                    "code": "CANCELLED" if cycle.cancel_requested else exc.code,
                    "detail": exc.detail,
                },
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }

        # Cancellation can be observed by the subprocess runner and returned as an
        # incomplete result instead of raising ``RealExecuteError``.  The concurrent
        # cancel command is authoritative either way: never let post-execute
        # completion validation rewrite CANCELLED into a generic BLOCKED outcome.
        if cycle.cancel_requested:
            if cycle.engine is not None and str(cycle.engine.state.phase) != "CANCELLED":
                cycle.engine.signal_cancel(reason="EXECUTION_CANCELLED_MID_RUN")
            if cycle.engine is not None:
                self._sync_cycle_phase_from_engine(cycle)
            cycle.terminal_reason = "CANCELLED"
            cycle.last_error_detail = "operator requested cancellation"
            cycle.last_progress_at = _utc()
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
                "error": {"code": "CANCELLED", "detail": cycle.last_error_detail},
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }

        if model_override and isinstance(result.token_usage, dict) and result.token_usage.get("ok"):
            # AIX-EXECUTION-006-S2: fail closed against the PROVIDER'S OWN echoed
            # model, not our own request payload — a mismatch here means the
            # provider silently substituted a different model than the bound
            # route selected, which our own request-side "enforcement" could
            # never catch.
            actual_model = str(result.token_usage.get("model") or "").strip()
            expected_model = model_override.strip()
            actual_norm = actual_model.removeprefix("openrouter/").lower()
            expected_norm = expected_model.removeprefix("openrouter/").lower()
            if actual_model and actual_norm != expected_norm:
                if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                    cycle.engine.wait_blocker()
                    cycle.engine.signal_blocker_resolved(blocker_ref="EXECUTOR_MODEL_MISMATCH", resolved=False)
                    self._sync_cycle_phase_from_engine(cycle)
                    cycle.terminal_reason = "EXECUTOR_MODEL_MISMATCH"
                    await self._maybe_record_cycle_outcome(cycle=cycle)
                self._emit_event(
                    cycle=cycle,
                    event_type="cycle.real_execute_failed",
                    payload_refs={
                        "code": "EXECUTOR_MODEL_MISMATCH",
                        "detail": f"expected {expected_model}, provider reported {actual_model}",
                    },
                )
                return {
                    "ok": False,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "terminal_reason": cycle.terminal_reason,
                    "error": {
                        "code": "EXECUTOR_MODEL_MISMATCH",
                        "detail": f"expected {expected_model}, provider reported {actual_model}",
                    },
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }

        cycle.real_execute = {
            "ok": result.ok,
            "code": result.code,
            "workspace": result.workspace,
            "evidence_refs": list(result.evidence_refs),
            "workhub_loaded": result.workhub_loaded,
            "artifacts": dict(result.artifacts),
            "token_usage": result.token_usage,
            "detail": result.detail,
            "workhub_lifecycle_authority": bool(self.mark_workhub_done),
            "aix_creates_workhub_tasks": bool(self.create_workhub_tasks),
            "aix_marks_workhub_done": bool(self.mark_workhub_done),
            "command_id": command_id,
            "executor_model_enforced": bool(model_override),
            # Real source-edit branch only: separated from `run_artifacts` and never
            # merged, so a reviewer only ever sees real repository changes here.
            "changed_source_files": list(result.changed_source_files),
            "run_artifacts": list(result.run_artifacts),
            "repository_root": result.repository_root,
            "diff": result.diff_text,
            "executor": str(getattr(result, "executor", "") or "objective_cycle.real_execute.v1"),
            "validation_commands": list(getattr(result, "validation_commands", None) or []),
            "events": list(result.events or []),
            # Real read-file branch only: no files changed, so `changed_source_files`
            # stays empty on purpose — this is the signal a client checks instead.
            "read_verification": result.read_verification,
            "read_file": result.read_file,
            # Real source-edit branch only: a real, computed change not yet applied to
            # `repository_root` — the client writes `new_content` itself once a human
            # reviews and keeps it.
            "proposed_edits": result.proposed_edits,
            "orchestration_status": result.orchestration_status,
            "planning_status": result.planning_status,
            "execution_status": result.execution_status,
            "verification_status": result.verification_status,
            "objective_status": result.objective_status,
            "strategy_id": getattr(result, "strategy_id", "") or "",
            "remaining_asked_work": bool(getattr(result, "remaining_asked_work", False)),
            "reason_code": result.reason_code,
            "reason": reason_details(result.reason_code),
            "real_operations": result.real_operations,
            "target_files_read": result.target_files_read,
            "target_files_modified": result.target_files_modified,
            "administrative_artifacts_generated": result.administrative_artifacts_generated,
        }
        cycle.completion_gates = result.completion_gates
        if getattr(result, "strategy_id", ""):
            self._last_strategy[obj.objective_id] = str(result.strategy_id)

        # AIX Closed-Loop Execution Plan, Sprint W8: reshape the already-computed
        # RealExecuteResult into an immutable StepAttemptV1 -- never a second success
        # computation (see step_attempt.py's own docstring). step_id prefers the
        # Sprint W5/W6 proposal's id when one exists; otherwise this system is still
        # single-step-per-cycle, so a stable synthetic id keyed to the cycle stands in.
        step_contract_for_attempt = self._step_contracts.get(obj.objective_id)
        step_attempt = build_step_attempt(
            result=result,
            step_id=step_contract_for_attempt.id if step_contract_for_attempt is not None else f"implicit-step:{cycle.cycle_id}",
            tenant_id=cycle.tenant_id,
            attempt_number=int(cycle.engine.state.attempt_number) if cycle.engine is not None else 1,
            executor_ref="aix.real_execute.v1",
            command_id=command_id,
            completed_at=_utc(),
            model_ref=model_override,
        )
        self._step_attempts.setdefault(obj.objective_id, []).append(step_attempt)
        if str(result.diff_text or "").strip():
            self._attempt_diffs.setdefault(obj.objective_id, []).append(str(result.diff_text))
        try:
            self._attempt_journal.mark_effects_applied(
                tenant_id=cycle.tenant_id, attempt_id=journal_entry.attempt_id
            )
            self._attempt_journal.commit(
                tenant_id=cycle.tenant_id,
                attempt_id=journal_entry.attempt_id,
                outcome="SUCCEEDED" if step_attempt.outcome == "SUCCEEDED" else "FAILED",
                payload={
                    "step_attempt_id": step_attempt.id,
                    "outcome": step_attempt.outcome,
                    "failure_class": step_attempt.failure_class,
                },
            )
        except AttemptJournalError as exc:
            logger.warning("C013 journal commit failed: %s %s", exc.code, exc.detail)
        try:
            self._evidence.put(
                tenant_id=cycle.tenant_id,
                run_id=cycle.cycle_id,
                payload={
                    "step_attempt_id": step_attempt.id,
                    "step_id": step_attempt.step_id,
                    "outcome": step_attempt.outcome,
                    "artifact_refs": list(step_attempt.artifact_refs),
                    "journal_attempt_id": journal_entry.attempt_id,
                },
                evidence_id=f"ev_{step_attempt.id[:16]}",
            )
        except Exception as exc:
            logger.warning("C015 evidence put skipped: %s", exc)
        await self._persist_event(
            tenant_id=cycle.tenant_id,
            aggregate_type="attempt",
            aggregate_id=step_attempt.id,
            expected_sequence_number=1,
            event_type="attempt.recorded",
            command_id=f"{persist_cmd}:attempt",
            payload={
                "step_id": step_attempt.step_id,
                "attempt_number": step_attempt.attempt_number,
                "outcome": step_attempt.outcome,
                "artifact_refs": list(step_attempt.artifact_refs),
                "failure_class": step_attempt.failure_class,
                "journal_attempt_id": journal_entry.attempt_id,
                "journal_status": "COMMITTED",
            },
        )

        # AIX Closed-Loop Execution Plan, Sprint W9: the work-order-aware independent
        # verifier. The mechanical layer always runs (no model call, reuses
        # verification_engine.py, zero cost) -- purely observational this pass:
        # computed, cached, and persisted as evidence, but does NOT yet override
        # `cycle.phase`/the response `ok` this pass (that already comes from the
        # existing completion_gates/engine-state path). The semantic (model) layer
        # only runs behind INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED (default
        # False) and only when a StepContractV1 exists. "Verdict actually gates
        # COMPLETED_FOR_REVIEW" is a deliberately deferred, separate hardening step,
        # not done in this pass -- see the plan doc's own Phase 7 status.
        from backend.hyeve_aix_service.config import settings as _verifier_settings

        prior_diffs = list(self._attempt_diffs.get(obj.objective_id) or [])
        # Current attempt's diff was appended above; only earlier attempts contribute.
        if prior_diffs and str(result.diff_text or "").strip():
            prior_diffs = prior_diffs[:-1]

        cycle.dispatch_state = "VERIFYING"
        cycle.dispatch_state = "VERIFYING"
        self._sync_cycle_phase_from_engine(cycle)
        cycle.last_progress_at = _utc()
        runtime_for_verify = self._step_runtime.get(obj.objective_id) or {}
        if step_contract_for_attempt is not None:
            current_for_verify = runtime_for_verify.get(step_contract_for_attempt.id)
            if current_for_verify is not None:
                runtime_for_verify[step_contract_for_attempt.id] = replace_runtime_status(
                    current_for_verify, status="VERIFYING"
                )
        self._emit_event(
            cycle=cycle,
            event_type="step.verification_started",
            payload_refs={
                "execution_claim_id": cycle.execution_claim_id,
                "step_id": cycle.execution_claim_step_id,
            },
        )
        cycle.version += 1
        await self._project_run(cycle)
        verification_outcome = await verify_step(
            result=result,
            step_contract=step_contract_for_attempt,
            repair_budget_remaining=cycle.retry_budget_remaining,
            semantic_verifier_enabled=bool(
                getattr(_verifier_settings, "INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", False)
            ),
            tenant_id=cycle.tenant_id,
            actor_id=actor_id,
            authorization_header=caller_auth or "",
            timeout_seconds=float(_verifier_settings.INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS),
            prior_attempt_diffs=tuple(prior_diffs),
        )
        log_verifier_verdict(
            objective_id=obj.objective_id,
            verdict=str(verification_outcome.verdict or ""),
            plan_id=(
                self._active_plan_revision(obj.objective_id).plan_id
                if self._active_plan_revision(obj.objective_id) is not None
                else None
            ),
            step_id=(
                step_contract_for_attempt.id
                if step_contract_for_attempt is not None
                else cycle.execution_claim_step_id
            ),
            attempt=cycle.execution_claim_attempt,
            reason="; ".join(verification_outcome.reasons) if verification_outcome.reasons else None,
        )
        self._verification_verdicts.setdefault(obj.objective_id, []).append(verification_outcome)
        await self._persist_event(
            tenant_id=cycle.tenant_id,
            aggregate_type="attempt",
            aggregate_id=step_attempt.id,
            expected_sequence_number=2,
            event_type="verification.verdict",
            command_id=f"{persist_cmd}:verification",
            payload={
                "verdict": verification_outcome.verdict,
                "reasons": list(verification_outcome.reasons),
                "mechanical_passed": verification_outcome.mechanical_passed,
                "semantic_checked": verification_outcome.semantic_checked,
            },
        )

        # AIX-MPE-003 Packet MPE-21: a step cannot PASS without declared evidence.
        if verification_outcome.verdict == "PASS" and step_contract_for_attempt is not None:
            missing = missing_required_evidence(step=step_contract_for_attempt, result=result)
            if missing:
                verification_outcome = SemanticVerificationOutcome(
                    verdict="REPAIR",
                    reasons=verification_outcome.reasons + (f"MISSING_REQUIRED_EVIDENCE:{list(missing)}",),
                    mechanical_passed=verification_outcome.mechanical_passed,
                    failed_mandatory_checks=verification_outcome.failed_mandatory_checks,
                    semantic_checked=verification_outcome.semantic_checked,
                    criteria_results=verification_outcome.criteria_results,
                )
                self._verification_verdicts[obj.objective_id][-1] = verification_outcome

        plan_now = self._active_plan_revision(obj.objective_id)
        runtime_now = self._step_runtime.get(obj.objective_id)
        tracker = self._loop_trackers.setdefault(obj.objective_id, AutonomousLoopTracker())
        if isinstance(cycle.real_execute, dict):
            cycle.real_execute["verifier_verdict"] = str(verification_outcome.verdict or "")
            cycle.real_execute["repair_count"] = int(tracker.step_repairs)
            cycle.real_execute["replan_count"] = int(tracker.plan_revisions)
            cycle.real_execute["mutation_summary"] = build_mutation_summary(
                real_execute=cycle.real_execute,
                events=list(result.events or []),
                verifier_verdict=str(verification_outcome.verdict or ""),
                repair_count=int(tracker.step_repairs),
                replan_count=int(tracker.plan_revisions),
            )
        budget_halt = None
        if plan_now is not None:
            budget_halt = tracker.record_attempt()
            if result.diff_text:
                budget_halt = budget_halt or tracker.record_patch(result.diff_text)
            if verification_outcome.verdict != "PASS":
                budget_halt = budget_halt or tracker.record_failure(
                    ";".join(verification_outcome.reasons)
                )
        if budget_halt:
            if isinstance(cycle.real_execute, dict):
                cycle.real_execute["objective_status"] = "INCOMPLETE"
                cycle.real_execute["ok"] = False
                cycle.real_execute["verification_status"] = "FAILED"
            if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                cycle.engine.wait_blocker()
                cycle.engine.signal_blocker_resolved(blocker_ref=budget_halt, resolved=False)
                self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = budget_halt
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
                "error": {"code": budget_halt, "detail": "autonomous loop budget exhausted"},
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }

        if (
            plan_now is not None
            and runtime_now is not None
            and step_contract_for_attempt is not None
            and verification_outcome.verdict == "PASS"
        ):
            sid = step_contract_for_attempt.id
            st = runtime_now.get(sid)
            if st is not None:
                runtime_now[sid] = replace_runtime_status(
                    st,
                    status="PASS",
                    latest_attempt_id=step_attempt.id,
                    verification_verdict="PASS",
                )
                cycle.last_progress_at = _utc()
            nxt = self._select_next_step_governed(plan_now, runtime_now)
            remaining = remaining_required_obligation_ids(
                plan=plan_now,
                runtime=runtime_now,
                obligations=self._obligations.get(obj.objective_id, ()),
            )
            # Do NOT skip remaining ACCEPTED plan steps just because obligation_refs
            # are already covered (common with a single DoD obligation attached to
            # step 1). That aborted multi-file greenfield plans (ERP steps 2–5) after
            # the first mutation while the CLI sat in WAITING_APPROVAL/STEP_PASSED.
            # Exploratory trailing work must be avoided at plan time, not by dropping
            # concrete PENDING steps here.
            if nxt is not None:
                previous_step_id = cycle.current_step_cursor
                self._step_contracts[obj.objective_id] = nxt
                _remember_mutation_evidence(cycle)
                return await self._mark_dispatch_ready(
                    cycle=cycle,
                    state="NEXT_STEP_READY",
                    command_id=command_id,
                    step=nxt,
                    previous_step_id=previous_step_id,
                    reason="previous_step_verified_pass",
                )
            if remaining:
                # All scheduled steps passed; no next step. Forcing REPLAN here used
                # to park WAITING_APPROVAL with ok:True while no auto-replan pipeline
                # adds covering steps — finalize then stamped STEP_PASSED without
                # continue_after_approval, and resume idempotency replayed forever.
                # Emit the gap, then fall through to final objective proof (still
                # fail-closed via OutcomeContract / evaluate_objective_proven).
                self._emit_event(
                    cycle=cycle,
                    event_type="objective.remaining_obligations_after_plan",
                    payload_refs={"remaining_obligation_ids": list(remaining)},
                )
            # A final step's criteria are not proof of the original objective.
            # Re-evaluate the cumulative result against the admitted DoD before
            # allowing a terminal success. The marker distinguishes this proof
            # from ordinary per-step verification in controlled acceptance runs.
            final_contract = step_contract_for_attempt.model_copy(
                update={
                    "purpose": obj.objective,
                    "acceptance_criteria": tuple(obj.definition_of_done),
                    "prohibited_actions": ("AIX_FINAL_OBJECTIVE_PROOF",)
                    + tuple(step_contract_for_attempt.prohibited_actions),
                }
            )
            final_outcome = await verify_step(
                result=result,
                step_contract=final_contract,
                repair_budget_remaining=cycle.retry_budget_remaining,
                semantic_verifier_enabled=bool(
                    getattr(
                        _verifier_settings,
                        "INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED",
                        False,
                    )
                ),
                tenant_id=cycle.tenant_id,
                actor_id=actor_id,
                authorization_header=caller_auth or "",
                timeout_seconds=float(
                    _verifier_settings.INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS
                ),
                prior_attempt_diffs=tuple(prior_diffs),
            )
            self._verification_verdicts[obj.objective_id].append(final_outcome)
            self._emit_event(
                cycle=cycle,
                event_type="objective.final_verifier_verdict",
                payload_refs={
                    "verdict": final_outcome.verdict,
                    "reasons": list(final_outcome.reasons),
                    "semantic_checked": final_outcome.semantic_checked,
                    "remaining_obligation_ids": list(remaining),
                },
            )
            verification_outcome = final_outcome

        verification_outcome, e1_failed = self._enforce_execute_diff_and_record_attempt(
            obj=obj,
            result=result,
            verification_outcome=verification_outcome,
            step_id=(
                step_contract_for_attempt.id
                if step_contract_for_attempt is not None
                else (cycle.execution_claim_step_id or "unknown-step")
            ),
            attempt=int(cycle.execution_claim_attempt or 1),
        )
        if e1_failed:
            cycle.terminal_reason = "E1"
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
                "error": {
                    "code": "E1",
                    "detail": (
                        "execute diff is not ⊆ accepted PlanTargets; "
                        f"reasons={list(verification_outcome.reasons)}"
                    ),
                },
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }

        # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P8: this block previously also
        # required `plan_now is not None and runtime_now is not None` (multi-step plan
        # runtime state) -- but nothing inside the block actually reads either value.
        # That extra condition meant a REUNDERSTAND verdict on the legacy single-
        # StepContract path (`INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED` off --
        # still this codebase's default) fell through every gate uncaught and reached
        # `return None`, letting the cycle silently reach COMPLETED_FOR_REVIEW off a
        # REUNDERSTAND verdict -- exactly the false-completion class of bug Packet P1
        # exists to prevent. Found empirically via a live regression test, not a
        # guess. `step_contract_for_attempt is not None` is kept -- "reunderstand"
        # presupposes an active step to reunderstand about.
        if (
            step_contract_for_attempt is not None
            and verification_outcome.verdict == "REUNDERSTAND"
        ):
            halt_u = tracker.record_reunderstand()
            if halt_u:
                return {
                    "ok": False,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "error": {"code": halt_u, "detail": "reunderstand budget exhausted"},
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }
            previous = self._provenance.latest_understanding(obj.objective_id)
            previous_id = previous.id if previous is not None else ""
            if obj.execution_context:
                obj.execution_context["understanding_complete"] = False
            self._understanding_evidence.pop(obj.objective_id, None)
            refreshed = self._refresh_understanding_v1(obj)
            if refreshed is not None and previous_id:
                e2 = validate_reunderstand(
                    previous_understanding_id=previous_id,
                    new_understanding=refreshed.revision,
                )
                if not e2.ok:
                    return await self._blocked_exploration_payload(
                        cycle=cycle,
                        code="E2",
                        detail=e2.detail or e2.rule_id,
                        agent_role="reunderstand",
                        agent_component="validate_reunderstand",
                        suggested_recovery="Emit a new UnderstandingRevision from Intent+UserTurns; do not keep facet SCOPE.",
                    )
            intent = self._provenance.get_intent(obj.objective_id)
            if refreshed is not None and intent is not None:
                for need in needs_from_understanding(refreshed.revision, intent=intent):
                    self._provenance.put_need(need)
            self._clear_plan_cache_for_replan(obj.objective_id)
            cycle.real_execute = None
            self._emit_event(
                cycle=cycle,
                event_type="cycle.reunderstand",
                payload_refs={"reasons": list(verification_outcome.reasons)},
            )
            return await self._plan_then_mark_dispatch_ready(
                cycle=cycle,
                state="STEP_READY",
                command_id=command_id,
                reason="reunderstand_completed_requires_replan",
                caller_auth=self._resolve_execution_auth(
                    tenant_id=cycle.tenant_id, cycle_id=cycle.cycle_id, caller_auth=None
                ),
                actor_id="system",
            )

        # AIX Closed-Loop Execution — Sprint CL2-P2: the semantic verifier hard gate.
        # Phase 7's original critical rule, made real: when the verifier is enabled, a
        # cycle must not reach a successful completion phase unless verify_step()
        # actually returned PASS. Flag OFF (default) leaves this entire block
        # unreachable -- byte-identical to the pre-CL2 soft evidence-only behavior,
        # same safety property as every other opt-in piece this program has added.
        # REPLAN is handled by its own pre-existing Sprint W10 block immediately below
        # (already returns to WAITING_APPROVAL correctly) -- not touched here.
        semantic_verifier_flag_on = bool(
            getattr(_verifier_settings, "INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED", False)
        )
        missing_evidence = any(
            str(reason).startswith("MISSING_REQUIRED_EVIDENCE")
            for reason in verification_outcome.reasons
        )
        if (
            (semantic_verifier_flag_on or missing_evidence)
            and verification_outcome.verdict in ("REPAIR", "HALT")
        ):
            terminal_reason = (
                "VERIFICATION_REPAIR_REQUIRED" if verification_outcome.verdict == "REPAIR" else "VERIFICATION_HALT"
            )
            result.objective_status = "INCOMPLETE"
            result.verification_status = "FAILED"
            result.ok = False
            if isinstance(cycle.real_execute, dict):
                cycle.real_execute["objective_status"] = "INCOMPLETE"
                cycle.real_execute["verification_status"] = "FAILED"
                cycle.real_execute["ok"] = False
            if getattr(result, "strategy_id", ""):
                self._last_strategy[obj.objective_id] = str(result.strategy_id)
            attempts = self._step_attempts.get(obj.objective_id) or []
            if attempts:
                attempts[-1] = build_step_attempt(
                    result=result,
                    step_id=attempts[-1].step_id,
                    tenant_id=attempts[-1].tenant_id,
                    attempt_number=attempts[-1].attempt_number,
                    executor_ref=attempts[-1].executor_ref,
                    command_id=command_id,
                    completed_at=attempts[-1].completed_at or _utc(),
                    model_ref=attempts[-1].model_ref,
                )
            verifier_detail = "; ".join(verification_outcome.reasons) or terminal_reason
            normalized_verifier_failure = normalize_failure(terminal_reason, verifier_detail)
            verifier_failure = FailureEvidenceV1(
                step_id=(
                    step_contract_for_attempt.id
                    if step_contract_for_attempt is not None
                    else cycle.execution_claim_step_id or f"implicit-step:{cycle.cycle_id}"
                ),
                attempt=max(1, int(cycle.execution_claim_attempt or 1)),
                failure_signature=normalized_verifier_failure.failure_id,
                failure_domain=normalized_verifier_failure.domain,
                failure_type=terminal_reason,
                executor_reason=terminal_reason,
                verifier_reason=verifier_detail,
                test_failures=tuple(verification_outcome.reasons),
                changed_files=tuple(
                    (getattr(result, "mutation_summary", None) or {}).get("files_changed", ())
                ),
                previous_step_contract=(
                    step_contract_for_attempt.model_dump(mode="json")
                    if step_contract_for_attempt is not None else None
                ),
                artifact_refs=tuple(getattr(result, "evidence_refs", ()) or ()),
            )
            self._failure_evidence.setdefault(obj.objective_id, []).append(verifier_failure)
            self._normalized_failures.setdefault(obj.objective_id, []).append(
                normalized_verifier_failure
            )
            runtime_on_failure = self._step_runtime.get(obj.objective_id) or {}
            if step_contract_for_attempt is not None:
                current_failure_state = runtime_on_failure.get(step_contract_for_attempt.id)
                if current_failure_state is not None:
                    runtime_on_failure[step_contract_for_attempt.id] = replace_runtime_status(
                        current_failure_state,
                        status="REPAIR" if verification_outcome.verdict == "REPAIR" else "FAILED",
                        blocked_reason=terminal_reason,
                        failure_refs=tuple(
                            dict.fromkeys((*current_failure_state.failure_refs, verifier_failure.id))
                        ),
                    )
            self._emit_event(
                cycle=cycle,
                event_type="evidence.failure_recorded",
                payload_refs={
                    "failure_evidence_id": verifier_failure.id,
                    "failure_signature": verifier_failure.failure_signature,
                    "failure_domain": verifier_failure.failure_domain,
                    "reason_code": terminal_reason,
                    "attempt": verifier_failure.attempt,
                },
            )
            if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                cycle.engine.wait_blocker()
                cycle.engine.signal_blocker_resolved(blocker_ref=terminal_reason, resolved=False)
                self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = terminal_reason
                cycle.last_error_detail = "; ".join(verification_outcome.reasons)[:800]
                await self._maybe_record_cycle_outcome(cycle=cycle)
            self._emit_event(
                cycle=cycle,
                event_type="cycle.verification_gate_blocked",
                payload_refs={
                    "verdict": verification_outcome.verdict,
                    "reasons": list(verification_outcome.reasons),
                    "terminal_reason": terminal_reason,
                },
            )

            # Autonomous Closed-Loop Lane, Packet AL-09: automatic structured repair,
            # opt-in (INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED, default False -- CL2-P2's
            # block-and-wait-for-a-human-retry_step behavior is otherwise unchanged).
            # Reuses the EXISTING retry_repair.py authorization/transition sequence
            # verbatim (the same one `_control`'s "retry_step" action performs) --
            # never a second repair mechanism. The structured context AL-09 asks for
            # (failed_attempt_id, verifier_reason, unchanged_constraints) already
            # reaches the next attempt via the Sprint AL-05 context capsule's
            # `relevant_previous_failure`, rebuilt from `self._verification_verdicts`
            # at the top of the next `_maybe_real_execute` call -- not appended as
            # "please try again" prose to the original objective.
            if (
                verification_outcome.verdict == "REPAIR"
                and bool(getattr(_verifier_settings, "INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", False))
                and cycle.engine is not None
                and str(cycle.engine.state.phase) == "BLOCKED"
            ):
                classification = _classify_terminal_reason_for_retry(cycle.terminal_reason)
                decision = decide_retry_routed(
                    classification,
                    current_attempt_number=cycle.engine.state.attempt_number,
                    remaining_budget=BudgetV1(
                        time_seconds=3600, tokens=1_000_000, cost_usd=100.0,
                        retries=cycle.retry_budget_remaining, steps=100, concurrency=1,
                    ),
                )
                if decision.disposition == "AUTHORIZED":
                    cycle.retry_budget_remaining = (
                        decision.remaining_budget.retries if decision.remaining_budget else 0
                    )
                    cycle.engine.enter_resolution_pending(reason="AUTO_REPAIR", note=terminal_reason)
                    cycle.engine.mark_ready_to_retry()
                    cycle.engine.begin_retry(
                        attempt_number=decision.next_attempt_number or (cycle.engine.state.attempt_number + 1)
                    )
                    cycle.terminal_reason = None
                    cycle.real_execute = None
                    if step_contract_for_attempt is not None:
                        directive = build_repair_directive(
                            step=step_contract_for_attempt,
                            reasons=tuple(verification_outcome.reasons),
                            last_strategy_id=getattr(result, "strategy_id", None)
                            or self._last_strategy.get(obj.objective_id),
                            remaining_asked_work=bool(getattr(result, "remaining_asked_work", False)),
                            unmet_effects=tuple(getattr(result, "unmet_effects", ()) or ()),
                            comment_only="comment" in ";".join(verification_outcome.reasons).lower(),
                            made_progress=False,
                        )
                        self._repair_directives.setdefault(obj.objective_id, []).append(directive)
                    tracker.record_repair()
                    self._emit_event(
                        cycle=cycle, event_type="cycle.auto_repairing",
                        payload_refs={
                            "attempt_number": cycle.engine.state.attempt_number,
                            "retry_budget_remaining": cycle.retry_budget_remaining,
                            "reasons": list(verification_outcome.reasons),
                        },
                    )
                    return await self._mark_dispatch_ready(
                        cycle=cycle,
                        state="STEP_READY",
                        command_id=command_id,
                        step=step_contract_for_attempt,
                        previous_step_id=step_contract_for_attempt.id,
                        reason="semantic_verifier_repair",
                    )
                self._emit_event(
                    cycle=cycle, event_type="cycle.auto_repair_denied",
                    payload_refs={"reason_codes": list(decision.reason_codes)},
                )

            # A REPAIR verdict is retry-eligible through the EXISTING, already-wired
            # retry_repair.py path (`_classify_terminal_reason_for_retry` /
            # `decide_retry`) once `VERIFICATION_REPAIR_REQUIRED` is a recognized
            # repairable reason -- see that classifier's own frozenset, extended for
            # this sprint. A HALT verdict is deliberately NOT added there: it stays
            # terminal-only, matching `_TERMINAL_ONLY_REASONS`' own discipline of
            # reasons a bare retry could never fix.
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
                "error": {
                    "code": terminal_reason,
                    "detail": (
                        f"semantic verifier returned {verification_outcome.verdict!r}; "
                        f"completion refused. reasons={list(verification_outcome.reasons)}"
                    ),
                },
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }

        # AIX Closed-Loop Execution Plan, Sprint W10: wire replan.py (built, previously
        # unwired -- confirmed distinct from retry_repair.py, which already handles the
        # REPAIR side) into the one verdict retry_repair was never meant to handle:
        # verify_step() returning "REPLAN" means the plan itself looks wrong, not just
        # incomplete -- a bare retry on the same StepContractV1 would repeat the same
        # mistake. Only reachable when the Sprint W9 semantic verifier is enabled (the
        # only source of a "REPLAN" verdict today), so this is unreachable in default
        # configuration, same safety property as every other opt-in piece this program
        # has added.
        if verification_outcome.verdict == "REPLAN":
            replan_classification = FailureClassificationV1("REPLAN_REQUIRED", verification_outcome.reasons)
            replans_used = len(self._replan_decisions.get(obj.objective_id, ()))
            authorization = authorize_replan_routed(
                classification=replan_classification,
                replans_used=replans_used,
                max_replans=replans_used + cycle.replan_budget_remaining,
            )
            if authorization.disposition == "AUTHORIZED":
                cycle.replan_budget_remaining = max(0, cycle.replan_budget_remaining - 1)
                decision = BoundedReplanPolicy().propose(
                    cycle_id=cycle.cycle_id,
                    tenant_id=cycle.tenant_id,
                    triggering_evidence_ref=f"attempt://{step_attempt.id}",
                    reason="; ".join(verification_outcome.reasons) or "semantic verifier returned REPLAN",
                    retained_step_ids=(),
                    scope_comparison="same objective, plan under review — no scope change proposed yet",
                    policy_decision_ref=f"verification://{step_attempt.id}",
                    decided_by="semantic_verifier",
                    authorization=authorization,
                    # Autonomous Closed-Loop Lane, Packet AL-10: derive requires_approval
                    # from the actual scope delta (ReplanDecisionV1's own rule) instead of
                    # replan.py's original always-True default. No `added_step_ids` is
                    # ever passed here today (no automatic re-planning pipeline exists
                    # yet -- see the scope_comparison note above), so this always resolves
                    # to same-scope, `requires_approval=False`. Whether the system then
                    # actually continues autonomously vs. still waits for a human is a
                    # separate, additional gate below (INTELLIGENCE_LAYER_AUTO_REPAIR_
                    # ENABLED) -- a correct requires_approval value on the decision record
                    # does not by itself authorize unattended continuation.
                    force_approval=False,
                )
                self._replan_decisions.setdefault(obj.objective_id, []).append(decision)
                tracker.record_replan(decision.id)
                await self._persist_event(
                    tenant_id=cycle.tenant_id, aggregate_type="cycle", aggregate_id=cycle.cycle_id,
                    expected_sequence_number=cycle.plan_revision + 1, event_type="plan.replanned",
                    command_id=f"{persist_cmd}:replan",
                    payload={"decision_id": decision.id, "reason": decision.reason, "requires_approval": decision.requires_approval},
                )

                if (
                    not decision.requires_approval
                    and bool(getattr(_verifier_settings, "INTELLIGENCE_LAYER_AUTO_REPAIR_ENABLED", False))
                    and cycle.engine is not None
                    and str(cycle.engine.state.phase) == "RUNNING_STEP"
                ):
                    provenance_blocks_auto = self._provenance_blocks_auto_replan(obj)
                    if not provenance_blocks_auto:
                        cycle.engine.wait_blocker()
                        cycle.engine.signal_blocker_resolved(blocker_ref="REPLAN_CONTINUE", resolved=False)
                        if str(cycle.engine.state.phase) == "BLOCKED":
                            cycle.engine.enter_resolution_pending(reason="REPLAN_CONTINUE_SAME_SCOPE", note=decision.reason)
                            cycle.engine.mark_ready_to_retry()
                            cycle.engine.begin_retry(attempt_number=cycle.engine.state.attempt_number + 1)
                        cycle.terminal_reason = None
                        cycle.real_execute = None
                        cycle.plan_revision += 1
                        cycle.current_gate_id = f"gate_{cycle.cycle_id}_r{cycle.plan_revision}"
                        cycle.approved_gate_id = cycle.current_gate_id
                        _failed_sid = step_contract_for_attempt.id if step_contract_for_attempt else None
                        self._clear_plan_cache_for_replan(obj.objective_id, failed_step_id=_failed_sid)
                        self._emit_event(
                            cycle=cycle, event_type="cycle.replan_auto_continued",
                            payload_refs={"decision_id": decision.id, "reasons": list(verification_outcome.reasons)},
                        )
                        return await self._plan_then_mark_dispatch_ready(
                            cycle=cycle,
                            state="STEP_READY",
                            command_id=command_id,
                            reason="authorized_replan_persisted",
                            caller_auth=self._resolve_execution_auth(
                                tenant_id=cycle.tenant_id, cycle_id=cycle.cycle_id, caller_auth=None
                            ),
                            actor_id="system",
                        )

                # Matches replan_from_blocked's own contract: BLOCKED -> WAITING_APPROVAL,
                # never straight back to RUNNING_STEP -- a scope-widening replan (or one
                # where auto-continue isn't enabled) always needs fresh approval, the
                # same rule the architecture doc states explicitly.
                if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                    cycle.engine.wait_blocker()
                    cycle.engine.signal_blocker_resolved(blocker_ref="REPLAN_REQUIRED", resolved=False)
                    if str(cycle.engine.state.phase) == "BLOCKED":
                        cycle.engine.replan_from_blocked(reason="SEMANTIC_VERIFIER_REPLAN")
                    self._sync_cycle_phase_from_engine(cycle)
                    cycle.plan_revision += 1
                    cycle.current_gate_id = f"gate_{cycle.cycle_id}_r{cycle.plan_revision}"
                    # Stale approved_gate_id must not silently fail later steps with
                    # PLAN_CHANGED_SINCE_APPROVAL while engine is WAITING_APPROVAL.
                    cycle.approved_gate_id = None
                    cycle.approved_plan_revision = None
                    cycle.terminal_reason = None
                self._emit_event(
                    cycle=cycle, event_type="cycle.replanned",
                    payload_refs={"decision_id": decision.id, "reasons": list(verification_outcome.reasons)},
                )
                # Return here rather than falling through: the engine is now
                # WAITING_APPROVAL, not RUNNING_STEP, so the caller (`_control`'s
                # "approve"/"resume" branches) must not call `continue_after_approval()`
                # afterward the way it does for a normal completion -- that call
                # requires RUNNING_STEP and would raise. Returning a real, `ok: True`
                # payload here (not an error) matches this function's own `dict | None`
                # contract: non-`None` always means "the caller must stop and return
                # this verbatim", which is exactly what a replan needs.
                return {
                    "ok": True,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "plan_revision": cycle.plan_revision,
                    "approval_gate_id": cycle.current_gate_id,
                    "replan_decision_id": decision.id,
                    "replan_reasons": list(verification_outcome.reasons),
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }
            self._emit_event(
                cycle=cycle, event_type="cycle.replan_denied",
                payload_refs={"reason_codes": list(authorization.reason_codes)},
            )

        if result.objective_status == "COMPLETED":
            if result.completion_gates is None:
                gates_passed = True
            elif "passed" in result.completion_gates:
                gates_passed = bool(result.completion_gates.get("passed"))
            else:
                gate_verdict = str(
                    result.completion_gates.get("verification_verdict") or ""
                ).upper()
                gate_values = result.completion_gates.get("gates")
                gates_passed = gate_verdict in {"PASS", "PASSED"} or (
                    isinstance(gate_values, dict)
                    and bool(gate_values)
                    and all(bool(value) for value in gate_values.values())
                ) or (
                    "gates" not in result.completion_gates
                    and bool(result.completion_gates)
                    and all(bool(value) for value in result.completion_gates.values())
                )
            plan_for_proof = self._active_plan_revision(obj.objective_id)
            runtime_for_proof = self._step_runtime.get(obj.objective_id) or {}
            steps_all_passed = bool(
                plan_for_proof is not None
                and all_required_steps_passed(plan_for_proof, runtime_for_proof)
            )
            outcome_contract = self._outcome_contracts.get(obj.objective_id)
            # DoD path: when a sealed OutcomeContract exists, last-step gates alone
            # cannot satisfy objective proof — five conjuncts required.
            # Use module-level evaluate_contract_coverage (do not re-import locally —
            # a late local import caused UnboundLocalError on earlier plan-prep uses).
            if outcome_contract is not None:
                coverage = evaluate_contract_coverage(
                    outcome_contract=outcome_contract,
                    steps=(plan_for_proof.steps if plan_for_proof is not None else ()),
                    require_outcome_contract=True,
                )
                integration = evaluate_integration_proof(
                    outcome_contract=outcome_contract,
                    steps=(plan_for_proof.steps if plan_for_proof is not None else ()),
                    evidence_refs=tuple(str(r) for r in (result.evidence_refs or [])),
                    require_outcome_contract=True,
                )
                self._integration_proofs[obj.objective_id] = integration
                evidence_complete = bool(result.evidence_refs) and bool(integration.evidence_refs)
                # Acceptance: step verification PASSED + no remaining asked work +
                # gates — still insufficient alone; must also have integration.
                acceptance_proven = (
                    result.execution_status in {"COMPLETED", "SUCCEEDED", "PASSED"}
                    and result.verification_status == "PASSED"
                    and gates_passed
                    and not result.remaining_asked_work
                    and not result.unmet_effects
                )
                proven = evaluate_objective_proven(
                    contract_covered=coverage.ok,
                    all_required_steps_passed=steps_all_passed,
                    integration_proven=integration.ok,
                    objective_acceptance_proven=acceptance_proven,
                    evidence_complete=evidence_complete,
                )
                if not proven.ok:
                    result.objective_status = "BLOCKED"
                    result.reason_code = proven.blocking_reasons[0] if proven.blocking_reasons else "OBJECTIVE_NOT_PROVEN"
                    result.detail = (
                        f"objective proof refused completion: {list(proven.blocking_reasons)}; "
                        f"integration={integration.result}"
                    )
                    self._emit_event(
                        cycle=cycle,
                        event_type="objective.final_verification_failed",
                        payload_refs={
                            "reason_code": result.reason_code,
                            "blocking_reasons": list(proven.blocking_reasons),
                            "integration_result": integration.result,
                            "proof_status": proven.result,
                        },
                    )
                else:
                    self._emit_event(
                        cycle=cycle,
                        event_type="objective.final_verified",
                        payload_refs={
                            "proof_status": proven.result,
                            "objective_status": "COMPLETED_FOR_REVIEW",
                            "integration_result": integration.result,
                            "contract_covered": True,
                            "steps_passed": True,
                        },
                    )
            else:
                objective_proof = evaluate_objective_proof(
                    all_mandatory_criteria_passed=(
                        result.execution_status in {"COMPLETED", "SUCCEEDED", "PASSED"}
                        and result.verification_status == "PASSED"
                        and gates_passed
                        and not result.remaining_asked_work
                        and not result.unmet_effects
                    ),
                    has_unresolved_regressions=result.verification_status != "PASSED",
                    is_snapshot_stale=False,
                    # Verification-only / read-only investigation may legitimately
                    # report real_operations=0 while still carrying evidence refs or
                    # status artifacts. Treat those as valid, not corrupted.
                    has_invalid_evidence=(
                        result.real_operations < 1
                        and not (result.evidence_refs or result.artifacts)
                    ),
                    # AIX prepares a reviewable result; WorkHub retains acceptance and
                    # lifecycle authority, so a sound proof lands at COMPLETED_FOR_REVIEW.
                    is_human_review_only=True,
                )
                if objective_proof.status in {"REPAIRABLE_FAILURE", "BLOCKED_FAILURE"}:
                    result.objective_status = "BLOCKED"
                    result.reason_code = objective_proof.blocking_reasons[0]
                    result.detail = (
                        f"objective proof refused completion: {list(objective_proof.blocking_reasons)}"
                    )
                    self._emit_event(
                        cycle=cycle,
                        event_type="objective.final_verification_failed",
                        payload_refs={
                            "reason_code": result.reason_code,
                            "blocking_reasons": list(objective_proof.blocking_reasons),
                            "proof_status": objective_proof.status,
                        },
                    )
                else:
                    self._emit_event(
                        cycle=cycle,
                        event_type="objective.final_verified",
                        payload_refs={
                            "proof_status": objective_proof.status,
                            "objective_status": "COMPLETED_FOR_REVIEW",
                            "mandatory_criteria_passed": True,
                            "unresolved_regressions": False,
                        },
                    )

        self._record_real_execute_in_context(obj=obj, result=result)
        self._emit_event(
            cycle=cycle,
            event_type="plan.generated",
            payload_refs={"status": result.planning_status},
        )
        # Real discovery/tool events captured during execution, so the client's event
        # stream shows real inspection/edit steps, not just a final "done" marker.
        for real_event in result.events:
            self._emit_event(
                cycle=cycle,
                event_type=str(real_event.get("event_type") or "tool.command_run"),
                payload_refs=real_event.get("payload_refs") or {},
            )
        outcome_event = (
            "cycle.real_execute_ok"
            if result.objective_status == "COMPLETED"
            else "cycle.real_execute_incomplete"
        )
        self._emit_event(
            cycle=cycle,
            event_type=outcome_event,
            payload_refs={
                "workspace": result.workspace,
                "workhub_loaded": result.workhub_loaded,
                "changed_source_file_count": len(result.changed_source_files),
                "objective_status": result.objective_status,
                "execution_status": result.execution_status,
                "verification_status": result.verification_status,
                "real_operations": result.real_operations,
                "reason_code": result.reason_code,
            },
        )
        if result.objective_status == "COMPLETED":
            plan_for_complete = self._active_plan_revision(obj.objective_id)
            runtime_for_complete = self._step_runtime.get(obj.objective_id) or {}
            mid_plan = (
                plan_for_complete is not None
                and not all_required_steps_passed(plan_for_complete, runtime_for_complete)
            )
            remaining_for_complete = (
                remaining_required_obligation_ids(
                    plan=plan_for_complete,
                    runtime=runtime_for_complete,
                    obligations=self._obligations.get(obj.objective_id, ()),
                )
                if plan_for_complete is not None
                else ()
            )
            if not mid_plan and not remaining_for_complete:
                self._emit_event(
                    cycle=cycle,
                    event_type="objective.completed",
                    payload_refs={"objective_status": result.objective_status},
                )
        elif result.objective_status == "AWAITING_REVIEW" or result.code == "PROPOSAL_READY":
            self._emit_event(
                cycle=cycle,
                event_type="objective.awaiting_review",
                payload_refs={
                    "objective_status": result.objective_status,
                    "code": result.code,
                    "workspace": result.workspace,
                },
            )
        else:
            if cycle.cancel_requested:
                if cycle.engine is not None and str(cycle.engine.state.phase) != "CANCELLED":
                    cycle.engine.signal_cancel(reason="EXECUTION_CANCELLED_MID_RUN")
                if cycle.engine is not None:
                    self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = "CANCELLED"
                cycle.last_error_detail = "operator requested cancellation"
                return {
                    "ok": False,
                    "operation": "real_execute",
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "phase": cycle.phase,
                    "terminal_reason": cycle.terminal_reason,
                    "real_execute": cycle.real_execute,
                    "error": {"code": "CANCELLED", "detail": cycle.last_error_detail},
                    "workhub_lifecycle_authority": False,
                    "aix_creates_workhub_tasks": False,
                }
            # AIX-CLOSED-LOOP-004 P1 -- canonical completion authority: before this
            # fix, this branch emitted `execution.blocked` / `verification.blocked` /
            # `objective.blocked` and then fell through to the function's
            # unconditional `return None` anyway -- the ONE signal `_control` treats as
            # "safe to call engine.continue_after_approval()", which always reaches
            # COMPLETED_FOR_REVIEW via stub `dispatch_step`/`verify_step`/
            # `build_review_packet` activities (see objective_cycle_workflow.py). That
            # let a genuinely blocked/incomplete `objective_status` (not COMPLETED, and
            # not the intentionally-terminal AWAITING_REVIEW/PROPOSAL_READY proposal
            # path handled above) reach completion anyway -- P0's documented
            # false-completion route #5. Fails closed: return an error payload instead
            # of falling through, and park the cycle in BLOCKED (the engine's existing
            # "needs a human/retry/replan decision" state) with a terminal_reason.
            self._emit_event(
                cycle=cycle,
                event_type="execution.blocked",
                payload_refs={"reason_code": result.reason_code or "NO_REAL_OPERATION_PERFORMED"},
            )
            self._emit_event(
                cycle=cycle,
                event_type="verification.blocked",
                payload_refs={"reason_code": result.reason_code or "VERIFICATION_STEP_MISSING"},
            )
            self._emit_event(
                cycle=cycle,
                event_type="objective.blocked",
                payload_refs={
                    "reason_code": result.reason_code or "NO_REAL_OPERATION_PERFORMED",
                    "objective_status": result.objective_status,
                },
            )
            terminal_reason = result.reason_code or f"OBJECTIVE_STATUS_{result.objective_status}"
            if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
                cycle.engine.wait_blocker()
                cycle.engine.signal_blocker_resolved(blocker_ref=terminal_reason, resolved=False)
                self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = terminal_reason
                await self._maybe_record_cycle_outcome(cycle=cycle)
            self._emit_event(
                cycle=cycle,
                event_type="cycle.completion_refused",
                payload_refs={
                    "objective_status": result.objective_status,
                    "reason_code": result.reason_code,
                    "terminal_reason": terminal_reason,
                },
            )
            return {
                "ok": False,
                "operation": "real_execute",
                "tenant_id": cycle.tenant_id,
                "cycle_id": cycle.cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
                "real_execute": cycle.real_execute,
                "error": {
                    "code": "OBJECTIVE_NOT_COMPLETE",
                    "detail": (
                        f"objective_status={result.objective_status!r} (reason_code="
                        f"{result.reason_code!r}) -- execution did not reach a "
                        "canonically complete or awaiting-review outcome; completion refused"
                    ),
                },
                "workhub_lifecycle_authority": False,
                "aix_creates_workhub_tasks": False,
            }
        # 001P-W13 / AIX-CH-001: mark-done failure fail-closes cycle control.
        # Do not raise (that 500s resume). Do not rewind mutation artifacts.
        mark_done_block = await self._maybe_mark_workhub_done(
            cycle=cycle, command_id=command_id, caller_auth=caller_auth
        )
        if mark_done_block is not None:
            return mark_done_block
        return None

    def _mark_done_fail_close_payload(
        self,
        *,
        cycle: _CycleRecord,
        operation: str,
        code: str,
        detail: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "operation": operation,
            "tenant_id": cycle.tenant_id,
            "cycle_id": cycle.cycle_id,
            "phase": cycle.phase,
            "terminal_reason": cycle.terminal_reason,
            "real_execute": cycle.real_execute,
            "close_path_blocked": True,
            "error": {"code": code, "detail": detail},
            "workhub_lifecycle_authority": bool(self.mark_workhub_done),
            "aix_marks_workhub_done": bool(self.mark_workhub_done),
            "exception": "001P-W13",
            "production_go": False,
        }

    async def _park_mark_done_fail_close(
        self,
        *,
        cycle: _CycleRecord,
        code: str,
        detail: str,
    ) -> dict[str, Any]:
        """Park verified-execute close failure as BLOCKED without discarding artifacts."""
        if cycle.engine is not None and str(cycle.engine.state.phase) == "RUNNING_STEP":
            cycle.engine.wait_blocker()
            cycle.engine.signal_blocker_resolved(blocker_ref=code, resolved=False)
            self._sync_cycle_phase_from_engine(cycle)
        else:
            if cycle.engine is not None and str(cycle.engine.state.phase) == 'RUNNING_STEP':
                cycle.engine.wait_blocker()
            cycle.dispatch_state = 'BLOCKED'
            self._sync_cycle_phase_from_engine(cycle)
        cycle.terminal_reason = code
        cycle.last_error_detail = detail[:800]
        # `_sync_cycle_phase_from_engine` overlays STEP_READY/EXECUTING/VERIFYING
        # onto cycle.phase. Close-path BLOCKED must not be rewritten as VERIFYING.
        if cycle.dispatch_state in {"STEP_READY", "NEXT_STEP_READY", "EXECUTING", "VERIFYING"}:
            cycle.dispatch_state = "STEP_PASSED"
        cycle.dispatch_required = False
        await self._maybe_record_cycle_outcome(cycle=cycle)
        return self._mark_done_fail_close_payload(
            cycle=cycle, operation="real_execute", code=code, detail=detail
        )

    async def _maybe_mark_workhub_done(
        self,
        *,
        cycle: _CycleRecord,
        command_id: str,
        caller_auth: str | None = None,
    ) -> dict[str, Any] | None:
        """001P-W13: after successful real execute, mark WorkHub task done when flagged.

        Close-path exceptions fail-close cycle control (BLOCKED) without raising and
        without rewinding already-produced `real_execute` artifacts.
        """
        if not self.mark_workhub_done:
            return
        if not cycle.real_execute or not cycle.real_execute.get("ok"):
            return
        if cycle.real_execute.get("workhub_mark_done") is not None:
            return
        # ``ok`` is an orchestration result, not proof that the requested work
        # happened.  WorkHub completion requires a real target operation and a
        # verified objective outcome.
        if (
            cycle.real_execute.get("objective_status") != "COMPLETED"
            or cycle.real_execute.get("execution_status") != "COMPLETED"
            or cycle.real_execute.get("verification_status") != "PASSED"
            or int(cycle.real_execute.get("real_operations") or 0) < 1
        ):
            cycle.real_execute["workhub_mark_done"] = {
                "ok": False,
                "code": cycle.real_execute.get("reason_code") or "NO_REAL_OPERATION_PERFORMED",
                "exception": "AIX-REAL-EXECUTION-001",
            }
            return
        # Required ordering for the real source-edit branch: RUNNING -> source edits ->
        # verification -> diff generated -> COMPLETED_FOR_REVIEW -> ... -> WorkHub
        # completion. `cycle.completion_gates` is only populated by that branch (the
        # legacy metadata-only demo path never sets it, and keeps its prior
        # `real_execute.ok`-only gate — it has no source edits or diff to gate on in
        # the first place). When gates ARE present, a not-yet-passed result means
        # implementation/verification/diff haven't actually finished — never mark
        # WorkHub done on a `real_execute.ok=True` alone in that case.
        if cycle.completion_gates is not None and not cycle.completion_gates.get("passed"):
            cycle.real_execute["workhub_mark_done"] = {
                "ok": False,
                "code": "COMPLETION_GATES_NOT_PASSED",
                "exception": "001P-W13",
            }
            return
        obj = self._objectives.get(cycle.objective_id)
        if obj is None:
            cycle.real_execute["workhub_mark_done"] = {
                "ok": False,
                "code": "OBJECTIVE_NOT_FOUND",
                "exception": "001P-W13",
            }
            return
        project_id = str(obj.project_id or "").strip()
        task_id = str(obj.workhub_task_id or "").strip()
        if not project_id or not task_id or is_workhub_task_id_placeholder(task_id):
            cycle.real_execute["workhub_mark_done"] = {
                "ok": False,
                "code": "WORKHUB_TASK_CONTEXT_MISSING",
                "exception": "001P-W13",
            }
            return

        evidence_refs = list(cycle.real_execute.get("evidence_refs") or [])
        artifacts = dict(cycle.real_execute.get("artifacts") or {})
        evidence_ref = (
            str(artifacts.get("execution_report") or "").strip()
            or (evidence_refs[0] if evidence_refs else "")
            or f"aix://cycles/{cycle.cycle_id}/evidence"
        )
        artifact_ref = (
            str(artifacts.get("proposed_html") or artifacts.get("proposed_tsx") or "").strip()
            or evidence_ref
        )
        try:
            client = build_workhub_client(self._settings, bearer_authorization=caller_auth)
            mark = await mark_workhub_task_done(
                client=client,
                project_id=project_id,
                task_id=task_id,
                cycle_id=cycle.cycle_id,
                evidence_ref=evidence_ref,
                artifact_ref=artifact_ref,
                evidence_json={
                    "workspace": cycle.real_execute.get("workspace"),
                    "evidence_refs": evidence_refs,
                    "artifacts": artifacts,
                    "token_usage": cycle.real_execute.get("token_usage"),
                    "command_id": command_id,
                },
                reason=f"AIX cycle {cycle.cycle_id} completed successfully",
                tenant_id=cycle.tenant_id,
                task_number=obj.task_number,
            )
        except MarkWorkHubDoneError as exc:
            self._emit_event(
                cycle=cycle,
                event_type="cycle.mark_workhub_done_failed",
                payload_refs={"code": exc.code, "detail": exc.detail},
            )
            cycle.real_execute["workhub_mark_done"] = {
                "ok": False,
                "code": exc.code,
                "detail": exc.detail,
                "exception": "001P-W13",
            }
            return await self._park_mark_done_fail_close(
                cycle=cycle, code=exc.code, detail=exc.detail
            )
        except Exception as exc:  # noqa: BLE001 - never 500 resume after successful execute
            logger.warning("workhub_mark_done_unexpected_error", exc_info=exc)
            detail = f"{type(exc).__name__}:{exc}"[:400]
            self._emit_event(
                cycle=cycle,
                event_type="cycle.mark_workhub_done_failed",
                payload_refs={"code": "WORKHUB_MARK_DONE_UNEXPECTED", "detail": detail},
            )
            cycle.real_execute["workhub_mark_done"] = {
                "ok": False,
                "code": "WORKHUB_MARK_DONE_UNEXPECTED",
                "detail": detail,
                "exception": "001P-W13",
            }
            return await self._park_mark_done_fail_close(
                cycle=cycle, code="WORKHUB_MARK_DONE_UNEXPECTED", detail=detail
            )

        payload = {
            "ok": mark.ok,
            "code": mark.code,
            "task_id": mark.task_id,
            "from_status": mark.from_status,
            "to_status": mark.to_status,
            "steps": list(mark.steps),
            "detail": mark.detail,
            "exception": "001P-W13",
        }
        cycle.real_execute["workhub_mark_done"] = payload
        cycle.events.append({"event": "cycle.mark_workhub_done_ok", "at": _utc(), **payload})
        return

    def _active_plan_revision(self, objective_id: str) -> PlanRevisionV1 | None:
        """AIX-MPE-003 Packet MPE-01: the most recently admitted `PlanRevisionV1` for
        this objective, or None if no plan has been admitted yet. The last element of
        the revision list is always the current plan -- a replan appends a new
        revision, it never mutates or removes an earlier one."""
        revisions = self._plan_revisions.get(objective_id)
        return revisions[-1] if revisions else None

    def _admit_plan_revision(self, plan: PlanRevisionV1) -> None:
        """AIX-MPE-003 Packet MPE-01: append a new, already-validated `PlanRevisionV1`
        to this objective's revision history. `PlanRevisionV1`'s own validators
        already enforce unique step ids, valid dependency references and acyclicity;
        this method only owns storage, matching "AIX -- not the LLM -- stores and
        owns the plan" (the sprint's own non-negotiable design principle)."""
        self._plan_revisions.setdefault(plan.objective_id, []).append(plan)
        self._maybe_project_work(plan)
        self._maybe_compile_f10(plan)
        self._maybe_project_scale(plan)

    def _maybe_project_work(self, plan: PlanRevisionV1) -> None:
        """F1: project Objective/Outcome/StepContract → Work when flag on (additive)."""
        if not bool(getattr(self._framework_flags, "work_adapters", False)):
            # F10/F11 still need Work input when compiler/multi_agent/scale flags on.
            if not (
                bool(getattr(self._framework_flags, "compiler", False))
                or bool(getattr(self._framework_flags, "multi_agent", False))
                or bool(getattr(self._framework_flags, "scale", False))
            ):
                return
        from backend.aix_core.framework.work.adapters import WorkDomainAdapter

        envelope = self._envelopes.get(plan.objective_id)
        if envelope is None:
            return
        outcome = self._outcome_contracts.get(plan.objective_id)
        work = WorkDomainAdapter.work_from_envelope(
            envelope,
            outcome,
            plan=plan,
        )
        self._works[plan.objective_id] = work

    def _maybe_compile_f10(self, plan: PlanRevisionV1) -> None:
        """F10: compile WorkGraph + assignments (+ multi-agent session) when flags on."""
        compiler_on = bool(getattr(self._framework_flags, "compiler", False))
        multi_on = bool(getattr(self._framework_flags, "multi_agent", False))
        if not compiler_on and not multi_on:
            return
        from backend.aix_core.framework.compiler.runtime_bridge import (
            compile_from_work_and_plan,
            ensure_work_from_canonical,
        )

        envelope = self._envelopes.get(plan.objective_id)
        if envelope is None:
            return
        outcome = self._outcome_contracts.get(plan.objective_id)
        work = ensure_work_from_canonical(
            envelope=envelope,
            plan=plan,
            outcome=outcome,
            existing=self._works.get(plan.objective_id),
        )
        self._works[plan.objective_id] = work
        # AssignStage may call GPR — bind_models only when multi_agent or compiler needs routes
        result = compile_from_work_and_plan(
            work=work,
            plan=plan,
            outcome=outcome,
            bind_models=True,
        )
        self._works[plan.objective_id] = result.work
        self._f10_compiles[plan.objective_id] = result
        if multi_on:
            from backend.aix_core.framework.multi_agent.protocol import MultiAgentProtocol

            protocol = MultiAgentProtocol()
            session = protocol.open_session(result.assigned)
            if session.collisions:
                raise ValueError(
                    "F10 multi-agent: unmanaged parallel scope collisions in WorkGraph: "
                    f"{session.collisions!r}"
                )
            self._f10_sessions[plan.objective_id] = {
                "protocol": protocol,
                "session": session,
            }

    def _maybe_project_scale(self, plan: PlanRevisionV1) -> None:
        """F11: project WorkGraph into store + open scale session when flag on."""
        if not bool(getattr(self._framework_flags, "scale", False)):
            return
        from backend.aix_core.framework.scale.runtime_bridge import open_scale_session
        from backend.aix_core.framework.scale.work_graph_store import WorkGraphStore
        from backend.aix_core.framework.work.models import WorkGraph

        if self._work_graph_store is None:
            self._work_graph_store = WorkGraphStore()

        existing_graph: WorkGraph | None = None
        compiled = self._f10_compiles.get(plan.objective_id)
        if compiled is not None and getattr(compiled, "work_graph", None) is not None:
            existing_graph = compiled.work_graph

        def _ckpt_append(**kwargs: Any) -> str:
            decision_state = dict(kwargs.get("decision_state") or {})
            checkpoint_id = f"chk_hib_{plan.objective_id}_{len(self._framework_checkpoints) + 1}"
            self._framework_checkpoints.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "cycle_id": kwargs.get("cycle_id"),
                    "graph_id": kwargs.get("graph_id"),
                    "reason": kwargs.get("reason"),
                    "decision_state": decision_state,
                }
            )
            return checkpoint_id

        session = open_scale_session(
            plan,
            store=self._work_graph_store,
            graph=existing_graph,
            checkpoint_append=_ckpt_append,
        )
        self._scale_sessions[plan.objective_id] = session

    def _select_next_step_governed(
        self,
        plan: PlanRevisionV1,
        runtime: dict[str, StepRuntimeStateV1],
    ):
        """Use step_scheduler; when scale on, apply lock-aware multi-lane adapter."""
        if not bool(getattr(self._framework_flags, "scale", False)):
            return select_next_step(plan, runtime)
        session = self._scale_sessions.get(plan.objective_id)
        if session is None:
            return select_next_step(plan, runtime)
        from backend.aix_core.framework.scale.batch_scheduler import select_next_step_via_scale

        return select_next_step_via_scale(
            plan,
            runtime,
            store=session.store,
            graph_id=session.graph.graph_id,
            scheduler=session.scheduler,
        )

    def suspend_scale_cycle(
        self,
        *,
        objective_id: str,
        cycle_id: str,
        reason: str = "SCHEDULED_HIBERNATION",
        simulated_elapsed_hours: float = 24.0,
    ) -> Any:
        """F11-P3: durable hibernate via scale session (F6 checkpoint hook)."""
        session = self._scale_sessions.get(objective_id)
        if session is None:
            raise KeyError(f"no scale session for objective {objective_id}")
        return session.hibernation.suspend_cycle(
            cycle_id,
            session.graph.graph_id,
            reason=reason,
            simulated_elapsed_hours=simulated_elapsed_hours,
        )

    def resume_scale_cycle(self, *, objective_id: str, cycle_id: str) -> Any:
        """F11-P3: idempotent resume from durable hibernation record."""
        session = self._scale_sessions.get(objective_id)
        if session is None:
            raise KeyError(f"no scale session for objective {objective_id}")
        return session.hibernation.resume_cycle(cycle_id)

    def _append_framework_checkpoint(
        self,
        *,
        cycle: "_CycleRecord",
        obj: "_ObjectiveRecord",
        step_id: str,
        attempt_id: str,
        reason: str,
    ) -> None:
        """F6: mirror lifecycle checkpoint into CheckpointEventV1 (additive, non-blocking)."""
        from datetime import UTC, datetime

        from backend.aix_core.framework.recovery.checkpoint import (
            CHECKPOINT_SCHEMA_VERSION,
            CheckpointEventV1,
        )

        recorded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        checkpoint_id = f"chk_{cycle.cycle_id}_{len(self._framework_checkpoints) + 1}"
        event_sequence = len(self._framework_checkpoints) + 1
        command_id = f"{cycle.cycle_id}:checkpoint:{reason}:{event_sequence}"
        workflow = {"phase": str(cycle.phase), "engine_version": 1}
        dispatcher = {"state": str(cycle.phase), "step_id": step_id, "claim_id": ""}
        plan = {"plan_id": "", "revision": 0}
        attempt = {"attempt_id": attempt_id, "outcome": "IN_PROGRESS"} if attempt_id else None
        budgets = {
            "retries_remaining": int(getattr(cycle, "retry_budget_remaining", 0) or 0),
            "replans_remaining": int(getattr(cycle, "replan_budget_remaining", 0) or 0),
        }
        decision_state = {"cursor": step_id, "terminal": False}
        evidence_refs: tuple[str, ...] = ()
        state_hash = CheckpointEventV1.compute_state_hash(
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            checkpoint_id=checkpoint_id,
            tenant_id=cycle.tenant_id,
            objective_id=obj.objective_id,
            cycle_id=cycle.cycle_id,
            event_sequence=event_sequence,
            command_id=command_id,
            reason=reason,
            recorded_at=recorded_at,
            workflow=workflow,
            dispatcher=dispatcher,
            plan=plan,
            attempt=attempt,
            budgets=budgets,
            approval=None,
            evidence_refs=evidence_refs,
            decision_state=decision_state,
        )
        event = CheckpointEventV1(
            checkpoint_id=checkpoint_id,
            tenant_id=cycle.tenant_id,
            objective_id=obj.objective_id,
            cycle_id=cycle.cycle_id,
            event_sequence=event_sequence,
            command_id=command_id,
            reason=reason,  # type: ignore[arg-type]
            recorded_at=recorded_at,
            workflow=workflow,
            dispatcher=dispatcher,
            plan=plan,
            attempt=attempt,
            budgets=budgets,
            approval=None,
            evidence_refs=evidence_refs,
            decision_state=decision_state,
            state_hash=state_hash,
        )
        self._framework_checkpoints.append(event)

    def _persist_accepted_plan_observability(
        self,
        *,
        cycle: "_CycleRecord",
        obj: "_ObjectiveRecord",
        accepted: PlanRevisionV1,
        proposal: Any,
        understanding: Any,
    ) -> None:
        """Write plan.md + debug JSON beside cycle evidence. Observability only."""
        from backend.aix_core.runtime.objective.objective_scope import (
            render_plan_markdown,
            selected_execution_lane,
            validate_objective_scope_coverage,
        )

        try:
            workspace = Path(
                str((cycle.real_execute or {}).get("workspace") or "")
            )
            if not workspace.is_dir():
                # Pre-execute: persist under a cycle-scoped observability folder.
                base = Path(getattr(self._settings, "OBJECTIVE_CYCLE_WORKSPACE_ROOT", "") or "")
                if not str(base):
                    base = Path(".tmp") / "aix-cycle-workspaces"
                workspace = (
                    base
                    / str(cycle.tenant_id)
                    / str(cycle.created_at)[:10]
                    / cycle.cycle_id
                    / "plan"
                )
            evidence_dir = workspace / "evidence" if (workspace / "proposed").exists() or workspace.name.startswith("att_") else workspace
            evidence_dir.mkdir(parents=True, exist_ok=True)

            original = self._intent_text(obj)
            obligations = self._obligations.get(obj.objective_id, ())
            coverage = validate_objective_scope_coverage(
                original_objective=original,
                plan_steps=accepted.steps,
            )
            demo_ok = self._demo_capability_from_accepted_plan(obj)
            lane = selected_execution_lane(
                original_objective=original,
                plan_steps=accepted.steps,
                demo_requested=demo_ok,
            )
            understanding_rev = self._provenance.latest_understanding(obj.objective_id)
            understanding_summary = ""
            if understanding_rev is not None:
                understanding_summary = "; ".join(
                    f"{c.kind}:{c.text}" for c in understanding_rev.claims if c.status == "ACTIVE"
                )[:800]
            elif understanding is not None:
                understanding_summary = str(getattr(understanding, "observed_problem", "") or "")
            constraints = self._planner_constraints(obj, self._envelopes.get(obj.objective_id))
            plan_md = render_plan_markdown(
                original_objective=original,
                understanding_summary=understanding_summary,
                obligations=obligations,
                constraints=constraints,
                plan=accepted,
                execution_lane=lane,
                warnings=() if coverage.ok else (coverage.detail,),
                coverage=coverage,
            )
            prov_plan = self._provenance.latest_plan(obj.objective_id)
            intent = self._provenance.get_intent(obj.objective_id)
            extra_lines = ["", "## Provenance", ""]
            extra_lines.append(f"- intent_id: `{intent.id if intent is not None else ''}`")
            extra_lines.append(f"- understanding_revision_id: `{understanding_rev.id if understanding_rev is not None else ''}`")
            extra_lines.append(f"- plan_id: `{prov_plan.plan_id if prov_plan is not None else accepted.plan_id}`")
            extra_lines.append(f"- approval_ref: `{prov_plan.approval_ref if prov_plan is not None else ''}`")
            extra_lines.append(
                "- evidence_ids: "
                + (", ".join(f"`{eid}`" for eid in (prov_plan.evidence_ids if prov_plan is not None else ())) or "_(none)_")
            )
            extra_lines.append(
                "- plan_targets: "
                + (
                    ", ".join(f"`{t.path}` ({t.kind}/{t.effect})" for t in (prov_plan.targets if prov_plan is not None else ()))
                    or "_(none)_"
                )
            )
            plan_md = plan_md.rstrip() + "\n" + "\n".join(extra_lines) + "\n"
            (evidence_dir / "plan.md").write_text(plan_md, encoding="utf-8")
            _write_json = None
            try:
                from backend.aix_core.runtime.executor.real_execute import _write_json as _wj

                _write_json = _wj
            except Exception:
                def _write_json(path: Path, payload: dict[str, Any]) -> None:  # type: ignore[misc]
                    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            try:
                from backend.aix_core.runtime.objective.discuss_store import persist_session_plan

                persist_session_plan(
                    tenant_id=str(cycle.tenant_id),
                    thread_id=obj.conversation_id,
                    plan_md=plan_md,
                    objective={
                        "thread_id": obj.conversation_id,
                        "tenant_id": str(cycle.tenant_id),
                        "objective_id": obj.objective_id,
                        "cycle_id": cycle.cycle_id,
                        "normalized_objective": original,
                        "task_class": obj.task_class,
                        "status": "ACCEPTED",
                    },
                )
            except Exception:
                pass

            _write_json(
                evidence_dir / "obligations.json",
                {"obligations": [o.model_dump(mode="json") for o in obligations]},
            )
            _write_json(
                evidence_dir / "step-contracts.json",
                {"steps": [s.model_dump(mode="json") for s in accepted.steps]},
            )
            planner_call = dict(getattr(proposal, "planner_call", None) or {})
            if planner_call:
                _write_json(evidence_dir / "planner-output.json", planner_call)
                evidence_payload = {
                    "objective": original,
                    "definition_of_done": list(obj.definition_of_done),
                    "observed_problem": understanding_summary,
                    "obligations": [
                        {"id": o.id, "description": o.description} for o in obligations
                    ],
                    "constraints": constraints,
                }
                _write_json(evidence_dir / "planner-input.json", evidence_payload)
        except Exception as exc:
            logger.warning(
                "accepted_plan_observability_failed cycle=%s err=%s",
                cycle.cycle_id,
                exc,
            )

    def _clear_plan_cache_for_replan(
        self, objective_id: str, *, failed_step_id: str | None = None,
    ) -> None:
        """AIX-MPE-003 Packet MPE-12: freeze PASSed runtime, supersede the rest,
        and drop the hot-path StepContract cache so the planner runs again.

        When *failed_step_id* is provided and a live plan exists, uses DAG-aware
        incremental replan (P7): only the failed step and its transitive dependents
        are superseded, preserving verified work. Falls back to blanket supersession
        when no plan or failed step is available.

        Only clears `_step_contracts` when a live planner PlanRevision exists.
        Seeded-step-contract bootstrap wraps (tests / empty-directory) keep the
        cached contract so REPAIR/REUNDERSTAND/REPLAN can re-dispatch it.
        """
        runtime = self._step_runtime.get(objective_id) or {}
        plan = self._active_plan_revision(objective_id)

        if failed_step_id and plan is not None and plan.steps:
            to_supersede = {failed_step_id} | find_transitive_dependents(
                failed_step_id, plan.steps,
            )
            for sid, st in list(runtime.items()):
                if sid in to_supersede and st.status not in ("PASS", "SUPERSEDED"):
                    runtime[sid] = replace_runtime_status(st, status="SUPERSEDED")
        else:
            for sid, st in list(runtime.items()):
                if st.status not in ("PASS", "SUPERSEDED"):
                    runtime[sid] = replace_runtime_status(st, status="SUPERSEDED")

        if plan is not None and str(plan.planner_reasoning_ref or "") != "seeded-step-contract":
            self._step_contracts.pop(objective_id, None)
            self._step_contract_errors.pop(objective_id, None)
        else:
            # Seeded contract is retained for re-dispatch. Runtime may have marked the
            # same step id SUPERSEDED/FAILED/REPAIR during the just-finished attempt;
            # reset it to PENDING so observe_accepted_plan can bind it again before
            # STEP_READY (never emit ready with a null step after replan).
            seeded = self._step_contracts.get(objective_id)
            if seeded is not None:
                prior = runtime.get(seeded.id)
                if prior is not None and prior.status not in {"PASS"}:
                    runtime[seeded.id] = replace_runtime_status(
                        prior, status="PENDING", blocked_reason=None
                    )
                self._step_runtime[objective_id] = runtime

    def _planner_constraints(self, obj: "_ObjectiveRecord", envelope: object | None) -> list[str]:
        """Governance forbidden effects only. allowed_files is not planner authority (K7)."""
        out: list[str] = []
        if envelope is not None:
            for effect in getattr(envelope, "forbidden_effects", ()) or ():
                out.append(f"forbidden effect: {effect}")
        gov = self._provenance.latest_governance(obj.objective_id)
        if gov is not None:
            for effect in gov.forbidden_effects:
                line = f"forbidden effect: {effect}"
                if line not in out:
                    out.append(line)
        return out

    def _objective_text(self, obj: "_ObjectiveRecord") -> str:
        return str(obj.original_objective or obj.objective or "").strip()

    def _require_outcome_contract_for(self, obj: "_ObjectiveRecord") -> bool:
        return requires_outcome_contract(
            objective=self._objective_text(obj),
            task_class=str(obj.task_class or ""),
            settings=self._settings,
            contract_already_present=obj.objective_id in self._outcome_contracts,
        )

    async def _ensure_outcome_contract(
        self,
        *,
        obj: "_ObjectiveRecord",
        cycle: "_CycleRecord",
        actor_id: str = "",
        caller_auth: str = "",
        use_llm: bool = True,
    ) -> OutcomeContractProposal | None:
        """Propose → validate → seal OutcomeContract when required or absent.

        Returns None when a contract is not required and none exists (legacy path).
        Never treats an unsealed proposal as authoritative.
        """
        existing = self._outcome_contracts.get(obj.objective_id)
        if existing is not None:
            self._compile_and_store_outcome_obligations(obj.objective_id, existing)
            return OutcomeContractProposal(
                ok=True,
                code="CONTRACT_ALREADY_SEALED",
                detail="sealed OutcomeContractV1 already present",
                contract=existing,
                source="heuristic",
            )
        if not self._require_outcome_contract_for(obj):
            return None
        objective = self._objective_text(obj)
        common = dict(
            objective_id=obj.objective_id,
            objective=objective,
            definition_of_done=tuple(obj.definition_of_done or ()),
            tenant_id=cycle.tenant_id,
            actor_id=actor_id,
            authorization_header=caller_auth or "",
        )
        # Product-class: seal from heuristic first so plan-prep cannot block
        # GET /cycles on a live OutcomeContract LLM call.
        proposal: OutcomeContractProposal | None = None
        if is_product_class_objective(
            objective=objective, task_class=str(obj.task_class or "")
        ):
            proposal = await propose_and_seal_outcome_contract(
                **common,
                use_llm=False,
            )
            if not proposal.ok and use_llm:
                proposal = await propose_and_seal_outcome_contract(
                    **common,
                    use_llm=True,
                )
        else:
            proposal = await propose_and_seal_outcome_contract(
                **common,
                use_llm=use_llm,
            )
        if proposal is None:
            return None
        self._outcome_contract_proposals[obj.objective_id] = {
            "ok": proposal.ok,
            "code": proposal.code,
            "detail": proposal.detail,
            "source": proposal.source,
            "contract_id": (
                proposal.contract.contract_id if proposal.contract is not None else None
            ),
        }
        if proposal.ok and proposal.contract is not None:
            self._outcome_contracts[obj.objective_id] = proposal.contract
            self._compile_and_store_outcome_obligations(obj.objective_id, proposal.contract)
            self._emit_event(
                cycle=cycle,
                event_type="outcome_contract.sealed",
                payload_refs={
                    "contract_id": proposal.contract.contract_id,
                    "contract_hash": proposal.contract.contract_hash,
                    "source": proposal.source,
                    "code": proposal.code,
                },
            )
        else:
            self._emit_event(
                cycle=cycle,
                event_type="outcome_contract.rejected",
                payload_refs={
                    "code": proposal.code,
                    "detail": proposal.detail[:500],
                    "source": proposal.source,
                },
            )
        return proposal

    def _compile_and_store_outcome_obligations(
        self, objective_id: str, contract: object
    ) -> tuple[OutcomeContractObligationV1, ...]:
        """DoD-002A/C: compile sealed contract → store mandatory obligations."""
        compiled = compile_outcome_obligations(contract)  # type: ignore[arg-type]
        if not compiled.ok:
            self._outcome_contract_obligations.pop(objective_id, None)
            return ()
        self._outcome_contract_obligations[objective_id] = compiled.obligations
        return compiled.obligations

    def _planner_outcome_contract_kwargs(self, obj: "_ObjectiveRecord") -> dict[str, Any]:
        """DoD-002C: kwargs sealed contract + compiled obligations into propose_step_contract."""
        contract = self._outcome_contracts.get(obj.objective_id)
        obligations = self._outcome_contract_obligations.get(obj.objective_id)
        if contract is not None and not obligations:
            obligations = self._compile_and_store_outcome_obligations(obj.objective_id, contract)
        if contract is None:
            return {}
        return {
            "outcome_contract": contract,
            "outcome_contract_obligations": obligations or (),
        }

    def _record_planner_trace(self, objective_id: str, payload: dict[str, Any]) -> None:
        self._planner_trace.setdefault(objective_id, []).append(dict(payload))

    def _planner_evidence_revision_ref(self, obj: "_ObjectiveRecord", understanding: UnderstandingEvidenceV1 | None) -> str | None:
        revisions = self._evidence_revisions.get(obj.objective_id) or []
        if revisions:
            last = revisions[-1]
            return str(last.get("evidence_id") or last.get("revision") or "")
        if understanding is not None:
            return str(understanding.id)
        return None

    def _link_planner_call_to_plan(self, objective_id: str, plan_id: str) -> None:
        calls = self._planner_calls.get(objective_id) or []
        if not calls:
            return
        last = dict(calls[-1])
        last["resulting_plan_revision_id"] = plan_id
        calls[-1] = last

    async def _capture_planner_proposal(
        self,
        *,
        cycle: "_CycleRecord",
        obj: "_ObjectiveRecord",
        proposal: Any,
        persist_cmd: str,
    ) -> None:
        artifact = dict(getattr(proposal, "planner_call", None) or {})
        if not artifact:
            return
        artifact.setdefault(
            "transformations",
            [dict(item) for item in (getattr(proposal, "transformations", ()) or ())],
        )
        if getattr(proposal, "budget_report", None):
            artifact.setdefault("budget", dict(proposal.budget_report))
        self._planner_calls.setdefault(obj.objective_id, []).append(artifact)
        try:
            record = self._evidence.put(
                tenant_id=cycle.tenant_id,
                run_id=cycle.cycle_id,
                payload=artifact,
                evidence_id=str(artifact.get("planner_call_id") or "")[:32] or None,
            )
            artifact["evidence_uri"] = record.uri
        except Exception:
            pass
        await self._persist_event(
            tenant_id=cycle.tenant_id,
            aggregate_type="cycle",
            aggregate_id=cycle.cycle_id,
            expected_sequence_number=1,
            event_type="planner.call_recorded",
            command_id=f"{persist_cmd}:planner_call:{artifact.get('planner_call_id')}",
            payload={
                "planner_call_id": artifact.get("planner_call_id"),
                "prompt_hash": artifact.get("prompt_hash"),
                "model": artifact.get("model"),
                "provider": artifact.get("provider"),
                "parser_version": artifact.get("parser_version"),
                "estimated_input_tokens": artifact.get("estimated_input_tokens"),
                "configured_input_cap": artifact.get("configured_input_cap"),
                "evidence_included": artifact.get("evidence_included"),
                "evidence_compacted": artifact.get("evidence_compacted"),
                "evidence_dropped": artifact.get("evidence_dropped"),
                "raw_response": artifact.get("raw_response"),
                "transformations": artifact.get("transformations"),
                "resulting_plan_revision_id": artifact.get("resulting_plan_revision_id"),
            },
        )
        self._emit_event(
            cycle=cycle,
            event_type="planner.call_recorded",
            payload_refs={
                "planner_call_id": artifact.get("planner_call_id"),
                "prompt_hash": artifact.get("prompt_hash"),
            },
        )

    @staticmethod
    def _planner_evidence_search_queries(*, query: str, kind: str) -> tuple[str, ...]:
        """Bounded search variants for natural-language planner evidence requests."""
        q = str(query or "").strip()
        if query_looks_unbounded(q):
            return ()
        queries: list[str] = []
        if q:
            queries.append(q)
        if kind == "related_tests":
            queries.append(f"{q} test")
        elif kind == "related_routes":
            queries.append(f"{q} route endpoint router")
        for token in _objective_keywords(q):
            if token not in queries:
                queries.append(token)
        if kind in {"find_symbol", "symbol_references"}:
            parts = q.split()
            if parts:
                tail = parts[-1][:80]
                if len(tail) >= 3 and tail not in queries:
                    queries.append(tail)
        return tuple(dict.fromkeys(item for item in queries if item))[:8]

    async def _read_repository_paths_into_context(
        self,
        *,
        executor: ContextToolExecutor,
        toolbox: RepositoryToolbox,
        paths: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        read: list[str] = []
        already = {r.path for r in executor.context.files_read}
        for path in paths[:8]:
            relative = str(path or "").strip().replace("\\", "/")
            if not relative:
                continue
            if relative in already:
                read.append(relative)
                continue
            try:
                result = await asyncio.to_thread(toolbox.read_file, relative)
            except (RepositoryToolError, ReadFileError, OSError):
                continue
            try:
                executor.call(
                    "filesystem.read_file",
                    lambda **_: result,
                    arguments={"path": relative},
                )
            except DuplicateToolCallError:
                read.append(relative)
                continue
            read.append(relative)
        return tuple(read)

    def _reconcile_understanding_from_exploration_evidence(
        self,
        obj: "_ObjectiveRecord",
    ) -> None:
        """Supersede ACTIVE MISSING_INFO when typed ExplorationEvidence already exists."""
        understanding = self._provenance.latest_understanding(obj.objective_id)
        intent = self._provenance.get_intent(obj.objective_id)
        if understanding is None or intent is None:
            return
        if not any(
            claim.kind == "MISSING_INFO" and claim.status == "ACTIVE"
            for claim in understanding.claims
        ):
            return
        evidence = self._provenance.evidence(obj.objective_id)
        if not evidence:
            return
        needs = self._provenance.needs(obj.objective_id)
        if not needs:
            minted = needs_from_understanding(understanding, intent=intent)
            if not minted:
                return
            needs = minted
            for need in needs:
                self._provenance.put_need(need)
        result = build_understanding_v2(
            intent=intent,
            parent=understanding,
            evidence=evidence,
            needs=needs,
            turns=self._provenance.turns(obj.objective_id),
            governance=self._provenance.latest_governance(obj.objective_id),
        )
        if result.validation.ok and result.decision != "REJECT":
            self._provenance.put_understanding(result.revision)

    def _ensure_exploration_evidence_for_paths(
        self,
        *,
        obj: "_ObjectiveRecord",
        paths: tuple[str, ...],
    ) -> None:
        """Persist typed ExplorationEvidence for repository reads (P1 compatibility)."""
        if not paths:
            return
        understanding = self._provenance.latest_understanding(obj.objective_id)
        intent = self._provenance.get_intent(obj.objective_id)
        if understanding is None or intent is None:
            return
        existing = {
            str(item.location or "").replace("\\", "/")
            for item in self._provenance.evidence(obj.objective_id)
        }
        from backend.aix_core.runtime.objective.provenance.validators import validate_need

        def _missing_info_claims(*, active_only: bool) -> list[Any]:
            rows = [c for c in understanding.claims if c.kind == "MISSING_INFO"]
            if active_only:
                rows = [c for c in rows if c.status == "ACTIVE"]
            return rows

        def _need_cites_missing_info(need: ExplorationNeed) -> bool:
            return any(
                c.id in need.claim_ids and c.kind == "MISSING_INFO"
                for c in understanding.claims
            )

        need = next(
            (item for item in reversed(self._provenance.needs(obj.objective_id))),
            None,
        )
        if need is not None:
            check = validate_need(need=need, understanding=understanding)
            if not check.ok and not _need_cites_missing_info(need):
                need = None
        if need is None:
            minted = needs_from_understanding(understanding, intent=intent)
            if minted:
                need = minted[0]
                self._provenance.put_need(need)
            else:
                missing = _missing_info_claims(active_only=True)
                if not missing:
                    missing = _missing_info_claims(active_only=False)
                if not paths:
                    return
                if not missing:
                    # Admission bootstrap: mint ACTIVE MISSING_INFO so P1 evidence chain
                    # (Need ← MISSING_INFO) can ACCEPT EXISTING PlanTargets.
                    from backend.aix_core.runtime.objective.provenance.contracts import (
                        ClaimCite,
                        UnderstandingClaim,
                    )

                    outcome = next(
                        (
                            c
                            for c in understanding.claims
                            if c.kind == "OUTCOME" and c.status == "ACTIVE"
                        ),
                        None,
                    )
                    cite = (
                        ClaimCite(kind="CLAIM", ref=outcome.id)
                        if outcome is not None
                        else ClaimCite(kind="INTENT_SPAN", ref=intent.id)
                    )
                    minted_missing = UnderstandingClaim(
                        kind="MISSING_INFO",
                        text="Where are the admission-scoped files implemented?",
                        standing="ASSERTED",
                        status="ACTIVE",
                        cites=(cite,),
                    )
                    understanding = understanding.model_copy(
                        update={
                            "id": new_ulid(),
                            "claims": tuple(understanding.claims) + (minted_missing,),
                        }
                    )
                    self._provenance.put_understanding(understanding)
                    missing = [minted_missing]
                claim = missing[-1]
                need = ExplorationNeed(
                    objective_id=obj.objective_id,
                    understanding_revision_id=understanding.id,
                    claim_ids=(claim.id,),
                    purpose=f"Answer MISSING_INFO: {claim.text}",
                    question=str(intent.text or claim.text)[:240],
                    source="repository",
                    result_type="file_content",
                    budget_files=max(8, len(paths)),
                )
                self._provenance.put_need(need)
        missing_info_active = bool(_missing_info_claims(active_only=True))
        for path in paths:
            norm = str(path or "").strip().replace("\\", "/")
            if not norm or norm in existing:
                continue
            hit = ExploreHit(
                location=norm,
                observation=f"Repository read grounded planner evidence for {norm}",
                exists=True,
                tool="read_file",
                query=norm,
            )
            if missing_info_active and validate_need(need=need, understanding=understanding).ok:
                evidence_items = execute_need(
                    need, understanding=understanding, collect=lambda _need, _hit=hit: (_hit,)
                )
            else:
                # Stale or superseded MISSING_INFO: persist via Need id without N1 gate.
                evidence_items = evidence_from_hits(need, (hit,))
            for item in evidence_items:
                try:
                    self._provenance.put_evidence(item)
                    existing.add(norm)
                except IntentFrozenError:
                    continue
        self._reconcile_understanding_from_exploration_evidence(obj)

    async def _sync_admission_reads_into_context(
        self,
        *,
        cycle: _CycleRecord,
        obj: "_ObjectiveRecord",
        context: ExecutionContext,
        repository_root: Path,
    ) -> None:
        """Ground ``context.files_read`` from admission hints without new ExplorationEvidence."""
        executor = ContextToolExecutor(context=context)
        toolbox = RepositoryToolbox(repository_root=repository_root, on_event=lambda *_a, **_k: None)
        paths = tuple(
            str(p or "").strip().replace("\\", "/")
            for p in obj.allowed_files
            if str(p or "").strip()
        )
        if paths:
            await self._read_repository_paths_into_context(
                executor=executor,
                toolbox=toolbox,
                paths=paths,
            )
        obj.execution_context = context.to_dict()
        und = self._understanding_evidence.get(obj.objective_id)
        if und is not None:
            read_paths = tuple(record.path for record in context.files_read)
            merged = tuple(dict.fromkeys([*read_paths, *paths]))
            if merged:
                self._understanding_evidence[obj.objective_id] = und.model_copy(
                    update={"files_read": merged}
                )

    async def _bootstrap_repository_grounding_for_planner(
        self,
        *,
        cycle: _CycleRecord,
        obj: "_ObjectiveRecord",
        context: ExecutionContext,
        repository_root: Path,
    ) -> UnderstandingEvidenceV1:
        """Collect bounded repository reads when planner evidence lacks files_read.

        ``allowed_files`` are admission hints for evidence collection only (P6), not
        execute authority.
        """
        executor = ContextToolExecutor(context=context)
        toolbox = RepositoryToolbox(repository_root=repository_root, on_event=lambda *_a, **_k: None)
        paths: list[str] = []
        for rel in obj.allowed_files:
            norm = str(rel or "").strip().replace("\\", "/")
            if norm and norm not in paths:
                paths.append(norm)
        objective_text = str(obj.original_objective or obj.objective or "")
        for rel in resolve_topic_hints_in_repository(
            repository_root,
            extract_topic_snake_hints(objective_text),
            objective=objective_text,
            max_hits=8,
        ):
            norm = str(rel or "").strip().replace("\\", "/")
            if norm and norm not in paths:
                paths.append(norm)
            if len(paths) >= 8:
                break
        for token in _objective_keywords(objective_text):
            try:
                result = await asyncio.to_thread(toolbox.search_files, re.escape(token))
            except (RepositoryToolError, OSError):
                continue
            for hit in result.get("hits") or []:
                path = str(hit.get("path") or "").replace("\\", "/")
                if path and path not in paths:
                    paths.append(path)
                if len(paths) >= 8:
                    break
            if len(paths) >= 8:
                break
        await self._read_repository_paths_into_context(
            executor=executor,
            toolbox=toolbox,
            paths=tuple(paths),
        )
        obj.execution_context = context.to_dict()
        snapshot = build_understanding_evidence(
            objective_id=obj.objective_id,
            cycle_id=cycle.cycle_id,
            context=context,
            observed_problem=(
                f"{obj.original_objective or obj.objective} "
                f"(repository grounding: {len(paths)} candidate path(s), "
                f"{len(context.files_read)} read(s))"
            ),
        )
        read_paths = tuple(record.path for record in context.files_read)
        allowed = tuple(
            str(p).strip().replace("\\", "/")
            for p in obj.allowed_files
            if str(p or "").strip()
        )
        merged = tuple(dict.fromkeys([*read_paths, *allowed]))
        if merged:
            snapshot = snapshot.model_copy(update={"files_read": merged})
        self._ensure_exploration_evidence_for_paths(obj=obj, paths=merged)
        self._understanding_evidence[obj.objective_id] = snapshot
        return snapshot

    async def _fulfill_planner_evidence_requests(
        self,
        *,
        cycle: _CycleRecord,
        obj: "_ObjectiveRecord",
        requests: tuple[dict[str, str], ...],
        persist_cmd: str,
    ) -> dict[str, Any]:
        """Satisfy bounded planner evidence requests via existing exploration tools.

        Never mutates source. Persists an EvidenceRevision so the next planner call
        sees new evidence rather than the same excerpts.
        """
        context = _context_from_dict(obj.objective_id, obj.execution_context)
        try:
            repository_root = resolve_repository_root(obj.repository_id, settings=self._settings)
        except Exception:
            repository_root = None
        findings: list[dict[str, Any]] = []
        if repository_root is not None:
            executor = ContextToolExecutor(context=context)

            def _noop_event(_event_type: str, _payload_refs: dict[str, Any]) -> None:
                return None

            toolbox = RepositoryToolbox(repository_root=repository_root, on_event=_noop_event)
            if obj.allowed_files:
                read_allowed = await self._read_repository_paths_into_context(
                    executor=executor,
                    toolbox=toolbox,
                    paths=tuple(
                        str(p).strip().replace("\\", "/")
                        for p in obj.allowed_files
                        if str(p or "").strip()
                    ),
                )
                if read_allowed:
                    self._ensure_exploration_evidence_for_paths(obj=obj, paths=read_allowed)
            for request in requests:
                kind = request["type"]
                query = request["query"]
                if query_looks_unbounded(query):
                    findings.append(
                        {
                            "type": kind,
                            "query": query,
                            "skipped": "unbounded",
                        }
                    )
                    continue
                try:
                    if kind in ("read_file", "find_file"):
                        # AIX-GAEF-001 Phase 9: the planner's `query` for these kinds is
                        # free text ("Read gaef001-scratch/calc_f.py, including the
                        # implementation..."), not a bare path -- treating the whole
                        # sentence as a path (the old behavior) never resolved to a real
                        # file, so a genuinely-existing target never actually got read.
                        candidate = extract_target_path(query) or query.replace("\\", "/")
                        relative = resolve_relative_path(candidate, repository_root) or candidate
                        try:
                            result = await asyncio.to_thread(toolbox.read_file, relative)
                        except (RepositoryToolError, FileNotFoundError) as exc:
                            not_found = isinstance(exc, FileNotFoundError) or (
                                isinstance(exc, RepositoryToolError) and exc.code == "FILE_NOT_FOUND"
                            )
                            if not not_found:
                                raise
                            # Confirmed-absent is a positive answer, not a failure -- a
                            # create-shaped objective's target is *expected* not to
                            # exist yet. Silence here (the old `error: True` catch-all)
                            # is what made the planner re-ask the same question forever.
                            findings.append(
                                {"type": kind, "query": query, "path": relative, "exists": False}
                            )
                            continue
                        executor.call("filesystem.read_file", lambda **_: result, arguments={"path": relative})
                        findings.append({"type": kind, "query": query, "path": relative, "exists": True})
                        continue
                    paths: list[str] = []
                    for search_query in self._planner_evidence_search_queries(query=query, kind=kind):
                        result = await asyncio.to_thread(toolbox.search_files, search_query)
                        hits = list(result.get("hits") or [])
                        for hit in hits[:8]:
                            path = str(hit.get("path") or "").replace("\\", "/")
                            if not path or path in paths:
                                continue
                            paths.append(path)
                        if paths:
                            break
                    # P3/K17: search hits are ExplorationEvidence; also read into context
                    # so the planner retry sees grounded files_read (compatibility boundary).
                    self._record_search_hits_as_evidence(obj=obj, query=query, paths=tuple(paths))
                    if paths:
                        read_paths = await self._read_repository_paths_into_context(
                            executor=executor,
                            toolbox=toolbox,
                            paths=tuple(paths),
                        )
                        if read_paths:
                            self._ensure_exploration_evidence_for_paths(
                                obj=obj,
                                paths=read_paths,
                            )
                    if kind in {"find_symbol", "symbol_references"}:
                        symbol_name = query.split()[-1][:80]
                        sym_res = await asyncio.to_thread(
                            SymbolResolver().resolve_symbol,
                            symbol_name=symbol_name,
                            target_file=None,
                            workspace_root=repository_root,
                            content_by_file={},
                        )
                        if sym_res.status == "RESOLVED":
                            locations = [
                                {"file_path": loc.file_path, "start_line": loc.start_line}
                                for loc in sym_res.locations
                            ]
                            executor.call(
                                "repository.resolve_symbol",
                                lambda **_: {"symbol_name": symbol_name, "locations": locations},
                                arguments={"symbol_name": symbol_name},
                            )
                            for loc in locations[:3]:
                                path = str(loc.get("file_path") or "").replace("\\", "/")
                                if path and path not in paths:
                                    paths.append(path)
                    findings.append({"type": kind, "query": query, "paths": paths[:8]})
                except DuplicateToolCallError:
                    findings.append({"type": kind, "query": query, "duplicate": True})
                except (RepositoryToolError, ReadFileError, OSError):
                    findings.append({"type": kind, "query": query, "error": True})
        _named_existing: tuple[str, ...] = ()
        if repository_root is not None:
            # AIX-EDIT planner grounding priority (generic): a file explicitly
            # named in the objective is the primary implementation target. The
            # planner often requests only adjacent tests (stronger textual
            # match); ground the named source regardless so scope/focus cannot
            # silently substitute tests for it. Tests stay grounded as
            # supporting context; multi-file scopes are only extended, never
            # narrowed.
            _objective_text = str(obj.original_objective or obj.objective or "")
            _named_missing: tuple[str, ...] = ()
            try:
                _named_existing, _named_missing = split_named_objective_files(
                    _objective_text, repository_root
                )
            except (OSError, ValueError, AttributeError, TypeError):
                _named_existing, _named_missing = (), ()
            _read_paths = {r.path for r in context.files_read}
            _to_ground = [p for p in _named_existing if p not in _read_paths]
            if _to_ground:
                _read_named = await self._read_repository_paths_into_context(
                    executor=executor,
                    toolbox=toolbox,
                    paths=tuple(_to_ground),
                )
                if _read_named:
                    self._ensure_exploration_evidence_for_paths(obj=obj, paths=_read_named)
            for _miss in _named_missing:
                if _miss not in {f.get("path") for f in findings if isinstance(f, dict)}:
                    findings.append(
                        {
                            "type": "explicit_target",
                            "query": _miss,
                            "path": _miss,
                            "exists": False,
                            "requires_clarification": bool(
                                objective_asserts_existing_file(_objective_text)
                            ),
                        }
                    )
            missing_allowed = any(
                str(p).replace("\\", "/") not in {r.path for r in context.files_read}
                for p in obj.allowed_files
                if str(p or "").strip()
            )
            if not context.files_read or missing_allowed:
                await self._bootstrap_repository_grounding_for_planner(
                    cycle=cycle,
                    obj=obj,
                    context=context,
                    repository_root=repository_root,
                )
        obj.execution_context = context.to_dict()
        # AIX-GAEF-001 Phase 9: a confirmed-absent path is itself the answer to a
        # create-shaped objective's "does the target exist" question -- surface it in
        # `observed_problem` (the one field the planner prompt actually receives) so a
        # retry doesn't ask the same question again. Silently dropping this on the
        # floor (the old behavior) is exactly why `NEEDS_MORE_EVIDENCE` never resolved
        # for a target that will never have `files_read` because it does not yet exist.
        not_found = [str(f.get("path")) for f in findings if f.get("exists") is False and f.get("path")]
        # AIX-EDIT: an explicitly named target the objective asserts exists
        # (not a create-shaped ask) must clarify/block, never silently
        # substitute adjacent files.
        clarify_missing = [
            str(f.get("path"))
            for f in findings
            if isinstance(f, dict)
            and f.get("exists") is False
            and f.get("requires_clarification")
            and f.get("path")
        ]
        create_missing = [p for p in not_found if p not in set(clarify_missing)]
        observed_problem = (
            f"{obj.objective} (planner evidence requests fulfilled: "
            f"{len(requests)} request(s); "
            f"{len(context.files_read)} file(s) grounded for planner retry)"
        )
        if clarify_missing:
            observed_problem += (
                " Explicitly named implementation target(s) required by the "
                "objective but NOT FOUND in the repository: "
                + ", ".join(dict.fromkeys(clarify_missing))
                + ". Clarify which file to change instead of substituting adjacent files."
            )
        if create_missing:
            observed_problem += (
                " Confirmed NOT FOUND (does not exist in the repository, which is "
                "expected if this objective creates it): " + ", ".join(dict.fromkeys(create_missing))
            )
        evidence = build_understanding_evidence(
            objective_id=obj.objective_id,
            cycle_id=cycle.cycle_id,
            context=context,
            observed_problem=observed_problem,
        )
        allowed = tuple(
            str(p).strip().replace("\\", "/")
            for p in obj.allowed_files
            if str(p or "").strip()
        )
        # Priority order: explicit named source targets first, then grounded
        # evidence, then admission hints. Multi-file scopes only extended.
        _read_set = {str(getattr(r, "path", "") or "") for r in context.files_read}
        _named_first = [p for p in _named_existing if p in _read_set]
        merged = tuple(dict.fromkeys([*_named_first, *evidence.files_read, *allowed]))
        if merged and merged != evidence.files_read:
            evidence = evidence.model_copy(update={"files_read": merged})
        self._ensure_exploration_evidence_for_paths(obj=obj, paths=merged)
        self._understanding_evidence[obj.objective_id] = evidence
        revision = {
            "revision": len(self._evidence_revisions.get(obj.objective_id, ())) + 1,
            "objective_id": obj.objective_id,
            "requests": [dict(item) for item in requests],
            "findings": findings,
            "files_read": list(evidence.files_read),
            "symbols_found": list(evidence.symbols_found),
            "evidence_id": evidence.id,
        }
        self._evidence_revisions.setdefault(obj.objective_id, []).append(revision)
        self._emit_event(cycle=cycle, event_type="evidence.revision_collected", payload_refs=revision)
        await self._persist_event(
            tenant_id=cycle.tenant_id,
            aggregate_type="objective",
            aggregate_id=obj.objective_id,
            expected_sequence_number=4,
            event_type="evidence.revision_collected",
            command_id=f"{persist_cmd}:evidence:{revision['revision']}",
            payload=revision,
        )
        return revision

    def _seed_create_directory_contract(
        self,
        *,
        obj: "_ObjectiveRecord",
        cycle: "_CycleRecord",
        envelope: object | None,
    ) -> None:
        """Bound empty-directory create is a named deterministic bootstrap, not live LLM.

        Classification (AIX-PA-002): legitimate explicit bootstrap workflow for the
        create-empty-directory objective family. Live CLI: a missing `.gitkeep` has no
        files_read, so the LLM planner returns NEEDS_MORE_EVIDENCE and never accepts a
        plan. The executor already mkdir's; it only needs an ACCEPTED one-step contract
        on the authorized path. This is NOT a silent planner-off fallback. It still
        must admit an ACCEPTED PlanRevision (`planner_reasoning_ref=seeded-step-contract`)
        before mutation. Ordinary edit objectives never take this path.
        """
        if obj.objective_id in self._step_contracts or obj.objective_id in self._step_contract_errors:
            return
        if not looks_like_create_empty_directory_objective(self._intent_text(obj)):
            return
        # Bootstrap metadata only: the marker is the user-named directory in Intent.
        # obj.allowed_files must not choose the workspace effect (P5 isolate).
        # Do not mint a provenance PlanTarget from allowed_files; this remains a
        # closed seeded StepContract family, not execution-target authority.
        marker = resolve_empty_directory_marker_path(
            objective=self._intent_text(obj),
            allowed_files=(),
        )
        if not marker:
            return
        budget = (
            envelope.budget_ceiling
            if envelope is not None and getattr(envelope, "budget_ceiling", None) is not None
            else BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)
        )
        self._step_contracts[obj.objective_id] = StepContractV1(
            cycle_id=cycle.cycle_id,
            tenant_id=cycle.tenant_id,
            step_index=0,
            purpose=f"Create empty directory marker {marker}",
            target_scope=(marker,),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=(
                f"{marker} exists",
                f"{Path(marker).parent.as_posix()} is a directory",
                "no existing source files are modified",
            ),
            validation_commands=(f"test -f {marker}",),
            assigned_role="executor",
            step_budget=budget,
            obligation_refs=(),
            actions=(
                ActionRequestV1(
                    step_id=None,
                    capability="filesystem",
                    operation="mkdir",
                    arguments={"path": marker, "marker": True},
                    expected_observation={"exists": True},
                ),
            ),
            status="PENDING",
        )
        self._ensure_accepted_plan_for_seeded_contract(obj=obj, cycle=cycle)

    def _seed_rename_path_contract(
        self,
        *,
        obj: "_ObjectiveRecord",
        cycle: "_CycleRecord",
        envelope: object | None,
    ) -> None:
        """Bound filesystem rename is a named deterministic bootstrap, same shape as
        `_seed_create_directory_contract`. Unlike that sibling, this emits a governed
        `ActionRequestV1` on `StepContractV1.actions` (AIX-GAEF-001) rather than a
        `generic_edit` schema ref -- the executor dispatches it through
        `foundation.capability_executor`, never through `classify_intent`'s phrase
        heuristics. Not a silent planner-off fallback: still requires the same
        ACCEPTED-plan wrap (`planner_reasoning_ref=seeded-step-contract`) as every
        other seeded bootstrap before mutation.
        """
        if obj.objective_id in self._step_contracts or obj.objective_id in self._step_contract_errors:
            return
        if not looks_like_rename_path_objective(self._intent_text(obj)):
            return
        paths = resolve_rename_paths(
            objective=self._intent_text(obj),
            allowed_files=(),
        )
        if not paths:
            return
        source_rel, dest_rel = paths
        budget = (
            envelope.budget_ceiling
            if envelope is not None and getattr(envelope, "budget_ceiling", None) is not None
            else BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)
        )
        self._step_contracts[obj.objective_id] = StepContractV1(
            cycle_id=cycle.cycle_id,
            tenant_id=cycle.tenant_id,
            step_index=0,
            purpose=f"Rename {source_rel} to {dest_rel}",
            target_scope=(source_rel, dest_rel),
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=(
                f"{dest_rel} exists",
                f"{source_rel} no longer exists",
                "no other files are modified",
            ),
            validation_commands=(f"test -f {dest_rel}",),
            assigned_role="executor",
            step_budget=budget,
            obligation_refs=(),
            actions=(
                ActionRequestV1(
                    step_id=None,
                    capability="filesystem",
                    operation="rename",
                    arguments={"source": source_rel, "destination": dest_rel},
                    expected_observation={"source_exists": False, "destination_exists": True},
                ),
            ),
            status="PENDING",
        )
        self._ensure_accepted_plan_for_seeded_contract(obj=obj, cycle=cycle)

    def _seed_verification_summary_contract(
        self,
        *,
        obj: "_ObjectiveRecord",
        cycle: "_CycleRecord",
        envelope: object | None,
    ) -> None:
        """Read-only repo/directory summary: seeded ACCEPTED plan without live planner NME.

        Live CLI often admits summary asks as single_file_helper with a directory-only
        allowed_files scope; the LLM planner then returns NEEDS_MORE_EVIDENCE forever.
        Same bootstrap pattern as `_seed_create_directory_contract`: deterministic
        one-step contract + ACCEPTED PlanRevision wrap, then real execution proceeds.
        """
        if obj.objective_id in self._step_contracts or obj.objective_id in self._step_contract_errors:
            return
        if looks_like_unbounded_codebase_audit(self._intent_text(obj)):
            return
        if not looks_like_read_only_summary_objective(self._intent_text(obj)):
            return
        from backend.aix_core.runtime.executor.read_file_execute import (
            extract_target_path,
            resolve_relative_path,
        )

        # P6: Intent-named directory only. obj.allowed_files cannot choose summary scope.
        scope_dirs: list[str] = ["."]
        extracted = extract_target_path(self._intent_text(obj))
        if extracted:
            try:
                root = resolve_repository_root(obj.repository_id, settings=self._settings)
                bound = resolve_relative_path(extracted, root)
                if bound and bound not in {".", ""}:
                    scope_dirs = [bound if bound.endswith("/") else f"{bound}/"]
            except (RepositoryLocationError, OSError, TypeError, ValueError):
                scope_dirs = ["."]
        budget = (
            envelope.budget_ceiling
            if envelope is not None and getattr(envelope, "budget_ceiling", None) is not None
            else BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)
        )
        summary_scope = tuple(dict.fromkeys(scope_dirs))
        obligation_refs = tuple(
            str(getattr(o, "id", "") or getattr(o, "obligation_id", "") or "")
            for o in self._obligations.get(obj.objective_id, ())
            if str(getattr(o, "id", "") or getattr(o, "obligation_id", "") or "").strip()
        )
        self._step_contracts[obj.objective_id] = StepContractV1(
            cycle_id=cycle.cycle_id,
            tenant_id=cycle.tenant_id,
            step_index=0,
            purpose=f"Read-only summary of {', '.join(summary_scope)}",
            target_scope=summary_scope,
            validation_commands=("echo status-check-ok",),
            expected_output_schema_ref="text",
            acceptance_criteria=(f"Return factual summary for {self._intent_text(obj)[:200]}",),
            assigned_role="executor",
            step_budget=budget,
            obligation_refs=obligation_refs,
            status="PENDING",
        )
        self._ensure_accepted_plan_for_seeded_contract(obj=obj, cycle=cycle)

    def _seed_codebase_audit_contract(
        self,
        *,
        obj: "_ObjectiveRecord",
        cycle: "_CycleRecord",
        envelope: object | None,
    ) -> None:
        """Named read-only audit markdown: seeded ACCEPTED plan, no whole-tree planner.

        The live CLI otherwise sends this family through propose_step_contract, which
        asks for repo-wide evidence and hangs plan-prep. Same bootstrap as mkdir/summary:
        one StepContract + ACCEPTED wrap, then the executor writes the named .md from
        a bounded live-spine file set.
        """
        if obj.objective_id in self._step_contracts or obj.objective_id in self._step_contract_errors:
            return
        if not looks_like_unbounded_codebase_audit(self._intent_text(obj)):
            return
        filename = codebase_audit_markdown_filename(self._intent_text(obj))
        budget = (
            envelope.budget_ceiling
            if envelope is not None and getattr(envelope, "budget_ceiling", None) is not None
            else BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)
        )
        obligation_refs = tuple(
            str(getattr(o, "id", "") or getattr(o, "obligation_id", "") or "")
            for o in self._obligations.get(obj.objective_id, ())
            if str(getattr(o, "id", "") or getattr(o, "obligation_id", "") or "").strip()
        )
        self._step_contracts[obj.objective_id] = StepContractV1(
            cycle_id=cycle.cycle_id,
            tenant_id=cycle.tenant_id,
            step_index=0,
            purpose=f"Write read-only AIX codebase audit markdown {filename}",
            target_scope=(filename,),
            validation_commands=(f"test -f {filename}",),
            expected_output_schema_ref="text",
            acceptance_criteria=(
                f"{filename} exists",
                "audit cites live spine files",
                "no source (.py/.ts) files are modified",
            ),
            assigned_role="executor",
            step_budget=budget,
            obligation_refs=obligation_refs,
            status="PENDING",
        )
        self._ensure_accepted_plan_for_seeded_contract(obj=obj, cycle=cycle)

    def _ensure_accepted_plan_for_seeded_contract(
        self,
        *,
        obj: "_ObjectiveRecord",
        cycle: _CycleRecord,
        allow_test_bootstrap: bool = False,
    ) -> None:
        """Wrap a closed seed-family StepContract so the P0 gate sees an ACCEPTED plan.

        Leftover live-Intent contracts are not seeds. Tests may pass
        allow_test_bootstrap=True to wrap an injected contract.
        """
        contract = self._step_contracts.get(obj.objective_id)
        if contract is None:
            return
        if (
            not allow_test_bootstrap
            and self._provenance.get_intent(obj.objective_id) is not None
            and not self._is_closed_seed_family(obj)
        ):
            return
        plan = self._active_plan_revision(obj.objective_id)
        if plan is not None and str(plan.status) == "ACCEPTED":
            return
        wrapped = PlanRevisionV1(
            objective_id=obj.objective_id,
            cycle_id=cycle.cycle_id,
            revision=len(self._plan_revisions.get(obj.objective_id, ())) + 1,
            steps=(contract,),
            obligation_coverage=tuple(dict.fromkeys(contract.obligation_refs)),
            planner_reasoning_ref="seeded-step-contract",
            status="ACCEPTED",
        )
        self._admit_plan_revision(wrapped)
        log_plan_accepted(wrapped, reason="seeded_step_contract")
        # P0 only needs ACCEPTED PlanRevision + the hot-path contract. The live
        # planner accept path already calls init_step_runtime. Pre-initializing
        # here made one-step seeded tests look terminal before REPAIR/REUNDERSTAND
        # could re-dispatch the same contract.

    def _planner_file_excerpts(
        self, obj: _ObjectiveRecord, understanding: UnderstandingEvidenceV1
    ) -> dict[str, str]:
        """Quarantined collector. Live planner passes file_excerpts={} and never
        authorizes execute from these outlines. files_read here is excerpt input
        only — not mutation scope.
        """
        from backend.aix_core.intelligence.repository.file_structure import build_file_outline
        from backend.aix_core.intelligence.repository.repository_location import resolve_repository_root

        try:
            root = resolve_repository_root(obj.repository_id, settings=self._settings)
        except Exception:
            return {}
        budget = 2400
        excerpts: dict[str, str] = {}
        candidates: list[str] = []
        for rel in list(understanding.files_read or ()):
            p = str(rel or "").strip().replace("\\", "/")
            if p and p not in candidates:
                candidates.append(p)
        # P6: allowed_files and keyword-seeded gateway files are not excerpt authority.
        for rel in candidates[:16]:
            if budget <= 80:
                break
            path = Path(rel)
            if not path.is_absolute():
                path = Path(root) / rel
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # God-files (live S6 CronJobController ~200KB) must not dominate the
            # planner input budget; AIX-PA-002 still token-packs after this.
            per_file_cap = 900 if len(text) > 80_000 else min(budget, 1600)
            outline = build_file_outline(str(rel), text, max_chars=min(budget, per_file_cap)).text
            if outline:
                excerpts[str(rel)] = outline
                budget -= len(outline)
        return excerpts

    def _objective_decision_fields(self, obj: _ObjectiveRecord) -> dict[str, Any]:
        return decision_state_objective(
            {
                "objective_id": obj.objective_id,
                "tenant_id": obj.tenant_id,
                "workhub_task_id": obj.workhub_task_id,
                "objective": obj.objective,
                "original_objective": obj.original_objective or obj.objective,
                "task_class": obj.task_class,
                "definition_of_done": obj.definition_of_done,
                "repository_id": obj.repository_id,
                "allowed_files": obj.allowed_files,  # admission metadata; not execute authority
                "project_id": obj.project_id,
                "task_number": obj.task_number,
                "version": obj.version,
                "created_at": obj.created_at,
                "workhub_task_created": obj.workhub_task_created,
                "task_family": obj.task_family,
                "conversation_id": obj.conversation_id,
                "objective_source_message_id": obj.objective_source_message_id,
            }
        )

    def _run_decision_fields(self, cycle: _CycleRecord) -> dict[str, Any]:
        return decision_state_run(
            {
                "cycle_id": cycle.cycle_id,
                "run_id": cycle.cycle_id,
                "objective_id": cycle.objective_id,
                "tenant_id": cycle.tenant_id,
                "candidate_sha": cycle.candidate_sha,
                "budgets": cycle.budgets,
                "phase": cycle.phase,
                "version": cycle.version,
                "terminal_reason": cycle.terminal_reason,
                "last_error_detail": cycle.last_error_detail,
                "approval_reference": cycle.approval_reference,
                "plan_id": cycle.plan_id,
                "plan_revision": cycle.plan_revision,
                "current_gate_id": cycle.current_gate_id,
                "approved_gate_id": cycle.approved_gate_id,
                "approved_plan_revision": cycle.approved_plan_revision,
                "approved_operations": cycle.approved_operations,
                "approved_resources": cycle.approved_resources,
                "event_sequence": cycle.event_sequence,
                "trace_id": cycle.trace_id,
                "created_at": cycle.created_at,
                "retry_budget_remaining": cycle.retry_budget_remaining,
                "replan_budget_remaining": cycle.replan_budget_remaining,
                "cancel_requested": cycle.cancel_requested,
                "current_step_cursor": cycle.current_step_cursor,
                "dispatch_state": cycle.dispatch_state,
                "dispatch_required": cycle.dispatch_required,
                "execution_claim_id": cycle.execution_claim_id,
                "execution_claim_step_id": cycle.execution_claim_step_id,
                "execution_claim_attempt": cycle.execution_claim_attempt,
                "execution_claim_expired": cycle.execution_claim_expired,
                "active_step_started_at": cycle.active_step_started_at,
                "step_lease_expires_at": cycle.step_lease_expires_at,
                "last_progress_at": cycle.last_progress_at,
                "previous_step_id": cycle.previous_step_id,
            }
        )

    def decision_bytes_for_cycle(self, cycle_id: str) -> bytes | None:
        """C011: byte-comparable decision state for objective+run pair."""
        cycle = self._cycles.get(cycle_id)
        if cycle is None:
            return None
        obj = self._objectives.get(cycle.objective_id)
        if obj is None:
            return None
        paired = {
            "objective": self._objective_decision_fields(obj),
            "run": self._run_decision_fields(cycle),
        }
        return decision_bytes(paired)

    def _project_run_sync(self, cycle: _CycleRecord) -> None:
        decision = self._run_decision_fields(cycle)
        history: list[dict[str, Any]] = []
        if cycle.engine is not None:
            history = list(getattr(cycle.engine.state, "history", []) or [])
        envelope = wrap_run_snapshot(
            decision=decision,
            sequence_number=max(cycle.event_sequence, cycle.version, 1),
            engine_history=history,
            real_execute=cycle.real_execute,
            model_routes=cycle.model_routes,
        )
        # Plan and step ledgers are authoritative lifecycle state, not disposable
        # serving caches.  Persist their complete typed payload beside the canonical
        # run decision so GET /plan and /steps reconstruct after process death.
        envelope["plan_revisions"] = [
            plan.model_dump(mode="json")
            for plan in self._plan_revisions.get(cycle.objective_id, ())
        ]
        envelope["step_runtime"] = {
            step_id: state.model_dump(mode="json")
            for step_id, state in (self._step_runtime.get(cycle.objective_id) or {}).items()
        }
        self._projections.upsert_snapshot(
            tenant_id=cycle.tenant_id,
            aggregate_type="run",
            aggregate_id=cycle.cycle_id,
            sequence_number=int(envelope["sequence_number"]),
            schema_version=DECISION_STATE_SCHEMA,
            snapshot=envelope,
        )

    async def _project_run(self, cycle: _CycleRecord) -> None:
        # NEVER call _project_run_sync on the event loop. Sync disk replace +
        # PermissionError retries freeze /health and every FE/CLI poll.
        await asyncio.to_thread(self._project_run_sync, cycle)
        if self._repository is None or self.mode != "postgres":
            return
        snap = self._projections.latest_snapshot(
            tenant_id=cycle.tenant_id, aggregate_type="run", aggregate_id=cycle.cycle_id
        )
        if snap is None:
            return
        await self._repository.upsert_aggregate_snapshot(
            tenant_id=snap.tenant_id,
            aggregate_type=snap.aggregate_type,
            aggregate_id=snap.aggregate_id,
            sequence_number=snap.sequence_number,
            schema_version=snap.schema_version,
            snapshot=snap.snapshot,
        )

    async def _project_objective(self, obj: _ObjectiveRecord) -> None:
        decision = self._objective_decision_fields(obj)
        envelope = wrap_objective_snapshot(decision=decision, sequence_number=max(obj.version, 1))
        self._projections.upsert_snapshot(
            tenant_id=obj.tenant_id,
            aggregate_type="objective",
            aggregate_id=obj.objective_id,
            sequence_number=int(envelope["sequence_number"]),
            schema_version=DECISION_STATE_SCHEMA,
            snapshot=envelope,
        )
        if self._repository is None or self.mode != "postgres":
            return
        await self._repository.upsert_aggregate_snapshot(
            tenant_id=obj.tenant_id,
            aggregate_type="objective",
            aggregate_id=obj.objective_id,
            sequence_number=int(envelope["sequence_number"]),
            schema_version=DECISION_STATE_SCHEMA,
            snapshot=envelope,
        )

    async def _load_projection_snapshot(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> AggregateSnapshot | None:
        snap = self._projections.latest_snapshot(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        if snap is not None:
            return snap
        if self._repository is None:
            return None
        try:
            row = await self._repository.load_latest_aggregate_snapshot(
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
            )
        except ObjectiveCycleRepositoryError:
            return None
        if row is None:
            return None
        self._projections.upsert_snapshot(
            tenant_id=row["tenant_id"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            sequence_number=row["sequence_number"],
            schema_version=row["schema_version"],
            snapshot=row["snapshot"],
        )
        return self._projections.latest_snapshot(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )

    def _rebuild_engine_from_snapshot(
        self, *, obj: _ObjectiveRecord, cycle: _CycleRecord, engine_history: list[dict[str, Any]]
    ) -> AixObjectiveCycleWorkflowEngine:
        plan_material = f"{obj.objective_id}:{cycle.cycle_id}:{cycle.candidate_sha}:{obj.objective}"
        workflow_input = ObjectiveCycleWorkflowInput(
            workflow_version=1,
            tenant_id=cycle.tenant_id,
            objective_id=obj.objective_id,
            cycle_id=cycle.cycle_id,
            candidate_sha=cycle.candidate_sha,
            plan_sha256=_sha256_hex(plan_material),
            start_request_id=f"rehydrate:{cycle.cycle_id}",
            correlation_id=cycle.trace_id or f"rehydrate:{cycle.cycle_id}",
        )
        if engine_history:
            engine = replay_history(workflow_input, engine_history, worker_version=1)
        else:
            engine = AixObjectiveCycleWorkflowEngine(workflow_input)
        # Seed decision-equivalent phase/version even when history is empty.
        # Mid-plan dispatcher states must map to RUNNING_STEP — seeding EXECUTING
        # or a stale STEP_PASSED phase makes _execute_claimed_step no-op.
        if cycle.dispatch_state in {
            "STEP_READY",
            "NEXT_STEP_READY",
            "EXECUTING",
            "VERIFYING",
            "STEP_PASSED",
        }:
            engine_phase = "RUNNING_STEP"
        else:
            engine_phase = cycle.phase if cycle.phase == "RUNNING_STEP" else cycle.phase
            if engine_phase in {"EXECUTING", "STEP_PASSED", "NEXT_STEP_READY", "STEP_READY", "VERIFYING"}:
                engine_phase = "RUNNING_STEP"
        engine.state.phase = engine_phase  # type: ignore[assignment]
        engine.state.aggregate_version = cycle.version
        engine.state.search_attributes["Phase"] = str(engine_phase)
        engine.state.search_attributes["Replayed"] = "true"
        return engine

    async def rehydrate_objective(self, *, tenant_id: str, objective_id: str) -> bool:
        """C012/C011: load objective decision snapshot into serving dicts."""
        if not tenant_id.strip():
            raise ReplayError("EMPTY_TENANT", "tenant_id required")
        existing = self._objectives.get(objective_id)
        if existing is not None and existing.tenant_id == tenant_id:
            return True
        obj_snap = await self._load_projection_snapshot(
            tenant_id=tenant_id, aggregate_type="objective", aggregate_id=objective_id
        )
        if obj_snap is None:
            return False
        if obj_snap.tenant_id != tenant_id:
            raise ReplayError("CROSS_TENANT_SNAPSHOT", objective_id)
        obj_dec = decision_state_objective(obj_snap.snapshot.get("decision") or {})
        obj = _ObjectiveRecord(
            objective_id=obj_dec["objective_id"],
            tenant_id=obj_dec["tenant_id"],
            workhub_task_id=obj_dec["workhub_task_id"],
            objective=obj_dec["objective"],
            original_objective=str(obj_dec.get("original_objective") or obj_dec["objective"] or ""),
            task_class=obj_dec["task_class"],
            definition_of_done=tuple(obj_dec["definition_of_done"]),
            repository_id=obj_dec["repository_id"],
            allowed_files=tuple(obj_dec["allowed_files"]),
            project_id=obj_dec.get("project_id"),
            task_number=obj_dec.get("task_number"),
            workhub_task_created=bool(obj_dec.get("workhub_task_created")),
            version=int(obj_dec.get("version") or 1),
            created_at=str(obj_dec.get("created_at") or _utc()),
            conversation_id=obj_dec.get("conversation_id"),
            objective_source_message_id=obj_dec.get("objective_source_message_id"),
            task_family=str(obj_dec.get("task_family") or "UNKNOWN"),
        )
        self._objectives[objective_id] = obj
        self._ensure_frozen_intent(obj)
        if objective_id not in self._objective_verification_contracts:
            objective_text = str(obj.original_objective or obj.objective or "").strip()
            if objective_text:
                truth = derive_canonical_truth(
                    original_text=objective_text,
                    objective_id=objective_id,
                )
                ovc = derive_ovc(truth)
                self._canonical_truths[objective_id] = truth
                self._objective_verification_contracts[objective_id] = ovc
        self._ensure_objective_envelope(
            obj=obj,
            actor_id="rehydrate",
            command_id=f"rehydrate-envelope:{objective_id}",
            body={},
        )
        return True

    def _ensure_ovc_for_objective(self, obj: _ObjectiveRecord) -> None:
        """Rebuild sealed OVC from objective text when in-memory state was lost on restart."""
        if obj.objective_id in self._objective_verification_contracts:
            return
        objective_text = str(obj.original_objective or obj.objective or "").strip()
        if not objective_text:
            return
        truth = derive_canonical_truth(
            original_text=objective_text,
            objective_id=obj.objective_id,
        )
        self._canonical_truths[obj.objective_id] = truth
        self._objective_verification_contracts[obj.objective_id] = derive_ovc(truth)

    def _ensure_workspace_for_cycle(self, cycle: _CycleRecord) -> Path | None:
        """Resolve deliverable workspace from disk pointer or step attempts; create if missing."""
        import shutil

        ws_path = _resolve_cycle_workspace(
            tenant_id=cycle.tenant_id,
            cycle_id=cycle.cycle_id,
            settings=self._settings,
        )
        re = cycle.real_execute if isinstance(cycle.real_execute, dict) else {}
        if ws_path is None and re.get("workspace"):
            ws_path = Path(str(re["workspace"]))
        if ws_path is None and re.get("repository_root"):
            ws_path = Path(str(re["repository_root"]))

        if ws_path is not None and not ws_path.is_dir():
            ws_path.parent.mkdir(parents=True, exist_ok=True)

        if ws_path is not None and ws_path.is_dir() and not (ws_path / "proposed").is_dir():
            latest_att = _latest_attempt_workspace(
                tenant_id=cycle.tenant_id,
                cycle_id=cycle.cycle_id,
                settings=self._settings,
            )
            copy_sources: list[Path] = []
            if latest_att is not None and (latest_att / "proposed").is_dir():
                copy_sources.append(latest_att / "proposed")
            for cycle_root in _cycle_roots_on_disk(
                tenant_id=cycle.tenant_id,
                cycle_id=cycle.cycle_id,
                settings=self._settings,
            ):
                for slug_dir in cycle_root.iterdir():
                    if slug_dir.is_dir() and (slug_dir / "proposed").is_dir():
                        copy_sources.append(slug_dir / "proposed")
                        break
            for proposed_src in copy_sources:
                try:
                    shutil.copytree(proposed_src, ws_path / "proposed", dirs_exist_ok=True)
                    break
                except OSError:
                    continue

        if ws_path is not None:
            ws_path.mkdir(parents=True, exist_ok=True)
            if cycle.real_execute is None:
                cycle.real_execute = {}
            cycle.real_execute["workspace"] = str(ws_path)
            if not cycle.real_execute.get("repository_root"):
                cycle.real_execute["repository_root"] = str(ws_path)

        exists = bool(ws_path and ws_path.is_dir())
        logger.info(
            "[REPAIR] workspace resolved %s exists=%s for cycle %s",
            ws_path,
            exists,
            cycle.cycle_id,
        )
        return ws_path if exists else None

    async def _rehydrate_cycle_workspace(self, cycle_id: str) -> Path | None:
        cycle = self._cycles.get(cycle_id)
        if cycle is None:
            return None
        return self._ensure_workspace_for_cycle(cycle)

    async def rehydrate_cycle(self, *, tenant_id: str, cycle_id: str) -> bool:
        """C011: rebuild objective+run serving records from decision snapshots.

        Returns True when the cycle is present in `self._cycles` afterward.
        """
        if not tenant_id.strip():
            raise ReplayError("EMPTY_TENANT", "tenant_id required")
        existing = self._cycles.get(cycle_id)
        if existing is not None and existing.tenant_id == tenant_id:
            if existing.engine is None:
                obj = self._objectives.get(existing.objective_id)
                if obj is not None:
                    existing.engine = self._rebuild_engine_from_snapshot(
                        obj=obj, cycle=existing, engine_history=[]
                    )
            obj = self._objectives.get(existing.objective_id)
            if obj is not None:
                self._ensure_ovc_for_objective(obj)
                self._ensure_objective_envelope(
                    obj=obj,
                    actor_id="rehydrate",
                    command_id=f"rehydrate-envelope:{obj.objective_id}",
                    body={},
                )
            if self._gap_store._pg_conn is not None or self._gap_store._database_url:
                await self._gap_store.load_pg(existing.objective_id)
            if str(existing.phase) in {"GAPS_FOUND", "REPAIRING", "EXECUTING"}:
                self._ensure_workspace_for_cycle(existing)
            return True

        run_snap = await self._load_projection_snapshot(
            tenant_id=tenant_id, aggregate_type="run", aggregate_id=cycle_id
        )
        if run_snap is None:
            return False
        if run_snap.tenant_id != tenant_id:
            raise ReplayError("CROSS_TENANT_SNAPSHOT", cycle_id)
        run_env = run_snap.snapshot
        if str(run_env.get("schema_version") or "") != DECISION_STATE_SCHEMA:
            raise ReplayError("UNKNOWN_DECISION_SCHEMA", str(run_env.get("schema_version")))
        decision = decision_state_run(run_env.get("decision") or {})
        objective_id = str(decision["objective_id"])

        obj = self._objectives.get(objective_id)
        if obj is None or obj.tenant_id != tenant_id:
            obj_snap = await self._load_projection_snapshot(
                tenant_id=tenant_id, aggregate_type="objective", aggregate_id=objective_id
            )
            if obj_snap is None:
                return False
            if obj_snap.tenant_id != tenant_id:
                raise ReplayError("CROSS_TENANT_SNAPSHOT", objective_id)
            obj_dec = decision_state_objective(obj_snap.snapshot.get("decision") or {})
            obj = _ObjectiveRecord(
                objective_id=obj_dec["objective_id"],
                tenant_id=obj_dec["tenant_id"],
                workhub_task_id=obj_dec["workhub_task_id"],
                objective=obj_dec["objective"],
                original_objective=str(obj_dec.get("original_objective") or obj_dec["objective"] or ""),
                task_class=obj_dec["task_class"],
                definition_of_done=tuple(obj_dec["definition_of_done"]),
                repository_id=obj_dec["repository_id"],
                allowed_files=tuple(obj_dec["allowed_files"]),
                project_id=obj_dec.get("project_id"),
                task_number=obj_dec.get("task_number"),
                workhub_task_created=bool(obj_dec.get("workhub_task_created")),
                version=int(obj_dec.get("version") or 1),
                created_at=str(obj_dec.get("created_at") or _utc()),
                conversation_id=obj_dec.get("conversation_id"),
                objective_source_message_id=obj_dec.get("objective_source_message_id"),
                task_family=str(obj_dec.get("task_family") or "UNKNOWN"),
            )
            self._objectives[objective_id] = obj
            self._ensure_frozen_intent(obj)
        self._ensure_ovc_for_objective(obj)

        cycle = _CycleRecord(
            cycle_id=decision["cycle_id"],
            objective_id=objective_id,
            tenant_id=decision["tenant_id"],
            candidate_sha=decision["candidate_sha"],
            budgets=dict(decision["budgets"]),
            version=int(decision["version"]),
            phase=str(decision["phase"]),
            terminal_reason=decision.get("terminal_reason"),
            last_error_detail=decision.get("last_error_detail"),
            approval_reference=decision.get("approval_reference"),
            created_at=str(decision.get("created_at") or _utc()),
            event_sequence=int(decision.get("event_sequence") or 0),
            trace_id=str(decision.get("trace_id") or f"trc_{new_ulid()[:16]}"),
            plan_id=str(decision.get("plan_id") or ""),
            plan_revision=int(decision.get("plan_revision") or 1),
            current_gate_id=decision.get("approval_gate_id"),
            approved_gate_id=decision.get("approved_gate_id"),
            approved_plan_revision=decision.get("approved_plan_revision"),
            approved_operations=tuple(decision.get("approved_operations") or ()),
            approved_resources=tuple(decision.get("approved_resources") or ()),
            retry_budget_remaining=int(decision.get("retry_budget_remaining") or 0),
            replan_budget_remaining=int(decision.get("replan_budget_remaining") or 0),
            cancel_requested=bool(decision.get("cancel_requested") or False),
            current_step_cursor=decision.get("current_step_cursor"),
            dispatch_state=str(decision.get("dispatch_state") or "IDLE"),
            dispatch_required=bool(decision.get("dispatch_required") or False),
            execution_claim_id=decision.get("execution_claim_id"),
            execution_claim_step_id=decision.get("execution_claim_step_id"),
            execution_claim_attempt=int(decision.get("execution_claim_attempt") or 0),
            execution_claim_expired=bool(decision.get("execution_claim_expired") or False),
            active_step_started_at=decision.get("active_step_started_at"),
            step_lease_expires_at=decision.get("step_lease_expires_at"),
            last_progress_at=str(decision.get("last_progress_at") or _utc()),
            previous_step_id=decision.get("previous_step_id"),
            real_execute=dict(run_env["real_execute"]) if isinstance(run_env.get("real_execute"), dict) else None,
            model_routes=dict(run_env.get("model_routes") or {}),
        )
        history = list(run_env.get("engine_history") or [])
        cycle.engine = self._rebuild_engine_from_snapshot(obj=obj, cycle=cycle, engine_history=history)
        self._cycles[cycle_id] = cycle
        try:
            revisions = tuple(
                PlanRevisionV1.model_validate(item)
                for item in (run_env.get("plan_revisions") or ())
            )
            if revisions:
                self._plan_revisions[objective_id] = list(revisions)
            runtime_payload = run_env.get("step_runtime") or {}
            if isinstance(runtime_payload, dict) and runtime_payload:
                self._step_runtime[objective_id] = {
                    str(step_id): StepRuntimeStateV1.model_validate(state)
                    for step_id, state in runtime_payload.items()
                }
            if revisions:
                active_plan = revisions[-1]
                active_contract = next(
                    (step for step in active_plan.steps if step.id == cycle.current_step_cursor),
                    None,
                )
                if active_contract is None:
                    active_contract = select_next_step(
                        active_plan, self._step_runtime.get(objective_id) or {}
                    )
                if active_contract is None and active_plan.steps:
                    # WAITING_APPROVAL snapshots have no cursor and dispatch_required
                    # is false; execute after restart still needs the accepted step.
                    active_contract = active_plan.steps[0]
                if active_contract is not None:
                    self._step_contracts[objective_id] = active_contract
        except (_ContractValidationError, TypeError, ValueError) as exc:
            raise ReplayError("INVALID_PLAN_SNAPSHOT", str(exc)) from exc
        # C013: OPEN attempts without COMMIT are orphans — never treat as complete.
        self._attempt_journal.compensate_orphans(
            tenant_id=tenant_id, run_id=cycle_id, reason="PROCESS_RESTART_ORPHAN"
        )
        # C016: reattach workspace lease if present.
        try:
            if self._workspaces.get(tenant_id=tenant_id, run_id=cycle_id) is not None:
                self._workspaces.attach(
                    tenant_id=tenant_id, run_id=cycle_id, holder="rehydrate"
                )
        except Exception:
            pass
        self._ensure_objective_envelope(
            obj=obj,
            actor_id="rehydrate",
            command_id=f"rehydrate-envelope:{objective_id}",
            body={},
        )
        # Execute runs in-process. After restart, any EXECUTING claim from the
        # projection is a zombie (no worker owns it). Expire so UI/dispatch can
        # recover instead of looping on valid_execution_claim_already_active.
        if (
            cycle.dispatch_state == "EXECUTING"
            and cycle.execution_claim_id
            and not cycle.cancel_requested
            and str(cycle.phase) not in {"GAPS_FOUND", "COMPLETED_FOR_REVIEW", "CERTIFIED"}
        ):
            cycle.execution_claim_expired = True
            cycle.dispatch_required = False
            cycle.step_lease_expires_at = None
            cycle.active_step_started_at = None
            if cycle.real_execute or cycle.mutation_evidence_history:
                cycle.dispatch_state = "VERIFYING"
                cycle.dispatch_state = "VERIFYING"
                self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = "EXPIRED_CLAIM_REQUIRES_EVIDENCE_RECOVERY"
            else:
                cycle.dispatch_state = "STEP_FAILED"
                cycle.dispatch_state = "STEP_FAILED"
                self._sync_cycle_phase_from_engine(cycle)
                cycle.terminal_reason = "STEP_EXECUTION_TIMED_OUT"
            self._emit_event(
                cycle=cycle,
                event_type="step.execution_claim_expired",
                payload_refs={
                    "execution_claim_id": cycle.execution_claim_id,
                    "step_id": cycle.execution_claim_step_id,
                    "recovery_state": cycle.dispatch_state,
                    "reason": "PROCESS_RESTART_ORPHAN",
                },
            )
            await self._project_run(cycle)
        elif cycle.cancel_requested or (
            cycle.engine is not None and str(cycle.engine.state.phase) == "CANCELLED"
        ):
            cycle.cancel_requested = True
            if cycle.engine is not None and str(cycle.engine.state.phase) not in {'CANCELLED', 'FAILED'}:
                try:
                    cycle.engine.request_cancel(reason='BACKEND_CANCEL')
                except Exception:
                    pass
            cycle.dispatch_state = 'CANCELLED'
            self._sync_cycle_phase_from_engine(cycle)
            cycle.terminal_reason = cycle.terminal_reason or "CANCELLED"
            cycle.dispatch_state = "IDLE"
            cycle.dispatch_required = False
            cycle.execution_claim_id = None
            cycle.execution_claim_step_id = None
            cycle.execution_claim_attempt = 0
            cycle.step_lease_expires_at = None
            await self._project_run(cycle)
        if self._gap_store._pg_conn is not None or self._gap_store._database_url:
            if self._gap_store.count_blocking_gaps(objective_id) == 0:
                await self._gap_store.load_pg(objective_id)
        if str(cycle.phase) in {"GAPS_FOUND", "REPAIRING", "EXECUTING"}:
            self._ensure_workspace_for_cycle(cycle)
        return True

    async def _persist_event(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self._repository is None:
            return
        kind = str(aggregate_type or "").strip()
        if kind not in AGGREGATE_TYPES:
            raise ObjectiveCycleRepositoryError(
                "objective-cycle ledger append failed: "
                f"aggregate_type {kind!r} is not in {sorted(AGGREGATE_TYPES)}"
            )
        sequence = expected_sequence_number
        if self.mode == "postgres":
            existing = await self._repository.load_events(
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
            )
            sequence = (existing[-1].sequence_number + 1) if existing else 1
        try:
            await self._repository.append_event(
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                expected_sequence_number=sequence,
                event_type=event_type,
                command_id=command_id,
                payload=payload,
            )
        except ObjectiveCycleRepositoryError:
            # Fail closed for postgres mode only; inprocess mode never reaches here without repo.
            if self.mode == "postgres":
                raise

    def _objective_payload(self, obj: _ObjectiveRecord) -> dict[str, Any]:
        obligations = self._obligations.get(obj.objective_id, ())
        return {
            "objective_id": obj.objective_id,
            "tenant_id": obj.tenant_id,
            "workhub_task_id": obj.workhub_task_id,
            "objective": obj.objective,
            "task_class": obj.task_class,
            "definition_of_done": list(obj.definition_of_done),
            "repository_id": obj.repository_id,
            "allowed_files": list(obj.allowed_files),
            "project_id": obj.project_id,
            "task_number": obj.task_number,
            "workhub_task_created": obj.workhub_task_created,
            "attachments": list(obj.attachments or []),
            "version": obj.version,
            "created_at": obj.created_at,
            "aix_creates_workhub_tasks": obj.workhub_task_created,
            # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2.
            "obligations": [o.model_dump(mode="json") for o in obligations],
            "obligation_ids": [o.id for o in obligations],
        }

    def _cycle_payload(self, cycle: _CycleRecord) -> dict[str, Any]:
        if cycle.step_lease_expires_at and cycle.engine is not None:
            try:
                lease_deadline = datetime.fromisoformat(cycle.step_lease_expires_at)
                if lease_deadline.tzinfo is None:
                    lease_deadline = lease_deadline.replace(tzinfo=UTC)
                if (
                    datetime.now(UTC) >= lease_deadline
                    and cycle.dispatch_state == "EXECUTING"
                ):
                    cycle.engine.wait_blocker()
                    cycle.engine.signal_blocker_resolved(
                        blocker_ref="STEP_EXECUTION_TIMED_OUT", resolved=False
                    )
                    self._sync_cycle_phase_from_engine(cycle)
                    cycle.terminal_reason = "STEP_EXECUTION_TIMED_OUT"
                    cycle.last_error_detail = "active step lease expired"
                    cycle.last_progress_at = _utc()
                    cycle.dispatch_state = "STEP_FAILED"
                    cycle.dispatch_required = False
                    cycle.execution_claim_expired = True
            except (TypeError, ValueError):
                logger.warning(
                    "invalid step lease timestamp cycle=%s value=%r",
                    cycle.cycle_id,
                    cycle.step_lease_expires_at,
                )
        # Heal WAITING_APPROVAL vs RUNNING_STEP desync on read so status clients
        # and resume recovery see the engine as source of truth.
        self._sync_cycle_phase_from_engine(cycle)
        status = cycle.engine.query_status() if cycle.engine is not None else {}
        artifacts = dict((cycle.real_execute or {}).get("artifacts") or {})
        has_html = bool(str(artifacts.get("proposed_html") or "").strip())
        obj = self._objectives.get(cycle.objective_id)
        plan = self._active_plan_revision(cycle.objective_id)
        step_runtime = self._step_runtime.get(cycle.objective_id, {})
        tracker = self._loop_trackers.get(cycle.objective_id)
        active_step = step_runtime.get(cycle.current_step_cursor or "")
        active_contract = (
            next((s for s in plan.steps if s.id == cycle.current_step_cursor), None)
            if plan is not None else None
        )
        next_contract = None
        if plan is not None:
            next_contract = select_next_step(plan, step_runtime)
        workspace = str((cycle.real_execute or {}).get("workspace") or "").strip()
        proposed_files: list[dict[str, Any]] = []
        tests: dict[str, Any] = {"positive": [], "negative": []}
        if workspace:
            ws = Path(workspace)
            for name in ("index.html", "styles.css"):
                path = ws / "proposed" / name
                if path.is_file():
                    proposed_files.append(
                        {
                            "name": name,
                            "bytes": path.stat().st_size,
                            "preview_url": f"/api/aix/cycles/{cycle.cycle_id}/preview/{name}",
                        }
                    )
            pos = _read_json_file(ws / "proposed" / "tests" / "positive_cases.json")
            neg = _read_json_file(ws / "proposed" / "tests" / "negative_cases.json")
            if pos:
                tests["positive"] = pos.get("cases") or []
            if neg:
                tests["negative"] = neg.get("cases") or []
        return {
            "cycle_id": cycle.cycle_id,
            "objective_id": cycle.objective_id,
            "tenant_id": cycle.tenant_id,
            "candidate_sha": cycle.candidate_sha,
            "budgets": dict(cycle.budgets),
            "phase": cycle.phase,
            # AIX Closed-Loop Execution Plan, Sprint W3: contracts.py's CycleStatus
            # equivalent of the live WorkflowPhase, when one exists (None for phases
            # with no defensible fit -- see cycle_status_mapping.py's own docstring,
            # e.g. WAITING_BLOCKER/BUILDING_REVIEW/the retry-lifecycle phases). Never
            # invented -- read straight from the phase this record already carries.
            "cycle_status": workflow_phase_to_cycle_status(cycle.phase),
            "version": cycle.version,
            "terminal_reason": cycle.terminal_reason,
            "last_error_detail": cycle.last_error_detail,
            "active_plan_revision_id": plan.plan_id if plan is not None else None,
            "active_step_id": cycle.current_step_cursor,
            "active_step": active_contract.model_dump(mode="json") if active_contract is not None else None,
            "active_step_index": active_contract.step_index if active_contract is not None else None,
            "active_step_status": active_step.status if active_step is not None else None,
            "step_index": (
                next((s.step_index for s in plan.steps if s.id == cycle.current_step_cursor), None)
                if plan is not None else None
            ),
            "total_steps": len(plan.steps) if plan is not None else 0,
            "attempt": active_step.attempt_count if active_step is not None else None,
            "dispatch_state": cycle.dispatch_state,
            "dispatch_required": cycle.dispatch_required,
            "execution_claim_id": cycle.execution_claim_id,
            "execution_claim_step_id": cycle.execution_claim_step_id,
            "execution_claim_attempt": cycle.execution_claim_attempt,
            "execution_claim_expired": cycle.execution_claim_expired,
            "lease_expiry": cycle.step_lease_expires_at,
            "previous_step_id": cycle.previous_step_id,
            "current_plan_revision": plan.model_dump(mode="json") if plan is not None else None,
            "completed_steps": sum(1 for state in step_runtime.values() if state.status == "PASS"),
            "next_step": next_contract.model_dump(mode="json") if next_contract is not None else None,
            "retry_budget_remaining": cycle.retry_budget_remaining,
            "repair_count": int(tracker.step_repairs) if tracker is not None else 0,
            "replan_count": int(tracker.plan_revisions) if tracker is not None else 0,
            "active_step_started_at": cycle.active_step_started_at,
            "last_progress_at": cycle.last_progress_at,
            "timeout_at": cycle.step_lease_expires_at,
            "approval_reference": cycle.approval_reference,
            # Approval identity — Studio/extension must echo these on approve/resume
            # so stale-gate / wrong-revision proofs can be verified (opt-in today;
            # mandatory once legacy implicit approval is removed).
            "plan_id": cycle.plan_id,
            "plan_revision": cycle.plan_revision,
            "approval_gate_id": cycle.current_gate_id,
            "approved_gate_id": cycle.approved_gate_id,
            "approved_plan_revision": cycle.approved_plan_revision,
            "approved_operations": list(cycle.approved_operations or ()),
            "approved_resources": list(cycle.approved_resources or ()),
            "status": status,
            "real_execute": cycle.real_execute,
            "preview_url": (
                f"/api/aix/cycles/{cycle.cycle_id}/preview/index.html" if has_html else None
            ),
            "created_at": cycle.created_at,
            "objective": obj.objective if obj is not None else "",
            "workhub_task_id": obj.workhub_task_id if obj is not None else "",
            "task_number": obj.task_number if obj is not None else None,
            "project_id": obj.project_id if obj is not None else None,
            "repository_id": obj.repository_id if obj is not None else None,
            "proposed_files": proposed_files,
            "tests": tests,
            "source": "memory",
            "workhub_lifecycle_authority": False,
            "aix_creates_workhub_tasks": False,
            # AIX-REAL-EXECUTION-005: canonical stage projection + truthful final
            # report, both backend-authored from the same `real_execute` truth above
            # -- Studio/WorkHub consume this, they do not independently infer status.
            "execution_stages": _execution_stage_projection(obj=obj, cycle=cycle) if obj is not None else [],
            "mutation_summary": (
                _aggregate_mutation_summary(
                    history=list(cycle.mutation_evidence_history or []),
                    current=cycle.real_execute,
                )
                or (
                    (_workspace_snapshot(
                        tenant_id=cycle.tenant_id,
                        cycle_id=cycle.cycle_id,
                        settings=self._settings,
                    ) or {}).get("mutation_summary")
                )
            ),
            "final_report": (
                build_final_report(
                    objective_id=obj.objective_id,
                    cycle_id=cycle.cycle_id,
                    trace_id=cycle.trace_id,
                    objective_text=obj.objective,
                    repository_id=obj.repository_id,
                    real_execute=cycle.real_execute,
                    approvals=(cycle.approval_reference,) if cycle.approval_reference else (),
                ).to_dict()
                if obj is not None and cycle.real_execute is not None
                else None
            ),
        }

    def resolve_preview_artifact(self, *, tenant_id: str, cycle_id: str, artifact_name: str) -> Path | None:
        """Return absolute path for a safe proposed/ preview artifact, or None."""
        allowed = {
            "index.html": "proposed_html",
            "styles.css": "proposed_css",
        }
        if artifact_name not in allowed:
            return None

        def _safe_return(path: Path, workspace_raw: str | None) -> Path | None:
            if not path.is_file():
                return None
            resolved = path.resolve()
            if workspace_raw:
                try:
                    resolved.relative_to(Path(workspace_raw).resolve())
                except ValueError:
                    return None
            if cycle_id not in str(resolved):
                return None
            return resolved

        cycle = self._cycles.get(cycle_id)
        if cycle is not None and cycle.tenant_id == tenant_id:
            artifacts = dict((cycle.real_execute or {}).get("artifacts") or {})
            raw = str(artifacts.get(allowed[artifact_name]) or "").strip()
            workspace_raw = str((cycle.real_execute or {}).get("workspace") or "").strip()
            if not raw and workspace_raw:
                raw = str(Path(workspace_raw) / "proposed" / artifact_name)
            if raw:
                found = _safe_return(Path(raw), workspace_raw or None)
                if found is not None:
                    return found

        # Disk fallback when cycle engine was lost on restart.
        workspace = _resolve_cycle_workspace(
            tenant_id=tenant_id, cycle_id=cycle_id, settings=self._settings
        )
        if workspace is None:
            return None
        return _safe_return(workspace / "proposed" / artifact_name, str(workspace))
