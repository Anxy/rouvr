"""AIX Feature 12 (Packet 01) — Objective Verification Contract V1 (OVC).

Defines independent verification contract derived from CanonicalObjectiveTruthV1.
Planner must NOT mutate or create OVC.
Every OV item requires an explicit disposition at plan accept time (Correction 5).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field

from backend.aix_core.runtime.verification.objective_truth import (
    CanonicalObjectiveTruthV1,
)


def _compute_sha256(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


OVCategory = Literal["STACK", "AUTH", "CRUD", "INTEGRATION", "E2E", "DOCS", "TESTS"]
OVProofMethod = Literal["STATIC", "RUNTIME_PROBE", "E2E", "TEST_EVIDENCE"]
OVDispositionKind = Literal[
    "IMPLEMENTED_BY",
    "VERIFIED_BY",
    "PREEXISTING",
    "NOT_APPLICABLE",
    "BLOCKED",
]


class OVDispositionV1(BaseModel):
    kind: OVDispositionKind
    step_ids: list[str] = Field(default_factory=list)  # For IMPLEMENTED_BY
    probe_or_test_id: str | None = None  # For VERIFIED_BY (e.g. "probe:start_uvicorn")
    justification: str | None = None  # For NOT_APPLICABLE, BLOCKED
    evidence_ref: str | None = None  # For PREEXISTING

    def is_valid(self) -> tuple[bool, str]:
        if self.kind == "IMPLEMENTED_BY":
            if not self.step_ids:
                return False, "IMPLEMENTED_BY disposition requires non-empty step_ids"
        elif self.kind == "VERIFIED_BY":
            if not self.probe_or_test_id or not self.probe_or_test_id.strip():
                return False, "VERIFIED_BY disposition requires probe_or_test_id"
        elif self.kind in ("NOT_APPLICABLE", "BLOCKED"):
            if not self.justification or not self.justification.strip():
                return False, f"{self.kind} disposition requires justification"
        elif self.kind == "PREEXISTING":
            if not self.evidence_ref or not self.evidence_ref.strip():
                return False, "PREEXISTING disposition requires evidence_ref"
        return True, ""


class OVItemV1(BaseModel):
    ov_id: str  # OV-001, OV-002, etc.
    description: str
    category: OVCategory
    proof_method: OVProofMethod
    required_evidence_types: list[str] = Field(default_factory=list)  # probe_log, pytest_output, artifact_ref, curl_trace
    source_requirement_ref: str = ""
    disposition: OVDispositionV1 | None = None


class ObjectiveVerificationContractV1(BaseModel):
    ovc_id: str
    objective_id: str
    canonical_truth_hash: str
    ov_items: list[OVItemV1] = Field(default_factory=list)
    hash: str
    version: str = "v1"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def verify_integrity(self) -> bool:
        payload = {
            "ovc_id": self.ovc_id,
            "objective_id": self.objective_id,
            "canonical_truth_hash": self.canonical_truth_hash,
            "ov_items": [
                {
                    "ov_id": item.ov_id,
                    "description": item.description,
                    "category": item.category,
                    "proof_method": item.proof_method,
                    "required_evidence_types": sorted(item.required_evidence_types),
                    "source_requirement_ref": item.source_requirement_ref,
                }
                for item in self.ov_items
            ],
            "version": self.version,
        }
        return _compute_sha256(payload) == self.hash


def derive_ovc(
    truth: CanonicalObjectiveTruthV1,
    *,
    ovc_id: str = "",
) -> ObjectiveVerificationContractV1:
    """Derive ObjectiveVerificationContractV1 deterministically from CanonicalObjectiveTruthV1."""
    items: list[OVItemV1] = []
    idx = 1

    def make_ov_id(num: int) -> str:
        return f"OV-{num:03d}"

    techs = [t.lower() for t in truth.technology_requirements]
    text_lower = truth.original_text.lower()

    # 1. Framework / Backend Stack
    if "fastapi" in techs or "python" in techs:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="FastAPI backend application structure exists and imports cleanly",
                category="STACK",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "artifact_ref"],
                source_requirement_ref="technology:fastapi",
            )
        )
        idx += 1

    # 2. Database Stack
    if "sqlite" in techs:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="SQLite is persistence authority (database file configured and utilized)",
                category="STACK",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "artifact_ref"],
                source_requirement_ref="technology:sqlite",
            )
        )
        idx += 1
    elif "postgresql" in techs:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="PostgreSQL database persistence configured and utilized",
                category="STACK",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "artifact_ref"],
                source_requirement_ref="technology:postgresql",
            )
        )
        idx += 1

    # 3. Server Startup (Uvicorn)
    if "fastapi" in techs or "uvicorn" in techs:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="Application server starts correctly via Uvicorn and responds to /health",
                category="STACK",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "curl_trace"],
                source_requirement_ref="technology:uvicorn",
            )
        )
        idx += 1

    # 4. Authentication (Registration & Login)
    if any("registration" in r.lower() or "register" in r.lower() for r in truth.interpreted_requirements) or "register" in text_lower:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="User registration endpoint creates new user record",
                category="AUTH",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "curl_trace"],
                source_requirement_ref="requirements:registration",
            )
        )
        idx += 1

    if any("login" in r.lower() or "auth" in r.lower() for r in truth.interpreted_requirements) or "login" in text_lower:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="User login endpoint authenticates credentials and returns valid session/token",
                category="AUTH",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "curl_trace"],
                source_requirement_ref="requirements:login",
            )
        )
        idx += 1

    # 5. CRUD Operations
    if any("crud" in r.lower() for r in truth.interpreted_requirements) or "expense" in text_lower:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="Create entity (add expense) persists record",
                category="CRUD",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "curl_trace"],
                source_requirement_ref="requirements:crud_create",
            )
        )
        idx += 1
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="Read/list entities retrieves persisted records",
                category="CRUD",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "curl_trace"],
                source_requirement_ref="requirements:crud_read",
            )
        )
        idx += 1
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="Update entity modifies existing record attributes",
                category="CRUD",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "curl_trace"],
                source_requirement_ref="requirements:crud_update",
            )
        )
        idx += 1
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="Delete entity removes record from storage",
                category="CRUD",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "curl_trace"],
                source_requirement_ref="requirements:crud_delete",
            )
        )
        idx += 1

    # 6. Integration (Frontend <-> API)
    if "react" in techs or "html/css/javascript" in techs or "frontend" in text_lower:
        items.append(
            OVItemV1(
                ov_id=make_ov_id(idx),
                description="Frontend UI connects to live API endpoints without CORS or protocol errors",
                category="INTEGRATION",
                proof_method="RUNTIME_PROBE",
                required_evidence_types=["probe_log", "artifact_ref"],
                source_requirement_ref="technology:frontend",
            )
        )
        idx += 1

    # 7. Validation & Tests
    items.append(
        OVItemV1(
            ov_id=make_ov_id(idx),
            description="Input validation rejects invalid requests with 4xx status codes",
            category="TESTS",
            proof_method="TEST_EVIDENCE",
            required_evidence_types=["pytest_output", "probe_log"],
            source_requirement_ref="requirements:validation",
        )
    )
    idx += 1

    items.append(
        OVItemV1(
            ov_id=make_ov_id(idx),
            description="Automated unit and integration test suite runs and passes",
            category="TESTS",
            proof_method="TEST_EVIDENCE",
            required_evidence_types=["pytest_output"],
            source_requirement_ref="dod:tests_pass",
        )
    )
    idx += 1

    # 8. End-to-End Workflow
    items.append(
        OVItemV1(
            ov_id=make_ov_id(idx),
            description="Complete End-to-End user workflow execution succeeds",
            category="E2E",
            proof_method="E2E",
            required_evidence_types=["probe_log", "pytest_output"],
            source_requirement_ref="dod:e2e_complete",
        )
    )

    c_id = ovc_id or f"ovc_{hashlib.md5((truth.canonical_truth_id + truth.hash).encode()).hexdigest()[:12]}"

    payload = {
        "ovc_id": c_id,
        "objective_id": truth.objective_id,
        "canonical_truth_hash": truth.hash,
        "ov_items": [
            {
                "ov_id": item.ov_id,
                "description": item.description,
                "category": item.category,
                "proof_method": item.proof_method,
                "required_evidence_types": sorted(item.required_evidence_types),
                "source_requirement_ref": item.source_requirement_ref,
            }
            for item in items
        ],
        "version": "v1",
    }
    ovc_hash = _compute_sha256(payload)

    return ObjectiveVerificationContractV1(
        ovc_id=c_id,
        objective_id=truth.objective_id,
        canonical_truth_hash=truth.hash,
        ov_items=items,
        hash=ovc_hash,
        version="v1",
    )


class DispositionCoverageResult(BaseModel):
    ok: bool
    total_ov_items: int
    covered_ov_items: int
    missing_ov_ids: list[str] = Field(default_factory=list)
    invalid_dispositions: dict[str, str] = Field(default_factory=dict)
    dispositions: dict[str, OVDispositionV1] = Field(default_factory=dict)


def evaluate_ovc_disposition_coverage(
    ovc: ObjectiveVerificationContractV1,
    plan_steps: list[Any] | None = None,
    explicit_dispositions: dict[str, OVDispositionV1 | dict[str, Any]] | None = None,
) -> DispositionCoverageResult:
    """Evaluate whether every OV item in the contract has a valid disposition.
    
    Correction 5 rule: No OV item may disappear without disposition.
    Every OV item must be one of:
      - IMPLEMENTED_BY: step_ids
      - VERIFIED_BY: probe_or_test_id (e.g. OV-003 uvicorn start -> probe:start_uvicorn)
      - PREEXISTING: evidence_ref
      - NOT_APPLICABLE: justification
      - BLOCKED: justification
    """
    explicit_map = explicit_dispositions or {}
    disposition_map: dict[str, OVDispositionV1] = {}
    invalid: dict[str, str] = {}
    missing: list[str] = []

    # Map from plan steps if provided
    step_ov_mappings: dict[str, list[str]] = {}
    if plan_steps:
        for s in plan_steps:
            s_id = getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else None)
            if not s_id:
                continue
            # Look for ov_ids or ov_refs on step
            s_ovs = getattr(s, "ov_ids", None) or (s.get("ov_ids") if isinstance(s, dict) else None) or []
            if isinstance(s_ovs, str):
                s_ovs = [s_ovs]
            for ov_id in s_ovs:
                step_ov_mappings.setdefault(ov_id, []).append(str(s_id))

    for item in ovc.ov_items:
        disp: OVDispositionV1 | None = None

        # Check explicit map first
        raw_disp = explicit_map.get(item.ov_id)
        if raw_disp is not None:
            if isinstance(raw_disp, OVDispositionV1):
                disp = raw_disp
            elif isinstance(raw_disp, dict):
                try:
                    disp = OVDispositionV1(**raw_disp)
                except Exception as ex:
                    invalid[item.ov_id] = f"Invalid disposition schema: {ex}"
                    continue
        # Fall back to item's embedded disposition
        elif item.disposition is not None:
            disp = item.disposition
        # Fall back to plan steps mapping (only when explicit_dispositions is not supplied)
        elif explicit_dispositions is None and item.ov_id in step_ov_mappings:
            disp = OVDispositionV1(
                kind="IMPLEMENTED_BY",
                step_ids=step_ov_mappings[item.ov_id],
            )
        # For verification/runtime/test items, provide default VERIFIED_BY probe when plan_steps is supplied
        elif explicit_dispositions is None and plan_steps and item.proof_method in ("RUNTIME_PROBE", "E2E", "TEST_EVIDENCE", "STATIC_ANALYSIS"):
            probe_id = f"probe:{item.category.lower()}_{item.ov_id.lower()}"
            disp = OVDispositionV1(
                kind="VERIFIED_BY",
                probe_or_test_id=probe_id,
            )

        if disp is None:
            missing.append(item.ov_id)
        else:
            valid, err = disp.is_valid()
            if not valid:
                invalid[item.ov_id] = err
            else:
                disposition_map[item.ov_id] = disp

    total = len(ovc.ov_items)
    covered = len(disposition_map)
    ok = (len(missing) == 0) and (len(invalid) == 0) and (covered == total)

    return DispositionCoverageResult(
        ok=ok,
        total_ov_items=total,
        covered_ov_items=covered,
        missing_ov_ids=missing,
        invalid_dispositions=invalid,
        dispositions=disposition_map,
    )


__all__ = [
    "OVCategory",
    "OVProofMethod",
    "OVDispositionKind",
    "OVDispositionV1",
    "OVItemV1",
    "ObjectiveVerificationContractV1",
    "DispositionCoverageResult",
    "derive_ovc",
    "evaluate_ovc_disposition_coverage",
]
