"""AIX-E2E-CYCLE-001A-W03/W04/W05: objective, cycle, step, attempt, replan and outcome contracts.

Canonical source: `docs/current/architecture/AIX-E2E-CYCLE-001-GOVERNED-OBJECTIVE-CYCLE.md`
sections 4 (cycle state model) and 5 (canonical contracts). Field shapes and the legal
state vocabulary here are taken directly from that document, not invented independently —
where this module and the architecture doc disagree, the architecture doc wins and this
module has drifted.

These are deterministic, versioned value objects, not ORM rows. They carry no WorkHub
lifecycle meaning: terminal cycle states end at `COMPLETED_FOR_REVIEW`, `REJECTED`,
`BLOCKED`, `FAILED`, or `CANCELLED` — WorkHub, not AIX, decides acceptance/close.

All contracts are `frozen=True` and `extra="forbid"`: a fact once recorded is immutable,
and an unknown field fails closed instead of being silently dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.aix_core.runtime.objective.ulid_compat import new_ulid

from backend.aix_core.runtime.governance.policy import SUPPORTED_TASK_CLASSES
from backend.aix_core.runtime.governance.policy import RiskTier as _PolicyRiskTier

CONTRACT_VERSION = "v1"


def _uuid() -> str:
    return new_ulid()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- Cycle state vocabulary (verbatim from architecture doc section 4) ---
#
# RECEIVED -> CLARIFICATION_REQUIRED | ADMITTED | REJECTED
# ADMITTED -> GROUNDING
# GROUNDING -> PLANNING | BLOCKED
# PLANNING -> WAITING_APPROVAL | READY | BLOCKED
# WAITING_APPROVAL -> READY | REJECTED | CANCELLED
# READY -> RUNNING_STEP | COMPLETED_FOR_REVIEW | BLOCKED
# RUNNING_STEP -> VERIFYING_STEP | CANCELLING | BLOCKED
# VERIFYING_STEP -> READY | REPLANNING | COMPLETED_FOR_REVIEW | FAILED | BLOCKED
# REPLANNING -> WAITING_APPROVAL | READY | FAILED | BLOCKED
# CANCELLING -> CANCELLED
#
# CLARIFICATION_REQUIRED has no outgoing edge in the architecture doc's section 4 table.
# The only semantically sensible resumption once a human answers is back to RECEIVED for
# re-admission; this is an assumption this packet is recording, not a canonical fact —
# see DRIFT note in the 001A evidence. Do not treat it as settled without a doc update.
CycleStatus = Literal[
    "RECEIVED",
    "CLARIFICATION_REQUIRED",
    "ADMITTED",
    "REJECTED",
    "GROUNDING",
    "PLANNING",
    "WAITING_APPROVAL",
    "READY",
    "RUNNING_STEP",
    "VERIFYING_STEP",
    "REPLANNING",
    "CANCELLING",
    "CANCELLED",
    "COMPLETED_FOR_REVIEW",
    "FAILED",
    "BLOCKED",
]

CYCLE_TERMINAL_STATES: frozenset[str] = frozenset(
    {"COMPLETED_FOR_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "REJECTED"}
)

StepStatus = Literal[
    "PENDING",
    "DISPATCHED",
    "IN_PROGRESS",
    "VERIFIED",
    "FAILED",
    "BLOCKED",
    "SKIPPED",
]

AttemptOutcome = Literal["IN_PROGRESS", "SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"]

AgentRole = Literal["planner", "executor", "verifier", "recovery", "reviewer"]

# Reused from the admission policy module rather than redefined here: two independent
# risk-tier vocabularies for the same domain concept is exactly the kind of drift
# 001A-W07 (schema evolution / contract discipline) exists to prevent.
RiskTier = _PolicyRiskTier

ApprovalPolicy = Literal["NEVER", "INITIAL_PLAN_ONLY", "EVERY_REPLAN", "ALWAYS"]

# AIX Closed-Loop Execution Plan, Sprint W9: the semantic verifier's terminal decision
# for one step attempt. Not itself a CycleStatus/StepStatus/AttemptOutcome value --
# durable_api_backend.py maps a verdict onto the appropriate cycle/step transition
# (PASS -> proceed, REPAIR -> retry within budget, REPLAN -> planning needs to change,
# HALT -> stop with evidence, budget exhausted or unrecoverable).
VerificationVerdict = Literal["PASS", "REPAIR", "REPLAN", "REUNDERSTAND", "HALT"]

# AIX-MPE-003 Packet MPE-04: runtime status of one step in a PlanRevisionV1.
# Distinct from frozen StepContractV1.status -- the contract says what should happen;
# this says what is happening.
StepRuntimeStatus = Literal[
    "PENDING",
    "READY",
    "RUNNING",
    "VERIFYING",
    "REPAIR",
    "PASS",
    "FAILED",
    "BLOCKED",
    "SUPERSEDED",
]

PlannerIterationDecision = Literal[
    "CONTINUE_PLAN",
    "REPAIR_STEP",
    "REVISE_PLAN",
    "REQUEST_UNDERSTANDING",
]

ControllerAction = Literal[
    "UNDERSTAND",
    "PLAN",
    "EXECUTE",
    "VERIFY",
    "REPAIR",
    "REUNDERSTAND",
    "REPLAN",
    "CONTINUE",
    "COMPLETE",
    "ESCALATE",
    "HALT",
]

# AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2: an ObjectiveObligationV1's lifecycle,
# taken verbatim from the sprint doc. Distinct from StepStatus/CycleStatus -- an
# obligation tracks whether *the thing the user actually asked for* has been
# satisfied, independent of any one step's own PENDING/DISPATCHED/... progression.
# UNPLANNED at admission (Packet P2, this module); PLANNED once a StepContract
# declares `obligation_refs` covering it (Packet P3); IN_PROGRESS/PARTIAL/SATISFIED/
# FAILED/UNKNOWN are set by objective-level verification (Packet P13), never by the
# obligation-derivation step itself.
ObligationStatus = Literal[
    "UNPLANNED",
    "PLANNED",
    "IN_PROGRESS",
    "PARTIAL",
    "SATISFIED",
    "FAILED",
    "UNKNOWN",
]

# Effects an objective/step may request. Must stay a subset of what admission policy
# (aix_core.objective_cycle.policy) is willing to grant; forbidden effects mirror
# policy._FORBIDDEN_EFFECTS deliberately as an independent, fail-closed redundant check
# rather than a fragile import of a private symbol from a concurrently-developed module.
# test_contracts.py asserts the two sets stay in lockstep.
FORBIDDEN_EFFECTS: frozenset[str] = frozenset(
    {
        "auto_accept",
        "auto_close",
        "auto_create_workhub_task",
        "direct_provider_route",
        "production_deploy",
        "production_mutation",
        "workhub_lifecycle_mutation",
    }
)


class BudgetV1(BaseModel):
    """A time/token/cost/retry/step/concurrency budget, used both as a ceiling
    (on `ObjectiveEnvelopeV1`) and as a cumulative-consumed counter (on `ExecutionCycleV1`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    time_seconds: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    retries: int = Field(ge=0)
    steps: int = Field(ge=0)
    concurrency: int = Field(ge=1, le=1, description="v1 is sequential-only; see architecture doc section 2.")


class UsageV1(BaseModel):
    """Actual resource consumption recorded against a step attempt or a cycle total."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tokens_in: int = Field(ge=0, default=0)
    tokens_out: int = Field(ge=0, default=0)
    cost_usd: float = Field(ge=0, default=0.0)
    tool_calls: int = Field(ge=0, default=0)
    time_seconds: float = Field(ge=0, default=0.0)


class _ContractBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = CONTRACT_VERSION


class ObjectiveEnvelopeV1(_ContractBase):
    """Admitted objective. One envelope may spawn at most one active `ExecutionCycleV1`
    unless policy explicitly permits another (architecture doc section 2)."""

    id: str = Field(default_factory=_uuid)
    tenant_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1, description="Human or service principal that requested this objective.")
    project_id: str = Field(min_length=1)
    workhub_task_ref: str = Field(min_length=1, description="Parent WorkHub task id; AIX never creates this.")
    task_class: str = Field(min_length=1)
    definition_of_done: str = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    repository_scope: tuple[str, ...] = Field(min_length=1, description="Repositories/services this objective may touch.")
    environment_scope: Literal["development", "staging", "production"] = "development"
    allowed_effects: tuple[str, ...] = ()
    forbidden_effects: tuple[str, ...] = tuple(sorted(FORBIDDEN_EFFECTS))
    budget_ceiling: BudgetV1
    approval_policy: ApprovalPolicy = "INITIAL_PLAN_ONLY"
    idempotency_key: str = Field(min_length=1)
    correlation_id: str = Field(default_factory=_uuid)
    causation_id: str | None = None
    context_capsule_hash: str | None = None
    created_at: str = Field(default_factory=utc_now)

    @field_validator("task_class")
    @classmethod
    def _task_class_supported(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_TASK_CLASSES:
            raise ValueError(f"unsupported task_class: {value!r}")
        return normalized

    @field_validator("allowed_effects")
    @classmethod
    def _allowed_effects_never_forbidden(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        overlap = set(value) & FORBIDDEN_EFFECTS
        if overlap:
            raise ValueError(f"allowed_effects may not include forbidden effects: {sorted(overlap)}")
        return value

    @field_validator("environment_scope")
    @classmethod
    def _no_production_scope_in_v1(cls, value: str) -> str:
        if value == "production":
            raise ValueError("production environment_scope is not authorized for the v1 governed cycle")
        return value


class ObjectiveObligationV1(_ContractBase):
    """AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2: the durable, binding representation
    of one distinct thing the user actually asked for -- what Packet P1's completion
    authority and Packet P3's coverage gate check against, instead of the raw
    `objective`/`definition_of_done` strings a caller happened to phrase things in.
    Deliberately extends this existing contract layer rather than a competing
    "objective model" (sprint guardrail): sits alongside `ObjectiveEnvelopeV1` as
    admission-time output, keyed to it by `objective_id`.

    `source` records where this obligation's text came from -- `definition_of_done`
    (the caller's own structured admission input, the preferred/primary source) or
    `objective_text` (the objective string itself, used only as a fallback when no
    `definition_of_done` was supplied, so admission still produces at least one
    obligation). Never silently replaces an explicit `definition_of_done` with the raw
    objective string -- see `obligations.derive_obligations`.

    `status` starts `UNPLANNED` here; later packets (P3 plan coverage, P13 objective
    verification) advance it. This module never sets anything but `UNPLANNED`.
    """

    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True
    source: Literal["definition_of_done", "objective_text"] = "definition_of_done"
    acceptance_criteria: tuple[str, ...] = ()
    verification_strategy: str | None = None
    status: ObligationStatus = "UNPLANNED"
    evidence_refs: tuple[str, ...] = ()
    created_at: str = Field(default_factory=utc_now)


class ExecutionCycleV1(_ContractBase):
    """One bounded execution attempt at an objective. `status` is a legal-transition slot,
    not a free-form field — writers must run new values through `state_machine.py`."""

    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    status: CycleStatus
    candidate_sha: str | None = None
    config_version: str | None = None
    step_ids: tuple[str, ...] = ()
    current_step_cursor: str | None = None
    consumed_budget: BudgetV1
    approval_refs: tuple[str, ...] = ()
    stop_the_line_active: bool = False
    stop_the_line_reason: str | None = None
    outcome_ref: str | None = None
    evidence_index: tuple[str, ...] = ()
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _cursor_must_be_a_known_step(self) -> "ExecutionCycleV1":
        if self.current_step_cursor is not None and self.current_step_cursor not in self.step_ids:
            raise ValueError("current_step_cursor must reference a step_id already in step_ids")
        return self

    @model_validator(mode="after")
    def _stop_the_line_reason_required_when_active(self) -> "ExecutionCycleV1":
        if self.stop_the_line_active and not self.stop_the_line_reason:
            raise ValueError("stop_the_line_reason is required when stop_the_line_active is True")
        return self


RequiredEffectKind = Literal[
    "GROUP_METHODS",
    "MOVE_METHODS",
    "EXTRACT_HELPER",
    "EXTRACT_TO_FILES",
    "STRIP_UNUSED_IMPORTS",
    "LITERAL_REPLACE",
    "PRESERVE_PUBLIC_INTERFACE",
    "RUN_DECLARED_VALIDATION",
]

STRUCTURAL_EFFECT_KINDS: frozenset[str] = frozenset(
    {"GROUP_METHODS", "MOVE_METHODS", "EXTRACT_HELPER", "EXTRACT_TO_FILES"}
)


class MethodGroupSpecV1(_ContractBase):
    """One named group for GROUP_METHODS / MOVE_METHODS. Methods listed here are
    authoritative; match_tokens are a generic fallback when the planner named a
    domain but not the members."""

    key: str = Field(min_length=1)
    methods: tuple[str, ...] = ()
    match_tokens: tuple[str, ...] = ()


class RequiredEffectV1(_ContractBase):
    """AIX-EC-004 Packet EC-01: the actual transformation a step must perform.

    Distinct from `purpose` prose. Dispatch, fallbacks, and verification read this
    field — they must not re-parse keywords from the objective string.
    """

    kind: RequiredEffectKind
    groups: tuple[MethodGroupSpecV1, ...] = ()
    grouping_keys: tuple[str, ...] = ()
    min_call_sites: int = Field(ge=2, default=2)
    helper_name: str | None = None


ActionStatus = Literal["SUCCESS", "FAILED", "BLOCKED"]


class ActionRequestV1(_ContractBase):
    """AIX-GAEF-001: planner-emitted executable action. The executor must not
    reclassify the English objective; it performs `capability.operation` with
    `arguments` or fails closed."""

    action_id: str = Field(default_factory=_uuid)
    step_id: str | None = None
    capability: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_observation: dict[str, Any] = Field(default_factory=dict)


class ActionObservationV1(_ContractBase):
    """Canonical result of one governed action. Failures never become a different operation."""

    action_id: str = Field(min_length=1)
    status: ActionStatus
    changed: bool = False
    observation: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


class StepContractV1(_ContractBase):
    id: str = Field(default_factory=_uuid)
    cycle_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    purpose: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    target_scope: tuple[str, ...] = Field(min_length=1, description="Exact files/services this step may touch.")
    required_context_refs: tuple[str, ...] = ()
    expected_output_schema_ref: str = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    validation_commands: tuple[str, ...] = Field(min_length=1)
    assigned_role: AgentRole
    risk_tier: RiskTier = RiskTier.LOW
    approval_required: bool = False
    step_budget: BudgetV1
    prohibited_actions: tuple[str, ...] = tuple(sorted(FORBIDDEN_EFFECTS))
    # AIX-EC-004 Packet EC-01: typed transformations this step must perform.
    required_effects: tuple[RequiredEffectV1, ...] = ()
    # AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: which `ObjectiveObligationV1.id`
    # values (Packet P2) this step addresses -- "implemented by a step, or explicitly
    # verified by a step" per the sprint doc. Empty by default (every pre-P3 caller
    # that constructs a `StepContractV1` directly, without going through
    # `obligation_coverage.py`'s gate, is unaffected). The planner may decide HOW; it
    # may never silently redefine WHAT by omitting a required obligation from every
    # step's `obligation_refs` -- see `obligation_coverage.validate_obligation_coverage`.
    obligation_refs: tuple[str, ...] = ()
    # OutcomeContract binding (DoD architecture): paths remain in target_scope for
    # mutation authority; these refs bind the step to sealed outcome facets/ops.
    # Empty = legacy unbound step (CoverageGate fails if a sealed contract exists).
    outcome_contract_id: str | None = None
    outcome_contract_hash: str | None = None
    facet_refs: tuple[str, ...] = ()
    operation_refs: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    mapping_refs: tuple[str, ...] = ()
    binding_role: Literal["IMPLEMENT", "VERIFY", "IMPLEMENT_AND_VERIFY"] | None = None
    # AIX-GAEF-001: structured actions the executor must perform without re-reading
    # the English objective. Empty means the legacy classify_intent path (Phase 15).
    actions: tuple[ActionRequestV1, ...] = ()
    # AIX-MPE-003 Packet MPE-21: declared validation the executor must return evidence for.
    expected_checks: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    estimated_complexity: str | None = None
    context_requirements: tuple[str, ...] = ()
    status: StepStatus
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _no_self_dependency(self) -> "StepContractV1":
        if self.id in self.depends_on:
            raise ValueError(f"step {self.id!r} cannot depend on itself")
        return self


PlanRevisionStatus = Literal["DRAFT", "ACCEPTED", "REJECTED", "SUPERSEDED"]


class PlanRevisionV1(_ContractBase):
    """AIX-MPE-003 Packet MPE-01: the canonical, versioned multi-step plan container.

    Replaces an ad hoc `dict[objective_id, StepContractV1]` (a single implicit step)
    with a durable record of the actual route the planner proposed: an ordered set of
    `StepContractV1` steps with real dependencies between them. A plan is immutable
    once constructed -- a replan does not mutate this object, it produces a new
    `PlanRevisionV1` with `revision` incremented and `created_from_plan_revision` set
    to this one's `plan_id` (see AIX-MPE-003 Packet MPE-12; this contract only defines
    the shape, MPE-12 owns the actual replan transition).

    `planner_reasoning_ref` is a short decision summary or evidence reference, never
    raw model chain-of-thought -- callers must not pass the model's full reasoning
    trace here.

    This model enforces only the invariants checkable from its own fields: unique
    `step_id`s, dependencies (`StepContractV1.depends_on`) that reference a real step
    in this same plan, and no dependency cycles. Obligation coverage and target-scope
    authorization need the objective's obligation set / authorized scope, which this
    contract does not carry -- those are the plan quality gate's job (AIX-MPE-003
    Packet MPE-03, reusing `obligation_coverage.validate_obligation_coverage` against
    `steps`), not a validator here.
    """

    plan_id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    cycle_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    created_from_evidence: str | None = None
    created_from_plan_revision: str | None = None
    steps: tuple[StepContractV1, ...] = Field(min_length=1)
    obligation_coverage: tuple[str, ...] = ()
    planner_reasoning_ref: str | None = None
    status: PlanRevisionStatus = "DRAFT"
    intent_id: str | None = None
    understanding_revision_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    attempt_ids: tuple[str, ...] = ()
    governance_revision_id: str | None = None
    approval_ref: str | None = None
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _step_ids_unique(self) -> "PlanRevisionV1":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"PlanRevisionV1.steps must have unique step ids, duplicates: {duplicates}")
        return self

    @model_validator(mode="after")
    def _dependencies_reference_known_steps(self) -> "PlanRevisionV1":
        known_ids = {step.id for step in self.steps}
        for step in self.steps:
            unknown = [dep for dep in step.depends_on if dep not in known_ids]
            if unknown:
                raise ValueError(
                    f"step {step.id!r} depends_on step id(s) not present in this plan: {unknown}"
                )
        return self

    @model_validator(mode="after")
    def _dependencies_are_acyclic(self) -> "PlanRevisionV1":
        graph = {step.id: step.depends_on for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def _visit(step_id: str, path: tuple[str, ...]) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise ValueError(f"circular dependency in plan steps: {' -> '.join((*path, step_id))}")
            visiting.add(step_id)
            for dep in graph.get(step_id, ()):
                _visit(dep, (*path, step_id))
            visiting.discard(step_id)
            visited.add(step_id)

        for step_id in graph:
            _visit(step_id, ())
        return self


class StepAttemptV1(_ContractBase):
    """Immutable record of one bounded attempt at one step. `move_token` is the single
    scoped authorization consumed by this attempt (enforced by 001F, carried here)."""

    id: str = Field(default_factory=_uuid)
    step_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    move_token: str = Field(min_length=1)
    executor_ref: str = Field(min_length=1)
    model_ref: str | None = None
    tool_versions: dict[str, str] = Field(default_factory=dict)
    outcome: AttemptOutcome
    usage: UsageV1 = Field(default_factory=UsageV1)
    raw_result_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    verification_result_ref: str | None = None
    failure_class: str | None = None
    retry_eligible: bool = False
    started_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None

    @model_validator(mode="after")
    def _terminal_outcome_requires_completed_at(self) -> "StepAttemptV1":
        if self.outcome != "IN_PROGRESS" and self.completed_at is None:
            raise ValueError(f"outcome {self.outcome!r} requires completed_at")
        return self


class ReplanDecisionV1(_ContractBase):
    id: str = Field(default_factory=_uuid)
    cycle_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    triggering_evidence_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    objective_unchanged: Literal[True] = True
    retained_step_ids: tuple[str, ...] = ()
    added_step_ids: tuple[str, ...] = ()
    replaced_step_ids: tuple[str, ...] = ()
    removed_step_ids: tuple[str, ...] = ()
    scope_comparison: str = Field(min_length=1)
    budget_delta: BudgetV1 | None = None
    policy_decision_ref: str = Field(min_length=1)
    requires_approval: bool = True
    decided_at: str = Field(default_factory=utc_now)
    decided_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def _scope_widening_requires_approval(self) -> "ReplanDecisionV1":
        if self.added_step_ids and not self.requires_approval:
            raise ValueError(
                "replan adds new steps beyond the prior plan; requires_approval must be True "
                "(no silent scope widening)"
            )
        return self


class UnderstandingEvidenceV1(_ContractBase):
    """AIX Closed-Loop Execution Plan, Sprint W4. A frozen, point-in-time snapshot of
    what exploration actually established before planning -- distinct from
    `execution_context.ExecutionContext`, which is the live, mutable, per-objective
    working memory that accumulates it. This contract is the immutable record taken
    *from* that working memory (`from_execution_context`, in `durable_api_backend.py`,
    not here -- `contracts.py` stays a pure schema layer with no dependency on the
    exploration/tool-tracking mechanism) plus the parts no mechanical tool-tracker can
    produce: `observed_problem`/`assumptions`/`uncertainties` are always
    planner-or-human-authored judgments, never derived.

    `files_read`/`symbols_found`/`relevant_tests`/`dependencies` reuse whatever
    `ExecutionContext` already recorded -- this contract must never become a second,
    independent evidence-collection path (same "never a second computation of an
    already-computed truth" discipline `verification_engine.py` already established).
    """

    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    cycle_id: str | None = None
    files_read: tuple[str, ...] = ()
    symbols_found: tuple[str, ...] = ()
    relevant_tests: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    observed_problem: str = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    understanding_complete: bool = False
    created_at: str = Field(default_factory=utc_now)


class StepContextCapsuleV1(_ContractBase):
    """Autonomous Closed-Loop Lane, Packet AL-05. The bounded, step-specific slice of
    `UnderstandingEvidenceV1` a persona actually needs -- not the complete exploration
    history. `understanding_evidence_id` is provenance back to the snapshot this was
    filtered from; a capsule with no evidence behind it (a fresh objective with no
    understanding phase yet) is legitimate and carries `understanding_evidence_id=None`.

    `relevant_previous_failure` is deliberately a single optional field, not a list --
    a capsule describes the *current* attempt's context, and a repair capsule carries
    exactly the one failure that triggered it (see `step_attempt.py`'s repair wiring),
    never an accumulating history of every past attempt.
    """

    id: str = Field(default_factory=_uuid)
    step_id: str = Field(min_length=1)
    understanding_evidence_id: str | None = None
    relevant_files: tuple[str, ...] = ()
    relevant_symbols: tuple[str, ...] = ()
    relevant_evidence_refs: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    relevant_previous_failure: str | None = None
    intent_id: str | None = None
    intent_text: str | None = None
    cited_claim_ids: tuple[str, ...] = ()
    cited_evidence_ids: tuple[str, ...] = ()
    plan_target_paths: tuple[str, ...] = ()
    plan_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class ObjectiveWorldStateV1(_ContractBase):
    """AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P6: the single, coherent, queryable
    view of what is currently true about one objective -- assembled
    (`world_state.build_world_state`) from the already-existing per-objective caches
    (obligations, understanding, step attempts, verification verdicts, replan
    decisions), never a second independent computation of any of them. A
    planning/repair iteration is meant to reason from this record, not reconstructed
    chat history or prior agent prose (the sprint doc's own framing).

    `obligations` carries each `ObjectiveObligationV1` with its *current* derived
    `status` (see `world_state.derive_obligation_status`) -- Packet P2's admission-time
    records are frozen at `UNPLANNED`; this is the mutable-in-effect (via a fresh
    tuple, never in-place mutation of a frozen contract) view of where they actually
    stand right now.
    """

    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    cycle_id: str | None = None
    objective: str = Field(min_length=1)
    obligations: tuple[ObjectiveObligationV1, ...] = ()
    known_facts: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    plan_revision: int = 0
    completed_step_ids: tuple[str, ...] = ()
    failed_attempt_count: int = 0
    remaining_obligation_ids: tuple[str, ...] = ()
    verification_state: str | None = None
    remaining_budget: BudgetV1 | None = None
    # Populated instead of `remaining_budget` when only the retry/replan counters are
    # actually tracked (this codebase's live state today) rather than a full
    # time/token/cost remaining-budget computation -- never fabricated to fill
    # `BudgetV1`'s required fields with meaningless zeros.
    retries_remaining: int | None = None
    replans_remaining: int | None = None
    intent_id: str | None = None
    understanding_revision_id: str | None = None
    plan_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    attempt_ids: tuple[str, ...] = ()
    produced_at: str = Field(default_factory=utc_now)


class ExecutorPackageV1(_ContractBase):
    """AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P7: the complete, authoritative
    record of what one executor dispatch actually received -- so "what authoritative
    package did the executor receive" (the sprint doc's own exit question) is
    answerable by reading this record after the fact, not by reconstructing it from
    scattered caches or prior agent prose. Bundles references to already-built
    contracts (`StepContractV1`, `StepContextCapsuleV1`) -- never a second, competing
    computation of their content. No accumulated conversation transcript, no
    append-only instructions: every field here is a typed, bounded reference.
    """

    id: str = Field(default_factory=_uuid)
    cycle_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    objective_boundary: str = Field(min_length=1, description="The objective text the executor is bounded by.")
    acceptance_criteria: tuple[str, ...] = ()
    forbidden_effects: tuple[str, ...] = ()
    step_contract_id: str | None = None
    obligation_ids: tuple[str, ...] = Field(
        default=(), description="ObjectiveObligationV1 ids this dispatch is meant to address."
    )
    context_capsule_id: str | None = None
    repair_context: str | None = None
    plan_id: str | None = None
    plan_revision: int | None = None
    executor_profile_id: str | None = None
    approval_ref: str | None = None
    target_paths: tuple[str, ...] = ()
    issued_at: str = Field(default_factory=utc_now)


class StepRuntimeStateV1(_ContractBase):
    """AIX-MPE-003 Packet MPE-04: mutable-in-effect (via replacement, never in-place
    mutation of a frozen record) runtime state for one step in one plan revision."""

    step_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    status: StepRuntimeStatus = "PENDING"
    attempt_count: int = Field(ge=0, default=0)
    latest_attempt_id: str | None = None
    verification_verdict: VerificationVerdict | None = None
    started_at: str | None = None
    completed_at: str | None = None
    blocked_reason: str | None = None
    failure_refs: tuple[str, ...] = ()


class ExecutorProfileV1(_ContractBase):
    """AIX-MPE-003 Packet MPE-08 / AIX-EC-004 Packet EC-02: model-independent
    executor capability description. Flags must match `supported_effects`.
    `supports_structural_edit` is never a blanket True — it is true only when
    this profile actually lists a structural effect kind."""

    executor_id: str = Field(min_length=1)
    supported_effects: tuple[RequiredEffectKind, ...] = ()
    supports_single_file: bool = True
    supports_multi_file: bool = False
    supports_shell: bool = True
    supports_tests: bool = False
    supports_symbol_search: bool = False
    supports_structural_edit: bool = False
    context_budget: int = Field(ge=1, default=8_000)
    preferred_complexity: str = "medium"
    max_recommended_scope: int = Field(ge=1, default=4)

    @model_validator(mode="after")
    def _structural_flag_is_truthful(self) -> "ExecutorProfileV1":
        has_structural = bool(set(self.supported_effects) & STRUCTURAL_EFFECT_KINDS)
        if self.supports_structural_edit and not has_structural:
            raise ValueError(
                "supports_structural_edit=True requires GROUP_METHODS, MOVE_METHODS, "
                "EXTRACT_HELPER, or EXTRACT_TO_FILES in supported_effects"
            )
        return self


class RepairDirectiveV1(_ContractBase):
    """AIX-MPE-003 Packet MPE-20 / AIX-EC-004 Packet EC-07: structured repair
    instruction. Never free-form 'try again more carefully' prose."""

    step_id: str = Field(min_length=1)
    failure_refs: tuple[str, ...] = ()
    remaining_requirements: tuple[str, ...] = ()
    required_correction: str = Field(min_length=1)
    constraints: tuple[str, ...] = ()
    verification_requirements: tuple[str, ...] = ()
    forbidden_strategies: tuple[str, ...] = ()
    preferred_strategy: str | None = None
    last_strategy_id: str | None = None
    remaining_asked_work: bool = False
    unmet_effects: tuple[str, ...] = ()
    insufficiency_class: str | None = None


class FailureEvidenceV1(_ContractBase):
    """Immutable, attempt-scoped evidence captured before repair or replan.

    The record keeps the executor's typed reason, the exact step contract that was
    attempted, and references to partial artifacts.  Repair logic consumes this
    record instead of retrying from an unstructured exception string.
    """

    id: str = Field(default_factory=_uuid)
    step_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    failure_signature: str = Field(min_length=1)
    failure_domain: str = Field(min_length=1)
    failure_type: str = Field(min_length=1)
    executor_reason: str = Field(min_length=1)
    verifier_reason: str | None = None
    test_failures: tuple[str, ...] = ()
    rejected_edits: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    previous_step_contract: dict[str, object] | None = None
    artifact_refs: tuple[str, ...] = ()
    created_at: str = Field(default_factory=utc_now)


def coerce_executor_reason(*, code: str, detail: str | None = None) -> str:
    """Never return empty: empty detail must not crash FailureEvidenceV1 construction.

    Live (2026-08-27): RealExecuteError with detail='' raised ValidationError on
    FailureEvidenceV1.executor_reason, which _control masked as UNCAUGHT_CONTROL_ERROR
    and hid the typed executor failure code from the CLI.
    """
    text = str(detail or "").strip()
    if text:
        return text
    code_text = str(code or "").strip()
    if code_text:
        return code_text
    return "REAL_EXECUTE_FAILED_NO_DETAIL"


class FailureReportV1(_ContractBase):
    """P30: Structured agent failure report — every agent in the pipeline submits
    a machine-readable report when it fails.

    Architecture rule #6: "Execution produces observations, not claims of success."
    Architecture rule #8: "Failure returns to repair/replan."

    Captures the full context of a failure so upstream agents, operators, and the
    feedback loop can diagnose root causes without tracing 7000-line backends.
    Persisted in the evidence system and surfaced through the API/CLI.
    """

    id: str = Field(default_factory=_uuid)
    agent_id: str = Field(min_length=1)
    agent_role: str = Field(
        default="unknown",
        description="Logical role: planner, executor, verifier, dispatcher, "
        "exploration_gate, obligation_coverage, scope_checker, admission, lifecycle",
    )
    agent_component: str = Field(
        default="",
        description="Specific function/module, e.g. 'propose_step_contract', 'run_real_execute'",
    )
    phase: str = Field(min_length=1)
    objective_id: str = ""
    cycle_id: str = ""
    step_id: str = ""
    attempt_number: int = 0
    severity: str = Field(
        default="BLOCKING",
        description="BLOCKING (stops pipeline), DEGRADED (continues with reduced quality), "
        "INFORMATIONAL (non-critical, logged for analysis)",
    )
    attempted_action: str = Field(min_length=1)
    failure_code: str = Field(min_length=1)
    failure_detail: str = ""
    evidence_available: tuple[str, ...] = ()
    evidence_missing: tuple[str, ...] = ()
    suggested_recovery: str = ""
    upstream_dependency: str = ""
    retryable: bool = False
    retry_after_condition: str = ""
    timestamp: str = Field(default_factory=utc_now)
    duration_ms: int | None = None


class LoopBudgetV1(_ContractBase):
    """AIX-MPE-003 Packet MPE-24: deterministic autonomous-loop ceilings."""

    max_step_repairs: int = Field(ge=0, default=3)
    max_plan_revisions: int = Field(ge=1, default=5)
    max_reunderstand_rounds: int = Field(ge=0, default=3)
    max_total_attempts: int = Field(ge=1, default=20)
    max_tokens: int = Field(ge=0, default=2_000_000)
    max_elapsed_seconds: int = Field(ge=0, default=3600)


class CycleOutcomeV1(_ContractBase):
    """Terminal AIX-side record. `terminal_status` is never a WorkHub-acceptance verb —
    AIX prepares evidence and a recommendation; it does not accept, close, or merge."""

    id: str = Field(default_factory=_uuid)
    cycle_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    terminal_status: CycleStatus
    completed_step_ids: tuple[str, ...] = ()
    failed_step_ids: tuple[str, ...] = ()
    skipped_step_ids: tuple[str, ...] = ()
    blocked_step_ids: tuple[str, ...] = ()
    objective_acceptance_map: dict[str, bool] = Field(default_factory=dict)
    evidence_bundle_ref: str | None = None
    unresolved_risks: tuple[str, ...] = ()
    total_usage: UsageV1 = Field(default_factory=UsageV1)
    review_recommendation: str | None = None
    produced_at: str = Field(default_factory=utc_now)

    @field_validator("terminal_status")
    @classmethod
    def _must_be_a_terminal_state(cls, value: str) -> str:
        if value not in CYCLE_TERMINAL_STATES:
            raise ValueError(f"terminal_status must be one of {sorted(CYCLE_TERMINAL_STATES)}, got {value!r}")
        return value

    @field_validator("terminal_status")
    @classmethod
    def _reject_lifecycle_acceptance_language(cls, value: str) -> str:
        forbidden_substrings = ("ACCEPT", "APPROV", "CLOS", "MERG")
        upper = value.upper()
        if any(token in upper for token in forbidden_substrings) and value != "COMPLETED_FOR_REVIEW":
            raise ValueError(f"terminal_status must not carry WorkHub-acceptance meaning: {value!r}")
        return value


__all__ = [
    "CONTRACT_VERSION",
    "CycleStatus",
    "CYCLE_TERMINAL_STATES",
    "StepStatus",
    "AttemptOutcome",
    "AgentRole",
    "RiskTier",
    "ApprovalPolicy",
    "VerificationVerdict",
    "StepRuntimeStatus",
    "PlannerIterationDecision",
    "ControllerAction",
    "FORBIDDEN_EFFECTS",
    "BudgetV1",
    "UsageV1",
    "ObjectiveEnvelopeV1",
    "ObligationStatus",
    "ObjectiveObligationV1",
    "ExecutionCycleV1",
    "RequiredEffectKind",
    "STRUCTURAL_EFFECT_KINDS",
    "MethodGroupSpecV1",
    "RequiredEffectV1",
    "ActionStatus",
    "ActionRequestV1",
    "ActionObservationV1",
    "StepContractV1",
    "PlanRevisionStatus",
    "PlanRevisionV1",
    "StepAttemptV1",
    "UnderstandingEvidenceV1",
    "StepContextCapsuleV1",
    "ObjectiveWorldStateV1",
    "ExecutorPackageV1",
    "StepRuntimeStateV1",
    "ExecutorProfileV1",
    "RepairDirectiveV1",
    "FailureEvidenceV1",
    "coerce_executor_reason",
    "LoopBudgetV1",
    "ReplanDecisionV1",
    "CycleOutcomeV1",
]
