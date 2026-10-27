"""Tests for #61–70 + #101–106 (AIX-E2E-CYCLE-001H-W07, 001I-W01…W07, 001J-W01…W02, 001M-W08…W13)."""

from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta


# ---------------------------------------------------------------------------
# #61  001H-W07 — Objective-level verifier
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.verification.experimental.objective_verifier import (
    ObjectiveLevelVerifier,
    ObjectiveVerifierError,
    StepVerificationSummary,
)
from backend.aix_core.runtime.verification.verification import VerificationResultV1


class TestObjectiveLevelVerifier:
    def _passed(self) -> VerificationResultV1:
        return VerificationResultV1(verdict="PASSED", reason_codes=("ALL_CRITERIA_AND_GATES_PASSED",))

    def _failed(self, unmet: tuple[str, ...] = ("criterion_A",)) -> VerificationResultV1:
        return VerificationResultV1(verdict="FAILED", reason_codes=("ACCEPTANCE_CRITERIA_UNMET",), unmet_criteria=unmet)

    def _blocked(self) -> VerificationResultV1:
        return VerificationResultV1(verdict="BLOCKED", reason_codes=("NO_INDEPENDENT_EVIDENCE_PROVIDED",))

    def _inconclusive(self) -> VerificationResultV1:
        return VerificationResultV1(verdict="INCONCLUSIVE", reason_codes=("INCONCLUSIVE",))

    def test_all_steps_passed_returns_passed(self):
        v = ObjectiveLevelVerifier()
        result = v.verify(
            objective_acceptance_criteria=("criterion_A", "criterion_B"),
            step_results={"s1": self._passed(), "s2": self._passed()},
            expected_step_ids=("s1", "s2"),
        )
        assert result.verdict == "PASSED"
        assert result.failed_step_ids == ()
        assert result.blocked_step_ids == ()

    def test_one_failed_step_fails_objective(self):
        v = ObjectiveLevelVerifier()
        result = v.verify(
            objective_acceptance_criteria=("criterion_A",),
            step_results={"s1": self._passed(), "s2": self._failed()},
            expected_step_ids=("s1", "s2"),
        )
        assert result.verdict == "FAILED"
        assert "s2" in result.failed_step_ids

    def test_blocked_step_blocks_objective(self):
        v = ObjectiveLevelVerifier()
        result = v.verify(
            objective_acceptance_criteria=("criterion_A",),
            step_results={"s1": self._blocked()},
            expected_step_ids=("s1",),
        )
        assert result.verdict == "BLOCKED"

    def test_missing_step_treated_as_blocked(self):
        v = ObjectiveLevelVerifier()
        result = v.verify(
            objective_acceptance_criteria=("criterion_A",),
            step_results={},  # s1 expected but absent
            expected_step_ids=("s1",),
        )
        assert result.verdict == "BLOCKED"
        assert "s1" in result.blocked_step_ids

    def test_inconclusive_step_makes_objective_inconclusive(self):
        v = ObjectiveLevelVerifier()
        result = v.verify(
            objective_acceptance_criteria=("criterion_A",),
            step_results={"s1": self._inconclusive()},
            expected_step_ids=("s1",),
        )
        assert result.verdict == "INCONCLUSIVE"

    def test_empty_criteria_raises(self):
        v = ObjectiveLevelVerifier()
        with pytest.raises(ObjectiveVerifierError):
            v.verify(
                objective_acceptance_criteria=(),
                step_results={"s1": self._passed()},
                expected_step_ids=("s1",),
            )

    def test_empty_expected_step_ids_raises(self):
        v = ObjectiveLevelVerifier()
        with pytest.raises(ObjectiveVerifierError):
            v.verify(
                objective_acceptance_criteria=("criterion_A",),
                step_results={},
                expected_step_ids=(),
            )

    def test_recommendation_never_carries_workhub_language(self):
        v = ObjectiveLevelVerifier()
        result = v.verify(
            objective_acceptance_criteria=("criterion_A",),
            step_results={"s1": self._passed()},
            expected_step_ids=("s1",),
        )
        rec_lower = result.review_recommendation.lower()
        for token in ("accept", "close", "approve", "merge"):
            assert token not in rec_lower, f"Forbidden token {token!r} found in recommendation"


