"""AIX Feature 12 (Packet 03/04) — Canonical Objective Verifier with GapV1 integration.

Evaluates objective satisfaction against ObjectiveVerificationContractV1 (OVC)
using isolated runtime probes and step verification evidence.
Produces authoritative VerificationResultV1 (scope=OBJECTIVE), VerificationDecisionV1, and GapV1 entities.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
)
from backend.aix_core.runtime.verification.runtime_probe_runner import (
    RuntimeProbeRunner,
)
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionKind,
    VerificationDecisionV1,
)
from backend.aix_core.runtime.verification.verification_result import (
    OVItemResultV1,
    ProofStatus,
    VerificationResultV1,
)
from backend.aix_core.runtime.verification.gap import (
    GapCategory,
    GapV1,
)


class ObjectiveVerifier:
    """Canonical objective-level verifier."""

    def __init__(self, probe_runner: RuntimeProbeRunner | None = None) -> None:
        self.probe_runner = probe_runner or RuntimeProbeRunner()

    def verify_objective(
        self,
        *,
        ovc: ObjectiveVerificationContractV1,
        workspace_path: Path | str | None = None,
        step_results: dict[str, Any] | None = None,
        require_runtime_probes: bool = True,
    ) -> tuple[VerificationResultV1, VerificationDecisionV1, list[GapV1]]:
        """Perform authoritative objective verification against OVC."""
        runner = self.probe_runner
        if workspace_path:
            runner = RuntimeProbeRunner(workspace_path)

        # 1. Run probes for OVC items
        ov_item_results: list[OVItemResultV1] = runner.run_all_probes_for_ovc(ovc)

        # 2. Evaluate static vs runtime status & create GapV1 entities
        static_pass = True
        runtime_pass = True
        blocking_failures: list[str] = []
        gaps: list[GapV1] = []

        v_id = f"v_obj_{ovc.objective_id}_{hashlib.md5((ovc.hash + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:8]}"

        for r in ov_item_results:
            if r.status == "FAIL":
                item_ovc = next((i for i in ovc.ov_items if i.ov_id == r.ov_id), None)
                if item_ovc and item_ovc.proof_method in ("RUNTIME_PROBE", "E2E"):
                    runtime_pass = False
                else:
                    static_pass = False
                
                cat: GapCategory = "OBJECTIVE_DEVIATION" if item_ovc and ("sqlite" in item_ovc.description.lower() or item_ovc.category == "STACK") else "RUNTIME_FAILURE"
                gap = GapV1(
                    gap_id=f"gap_{ovc.objective_id}_{r.ov_id.lower()}_{hashlib.md5(r.observed.encode()).hexdigest()[:6]}",
                    objective_id=ovc.objective_id,
                    verification_id=v_id,
                    ov_id=r.ov_id,
                    category=cat,
                    expected=r.expected,
                    observed=r.observed,
                    evidence=r.evidence_refs,
                    severity="BLOCKER",
                    status="OPEN",
                    provenance={"verifier": "objective_verifier", "ov_id": r.ov_id},
                )
                gaps.append(gap)
                blocking_failures.append(f"{r.ov_id} failed: {r.observed}")

        runtime_status: ProofStatus = "PASS" if runtime_pass else "FAIL"
        static_status: ProofStatus = "PASS" if static_pass else "FAIL"

        # If runtime probes were required but not run or failed closed
        has_runtime_items = any(i.proof_method in ("RUNTIME_PROBE", "E2E") for i in ovc.ov_items)
        if has_runtime_items and not runtime_pass:
            runtime_status = "FAIL"

        result = VerificationResultV1(
            verification_id=v_id,
            objective_id=ovc.objective_id,
            scope="OBJECTIVE",
            ov_item_results=ov_item_results,
            static_proof_status=static_status,
            runtime_proof_status=runtime_status,
            blocking_failures=blocking_failures,
            verifier={"role": "objective_verifier", "model": "governed_independent"},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # 3. Decision
        if gaps or runtime_status == "FAIL" or static_status == "FAIL":
            dec_kind: VerificationDecisionKind = "REPAIR"
            reasoning = f"Objective verification failed: {len(gaps)} gaps detected. {'; '.join(blocking_failures[:3])}"
        else:
            dec_kind = "PASS"
            reasoning = "All OVC items verified via runtime probes and static proof."

        decision = VerificationDecisionV1(
            decision_id=f"dec_{v_id}",
            verification_id=v_id,
            objective_id=ovc.objective_id,
            decision=dec_kind,
            reasoning=reasoning,
            blocking_gaps=[g.gap_id for g in gaps],
            decision_model="objective_verifier",
        )

        return result, decision, gaps


__all__ = [
    "ObjectiveVerifier",
]
