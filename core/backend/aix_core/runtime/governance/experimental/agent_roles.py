"""Canonical five-role decision for the governed objective cycle."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

FIVE_ROLE_DECISION_VERSION = "aix.objective-cycle.five-role-decision.v1"

UNIVERSAL_PROHIBITIONS = (
    "auto_accept",
    "auto_close",
    "auto_create_workhub_task",
    "direct_provider_route",
    "recursive_agent_spawn",
    "workhub_lifecycle_mutation",
)


class AgentRoleId(StrEnum):
    PLANNER = "planner"
    EXECUTOR_CODER = "executor_coder"
    VERIFIER = "verifier"
    RECOVERY_PLANNER = "recovery_planner"
    REVIEW_PACKET_CURATOR = "review_packet_curator"


class AgentRoleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_version: str = FIVE_ROLE_DECISION_VERSION
    role_id: AgentRoleId
    responsibility: str = Field(min_length=1)
    may: tuple[str, ...] = Field(min_length=1)
    must_not: tuple[str, ...] = Field(min_length=1)
    autonomy_cap: str = Field(pattern=r"^L[0-4]$")
    requires_independence_from: tuple[AgentRoleId, ...] = ()


FIVE_ROLE_DECISIONS: tuple[AgentRoleDecision, ...] = (
    AgentRoleDecision(
        role_id=AgentRoleId.PLANNER,
        responsibility="Ground, clarify, risk-assess and compile a bounded candidate plan.",
        may=(
            "read_authoritative_context",
            "request_human_clarification",
            "propose_step_contracts",
            "compile_dependencies",
        ),
        must_not=UNIVERSAL_PROHIBITIONS
        + (
            "execute_commands",
            "grant_approval",
            "widen_objective_scope",
        ),
        autonomy_cap="L1",
    ),
    AgentRoleDecision(
        role_id=AgentRoleId.EXECUTOR_CODER,
        responsibility="Execute one approved step attempt inside its scoped workspace and Move Token.",
        may=("read_step_capsule", "use_scoped_tools", "produce_diff_or_artifact", "attach_attempt_evidence"),
        must_not=UNIVERSAL_PROHIBITIONS
        + (
            "self_retry",
            "self_replan",
            "self_verify",
            "select_fallback_executor",
            "escape_workspace",
        ),
        autonomy_cap="L2",
    ),
    AgentRoleDecision(
        role_id=AgentRoleId.VERIFIER,
        responsibility="Independently test step output and issue an evidence-backed pass or fail verdict.",
        may=("read_step_contract", "read_attempt_evidence", "run_read_only_validation", "issue_step_verdict"),
        must_not=UNIVERSAL_PROHIBITIONS
        + (
            "rewrite_executor_output",
            "hide_skipped_checks",
            "repair_failed_output",
        ),
        autonomy_cap="L0",
        requires_independence_from=(AgentRoleId.EXECUTOR_CODER,),
    ),
    AgentRoleDecision(
        role_id=AgentRoleId.RECOVERY_PLANNER,
        responsibility="Classify failure and propose a bounded repair or replan within objective, policy and budget.",
        may=("read_failure_evidence", "classify_failure", "propose_repair", "propose_replan"),
        must_not=UNIVERSAL_PROHIBITIONS
        + (
            "execute_repair",
            "silently_widen_scope",
            "bypass_retry_budget",
            "bypass_required_approval",
        ),
        autonomy_cap="L1",
        requires_independence_from=(AgentRoleId.EXECUTOR_CODER,),
    ),
    AgentRoleDecision(
        role_id=AgentRoleId.REVIEW_PACKET_CURATOR,
        responsibility="Map verified objective criteria to immutable evidence and prepare a review recommendation.",
        may=("read_verified_evidence", "map_objective_acceptance", "prepare_review_packet", "recommend_disposition"),
        must_not=UNIVERSAL_PROHIBITIONS
        + (
            "accept_workhub_task",
            "reject_workhub_task",
            "manufacture_human_approval",
            "change_verified_evidence",
        ),
        autonomy_cap="L0",
        requires_independence_from=(AgentRoleId.EXECUTOR_CODER,),
    ),
)


def get_role_decision(role_id: AgentRoleId | str) -> AgentRoleDecision:
    normalized = AgentRoleId(role_id)
    return next(decision for decision in FIVE_ROLE_DECISIONS if decision.role_id is normalized)