# ---------------------------------------------------------------------------
# #62–68  001I-W01…W07 — Auth/WorkHub integration
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.objective.experimental.workhub_integration import (
    AuthLoginRepair,
    AixServicePrincipalV1,
    TenantPropagationGuard,
    TenantViolation,
    WorkHubParentTaskLoader,
    WorkHubTaskLoadMode,
    ApprovalReader,
    emit_evidence_writeback,
    WorkHubIntegrationError,
    CrossTenantAbuseGuard,
    AuthError,
    build_default_service_principal,
)


class TestAuthLoginRepair:
    def test_validate_simulated_ok(self):
        r = AuthLoginRepair(mode="simulated")
        result = r.validate("tok-123", "tenant-A")
        assert result.is_valid
        assert result.token is not None
        assert result.token.tenant_id == "tenant-A"

    def test_empty_token_fails(self):
        r = AuthLoginRepair(mode="simulated")
        result = r.validate("", "tenant-A")
        assert not result.is_valid

    def test_real_enabled_raises(self):
        r = AuthLoginRepair(mode="real_enabled")
        with pytest.raises(AuthError):
            r.validate("tok-123", "tenant-A")

    def test_repair_simulated(self):
        r = AuthLoginRepair(mode="simulated")
        result = r.repair("tok-123", "tenant-A")
        assert result.is_valid
        assert result.repaired is True


class TestAixServicePrincipal:
    def test_default_principal_built(self):
        sp = build_default_service_principal()
        assert "aix" in sp.principal_id

    def test_forbidden_scope_raises(self):
        sp = build_default_service_principal()
        with pytest.raises(AuthError):
            sp.assert_scope_allowed("workhub:lifecycle:write")

    def test_allowed_scope_passes(self):
        sp = build_default_service_principal()
        sp.assert_scope_allowed("aix:cycle:write")  # must not raise

    def test_unknown_scope_raises(self):
        sp = build_default_service_principal()
        with pytest.raises(AuthError):
            sp.assert_scope_allowed("unknown:scope")

    def test_empty_principal_id_raises(self):
        with pytest.raises(ValueError):
            AixServicePrincipalV1(principal_id="")


class TestTenantPropagationGuard:
    def _make_obj(self, tenant_id: str):
        class Obj:
            pass
        o = Obj()
        o.tenant_id = tenant_id
        return o

    def test_matching_tenants_pass(self):
        guard = TenantPropagationGuard()
        guard.validate_chain("t1", self._make_obj("t1"), self._make_obj("t1"))  # no raise

    def test_mismatch_raises(self):
        guard = TenantPropagationGuard()
        with pytest.raises(TenantViolation):
            guard.validate_chain("t1", self._make_obj("t2"))

    def test_header_mismatch_raises(self):
        guard = TenantPropagationGuard()
        with pytest.raises(TenantViolation):
            guard.validate_headers("tenant-A", {"X-Tenant-ID": "tenant-B"})

    def test_header_match_passes(self):
        guard = TenantPropagationGuard()
        guard.validate_headers("tenant-A", {"X-Tenant-ID": "tenant-A"})


class TestWorkHubParentTaskLoader:
    def test_simulated_load_returns_task(self):
        loader = WorkHubParentTaskLoader(mode=WorkHubTaskLoadMode.SIMULATED)
        result = loader.load(task_ref="wh-task-001", tenant_id="tenant-A")
        assert result.found
        assert result.task.task_ref == "wh-task-001"

    def test_empty_task_ref_fails(self):
        loader = WorkHubParentTaskLoader(mode=WorkHubTaskLoadMode.SIMULATED)
        result = loader.load(task_ref="", tenant_id="tenant-A")
        assert not result.found

    def test_real_disabled_raises(self):
        loader = WorkHubParentTaskLoader(mode=WorkHubTaskLoadMode.REAL_DISABLED)
        with pytest.raises(WorkHubIntegrationError):
            loader.load(task_ref="wh-task-001", tenant_id="tenant-A")


