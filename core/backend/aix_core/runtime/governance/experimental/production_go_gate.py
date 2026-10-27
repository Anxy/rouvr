"""Fail-closed evaluator for AIX-E2E-CYCLE-001P production evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

REQUIRED_PRODUCTION_CHILDREN = (
    "001P-W01",
    "001P-W02",
    "001P-W03",
    "001P-W04",
    "001P-W05",
    "001P-W06",
    "001P-W07",
    "001P-W08",
    "001P-W09",
    "001P-W10",
    "001P.5",
)

_PRODUCTION_GO_AUTHORITY = {"001P-W10", "001P.5"}
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ProductionGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: str
    candidate_sha: str | None
    reason_codes: tuple[str, ...]
    child_statuses: dict[str, str] = Field(default_factory=dict)
    human_signature_present: bool = False
    independent_checkpoint_go: bool = False


def load_production_evidence(evidence_root: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(evidence_root)
    payloads: dict[str, dict[str, Any]] = {}
    for child in REQUIRED_PRODUCTION_CHILDREN:
        result_path = root / child / "result.json"
        if result_path.is_file():
            payloads[child] = json.loads(result_path.read_text(encoding="utf-8"))
    return payloads


def evaluate_production_gate(
    evidence: Mapping[str, Mapping[str, Any]],
) -> ProductionGateDecision:
    """Return GO only for one frozen candidate with every authority gate satisfied."""

    reasons: set[str] = set()
    statuses: dict[str, str] = {}
    candidate_values: set[str] = set()

    for child in REQUIRED_PRODUCTION_CHILDREN:
        payload = evidence.get(child)
        if payload is None:
            reasons.add(f"EVIDENCE_MISSING:{child}")
            continue
        if payload.get("child") != child:
            reasons.add(f"CHILD_ID_MISMATCH:{child}")
        status = str(payload.get("status") or "").upper()
        statuses[child] = status
        if status != "GO":
            reasons.add(f"CHILD_NOT_GO:{child}:{status or 'MISSING'}")
        candidate_sha = str(payload.get("candidate_sha") or "").lower()
        if candidate_sha:
            candidate_values.add(candidate_sha)
        else:
            reasons.add(f"CANDIDATE_SHA_MISSING:{child}")
        production = str(payload.get("production") or "").upper()
        if production == "GO" and child not in _PRODUCTION_GO_AUTHORITY:
            reasons.add(f"UNAUTHORIZED_PRODUCTION_GO:{child}")

    candidate_sha = next(iter(candidate_values)) if len(candidate_values) == 1 else None
    if len(candidate_values) != 1:
        reasons.add("CANDIDATE_SHA_DRIFT")
    elif not _SHA_PATTERN.fullmatch(candidate_sha or ""):
        reasons.add("CANDIDATE_NOT_FROZEN_SHA")

    human_payload = evidence.get("001P-W10", {})
    human_signature = bool(human_payload.get("human_signature_present"))
    if not human_signature:
        reasons.add("HUMAN_SIGNATURE_REQUIRED")
    if str(human_payload.get("production") or "").upper() != "GO":
        reasons.add("HUMAN_PRODUCTION_GO_REQUIRED")

    independent_payload = evidence.get("001P.5", {})
    independent_go = (
        bool(independent_payload.get("independent_reviewer"))
        and str(independent_payload.get("independent_decision") or "").upper() == "GO"
        and str(independent_payload.get("production") or "").upper() == "GO"
    )
    if not independent_go:
        reasons.add("INDEPENDENT_CHECKPOINT_GO_REQUIRED")

    ordered_reasons = tuple(sorted(reasons))
    return ProductionGateDecision(
        decision="GO" if not ordered_reasons else "NO_GO",
        candidate_sha=candidate_sha,
        reason_codes=ordered_reasons,
        child_statuses=statuses,
        human_signature_present=human_signature,
        independent_checkpoint_go=independent_go,
    )
