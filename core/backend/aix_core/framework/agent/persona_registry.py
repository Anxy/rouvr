"""AIX Framework Persona Registry.

Sprint: F7 Agent Framework (F7-P3)
Grounded in: agent_roles.py FIVE_ROLE_DECISIONS, system prompts for planner/executor/verifier.
"""

from __future__ import annotations

from backend.aix_core.framework.agent.models import Persona, Role

PLANNER_SYSTEM_PROMPT = """You are the AIX Objective Planner.
Your responsibility is to ground, clarify, risk-assess, and compile a bounded candidate plan.
You must NOT execute commands, grant approvals, or widen the objective scope."""

EXECUTOR_CODER_SYSTEM_PROMPT = """You are the AIX Executor Coder.
Your responsibility is to execute one approved step attempt inside its scoped workspace and Move Token.
You must NOT self-retry, self-replan, self-verify, select fallback executors, or escape your scoped workspace."""

VERIFIER_SYSTEM_PROMPT = """You are the AIX Independent Verifier.
Your responsibility is to independently test step output and issue an evidence-backed pass or fail verdict.
You must NOT rewrite executor output, hide skipped checks, or repair failed output. Fail-closed is absolute."""

RECOVERY_PLANNER_SYSTEM_PROMPT = """You are the AIX Recovery Planner.
Your responsibility is to classify failures and propose bounded repairs or replans within policy and budget.
You must NOT execute repairs, silently widen scope, or bypass retry/replan budgets."""

REVIEW_PACKET_CURATOR_SYSTEM_PROMPT = """You are the AIX Review Packet Curator.
Your responsibility is to summarize diffs, test verdicts, and evidence bundles for human operator review.
You must NOT alter verification verdicts or emit synthetic approval tokens."""


CANONICAL_PERSONAS: dict[Role, Persona] = {
    Role.PLANNER: Persona(
        persona_id="persona_planner_v1",
        name="Objective Planner",
        role=Role.PLANNER,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        description="Decomposes objectives into validated step contracts.",
        autonomy_cap="L1",
    ),
    Role.EXECUTOR_CODER: Persona(
        persona_id="persona_executor_coder_v1",
        name="Executor Coder",
        role=Role.EXECUTOR_CODER,
        system_prompt=EXECUTOR_CODER_SYSTEM_PROMPT,
        description="Produces scoped diffs conforming to step contracts.",
        autonomy_cap="L2",
    ),
    Role.VERIFIER: Persona(
        persona_id="persona_verifier_v1",
        name="Independent Verifier",
        role=Role.VERIFIER,
        system_prompt=VERIFIER_SYSTEM_PROMPT,
        description="Independently verifies step outcomes against acceptance criteria.",
        autonomy_cap="L0",
    ),
    Role.RECOVERY_PLANNER: Persona(
        persona_id="persona_recovery_planner_v1",
        name="Recovery Planner",
        role=Role.RECOVERY_PLANNER,
        system_prompt=RECOVERY_PLANNER_SYSTEM_PROMPT,
        description="Formulates bounded repair directives and replans on failure.",
        autonomy_cap="L1",
    ),
    Role.REVIEW_PACKET_CURATOR: Persona(
        persona_id="persona_review_packet_curator_v1",
        name="Review Packet Curator",
        role=Role.REVIEW_PACKET_CURATOR,
        system_prompt=REVIEW_PACKET_CURATOR_SYSTEM_PROMPT,
        description="Packages verification and attempt evidence for human review.",
        autonomy_cap="L0",
    ),
}


class PersonaRegistry:
    """Registry managing canonical agent personas."""

    def __init__(self) -> None:
        self._personas: dict[Role, Persona] = dict(CANONICAL_PERSONAS)

    def get_persona(self, role: Role | str) -> Persona | None:
        role_enum = Role(role) if isinstance(role, str) and role in Role._value2member_map_ else role
        return self._personas.get(role_enum)  # type: ignore[arg-type]

    def register_persona(self, persona: Persona) -> None:
        self._personas[persona.role] = persona

    def list_personas(self) -> list[Persona]:
        return list(self._personas.values())


_default_persona_registry = PersonaRegistry()


def get_persona_registry() -> PersonaRegistry:
    return _default_persona_registry