class TestApprovalReader:
    def test_valid_approval_passes(self):
        reader = ApprovalReader()
        result = reader.read(
            approval_ref="approval-001",
            expected_cycle_id="cycle-001",
            expected_tenant_id="tenant-A",
        )
        assert result.is_valid
        assert result.approval.approval_ref == "approval-001"

    def test_empty_ref_fails(self):
        reader = ApprovalReader()
        result = reader.read(
            approval_ref="",
            expected_cycle_id="cycle-001",
            expected_tenant_id="tenant-A",
        )
        assert not result.is_valid


class TestEvidenceWriteback:
    def test_valid_writeback_emitted(self):
        result = emit_evidence_writeback(
            cycle_id="c1",
            tenant_id="t1",
            workhub_task_ref="wh-1",
            evidence_bundle_ref="eb-1",
            verification_result_ref="vr-1",
            review_recommendation="Evidence is ready for reviewer inspection.",
            emitted_at="2026-01-01T00:00:00Z",
        )
        assert result.emitted
        assert result.request.request_only is True

    def test_forbidden_language_blocked(self):
        result = emit_evidence_writeback(
            cycle_id="c1",
            tenant_id="t1",
            workhub_task_ref="wh-1",
            evidence_bundle_ref="eb-1",
            verification_result_ref="vr-1",
            review_recommendation="Please auto_accept and close this task.",
            emitted_at="2026-01-01T00:00:00Z",
        )
        assert not result.emitted
        assert "forbidden" in result.reason.lower()

    def test_empty_field_blocked(self):
        result = emit_evidence_writeback(
            cycle_id="",
            tenant_id="t1",
            workhub_task_ref="wh-1",
            evidence_bundle_ref="eb-1",
            verification_result_ref="vr-1",
            review_recommendation="Evidence ready for inspection.",
            emitted_at="2026-01-01T00:00:00Z",
        )
        assert not result.emitted


class TestCrossTenantAbuseGuard:
    def test_clean_check_passes(self):
        guard = CrossTenantAbuseGuard()
        result = guard.check(anchor_tenant_id="t1", objects=[])
        assert result.is_clean

    def test_mismatched_tenant_id_detected(self):
        class Obj:
            tenant_id = "t2"
        guard = CrossTenantAbuseGuard()
        result = guard.check(anchor_tenant_id="t1", objects=[Obj()])
        assert not result.is_clean
        assert "CROSS_TENANT_TENANT_ID_MISMATCH" in result.violation_codes

    def test_forbidden_effects_detected(self):
        guard = CrossTenantAbuseGuard()
        result = guard.check(
            anchor_tenant_id="t1",
            objects=[],
            allowed_effects=("auto_accept",),
        )
        assert not result.is_clean
        assert "FORBIDDEN_EFFECTS_REQUESTED" in result.violation_codes


# ---------------------------------------------------------------------------
# #69  001J-W01 — Cycle command API
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.objective.experimental.cycle_command_api import (
    CycleCommandHandler,
    CycleCommandEnvelopeV1,
    CycleCommandError,
    CommandIdempotencyStore,
)


