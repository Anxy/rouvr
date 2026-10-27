"""AIX Framework Verification Evidence Bundle.

Sprint: F5 Verification Framework (F5-P3)
Grounded in: EvidenceGraph, ContentAddressableEvidenceStore, RealExecuteResult.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.aix_core.framework.verification.contract import (
    GateResult,
    VERIFICATION_TYPES,
    VERIFIER_VERSION,
    VerificationCheck,
    VerificationOutcome,
)


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class EvidenceBundle:
    """Immutable, content-addressed verification evidence bundle.

    References existing artifacts, diffs, test outputs, and gate results
    without duplicating mutable source tree files or leaking credentials.
    """

    tenant_id: str
    objective_id: str
    cycle_id: str
    step_id: str
    attempt_id: str
    artifact_refs: tuple[str, ...] = ()
    diff_ref: str | None = None
    test_refs: tuple[str, ...] = ()
    observation_refs: tuple[str, ...] = ()
    gate_results: tuple[GateResult, ...] = ()
    semantic_result_ref: str | None = None
    content_hashes: tuple[str, ...] = ()
    schema_version: Literal["aix.verification.evidence.v1"] = "aix.verification.evidence.v1"
    sealed_at: str = field(default_factory=_utc)

    def canonical_hash(self) -> str:
        """Compute SHA-256 hash of deterministic JSON representation."""
        payload = {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "objective_id": self.objective_id,
            "cycle_id": self.cycle_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "artifact_refs": sorted(self.artifact_refs),
            "diff_ref": self.diff_ref,
            "test_refs": sorted(self.test_refs),
            "observation_refs": sorted(self.observation_refs),
            "gate_results": [
                {
                    "gate_id": g.gate_id,
                    "passed": g.passed,
                    "reasons": list(g.reasons),
                    "mandatory": g.mandatory,
                    "immutable": g.immutable,
                }
                for g in self.gate_results
            ],
            "semantic_result_ref": self.semantic_result_ref,
            "content_hashes": sorted(self.content_hashes),
        }
        raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "objective_id": self.objective_id,
            "cycle_id": self.cycle_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "artifact_refs": list(self.artifact_refs),
            "diff_ref": self.diff_ref,
            "test_refs": list(self.test_refs),
            "observation_refs": list(self.observation_refs),
            "gate_results": [
                {
                    "gate_id": g.gate_id,
                    "passed": g.passed,
                    "reasons": list(g.reasons),
                    "details": g.details,
                    "mandatory": g.mandatory,
                    "immutable": g.immutable,
                    "evidence_ref": g.evidence_ref,
                    "started_at": g.started_at,
                    "completed_at": g.completed_at,
                }
                for g in self.gate_results
            ],
            "semantic_result_ref": self.semantic_result_ref,
            "content_hashes": list(self.content_hashes),
            "canonical_hash": self.canonical_hash(),
            "sealed_at": self.sealed_at,
        }


class EvidenceBundleBuilder:
    """Single-use builder for constructing and sealing an EvidenceBundle."""

    def __init__(
        self,
        *,
        tenant_id: str = "",
        objective_id: str = "",
        cycle_id: str = "",
        step_id: str = "",
        attempt_id: str = "",
    ) -> None:
        self.tenant_id = tenant_id
        self.objective_id = objective_id
        self.cycle_id = cycle_id
        self.step_id = step_id
        self.attempt_id = attempt_id
        self._artifact_refs: list[str] = []
        self._diff_ref: str | None = None
        self._test_refs: list[str] = []
        self._observation_refs: list[str] = []
        self._gate_results: list[GateResult] = []
        self._semantic_result_ref: str | None = None
        self._content_hashes: list[str] = []
        self._sealed: bool = False

    def with_identity(
        self,
        *,
        tenant_id: str,
        objective_id: str,
        cycle_id: str,
        step_id: str,
        attempt_id: str,
    ) -> EvidenceBundleBuilder:
        self.tenant_id = tenant_id
        self.objective_id = objective_id
        self.cycle_id = cycle_id
        self.step_id = step_id
        self.attempt_id = attempt_id
        return self

    def add_artifact_ref(self, ref: str) -> EvidenceBundleBuilder:
        if ref and ref not in self._artifact_refs:
            self._artifact_refs.append(ref)
        return self

    def set_diff_ref(self, ref: str | None) -> EvidenceBundleBuilder:
        self._diff_ref = ref
        return self

    def add_test_ref(self, ref: str) -> EvidenceBundleBuilder:
        if ref and ref not in self._test_refs:
            self._test_refs.append(ref)
        return self

    def add_observation_ref(self, ref: str) -> EvidenceBundleBuilder:
        if ref and ref not in self._observation_refs:
            self._observation_refs.append(ref)
        return self

    def add_gate_result(self, result: GateResult) -> EvidenceBundleBuilder:
        self._gate_results.append(result)
        return self

    def set_gate_results(self, results: tuple[GateResult, ...]) -> EvidenceBundleBuilder:
        self._gate_results = list(results)
        return self

    def set_semantic_result_ref(self, ref: str | None) -> EvidenceBundleBuilder:
        self._semantic_result_ref = ref
        return self

    def add_content_hash(self, content_hash: str) -> EvidenceBundleBuilder:
        if content_hash and content_hash not in self._content_hashes:
            self._content_hashes.append(content_hash)
        return self

    def seal(self) -> EvidenceBundle:
        """Seal and freeze the evidence bundle."""
        if self._sealed:
            raise RuntimeError("EvidenceBundleBuilder has already been sealed.")
        self._sealed = True

        return EvidenceBundle(
            tenant_id=self.tenant_id,
            objective_id=self.objective_id,
            cycle_id=self.cycle_id,
            step_id=self.step_id,
            attempt_id=self.attempt_id,
            artifact_refs=tuple(self._artifact_refs),
            diff_ref=self._diff_ref,
            test_refs=tuple(self._test_refs),
            observation_refs=tuple(self._observation_refs),
            gate_results=tuple(self._gate_results),
            semantic_result_ref=self._semantic_result_ref,
            content_hashes=tuple(self._content_hashes),
        )

    @classmethod
    def from_real_execute(
        cls,
        result: Any,
        identity: dict[str, str],
    ) -> EvidenceBundle:
        """Construct a sealed EvidenceBundle from a RealExecuteResult."""
        builder = cls(
            tenant_id=identity.get("tenant_id", ""),
            objective_id=identity.get("objective_id", ""),
            cycle_id=identity.get("cycle_id", ""),
            step_id=identity.get("step_id", ""),
            attempt_id=identity.get("attempt_id", ""),
        )

        diff_ref = getattr(result, "diff_evidence_ref", None)
        if diff_ref:
            builder.set_diff_ref(str(diff_ref))

        test_ref = getattr(result, "test_evidence_ref", None)
        if test_ref:
            builder.add_test_ref(str(test_ref))

        for art in getattr(result, "artifacts", ()) or ():
            builder.add_artifact_ref(str(art))

        for obs in getattr(result, "observation_refs", ()) or ():
            builder.add_observation_ref(str(obs))

        return builder.seal()


# ==============================================================================
# Mapping Helpers (Moved from verification_engine.py for F5-P4 Clean Migration)
# ==============================================================================

def build_checks_from_read_verification(
    verification: dict[str, bool] | None, *, evidence_ref: str = ""
) -> tuple[VerificationCheck, ...]:
    """Map read verification booleans into typed checks."""
    if not verification:
        return ()
    order = ("resource_resolved", "file_read_non_empty", "target_resource_accessed", "secret_values_redacted")
    checks: list[VerificationCheck] = []
    for name in order:
        if name not in verification:
            continue
        passed = bool(verification[name])
        checks.append(
            VerificationCheck(
                check_type=name,
                mandatory=True,
                status="PASSED" if passed else "FAILED",
                evidence_ref=evidence_ref,
                failure_reason=None if passed else f"{name} was not satisfied",
            )
        )
    return tuple(checks)


def build_checks_from_completion_gates(
    gates: dict[str, Any] | None, *, evidence_ref: str = ""
) -> tuple[VerificationCheck, ...]:
    """Map completion gates into typed checks for EDIT/CREATE objectives."""
    if not gates:
        return ()
    gate_bools = gates.get("gates") or {}
    checks: list[VerificationCheck] = []
    for name, passed in gate_bools.items():
        checks.append(
            VerificationCheck(
                check_type=name,
                mandatory=True,
                status="PASSED" if passed else "FAILED",
                evidence_ref=evidence_ref,
                failure_reason=None if passed else f"gate {name!r} did not pass",
            )
        )
    verdict = str(gates.get("verification_verdict") or "")
    if verdict:
        checks.append(
            VerificationCheck(
                check_type="diff_matches_expected",
                mandatory=True,
                status="PASSED" if verdict == "PASSED" else "FAILED",
                evidence_ref=evidence_ref,
                failure_reason=None if verdict == "PASSED" else f"independent verifier verdict: {verdict}",
            )
        )
    return tuple(checks)


def aggregate_outcome(checks: tuple[VerificationCheck, ...]) -> VerificationOutcome:
    """Deterministic aggregation: all mandatory checks must be PASSED."""
    mandatory = [c for c in checks if c.mandatory]
    failed = tuple(c.check_type for c in mandatory if c.status != "PASSED")
    return VerificationOutcome(
        checks=checks,
        all_mandatory_passed=not failed,
        failed_mandatory=failed,
    )
