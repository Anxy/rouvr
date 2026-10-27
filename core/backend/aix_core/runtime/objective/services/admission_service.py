"""AIX Objective Admission Service.

Extracts admission, intent freezing, understanding grounding, envelope construction,
and obligation derivation into a dedicated service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.objective.contracts import (
    ObjectiveEnvelopeV1,
    ObjectiveObligationV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.obligations import derive_obligations
from backend.aix_core.runtime.objective.outcome_obligations import (
    compile_outcome_obligations,
)
from backend.aix_core.runtime.objective.provenance.admit import (
    freeze_user_intent,
    original_objective_alias,
)
from backend.aix_core.runtime.objective.provenance.contracts import UserIntent
from backend.aix_core.runtime.objective.provenance.understanding import (
    UnderstandingV1Result,
    build_understanding_v1,
)
from backend.aix_core.runtime.objective.task_family import classify_task_family
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
from backend.aix_core.runtime.verification.objective_truth import derive_canonical_truth
from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
    derive_ovc,
)

logger = logging.getLogger(__name__)


@dataclass
class AdmittedObjectivePackage:
    objective_id: str
    intent: UserIntent
    understanding: UnderstandingV1Result
    envelope: ObjectiveEnvelopeV1 | None
    obligations: list[ObjectiveObligationV1]
    ovc: ObjectiveVerificationContractV1 | None
    task_family: str


class AdmissionService:
    """Handles objective admission, intent grounding, and initial contract derivation."""

    def __init__(self) -> None:
        pass

    def admit(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        body: dict[str, Any],
        command_id: str,
    ) -> AdmittedObjectivePackage:
        """Processes raw admission request and produces admitted domain objects."""
        objective_text = str(body.get("objective") or "").strip()
        task_class = str(body.get("task_class") or "bounded_repair").strip()
        raw_dod = body.get("definition_of_done") or ()
        if isinstance(raw_dod, str):
            dod = (raw_dod,)
        else:
            dod = tuple(str(x) for x in raw_dod)

        raw_allowed = body.get("allowed_files") or ()
        if isinstance(raw_allowed, str):
            allowed_files = (raw_allowed,)
        else:
            allowed_files = tuple(str(x) for x in raw_allowed)

        objective_id = f"obj_{new_ulid()}"
        intent = freeze_user_intent(
            objective_id=objective_id,
            text=objective_text,
            definition_of_done=dod,
        )

        task_family_res = classify_task_family(objective_text)
        task_family = task_family_res.task_family

        understanding = build_understanding_v1(
            intent=intent,
        )

        obligations = list(
            derive_obligations(
                objective_id=objective_id,
                objective=objective_text,
                definition_of_done=dod,
            )
        )

        truth = derive_canonical_truth(
            original_text=objective_text,
            objective_id=objective_id,
        )

        ovc = derive_ovc(truth)

        try:
            envelope = ObjectiveEnvelopeV1(
                version="v1",
                id=f"env_{new_ulid()}",
                objective_id=objective_id,
                tenant_id=tenant_id,
                task_class=task_class,
                intent_text=objective_text,
                definition_of_done=list(dod),
                allowed_files=list(allowed_files),
                created_at=intent.created_at,
            )
        except Exception:
            envelope = None

        return AdmittedObjectivePackage(
            objective_id=objective_id,
            intent=intent,
            understanding=understanding,
            envelope=envelope,
            obligations=obligations,
            ovc=ovc,
            task_family=task_family,
        )