class TestCycleCommandApi:
    def _envelope(self, operation: str = "admit", cycle_id: str | None = None, body: dict | None = None):
        return CycleCommandEnvelopeV1(
            operation=operation,
            cycle_id=cycle_id,
            tenant_id="tenant-A",
            actor_id="actor-1",
            idempotency_key="idem-001",
            body=body or ({"workhub_task_ref": "wh-1"} if operation == "admit" else {}),
        )

    def test_admit_accepted(self):
        h = CycleCommandHandler()
        result = h.handle(self._envelope("admit"))
        assert result.accepted
        assert result.operation == "admit"

    def test_cancel_requires_cycle_id(self):
        h = CycleCommandHandler()
        with pytest.raises(CycleCommandError) as exc_info:
            h.handle(self._envelope("cancel"))  # no cycle_id
        assert exc_info.value.code == "CYCLE_ID_REQUIRED"

    def test_cancel_with_cycle_id_accepted(self):
        h = CycleCommandHandler()
        result = h.handle(self._envelope("cancel", cycle_id="c-001"))
        assert result.accepted

    def test_unknown_operation_raises(self):
        h = CycleCommandHandler()
        with pytest.raises(CycleCommandError) as exc_info:
            h.handle(self._envelope("auto_accept"))
        assert exc_info.value.code == "UNKNOWN_OPERATION"

    def test_forbidden_effect_in_body_raises(self):
        h = CycleCommandHandler()
        with pytest.raises(CycleCommandError) as exc_info:
            h.handle(self._envelope("cancel", cycle_id="c-1", body={"requested_effects": ["production_deploy"]}))
        assert exc_info.value.code == "FORBIDDEN_EFFECTS_IN_BODY"

    def test_admit_without_workhub_task_ref_raises(self):
        h = CycleCommandHandler()
        with pytest.raises(CycleCommandError) as exc_info:
            h.handle(CycleCommandEnvelopeV1(
                operation="admit",
                tenant_id="t1",
                actor_id="a1",
                idempotency_key="k1",
                body={},
            ))
        assert exc_info.value.code == "WORKHUB_TASK_REF_REQUIRED"

    def test_idempotency_store_records_and_retrieves(self):
        store = CommandIdempotencyStore()
        h = CycleCommandHandler()
        env = self._envelope("admit")
        result = h.handle(env)
        store.record(env.tenant_id, env.idempotency_key, result)
        retrieved = store.get_or_none(env.tenant_id, env.idempotency_key)
        assert retrieved is result


# ---------------------------------------------------------------------------
# #70  001J-W02 — Cycle query API
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.objective.experimental.cycle_query_api import (
    CycleQueryHandler,
    CycleQueryEnvelopeV1,
    CycleQueryError,
    encode_cursor,
    decode_cursor,
)


class TestCycleQueryApi:
    def _envelope(self, operation: str = "list_cycles", resource_id: str | None = None, filters: dict | None = None):
        return CycleQueryEnvelopeV1(
            operation=operation,
            tenant_id="tenant-A",
            resource_id=resource_id,
            filters=filters or {},
        )

    def test_list_cycles_ok(self):
        h = CycleQueryHandler()
        result = h.handle(self._envelope("list_cycles"))
        assert result.operation == "list_cycles"

    def test_get_cycle_requires_resource_id(self):
        h = CycleQueryHandler()
        with pytest.raises(CycleQueryError) as exc_info:
            h.handle(self._envelope("get_cycle"))  # no resource_id
        assert exc_info.value.code == "RESOURCE_ID_REQUIRED"

    def test_get_cycle_with_resource_id_ok(self):
        h = CycleQueryHandler()
        result = h.handle(self._envelope("get_cycle", resource_id="c-001"))
        assert result.operation == "get_cycle"

    def test_unknown_operation_raises(self):
        h = CycleQueryHandler()
        with pytest.raises(CycleQueryError) as exc_info:
            h.handle(self._envelope("delete_all"))
        assert exc_info.value.code == "UNKNOWN_OPERATION"

    def test_unknown_filter_key_raises(self):
        h = CycleQueryHandler()
        with pytest.raises(CycleQueryError) as exc_info:
            h.handle(self._envelope("list_cycles", filters={"unknown_key": "v"}))
        assert exc_info.value.code == "UNKNOWN_FILTER_KEY"

    def test_cursor_encode_decode_roundtrip(self):
        payload = {"last_id": "c-100", "after_ts": "2026-01-01T00:00:00Z"}
        cursor = encode_cursor(payload)
        assert isinstance(cursor, str)
        decoded = decode_cursor(cursor)
        assert decoded == payload

    def test_invalid_cursor_raises(self):
        with pytest.raises(CycleQueryError) as exc_info:
            decode_cursor("not-valid-base64!!!")
        assert exc_info.value.code == "INVALID_CURSOR"

    def test_list_steps_requires_resource_id(self):
        h = CycleQueryHandler()
        with pytest.raises(CycleQueryError) as exc_info:
            h.handle(self._envelope("list_steps"))
        assert exc_info.value.code == "RESOURCE_ID_REQUIRED"

    def test_get_step_requires_child_id(self):
        h = CycleQueryHandler()
        with pytest.raises(CycleQueryError) as exc_info:
            h.handle(CycleQueryEnvelopeV1(
                operation="get_step",
                tenant_id="t1",
                resource_id="c-1",
                child_id=None,
            ))
        assert exc_info.value.code == "CHILD_ID_REQUIRED"


