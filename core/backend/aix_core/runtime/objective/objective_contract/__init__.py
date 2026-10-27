"""C020: ObjectiveContract + mandatory understanding admission.

DoD: raw text never reaches planning without an admitted ObjectiveContract;
unknowns cannot silently become assumptions.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

CONTRACT_SCHEMA = "aix.objective_contract.v1"

AdmissionVerdict = Literal["ADMITTED", "INSUFFICIENT_EVIDENCE", "REJECTED"]


class ObjectiveContractError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class OutcomeSpec:
    outcome_id: str
    statement: str
    measurable: bool = True


@dataclass(frozen=True)
class ConstraintSpec:
    constraint_id: str
    statement: str
    hard: bool = True


@dataclass(frozen=True)
class AssumptionSpec:
    assumption_id: str
    statement: str
    source: Literal["stated", "derived"] = "stated"


@dataclass(frozen=True)
class UnknownSpec:
    unknown_id: str
    statement: str
    blocks_planning: bool = True


@dataclass
class ObjectiveContractV1:
    contract_id: str
    tenant_id: str
    objective_id: str | None
    raw_objective: str
    outcomes: list[OutcomeSpec] = field(default_factory=list)
    constraints: list[ConstraintSpec] = field(default_factory=list)
    assumptions: list[AssumptionSpec] = field(default_factory=list)
    unknowns: list[UnknownSpec] = field(default_factory=list)
    admission: AdmissionVerdict = "INSUFFICIENT_EVIDENCE"
    schema_version: str = CONTRACT_SCHEMA
    created_at: str = field(default_factory=_utc)
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "tenant_id": self.tenant_id,
            "objective_id": self.objective_id,
            "raw_objective": self.raw_objective,
            "outcomes": [o.__dict__ for o in self.outcomes],
            "constraints": [c.__dict__ for c in self.constraints],
            "assumptions": [a.__dict__ for a in self.assumptions],
            "unknowns": [u.__dict__ for u in self.unknowns],
            "admission": self.admission,
            "created_at": self.created_at,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ObjectiveContractV1":
        if str(raw.get("schema_version") or "") != CONTRACT_SCHEMA:
            raise ObjectiveContractError("UNKNOWN_CONTRACT_SCHEMA", str(raw.get("schema_version")))
        return cls(
            contract_id=str(raw["contract_id"]),
            tenant_id=str(raw["tenant_id"]),
            objective_id=raw.get("objective_id"),
            raw_objective=str(raw.get("raw_objective") or ""),
            outcomes=[OutcomeSpec(**o) for o in (raw.get("outcomes") or [])],
            constraints=[ConstraintSpec(**c) for c in (raw.get("constraints") or [])],
            assumptions=[AssumptionSpec(**a) for a in (raw.get("assumptions") or [])],
            unknowns=[UnknownSpec(**u) for u in (raw.get("unknowns") or [])],
            admission=str(raw.get("admission") or "INSUFFICIENT_EVIDENCE"),  # type: ignore[arg-type]
            created_at=str(raw.get("created_at") or _utc()),
            content_hash=str(raw.get("content_hash") or ""),
        )


_AMBIGUITY_MARKERS = (
    "maybe",
    "somehow",
    "whatever works",
    "figure it out",
    "as appropriate",
    "tbd",
    "todo",
    "not sure",
    "either",
    "or something",
)

_ASSUMPTION_MARKERS = (
    "assume ",
    "assuming ",
    "we assume",
)


def _sid(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def understand_objective(
    *,
    tenant_id: str,
    raw_objective: str,
    definition_of_done: list[str] | tuple[str, ...] | None = None,
    objective_id: str | None = None,
    allowed_files: list[str] | tuple[str, ...] | None = None,
) -> ObjectiveContractV1:
    """Deterministic understanding stage (model-backed hooks can enrich later).

    Never promotes unknowns into assumptions.
    """
    text = str(raw_objective or "").strip()
    if not text:
        raise ObjectiveContractError("EMPTY_OBJECTIVE", "raw objective required")
    if not tenant_id.strip():
        raise ObjectiveContractError("EMPTY_TENANT")

    outcomes: list[OutcomeSpec] = []
    for item in definition_of_done or ():
        stmt = str(item).strip()
        if stmt:
            outcomes.append(OutcomeSpec(outcome_id=_sid("out", stmt), statement=stmt))
    if not outcomes:
        # Primary sentence as a provisional outcome candidate — still may be insufficient.
        first = re.split(r"[.!\n]", text)[0].strip()
        if first:
            outcomes.append(OutcomeSpec(outcome_id=_sid("out", first), statement=first, measurable=False))

    constraints: list[ConstraintSpec] = []
    for path in allowed_files or ():
        p = str(path).strip()
        if p:
            stmt = f"mutate only within allowed file: {p}"
            constraints.append(ConstraintSpec(constraint_id=_sid("con", stmt), statement=stmt))

    assumptions: list[AssumptionSpec] = []
    unknowns: list[UnknownSpec] = []
    lowered = text.lower()

    for marker in _ASSUMPTION_MARKERS:
        if marker in lowered:
            # Explicitly stated assumptions only — never invented.
            idx = lowered.index(marker)
            snippet = text[idx : idx + 80].strip()
            assumptions.append(
                AssumptionSpec(assumption_id=_sid("asm", snippet), statement=snippet, source="stated")
            )

    for marker in _AMBIGUITY_MARKERS:
        if marker in lowered:
            unknowns.append(
                UnknownSpec(
                    unknown_id=_sid("unk", marker),
                    statement=f"Ambiguous phrasing requires clarification: {marker!r}",
                    blocks_planning=True,
                )
            )

    # Guardrail: no assumption statement may equal an unknown statement.
    assumption_texts = {a.statement.lower() for a in assumptions}
    for unk in unknowns:
        if unk.statement.lower() in assumption_texts:
            raise ObjectiveContractError(
                "UNKNOWN_PROMOTED_TO_ASSUMPTION",
                unk.statement,
            )

    contract = ObjectiveContractV1(
        contract_id=f"oc_{new_ulid()[:12]}",
        tenant_id=tenant_id,
        objective_id=objective_id,
        raw_objective=text,
        outcomes=outcomes,
        constraints=constraints,
        assumptions=assumptions,
        unknowns=unknowns,
    )
    contract.admission = admit_contract(contract)
    payload = {k: v for k, v in contract.to_dict().items() if k != "content_hash"}
    contract.content_hash = "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return contract


def admit_contract(contract: ObjectiveContractV1) -> AdmissionVerdict:
    """ADR-002 style deterministic admission validator."""
    if not contract.raw_objective.strip():
        return "REJECTED"
    blocking = [u for u in contract.unknowns if u.blocks_planning]
    if blocking:
        return "INSUFFICIENT_EVIDENCE"
    if not contract.outcomes:
        return "INSUFFICIENT_EVIDENCE"
    # At least one measurable outcome preferred; if all non-measurable and no DoD, insufficient.
    if not any(o.measurable for o in contract.outcomes) and len(contract.outcomes) == 1:
        # Single provisional sentence without DoD is not enough for planning.
        return "INSUFFICIENT_EVIDENCE"
    return "ADMITTED"


def assert_planning_allowed(contract: ObjectiveContractV1 | None) -> None:
    if contract is None:
        raise ObjectiveContractError("OBJECTIVE_CONTRACT_REQUIRED", "planning blocked")
    if contract.admission != "ADMITTED":
        raise ObjectiveContractError(contract.admission, contract.contract_id)
    blocking = [u for u in contract.unknowns if u.blocks_planning]
    if blocking:
        raise ObjectiveContractError("INSUFFICIENT_EVIDENCE", blocking[0].statement)


def unknowns_were_not_assumed(contract: ObjectiveContractV1) -> bool:
    assumed = {a.statement.lower() for a in contract.assumptions}
    return all(u.statement.lower() not in assumed for u in contract.unknowns)


__all__ = [
    "CONTRACT_SCHEMA",
    "AdmissionVerdict",
    "ObjectiveContractError",
    "OutcomeSpec",
    "ConstraintSpec",
    "AssumptionSpec",
    "UnknownSpec",
    "ObjectiveContractV1",
    "understand_objective",
    "admit_contract",
    "assert_planning_allowed",
    "unknowns_were_not_assumed",
]
