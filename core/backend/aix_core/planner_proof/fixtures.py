"""Representative multi-obligation fixture for planner proof."""

from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    UnderstandingEvidenceV1,
)


@dataclass(frozen=True)
class PlannerProofFixture:
    objective: str
    definition_of_done: list[str]
    understanding: UnderstandingEvidenceV1
    constraints: list[str]
    obligations: tuple[ObjectiveObligationV1, ...]
    budget: BudgetV1
    tenant_id: str
    cycle_id: str
    objective_id: str
    # Recorded Integration-shaped multi-step reply (production path, no live LLM required)
    recorded_multi_step_payload: dict


def representative_multi_obligation_fixture() -> PlannerProofFixture:
    obs = (
        ObjectiveObligationV1(objective_id="obj-proof", description="group methods", required=True),
        ObjectiveObligationV1(objective_id="obj-proof", description="extract helpers", required=True),
        ObjectiveObligationV1(objective_id="obj-proof", description="remove unused imports", required=True),
    )
    understanding = UnderstandingEvidenceV1(
        objective_id="obj-proof",
        cycle_id="cyc-proof",
        files_read=("CronJobController.php",),
        symbols_found=("runBillingCron",),
        observed_problem="refactor CronJobController.php into coherent units",
        understanding_complete=True,
    )
    payload = {
        "reply": "multi-step decomposition with provenance proof",
        "needs_more_evidence": False,
        "steps": [
            {
                "purpose": "inventory public cron actions",
                "target_scope": ["CronJobController.php"],
                "acceptance_criteria": ["inventory exists"],
                "validation_commands": ["php -l CronJobController.php"],
                "obligation_refs": [obs[0].id],
                "depends_on": [],
                "risk_tier": "LOW",
            },
            {
                "purpose": "extract shared billing helpers",
                "target_scope": ["CronJobController.php", "runBillingCron"],
                "acceptance_criteria": ["helpers extracted"],
                "validation_commands": ["php -l CronJobController.php"],
                "obligation_refs": [obs[1].id],
                "depends_on": [0],
                "risk_tier": "MEDIUM",
            },
            {
                "purpose": "remove unused imports",
                "target_scope": ["CronJobController.php"],
                "acceptance_criteria": ["imports cleaned"],
                "validation_commands": ["php -l CronJobController.php"],
                "obligation_refs": [obs[2].id],
                "depends_on": [1],
                "risk_tier": "LOW",
            },
        ],
    }
    return PlannerProofFixture(
        objective="Refactor CronJobController.php: group methods, extract helpers, remove unused imports",
        definition_of_done=["group methods", "extract helpers", "remove unused imports"],
        understanding=understanding,
        constraints=["do not rename public methods"],
        obligations=obs,
        budget=BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1),
        tenant_id="tenant-proof",
        cycle_id="cyc-proof",
        objective_id="obj-proof",
        recorded_multi_step_payload=payload,
    )