# ---------------------------------------------------------------------------
# #101  001M-W08 — Executor threat model
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.executor.experimental.executor_threat_model import (
    ExecutorSecurityAssessor,
    ExecutorType,
    EXECUTOR_THREAT_PROFILES,
    SecurityError,
)


class TestExecutorThreatModel:
    def test_all_executors_have_profiles(self):
        for executor_type in ExecutorType:
            assert executor_type in EXECUTOR_THREAT_PROFILES

    def test_all_profiles_cleared(self):
        assessor = ExecutorSecurityAssessor()
        for executor_type in ExecutorType:
            profile = assessor.assert_cleared(executor_type)
            assert profile.is_cleared

    def test_unknown_executor_raises(self):
        assessor = ExecutorSecurityAssessor()
        with pytest.raises(ValueError):
            assessor.assess("unknown_executor_xyz")

    def test_profile_has_threats(self):
        assessor = ExecutorSecurityAssessor()
        profile = assessor.assess(ExecutorType.ORCHESTRATOR_V2)
        assert len(profile.known_threats) > 0

    def test_missing_control_detected(self):
        from backend.aix_core.runtime.executor.experimental.executor_threat_model import (
            ExecutorSecurityProfile, REQUIRED_EXECUTOR_CONTROLS
        )
        partial = ExecutorSecurityProfile(
            executor_type=ExecutorType.AIDER,
            controls_verified=frozenset({"sandbox"}),  # missing 3
            known_threats=("test threat",),
            residual_risks=(),
        )
        assert not partial.is_cleared
        assert "network" in partial.missing_controls


# ---------------------------------------------------------------------------
# #102  001M-W09 — Security scan gate
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.governance.experimental.security_scan_gate import (
    SecurityScanGate,
    ScanResultV1,
    ScanFindingV1,
    Severity,
    noop_scanner,
)


class TestSecurityScanGate:
    def test_no_scanners_passes(self):
        gate = SecurityScanGate()
        result = gate.scan("some/target.py")
        assert result.passed

    def test_noop_scanner_passes(self):
        gate = SecurityScanGate(scanners=[noop_scanner])
        result = gate.scan("some/target.py")
        assert result.passed

    def test_critical_finding_blocks(self):
        def evil_scanner(target: str) -> ScanResultV1:
            return ScanResultV1(
                scanner_id="evil",
                scan_target=target,
                findings=(ScanFindingV1(
                    scanner_id="evil",
                    rule_id="SQL-INJECT",
                    severity="CRITICAL",
                    title="SQL injection",
                    detail="User input interpolated directly",
                ),),
            )
        gate = SecurityScanGate(scanners=[evil_scanner])
        result = gate.scan("app.py")
        assert not result.passed
        assert len(result.blocking_findings) == 1

    def test_low_finding_does_not_block(self):
        def low_scanner(target: str) -> ScanResultV1:
            return ScanResultV1(
                scanner_id="low",
                scan_target=target,
                findings=(ScanFindingV1(
                    scanner_id="low",
                    rule_id="STYLE",
                    severity="LOW",
                    title="Style issue",
                    detail="Minor",
                ),),
            )
        gate = SecurityScanGate(scanners=[low_scanner])
        result = gate.scan("app.py")
        assert result.passed

    def test_incomplete_scan_blocks_by_default(self):
        def broken_scanner(target: str) -> ScanResultV1:
            return ScanResultV1(
                scanner_id="broken",
                scan_target=target,
                findings=(),
                scan_completed=False,
                error="Timeout",
            )
        gate = SecurityScanGate(scanners=[broken_scanner])
        result = gate.scan("app.py")
        assert not result.passed

    def test_empty_target_fails(self):
        gate = SecurityScanGate()
        result = gate.scan("")
        assert not result.passed


# ---------------------------------------------------------------------------
# #103  001M-W10 — Data privacy / retention
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.governance.experimental.data_privacy_retention import (
    check_retention,
    RetentionPolicyError,
    PiiFieldGuard,
    RETENTION_FLOORS_DAYS,
)


class TestDataPrivacyRetention:
    def test_within_floor_not_eligible(self):
        now = datetime.now(UTC)
        created_at = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        result = check_retention(
            data_class="step_attempt",
            record_id="r1",
            tenant_id="t1",
            created_at=created_at,
            erasure_requested=True,
            now=now,
        )
        assert not result.erasure_eligible
        assert result.days_remaining > 0

    def test_elapsed_floor_with_request_eligible(self):
        now = datetime.now(UTC)
        floor = RETENTION_FLOORS_DAYS["step_attempt"]
        created_at = (now - timedelta(days=floor + 5)).isoformat().replace("+00:00", "Z")
        result = check_retention(
            data_class="step_attempt",
            record_id="r1",
            tenant_id="t1",
            created_at=created_at,
            erasure_requested=True,
            now=now,
        )
        assert result.erasure_eligible

    def test_legal_hold_blocks_erasure(self):
        now = datetime.now(UTC)
        floor = RETENTION_FLOORS_DAYS["step_attempt"]
        created_at = (now - timedelta(days=floor + 5)).isoformat().replace("+00:00", "Z")
        result = check_retention(
            data_class="step_attempt",
            record_id="r1",
            tenant_id="t1",
            created_at=created_at,
            erasure_requested=True,
            legal_hold=True,
            now=now,
        )
        assert not result.erasure_eligible
        assert result.legal_hold

    def test_pii_guard_blocks_known_fields(self):
        guard = PiiFieldGuard()
        with pytest.raises(RetentionPolicyError) as exc_info:
            guard.assert_no_pii({"email": "user@example.com", "data": "ok"})
        assert "email" in str(exc_info.value)

    def test_pii_guard_passes_clean_payload(self):
        guard = PiiFieldGuard()
        guard.assert_no_pii({"cycle_id": "c1", "status": "RUNNING"})  # must not raise

    def test_unknown_data_class_raises(self):
        with pytest.raises(RetentionPolicyError):
            check_retention(
                data_class="unknown_class",  # type: ignore[arg-type]
                record_id="r1",
                tenant_id="t1",
                created_at="2026-01-01T00:00:00Z",
            )


# ---------------------------------------------------------------------------
# #104  001M-W11 — Backup / restore / DR
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.governance.experimental.backup_restore_dr import (
    BackupRecordV1,
    verify_backup_coverage,
    DrActivationGate,
    RestoreRequestV1,
    BACKUP_COVERAGE,
    DR_RTO_MINUTES,
    DR_RPO_MINUTES,
)


class TestBackupRestoreDr:
    def test_policy_constants_sensible(self):
        assert DR_RTO_MINUTES > 0
        assert DR_RPO_MINUTES > 0
        assert DR_RPO_MINUTES <= DR_RTO_MINUTES

    def test_full_coverage_backup_verifies(self):
        backup = BackupRecordV1(
            backup_id="bk-1",
            tenant_id="t1",
            completed_at="2026-01-01T00:00:00Z",
            coverage=BACKUP_COVERAGE,
            backup_size_bytes=1024,
            checksum="sha256:abc123",
            storage_location="s3://backup-bucket/bk-1",
        )
        result = verify_backup_coverage(backup)
        assert result.verified

    def test_partial_coverage_fails_verification(self):
        backup = BackupRecordV1(
            backup_id="bk-2",
            tenant_id="t1",
            completed_at="2026-01-01T00:00:00Z",
            coverage=frozenset({"objective_envelopes"}),
            backup_size_bytes=512,
            checksum="sha256:xyz",
            storage_location="s3://backup-bucket/bk-2",
        )
        result = verify_backup_coverage(backup)
        assert not result.verified
        assert len(result.missing_coverage) > 0

    def test_dr_activation_requires_human_auth(self):
        gate = DrActivationGate()
        result = gate.request_restore(RestoreRequestV1(
            restore_id="r1",
            tenant_id="t1",
            backup_id="bk-1",
            requested_at="2026-01-01T00:00:00Z",
            human_auth_token="",  # empty — should fail
        ))
        assert not result.succeeded

    def test_dr_activation_with_auth_succeeds(self):
        gate = DrActivationGate()
        result = gate.request_restore(RestoreRequestV1(
            restore_id="r1",
            tenant_id="t1",
            backup_id="bk-1",
            requested_at="2026-01-01T00:00:00Z",
            human_auth_token="human-token-abc123",
        ))
        assert result.succeeded
        assert result.integrity_passed

    def test_dr_activation_requires_tenant_id(self):
        gate = DrActivationGate()
        result = gate.request_restore(RestoreRequestV1(
            restore_id="r1",
            tenant_id="",  # empty
            backup_id="bk-1",
            requested_at="2026-01-01T00:00:00Z",
            human_auth_token="human-token",
        ))
        assert not result.succeeded


# ---------------------------------------------------------------------------
# #105  001M-W12 — Soak / chaos
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.executor.experimental.soak_chaos import (
    SoakScenarioV1,
    ChaosFaultInjectionV1,
    SoakAndChaosRunner,
    ChaosError,
    ALLOWED_CHAOS_FAULTS,
    FORBIDDEN_CHAOS_FAULTS,
)


class TestSoakChaos:
    def test_valid_scenario_created(self):
        s = SoakScenarioV1(
            scenario_id="soak-001",
            description="Basic soak",
            duration_seconds=3600,
            target_concurrency=2,
            max_error_rate_pct=5.0,
            min_throughput_cycles=10,
            chaos_faults=("kill_worker",),
        )
        assert s.scenario_id == "soak-001"

    def test_forbidden_fault_raises(self):
        with pytest.raises(ChaosError):
            SoakScenarioV1(
                scenario_id="bad",
                description="bad",
                duration_seconds=60,
                target_concurrency=1,
                max_error_rate_pct=5.0,
                min_throughput_cycles=1,
                chaos_faults=("production_mutation",),  # forbidden
            )

    def test_unknown_fault_raises(self):
        with pytest.raises(ChaosError):
            SoakScenarioV1(
                scenario_id="bad",
                description="bad",
                duration_seconds=60,
                target_concurrency=1,
                max_error_rate_pct=5.0,
                min_throughput_cycles=1,
                chaos_faults=("launch_rockets",),  # unknown
            )

    def test_fault_injection_forbidden_fault_raises(self):
        with pytest.raises(ChaosError):
            ChaosFaultInjectionV1(
                fault_type="workhub_signal",
                target_service="svc",
                parameters={},
                inject_at_second=30,
            )

    def test_runner_evaluate_passed(self):
        runner = SoakAndChaosRunner()
        scenario = SoakScenarioV1(
            scenario_id="s1",
            description="d",
            duration_seconds=3600,
            target_concurrency=1,
            max_error_rate_pct=5.0,
            min_throughput_cycles=5,
        )
        result = runner.evaluate_result(
            scenario=scenario,
            cycles_completed=10,
            error_rate_pct=2.0,
            duration_seconds_actual=3600,
            faults_injected=(),
            evidence_refs=("ev-001",),
        )
        assert result.passed

    def test_runner_evaluate_failed_throughput(self):
        runner = SoakAndChaosRunner()
        scenario = SoakScenarioV1(
            scenario_id="s1",
            description="d",
            duration_seconds=3600,
            target_concurrency=1,
            max_error_rate_pct=5.0,
            min_throughput_cycles=50,  # requires 50
        )
        result = runner.evaluate_result(
            scenario=scenario,
            cycles_completed=3,   # only 3
            error_rate_pct=1.0,
            duration_seconds_actual=3600,
            faults_injected=(),
            evidence_refs=(),
        )
        assert not result.passed


# ---------------------------------------------------------------------------
# #106  001M-W13 — Cost circuit-breaker
# ---------------------------------------------------------------------------
from backend.aix_core.runtime.governance.experimental.cost_circuit_breaker import (
    CostCircuitBreaker,
    BudgetCeiling,
    ConsumedBudget,
    CircuitState,
    CircuitBreakerError,
)


class TestCostCircuitBreaker:
    def _cb(self, cost_usd=100.0, tokens=10000, time_seconds=3600, retries=5, steps=20):
        ceiling = BudgetCeiling(
            cost_usd=cost_usd,
            tokens=tokens,
            time_seconds=time_seconds,
            retries=retries,
            steps=steps,
        )
        return CostCircuitBreaker(ceiling=ceiling)

    def test_closed_when_well_within_budget(self):
        cb = self._cb()
        result = cb.check(ConsumedBudget(cost_usd=10.0, tokens=100, time_seconds=60, retries=0, steps=1))
        assert result.state == CircuitState.CLOSED

    def test_warning_when_approaching_ceiling(self):
        cb = self._cb(cost_usd=100.0)
        result = cb.check(ConsumedBudget(cost_usd=85.0, tokens=100, time_seconds=60, retries=0, steps=1))
        assert result.state == CircuitState.WARNING
        assert "cost_usd" in result.warning_dimensions

    def test_trips_when_ceiling_breached(self):
        cb = self._cb(cost_usd=100.0)
        result = cb.check(ConsumedBudget(cost_usd=110.0, tokens=100, time_seconds=60, retries=0, steps=1))
        assert result.state == CircuitState.TRIPPED
        assert "cost_usd" in result.breached_dimensions

    def test_stays_tripped_after_trip(self):
        cb = self._cb(cost_usd=10.0)
        cb.check(ConsumedBudget(cost_usd=20.0))  # trip
        result = cb.check(ConsumedBudget(cost_usd=1.0))  # well within — but still TRIPPED
        assert result.state == CircuitState.TRIPPED

    def test_authorize_resumption_requires_nonempty_token(self):
        cb = self._cb(cost_usd=10.0)
        cb.check(ConsumedBudget(cost_usd=20.0))  # trip
        with pytest.raises(CircuitBreakerError):
            cb.authorize_resumption("")

    def test_authorize_resumption_resets_circuit(self):
        cb = self._cb(cost_usd=10.0)
        cb.check(ConsumedBudget(cost_usd=20.0))  # trip
        cb.authorize_resumption("human-auth-token")
        assert cb.state == CircuitState.CLOSED

    def test_authorize_resumption_on_closed_circuit_raises(self):
        cb = self._cb()
        with pytest.raises(CircuitBreakerError):
            cb.authorize_resumption("token")

    def test_invalid_warning_pct_raises(self):
        with pytest.raises(CircuitBreakerError):
            CostCircuitBreaker(ceiling=BudgetCeiling(100, 1000, 3600, 5, 20), warning_pct=1.5)
