"""Muse adapter slice tests: failure ordering, fresh authority, real evidence.

The session transport is stubbed at the ``MuseSessionPort`` protocol only
(adapter seam tests); the production transport itself is driven by
``test_muse_session_transport.py`` against the exact-shape fake executable.
Binding validation uses the real maintained ``require_workhub_binding`` and
the real workflow-id function; materialization uses a real repository dir
through the maintained registry + boundary owners.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.aix_core.framework.execution.muse_execute_adapter import (
    MUSE_EXECUTOR_ID,
    MuseCapabilityPolicy,
    MuseExecuteAdapter,
    validate_capability_policy,
)
from backend.aix_core.framework.execution.muse_session import (
    FileChange,
    MuseSessionError,
    MuseToolFailure,
    MuseTurnParams,
    MuseTurnResult,
    is_muse_model_id,
    write_evidence_file,
)
from backend.aix_core.framework.execution.port import ExecuteStepRequest
from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteError,
    RealExecuteResult,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    PlanRevisionV1,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
)
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot
from backend.workflows.temporal.objective_cycle_contracts import (
    objective_cycle_workflow_id,
)

TENANT = "tenant-1"
PROJECT = "proj-1"
TASK = "task-1"
ACTIVATION = "act-1"
WH_ATTEMPT = "whattempt-1"
OBJECTIVE = "objective-1"
CYCLE = "cycle-1"
SELECTION = "sel-1"
CANDIDATE = "abcdef1"
PLAN = "ab" * 32


def _binding(**overrides: Any) -> dict[str, Any]:
    binding: dict[str, Any] = {
        "schema_version": 1,
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "task_id": TASK,
        "task_number": 1,
        "assignment_id": "assign-1",
        "assignment_revision": 1,
        "executor_sub": "sub-1",
        "workhub_attempt_id": WH_ATTEMPT,
        "attempt_version": 1,
        "controller_attempt_id": "catt-1",
        "lease_epoch": 1,
        "lease_holder": "holder-1",
        "contract_ref": "contract-1",
        "authority_ref": "auth-1",
        "plan_digest": "p",
        "route_digest": "r",
        # Full canonical selection: maintained _pinned_selection requires
        # the binding selection to carry the task scope and to equal the
        # live-contract execution_spec block on every key, so a minimal
        # fixture selection can never prove. Values stay consistent with
        # the outer binding and the record-level refs; equality stays
        # exact (the fake spec row echoes this dict verbatim).
        "selection": {
            "tenant_id": TENANT,
            "project_id": PROJECT,
            "task_id": TASK,
            "task_number": 1,
            "objective_id": OBJECTIVE,
            "cycle_id": CYCLE,
            "selection_id": SELECTION,
            "mandate_id": "m-1",
            "session_id": "s-1",
            "worker_id": "w-1",
            "candidate_sha": CANDIDATE,
            "plan_sha256": PLAN,
            "correlation_id": "corr-1",
        },
    }
    binding.update(overrides)
    return binding


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "activation_id": ACTIVATION,
        "tenant_id": TENANT,
        "objective_id": OBJECTIVE,
        "cycle_id": CYCLE,
        "selection_id": SELECTION,
        "mandate_id": "m-1",
        "session_id": "s-1",
        "worker_id": "w-1",
        "accepted": True,
        "workflow_version": 1,
        "candidate_sha": CANDIDATE,
        "plan_sha256": PLAN,
        "correlation_id": "corr-1",
        "model": "muse-spark-1.3",
        "executor": "muse-lane",
        "max_repairs": 2,
        "workhub_binding": _binding(),
        "workflow_id": objective_cycle_workflow_id(
            tenant_id=TENANT, objective_id=OBJECTIVE, cycle_id=CYCLE
        ),
        "status": "STARTED",
    }
    record.update(overrides)
    return record


class FakeLookup:
    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records = records if records is not None else {ACTIVATION: _record()}
        self.round = 0
        self.get_calls: list[str] = []
        self.fail_with: BaseException | None = None

    async def get_activation(self, activation_id: str) -> dict[str, Any] | None:
        self.get_calls.append(activation_id)
        if self.fail_with is not None:
            raise self.fail_with
        return self.records.get(activation_id)

    async def find_attempt_activation(
        self, tenant_id: str, project_id: str, workhub_attempt_id: str
    ) -> list[str]:
        return [
            activation_id
            for activation_id, record in self.records.items()
            if isinstance(record.get("workhub_binding"), dict)
            and record["workhub_binding"].get("tenant_id") == tenant_id
            and record["workhub_binding"].get("project_id") == project_id
            and record["workhub_binding"].get("workhub_attempt_id") == workhub_attempt_id
        ]

    async def get_repair_count(self, activation_id: str) -> int:
        return self.round


class StubSessionPort:
    """Protocol-level stub returning real result shapes (adapter seam only)."""

    # Process-wide turn sequence so distinct stub instances sharing one
    # durable evidence directory never reuse a transcript name. The
    # production no-replace rule (O_EXCL hard-link publication) refuses a
    # second write to the same final name, so per-instance counters
    # manufacture a REDIRECTED refusal when two ports share a directory
    # within one test. The global sequence keeps every fake turn
    # collision-free and deterministic in run order; production semantics
    # are untouched.
    _sequence = 0

    def __init__(self) -> None:
        self.params: list[MuseTurnParams] = []
        self.agent_text = "did the thing"
        self.model_echo = "muse-spark-1.3"
        self.changes: tuple[FileChange, ...] = ()
        self.tool_failures: tuple[MuseToolFailure, ...] = ()
        self.usage: dict[str, Any] | None = {
            "cachedTokens": 0, "inputTokens": 10, "outputTokens": 5, "reasoningTokens": 0,
        }
        self.append_to: tuple[str, ...] = ()
        self.fail_with: BaseException | None = None
        self.calls = 0

    async def run_turn(self, params: MuseTurnParams) -> MuseTurnResult:
        self.params.append(params)
        if self.fail_with is not None:
            raise self.fail_with
        for rel in self.append_to:
            target = Path(params.workspace) / rel
            with open(target, "a", encoding="utf-8") as handle:
                handle.write("# stub worker edit\n")
        # Each distinct fake turn gets a unique identity and evidence file:
        # the production no-replace rule refuses a second write to the same
        # transcript name, so reusing one name across calls is not a valid
        # fixture (it manufactures a REDIRECTED refusal).
        self.calls += 1
        StubSessionPort._sequence += 1
        turn_no = StubSessionPort._sequence
        transcript = write_evidence_file(
            Path(params.evidence_dir), f"stub-turn-{turn_no}", b'{"stub": true}'
        )
        return MuseTurnResult(
            session_id="sess-1",
            turn_id=f"turn-{turn_no}",
            terminal="completed",
            agent_text=self.agent_text,
            model_echo=self.model_echo,
            changes=self.changes,
            evidence_files=(str(transcript),),
            token_usage=dict(self.usage) if self.usage else None,
            turn_record={"terminal": "completed"},
            tool_failures=self.tool_failures,
        )


def _repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in (files if files is not None else {"helper.py": "x = 1\n"}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _settings(repo: Path) -> Any:
    return SimpleNamespace(
        OBJECTIVE_CYCLE_REPOSITORY_ROOTS=json.dumps({"repo-1": str(repo)}),
        OBJECTIVE_CYCLE_WORKSPACE_BASE="",
    )


def _request(tmp_path: Path, repo: Path, **overrides: Any) -> ExecuteStepRequest:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "cycle_id": CYCLE,
        "objective_id": OBJECTIVE,
        "objective": "Add a helper function",
        "workhub_task_id": TASK,
        "project_id": PROJECT,
        "task_number": 1,
        "repository_id": "repo-1",
        "settings": _settings(repo),
        "model_override": "muse-spark-1.3",
        "workspace_base": tmp_path / "ws-base",
        "workspace_step_id": "step-1",
        "workspace_attempt_id": "attempt-1",
        "allowed_files": ("helper.py",),
        "activation_id": ACTIVATION,
        "workhub_attempt_id": WH_ATTEMPT,
        "selection_id": SELECTION,
        "candidate_sha": CANDIDATE,
        "plan_sha256": PLAN,
        "worker_id": "w-1",
        "accepted_model_id": "muse-spark-1.3",
    }
    values.update(overrides)
    return ExecuteStepRequest(**values)


def _adapter(port: StubSessionPort, lookup: FakeLookup, **kwargs: Any) -> MuseExecuteAdapter:
    if "preflight_verifier" not in kwargs:
        bindings = [
            record["workhub_binding"]
            for record in lookup.records.values()
            if isinstance(record, dict)
            and isinstance(record.get("workhub_binding"), dict)
        ]
        kwargs["preflight_verifier"] = FakePreflight(FakeWorkHubTransport(bindings))
    return MuseExecuteAdapter(session_port=port, binding_lookup=lookup, **kwargs)  # type: ignore[arg-type]


# --- Lane-ops #23 M3 owner preflight fakes ---------------------------------

_SENTINEL_JWT = "Bearer fake-workhub-jwt.sentinel-7f3a9c4d"
_FAR_FUTURE_EXPIRY = "2035-01-01T00:00:00+00:00"


def _live_rows_for(binding: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Mirror an accepted binding into fake live owner rows.

    Shapes mirror exactly what the maintained ``verify_binding`` reads:
    task row, assignment-binding row, and the handoff-receipt plus
    execution-spec evidence rows. Movement tests mutate the live side
    only; the accepted binding stays fixed.
    """
    task = {
        "id": binding["task_id"],
        "tenant_id": binding["tenant_id"],
        "active_execution_contract_ref": binding["contract_ref"],
    }
    assignment = {
        "task_id": binding["task_id"],
        "assignment_status": "active",
        "executor_sub": binding["executor_sub"],
        "assignment_record_version": int(binding["assignment_revision"]),
        "assignment_id": binding["assignment_id"],
        "authority_ref": binding["authority_ref"],
        "plan_digest": binding["plan_digest"],
        "route_digest": binding.get("route_digest"),
        "lease_epoch": int(binding["lease_epoch"]),
        "holding_principal": binding["lease_holder"],
        "lease_expires_at": _FAR_FUTURE_EXPIRY,
    }
    evidence = [
        {
            "evidence_type": "temporal_handoff",
            "evidence_json": {
                "effect_fence": {
                    "claim_id": str(binding["assignment_id"]),
                    "fence_token": {
                        "assignment_revision": int(binding["assignment_revision"]),
                        "lease_epoch": int(binding["lease_epoch"]),
                    },
                    "idempotency_key": "temporal-handoff-%s-e%s" % (
                        binding["controller_attempt_id"], binding["lease_epoch"]),
                },
                "workhub_attempt_id": binding["workhub_attempt_id"],
                "attempt_version": binding["attempt_version"],
                "executor_sub": binding["executor_sub"],
                "controller_attempt_id": binding["controller_attempt_id"],
            },
        },
        {
            "evidence_type": "execution_spec",
            "evidence_json": {
                "contract_ref": binding["contract_ref"],
                "aix_selection": dict(binding["selection"]),
            },
        },
    ]
    return task, assignment, evidence


class FakeWorkHubTransport:
    """Controllable stand-in for the maintained BindingTransport.

    Holds a sentinel credential that must never leave this object: it is
    never attached to returned rows, call logs, or raised errors, which
    is exactly what the secret-hygiene test asserts.
    """

    def __init__(self, bindings: Any = ()) -> None:
        self.credential = _SENTINEL_JWT
        self.live_tasks: dict[Any, Any] = {}
        self.live_assignments: dict[Any, Any] = {}
        self.live_evidence: dict[Any, Any] = {}
        self.calls: list[Any] = []
        self.fail_with: BaseException | None = None
        for binding in bindings:
            self.mirror(binding)

    def mirror(self, binding: dict[str, Any]) -> None:
        task, assignment, evidence = _live_rows_for(binding)
        key = (binding["project_id"], int(binding["task_number"]))
        self.live_tasks[key] = task
        self.live_assignments[key] = assignment
        # Evidence accumulates per task: several attempts may share one
        # task, and verification selects its own receipt by exact fence.
        # Canonical append-only evidence files one execution_spec
        # authority row per contract: a later attempt under the same
        # contract shares that row, so an identical spec row is never
        # manufactured twice (production requires exactly one pinned
        # spec block, and a duplicate would make the proof ambiguous).
        # Distinct handoff receipts still accumulate per attempt, and a
        # genuinely differing spec still accumulates and still refuses.
        stored = self.live_evidence.setdefault(binding["task_id"], [])
        for row in evidence:
            if row.get("evidence_type") == "execution_spec" and any(
                existing == row for existing in stored
            ):
                continue
            stored.append(row)

    def _check(self, kind: str, *args: Any) -> None:
        self.calls.append((kind,) + args)
        if self.fail_with is not None:
            raise self.fail_with

    async def get_task_by_number(
        self, *, project_id: str, task_number: int
    ) -> dict[str, Any] | None:
        self._check("get_task_by_number", project_id, int(task_number))
        return dict(self.live_tasks.get((project_id, int(task_number)), {}) or {})

    async def get_assignment_binding(
        self, *, project_id: str, task_number: int
    ) -> dict[str, Any] | None:
        self._check("get_assignment_binding", project_id, int(task_number))
        return dict(
            self.live_assignments.get((project_id, int(task_number)), {}) or {}
        )

    async def list_task_evidence(
        self, *, project_id: str, task_id: str
    ) -> list[dict[str, Any]]:
        self._check("list_task_evidence", project_id, task_id)
        return [dict(row) for row in self.live_evidence.get(task_id, [])]


class FakePreflight:
    """Owner-side verifier handle using the REAL production wrapper."""

    def __init__(self, transport: FakeWorkHubTransport) -> None:
        self._transport = transport
        self.calls: list[dict[str, Any]] = []

    async def verify_assignment_lease(
        self, binding: dict[str, Any]
    ) -> dict[str, Any]:
        from backend.session_factory.muse_preflight import (
            WorkHubPreflightVerifier,
        )

        self.calls.append(dict(binding))
        return await WorkHubPreflightVerifier(
            transport=self._transport  # type: ignore[arg-type]
        ).verify_assignment_lease(binding)


def _preflight_for(
    binding: dict[str, Any] | None = None,
) -> tuple[FakePreflight, FakeWorkHubTransport]:
    transport = FakeWorkHubTransport(
        [binding] if binding is not None else [_binding()]
    )
    return FakePreflight(transport), transport


def _contract(**overrides: Any) -> StepContractV1:
    values: dict[str, Any] = {
        "cycle_id": CYCLE, "tenant_id": TENANT, "step_index": 0, "purpose": "fix",
        "target_scope": ("helper.py",),
        "expected_output_schema_ref": "aix.step_contract.v1.generic_edit",
        "acceptance_criteria": ("works",), "validation_commands": ("true",),
        "assigned_role": "executor",
        "step_budget": BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0,
                                retries=2, steps=10, concurrency=1),
        "obligation_refs": (), "status": "PENDING",
    }
    values.update(overrides)
    return StepContractV1(**values)


def _changed(path: str, change: str = "modified") -> FileChange:
    return FileChange(path=path, change=change, kind="file", mode=0o644,
                      size=10, sha256="0" * 64)


@pytest.mark.parametrize(
    "model_id",
    ["Muse", "muse-spark-1.3", "meta/muse-spark-1.3",
     "openrouter/meta/muse-spark-1.3", "  Muse  "],
)
def test_is_muse_model_id_accepts_canonical_family(model_id: str) -> None:
    assert is_muse_model_id(model_id) is True


@pytest.mark.parametrize(
    "model_id",
    ["spark-1.3", "glimmer", "claude-sonnet-5", "google/gemini-3-pro",
     "openrouter/google/gemini-3-pro", "", None, "amuse-x", "museum-guide"],
)
def test_is_muse_model_id_rejects_foreign_or_empty(model_id: Any) -> None:
    assert is_muse_model_id(model_id) is False


@pytest.mark.asyncio
async def test_failure_ordering_cancelled_first(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    request = _request(
        tmp_path, repo, is_cancelled=lambda: True, model_override="codex-1",
        activation_id=None,
    )
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(request)
    assert caught.value.code == "CANCELLED"
    assert port.params == [] and lookup.get_calls == []


@pytest.mark.asyncio
async def test_foreign_model_route_refuses_before_lookup(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for model in ("claude-sonnet-5", None):
        port, lookup = StubSessionPort(), FakeLookup()
        with pytest.raises(RealExecuteError) as caught:
            await _adapter(port, lookup).execute_step(
                _request(tmp_path, repo, model_override=model)
            )
        assert caught.value.code == "MODEL_ROUTE_NOT_MUSE"
        assert port.params == [] and lookup.get_calls == []


@pytest.mark.asyncio
async def test_demo_capability_refuses_before_spawn(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, demo_capability=True)
        )
    assert caught.value.code == "MUSE_DEMO_CAPABILITY_UNSUPPORTED"
    assert port.params == []


@pytest.mark.asyncio
async def test_missing_binding_refs_refuse(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, activation_id=None)
        )
    assert caught.value.code == "WORKHUB_BINDING_MISSING"
    assert port.params == [] and lookup.get_calls == []


@pytest.mark.asyncio
async def test_unknown_activation_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup(records={})
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "WORKHUB_BINDING_UNKNOWN_ACTIVATION"
    assert port.params == []


@pytest.mark.asyncio
async def test_terminal_activation_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lookup = FakeLookup({ACTIVATION: _record(status="SUCCEEDED")})
    port = StubSessionPort()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_ACTIVATION_TERMINAL"
    assert port.params == []


@pytest.mark.asyncio
async def test_unaccepted_activation_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lookup = FakeLookup({ACTIVATION: _record(accepted=False)})
    port = StubSessionPort()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_ACTIVATION_NOT_ACCEPTED"
    assert port.params == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,code",
    [
        ("candidate_sha", "deadbee", "ACTIVATION_CANDIDATE_MISMATCH"),
        ("plan_sha256", "ff" * 32, "ACTIVATION_PLAN_MISMATCH"),
        ("selection_id", "sel-9", "ACTIVATION_SELECTION_MISMATCH"),
    ],
)
async def test_selection_ref_mismatch_refuses(
    tmp_path: Path, field: str, value: str, code: str
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, **{field: value})
        )
    assert caught.value.code == code
    assert port.params == []


@pytest.mark.asyncio
async def test_workflow_identity_recomputed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lookup = FakeLookup({ACTIVATION: _record(workflow_id="wrong-id")})
    port = StubSessionPort()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "ACTIVATION_IDENTITY_MISMATCH"
    assert port.params == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,code",
    [
        ("model", "spark-1.3", "ACTIVATION_MODEL_MISMATCH"),
        ("executor", "spark-lane", "ACTIVATION_EXECUTOR_MISMATCH"),
    ],
)
async def test_selection_lane_mismatch_refuses(
    tmp_path: Path, field: str, value: str, code: str
) -> None:
    repo = _repo(tmp_path)
    lookup = FakeLookup({ACTIVATION: _record(**{field: value})})
    port = StubSessionPort()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == code
    assert port.params == []


@pytest.mark.asyncio
async def test_malformed_stored_binding_refuses_with_maintained_code(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    bad = _record()
    bad["workhub_binding"] = _binding(schema_version=999)
    lookup = FakeLookup({ACTIVATION: bad})
    port = StubSessionPort()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "WORKHUB_BINDING_REFUSED"
    assert "WORKHUB_BINDING_VERSION_UNSUPPORTED" in caught.value.detail
    assert port.params == []


@pytest.mark.asyncio
async def test_stale_attempt_ref_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, workhub_attempt_id="whattempt-2")
        )
    assert caught.value.code == "WORKHUB_BINDING_ATTEMPT_MISMATCH"
    assert port.params == []


@pytest.mark.asyncio
async def test_foreign_attempt_owner_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lookup = FakeLookup()
    port = StubSessionPort()

    async def only_other(tenant_id: str, project_id: str, attempt: str) -> list[str]:
        assert (tenant_id, project_id, attempt) == (TENANT, PROJECT, WH_ATTEMPT)
        return ["act-other"]

    lookup.find_attempt_activation = only_other  # type: ignore[method-assign]
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "WORKHUB_BINDING_ATTEMPT_FOREIGN"
    assert port.params == []


@pytest.mark.asyncio
async def test_missing_repo_registry_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.delenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", raising=False)
    settings = SimpleNamespace(OBJECTIVE_CYCLE_REPOSITORY_ROOTS="{}", OBJECTIVE_CYCLE_WORKSPACE_BASE="")
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, settings=settings)
        )
    assert caught.value.code == "REPOSITORY_ROOT_UNKNOWN"
    assert port.params == []


@pytest.mark.asyncio
async def test_missing_admitted_file_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, allowed_files=("ghost.py",))
        )
    assert caught.value.code == "MUSE_MATERIALIZE_MISSING"
    assert port.params == []


@pytest.mark.asyncio
async def test_escaping_repo_symlink_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (tmp_path / "secret.txt").write_text("s3cr3t\n", encoding="utf-8")
    os.symlink(str(tmp_path / "secret.txt"), repo / "link.py")
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, allowed_files=("link.py",))
        )
    assert caught.value.code == "MUSE_MATERIALIZE_UNSAFE_SOURCE"
    assert port.params == []


@pytest.mark.asyncio
async def test_unproven_scope_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, allowed_files=None)
        )
    assert caught.value.code == "MUSE_SCOPE_UNPROVEN"
    assert port.params == []


@pytest.mark.asyncio
async def test_success_returns_authentic_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    lookup.round = 2
    port.append_to = ("helper.py",)
    port.changes = (_changed("helper.py"),)
    secret_bearer = "Bearer REDACTED_CREDENTIALS"
    request = _request(
        tmp_path, repo,
        caller_authorization=secret_bearer,
        objective="Set API_KEY=supersecret123 in config",
    )
    result = await _adapter(port, lookup).execute_step(request)
    assert result.ok is True
    assert result.code == "MUSE_EXECUTE_OK"
    assert result.executor == MUSE_EXECUTOR_ID
    assert result.objective_status == "INCOMPLETE"
    assert result.execution_status == "COMPLETED"
    assert result.workhub_lifecycle_authority is False
    assert len(port.params) == 1
    assert str(port.params[0].workspace) == result.workspace
    scope_parts = Path(result.workspace).parts
    assert TENANT in scope_parts and CYCLE in scope_parts
    # Materialized exact candidate, then the stub's edit on top.
    staged = Path(result.workspace, "helper.py")
    assert staged.is_file()
    assert "stub worker edit" in staged.read_text(encoding="utf-8")
    # Evidence: transport transcript + adapter summary, outside the workspace.
    assert len(result.evidence_refs) == 2
    for ref in result.evidence_refs:
        assert Path(ref).is_file()
        assert Path(result.workspace) not in Path(ref).parents
    assert result.changed_source_files[0]["path"] == "helper.py"
    assert result.changed_source_files[0]["change"] == "modified"
    assert result.token_usage is not None
    assert result.token_usage["model"] == "muse-spark-1.3"
    # Round + selection authority recorded, never trusted from the caller.
    assert port.params[0].prompt.count("authorized round 2") == 1
    # No credentials travel: the bearer never reaches prompt or params.
    assert secret_bearer not in port.params[0].prompt
    assert "supersecret123" not in port.params[0].prompt
    assert "***REDACTED***" in port.params[0].prompt
    assert "authorization" not in {
        f.name for f in dataclasses.fields(port.params[0])
    }
    assert ACTIVATION in port.params[0].prompt
    assert WH_ATTEMPT in port.params[0].prompt


@pytest.mark.asyncio
async def test_read_only_empty_scope_stages_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    result = await _adapter(port, lookup).execute_step(
        _request(tmp_path, repo, allowed_files=())
    )
    assert result.ok is True
    assert result.changed_source_files == []
    assert not Path(result.workspace, "helper.py").exists()


@pytest.mark.asyncio
async def test_reverse_suffix_is_not_coverage(tmp_path: Path) -> None:
    repo = _repo(tmp_path, files={"src/helper.py": "x = 1\n"})
    port, lookup = StubSessionPort(), FakeLookup()
    port.changes = (_changed("helper.py"),)
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, allowed_files=("src/helper.py",))
        )
    assert caught.value.code == "MUSE_PATH_OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_subdirectory_coverage_is_exact(tmp_path: Path) -> None:
    repo = _repo(tmp_path, files={"src/a.py": "x = 1\n"})
    port, lookup = StubSessionPort(), FakeLookup()
    port.append_to = ("src/a.py",)
    port.changes = (_changed("src/a.py"),)
    result = await _adapter(port, lookup).execute_step(
        _request(tmp_path, repo, allowed_files=("src/",))
    )
    assert result.ok is True
    assert result.changed_source_files[0]["path"] == "src/a.py"


@pytest.mark.asyncio
async def test_out_of_scope_deletion_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path, files={"helper.py": "x = 1\n", "other.py": "y = 2\n"})
    port, lookup = StubSessionPort(), FakeLookup()
    port.changes = (FileChange(path="other.py", change="deleted", kind="file",
                               mode=0o644, size=6, sha256=None),)
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PATH_OUT_OF_SCOPE"
    assert caught.value.events, "transcript refs must ride the refusal"


@pytest.mark.asyncio
async def test_escaping_symlink_output_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    port.changes = (FileChange(path="link", change="added", kind="symlink",
                               mode=None, link_target="/etc/passwd"),)
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PATH_ESCAPES_WORKSPACE"


@pytest.mark.asyncio
async def test_provider_echo_mismatch_refuses_with_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    port.model_echo = "codex-other"
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "EXECUTOR_MODEL_MISMATCH"
    assert caught.value.events, "transcript refs must ride the refusal"


@pytest.mark.asyncio
async def test_tool_failure_is_explicit_failure_result(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    port.tool_failures = (MuseToolFailure(item_id="it-t", kind="toolCall", status="failed"),)
    port.changes = (_changed("helper.py"),)
    result = await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert result.ok is False
    assert result.code == "MUSE_TOOL_FAILED"
    assert result.reason_code == "MUSE_TOOL_FAILED"
    assert result.objective_status == "INCOMPLETE"
    assert len(result.evidence_refs) == 2
    assert all(Path(ref).is_file() for ref in result.evidence_refs)


@pytest.mark.asyncio
async def test_budget_overrun_is_explicit_failure_result(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    contract = _contract(step_budget=BudgetV1(
        time_seconds=3600, tokens=10, cost_usd=5.0, retries=2, steps=10, concurrency=1,
    ))
    result = await _adapter(port, lookup).execute_step(
        _request(tmp_path, repo, step_contract=contract)
    )
    assert result.ok is False
    assert result.code == "MUSE_BUDGET_EXCEEDED"
    assert port.params[0].token_budget == 10


@pytest.mark.asyncio
async def test_step_budget_narrows_limits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    contract = _contract(step_budget=BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=2, concurrency=1,
    ))
    await _adapter(port, lookup).execute_step(
        _request(tmp_path, repo, step_contract=contract)
    )
    assert port.params[0].timeout_seconds == 60
    assert port.params[0].token_budget == 1000
    port2 = StubSessionPort()
    await _adapter(port2, lookup).execute_step(_request(tmp_path, repo))
    assert port2.params[0].timeout_seconds == 300.0
    assert port2.params[0].token_budget is None


@pytest.mark.asyncio
async def test_evidence_dir_private_under_permissive_umask(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    old_umask = os.umask(0o002)
    try:
        result = await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    finally:
        os.umask(old_umask)
    assert result.ok is True
    # Every newly created evidence component stays private even when the
    # host umask/default ACL would make a plain mkdir group-writable.
    evidence_dir = Path(port.params[0].evidence_dir)
    stop = tmp_path / "ws-base"
    node = evidence_dir
    checked = 0
    while node != stop:
        mode = os.stat(node).st_mode
        assert mode & 0o022 == 0, f"{node} mode {oct(mode)}"
        assert os.stat(node).st_uid == os.geteuid()
        checked += 1
        node = node.parent
    assert checked == 5


@pytest.mark.asyncio
async def test_preexisting_shared_evidence_dir_refuses_before_turn(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    base = tmp_path / "ws-base" / ".aix-muse-evidence"
    leaf = base / TENANT / CYCLE / "step-1" / "attempt-1"
    leaf.mkdir(parents=True, exist_ok=True)
    # Ancestors stay private so the refusal names the pre-existing leaf;
    # the leaf itself is never chmodded, adopted, or replaced.
    node = base
    for part in (TENANT, CYCLE, "step-1"):
        node = node / part
        os.chmod(node, 0o700)
    os.chmod(base, 0o700)
    os.chmod(leaf, 0o775)
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_EVIDENCE_DIR_SHARED"
    assert port.params == [], "no model turn may run into an unsafe directory"
    assert os.stat(leaf).st_mode & 0o022 != 0, "fixture must not repair itself"


@pytest.mark.asyncio
async def test_transport_error_code_survives(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    port.fail_with = MuseSessionError("MUSE_TURN_TIMEOUT", "too slow")
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_TURN_TIMEOUT"
    assert caught.value.detail == "too slow"


@pytest.mark.asyncio
async def test_no_cross_request_state(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup({
        ACTIVATION: _record(),
        "act-2": _record(
            activation_id="act-2",
            workhub_binding=_binding(workhub_attempt_id="whattempt-2"),
        ),
    })
    adapter = _adapter(port, lookup)
    first = await adapter.execute_step(_request(tmp_path, repo))
    second = await adapter.execute_step(
        _request(tmp_path, repo, activation_id="act-2", workhub_attempt_id="whattempt-2",
                 workspace_attempt_id="attempt-2")
    )
    assert first.ok and second.ok
    assert len(port.params) == 2
    assert WH_ATTEMPT in port.params[0].prompt
    assert "whattempt-2" in port.params[1].prompt
    assert "whattempt-2" not in port.params[0].prompt


@pytest.mark.asyncio
async def test_capability_policy_flows_and_forbidden_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    policy = MuseCapabilityPolicy(approval_mode="promptUnmatched",
                                  permission_profile=":auto-review")
    result = await _adapter(port, lookup, capability_policy=policy).execute_step(
        _request(tmp_path, repo)
    )
    assert result.ok is True
    assert port.params[0].approval_mode == "promptUnmatched"
    assert port.params[0].permission_profile == ":auto-review"
    with pytest.raises(ValueError, match="MUSE_POLICY_FORBIDDEN"):
        MuseExecuteAdapter(session_port=port, binding_lookup=lookup,  # type: ignore[arg-type]
                           capability_policy=MuseCapabilityPolicy(approval_mode="allowAll"))
    with pytest.raises(ValueError, match="MUSE_POLICY_FORBIDDEN"):
        MuseExecuteAdapter(session_port=port, binding_lookup=lookup,  # type: ignore[arg-type]
                           capability_policy=MuseCapabilityPolicy(permission_profile=":unrestricted"))
    with pytest.raises(ValueError, match="MUSE_CAPABILITY_POLICY_INVALID"):
        validate_capability_policy(MuseCapabilityPolicy(approval_mode="sometimes"))


class _RecordingLegacyPort:
    def __init__(self) -> None:
        from backend.aix_core.framework.execution.port import ExecuteStepRequest

        self.requests: list[ExecuteStepRequest] = []

    async def execute_step(self, request: Any) -> RealExecuteResult:
        self.requests.append(request)
        return RealExecuteResult(ok=True, code="REAL_EXECUTE_OK", workspace="ws")


async def _admit_start_approve_muse_routed(backend: Any) -> tuple[str, str]:
    admit = await backend.command(
        operation="admit_objective", tenant_id=TENANT, actor_id="human-1", resource_id=None,
        body={"workhub_task_id": "task-1", "objective": "Add a helper function",
              "task_class": "single_file_helper", "definition_of_done": ("done",),
              "repository_id": "aix-frontend", "allowed_files": (), "project_id": "proj-1"},
        idempotency_key="admit-muse", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id=TENANT, actor_id="human-1",
        resource_id=admit["objective_id"], body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-muse", expected_version=None,
    )
    cycle_id = started["cycle_id"]
    backend._cycles[cycle_id].model_routes = {  # noqa: SLF001
        "executor": {"selected_model_id": "muse-spark-1.3"}
    }
    backend._understanding_evidence[admit["objective_id"]] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=admit["objective_id"], cycle_id=cycle_id,
        files_read=("helper.py",), observed_problem="test evidence",
        understanding_complete=True,
    )
    seed_contract = _contract(cycle_id=cycle_id, tenant_id=TENANT)
    # Semantically complete accepted contract: cover every admission-derived
    # required obligation through the maintained obligation store (same
    # producer pattern as the execution harness), so dispatch reaches the
    # genuine Muse refusal instead of stopping at PLAN_INCOMPLETE.
    obligation_ids = tuple(
        o.id for o in backend._obligations.get(admit["objective_id"], ())  # noqa: SLF001
    )
    if obligation_ids:
        seed_contract = seed_contract.model_copy(
            update={"obligation_refs": obligation_ids}
        )
    backend._step_contracts[admit["objective_id"]] = seed_contract  # noqa: SLF001
    await backend.command(
        operation="validate_approval_reference", tenant_id=TENANT, actor_id="human-1",
        resource_id=cycle_id, body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-muse", expected_version=started["version"],
    )
    return admit["objective_id"], cycle_id


async def _settle_pending_execute_task(
    backend: DurableObjectiveCycleApiBackend, cycle_id: str
) -> None:
    """Await any approve-time background self-drive through the maintained
    task registry (same mechanism the dispatcher tests use).

    Approval schedules a body-less background dispatch that carries no
    activity command body — and therefore no owner refs. If it is still in
    flight when the command below runs, it can win the race and finalize
    the cycle with an unbound refusal before the command-driven dispatch
    (which carries the body exactly as production ``_execute_via_backend``
    supplies it) ever executes. Settling first makes the command the
    deterministic executor; the ready mark below then re-arms the cycle
    through the maintained lifecycle. No terminal fields are set directly.
    """
    pending = backend._execute_tasks.get(cycle_id)  # noqa: SLF001
    if pending is not None:
        await asyncio.wait_for(pending, timeout=120.0)


async def _drive_muse_route_to_refusal(
    backend: DurableObjectiveCycleApiBackend,
    cycle_id: str,
    command_id: str,
    command_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drive the maintained execution route after approval and wait it out.

    Seeds an ACCEPTED plan wrapping the admitted step contract and marks
    STEP_READY through the only producer of that state (the same maintained
    seeding the dispatcher tests use), then issues the maintained
    dispatch_ready_step command and awaits the real execution task when
    dispatch runs it in the background. Admission itself is never bypassed:
    the cycle was admitted, started, and approved through real commands
    before this runs. Nothing here forces last_error or fabricates a
    refusal; the returned dispatch result and the recorded cycle state are
    whatever production produced.
    """
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    await _settle_pending_execute_task(backend, cycle_id)
    step = backend._step_contracts[cycle.objective_id]  # noqa: SLF001
    plan = PlanRevisionV1(
        objective_id=cycle.objective_id,
        cycle_id=cycle.cycle_id,
        revision=1,
        status="ACCEPTED",
        steps=(step,),
    )
    backend._admit_plan_revision(plan)  # noqa: SLF001
    ready = await backend._mark_dispatch_ready(  # noqa: SLF001
        cycle=cycle,
        state="STEP_READY",
        command_id=f"{command_id}-ready",
        step=step,
        reason="muse-route-refusal",
    )
    assert ready.get("ok") is True, ready
    dispatched = await backend.command(
        operation="dispatch_ready_step",
        tenant_id=cycle.tenant_id,
        actor_id="tester",
        resource_id=cycle.cycle_id,
        body=dict(command_body) if command_body is not None else {},
        idempotency_key=f"{command_id}-dispatch",
        expected_version=None,
    )
    task = backend._execute_tasks.get(cycle.cycle_id)  # noqa: SLF001
    if task is not None:
        await asyncio.wait_for(task, timeout=120.0)
    return dispatched


@pytest.mark.asyncio
async def test_backend_muse_route_without_deps_refuses_without_spawn() -> None:
    legacy = _RecordingLegacyPort()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=legacy,  # type: ignore[arg-type]
        framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    _, cycle_id = await _admit_start_approve_muse_routed(backend)
    dispatched = await _drive_muse_route_to_refusal(backend, cycle_id, "muse-route-no-deps")
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    assert legacy.requests == [], "foreground executor must not run a Muse route"
    assert cycle.real_execute is None, "no result may be recorded"
    # Maintained recording contract: the exact refusal code lands on
    # terminal_reason while last_error_detail carries the human detail
    # (finalize overwrites the code-prefixed draft with the error's own
    # detail), so a code prefix is the wrong shape to assert.
    assert cycle.terminal_reason == "MUSE_RUNTIME_NOT_BOUND", (
        dispatched, cycle.dispatch_state, cycle.last_error_detail,
    )
    assert "Muse" in (cycle.last_error_detail or "")


@pytest.mark.asyncio
async def test_backend_muse_route_with_deps_selects_adapter_and_refuses_unbound() -> None:
    legacy = _RecordingLegacyPort()
    session_port, lookup = StubSessionPort(), FakeLookup()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=legacy,  # type: ignore[arg-type]
        muse_session_port=session_port,  # type: ignore[arg-type]
        muse_binding_lookup=lookup,  # type: ignore[arg-type]
        framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    _, cycle_id = await _admit_start_approve_muse_routed(backend)
    dispatched = await _drive_muse_route_to_refusal(backend, cycle_id, "muse-route-unbound")
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    # Adapter was selected (legacy untouched) and refused before any spawn:
    # the command seam carries no activation refs yet (M1 follow-up).
    assert legacy.requests == []
    assert session_port.params == []
    assert cycle.real_execute is None
    assert cycle.terminal_reason == "WORKHUB_BINDING_MISSING", (
        dispatched, cycle.dispatch_state, cycle.last_error_detail,
    )
    assert "Muse" in (cycle.last_error_detail or "")


@pytest.mark.asyncio
async def test_backend_muse_route_flag_off_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(**kwargs: Any):  # type: ignore[no-untyped-def]
        raise AssertionError("legacy executor must not run a Muse route")

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute", explode
    )
    legacy = _RecordingLegacyPort()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=legacy,  # type: ignore[arg-type]
        framework_flags=FrameworkFlagsSnapshot(execution_port=False),
    )
    _, cycle_id = await _admit_start_approve_muse_routed(backend)
    dispatched = await _drive_muse_route_to_refusal(backend, cycle_id, "muse-route-flag-off")
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    assert legacy.requests == []
    assert cycle.real_execute is None
    assert cycle.terminal_reason == "MUSE_RUNTIME_NOT_BOUND", (
        dispatched, cycle.dispatch_state, cycle.last_error_detail,
    )
    assert "Muse" in (cycle.last_error_detail or "")


# --- Lane-ops #23 M1 owner-ref wiring acceptance -------------------------


def _owner_expected() -> dict[str, str]:
    """The exact opaque owner tuple the canonical record provides."""
    return {
        "activation_id": ACTIVATION,
        "selection_id": SELECTION,
        "workhub_attempt_id": WH_ATTEMPT,
        "candidate_sha": CANDIDATE,
        "plan_sha256": PLAN,
        "accepted_model_id": "muse-spark-1.3",
        "worker_id": "w-1",
    }


# Workflow-input fields M1 newly added (candidate_sha/plan_sha256 were
# already required before M1). Backward compatibility means old VALID
# payloads, so only these are omitted for the pre-M1 replay check.
_M1_WORKFLOW_INPUT_FIELDS = (
    "activation_id",
    "selection_id",
    "workhub_attempt_id",
    "accepted_model_id",
    "worker_id",
)


def test_owner_contract_deterministic_replay_and_legacy_exclusion(
    tmp_path: Path,
) -> None:
    from backend.workflows.temporal.objective_cycle_contracts import (
        ObjectiveCycleWorkflowInput,
        build_activity_input,
        muse_owner_refs_from_mapping,
    )

    raw = {
        "contract_version": "aix.objective-cycle.temporal-contract.v1",
        "workflow_version": 1,
        "tenant_id": TENANT,
        "objective_id": OBJECTIVE,
        "cycle_id": CYCLE,
        "candidate_sha": CANDIDATE,
        "plan_sha256": PLAN,
        "start_request_id": SELECTION,
        "correlation_id": "corr-1",
        **_owner_expected(),
    }
    first = ObjectiveCycleWorkflowInput.model_validate(raw)
    # Mapping and attribute sources agree byte-for-byte.
    assert muse_owner_refs_from_mapping(first) == _owner_expected()
    assert muse_owner_refs_from_mapping(raw) == _owner_expected()
    # Deterministic replay: same input serializes identically.
    again = ObjectiveCycleWorkflowInput.model_validate(raw)
    assert first.model_dump(mode="json") == again.model_dump(mode="json")
    refs = {
        "ledger_ref": f"pg://cycle/{CYCLE}",
        **muse_owner_refs_from_mapping(first),
    }
    first_activity = build_activity_input(
        workflow_input=first, activity_name="dispatch_step",
        aggregate_version=3, causation_id="cause-1", payload_refs=refs,
        step_id="step-1", attempt_id="attempt-1",
    )
    second_activity = build_activity_input(
        workflow_input=again, activity_name="dispatch_step",
        aggregate_version=3, causation_id="cause-1", payload_refs=refs,
        step_id="step-1", attempt_id="attempt-1",
    )
    assert first_activity.activity_id == second_activity.activity_id
    assert {
        key: first_activity.payload_refs[key] for key in _owner_expected()
    } == _owner_expected()
    # Pre-M1 payloads still validate (backward compatible): omit only the
    # fields M1 newly added, retaining the pre-existing required
    # candidate_sha/plan_sha256. They carry no owner refs and a Muse route
    # refuses them downstream.
    legacy_raw = {
        key: value
        for key, value in raw.items()
        if key not in _M1_WORKFLOW_INPUT_FIELDS
    }
    legacy_input = ObjectiveCycleWorkflowInput.model_validate(legacy_raw)
    # The pre-existing required candidate/plan refs are intentionally
    # returned by the shared helper; only the five M1-new fields stay absent.
    assert muse_owner_refs_from_mapping(legacy_input) == {
        "candidate_sha": CANDIDATE,
        "plan_sha256": PLAN,
    }
    # Owner refs never reach the legacy executor surface.
    repo = _repo(tmp_path)
    kwargs = _request(tmp_path, repo).to_legacy_kwargs()
    for key in (
        "activation_id", "workhub_attempt_id", "selection_id",
        "candidate_sha", "plan_sha256", "worker_id", "accepted_model_id",
    ):
        assert key not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,code",
    [
        ("selection_id", "ACTIVATION_SELECTION_MISSING"),
        ("candidate_sha", "ACTIVATION_CANDIDATE_MISSING"),
        ("plan_sha256", "ACTIVATION_PLAN_MISSING"),
        ("accepted_model_id", "ACTIVATION_MODEL_MISSING"),
        ("worker_id", "ACTIVATION_WORKER_MISSING"),
    ],
)
async def test_missing_owner_ref_refuses_before_spawn(
    tmp_path: Path, field: str, code: str
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, **{field: None})
        )
    assert caught.value.code == code
    assert port.params == [], "no model turn may spawn without the full owner tuple"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,code",
    [
        ("accepted_model_id", "muse-spark-2.0", "ACTIVATION_MODEL_MISMATCH"),
        ("worker_id", "w-9", "ACTIVATION_WORKER_MISMATCH"),
    ],
)
async def test_model_worker_mismatch_refuses_before_spawn(
    tmp_path: Path, field: str, value: str, code: str
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, **{field: value})
        )
    assert caught.value.code == code
    assert port.params == []


@pytest.mark.asyncio
async def test_route_model_mismatch_refuses_before_spawn(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(
            _request(tmp_path, repo, model_override="muse-spark-2.0")
        )
    assert caught.value.code == "ACTIVATION_MODEL_MISMATCH"
    assert port.params == []


@pytest.mark.asyncio
async def test_legacy_family_model_record_refuses_before_spawn(
    tmp_path: Path,
) -> None:
    # A record carrying only the family name ("Muse") cannot prove which
    # exact model was accepted: fail closed until the selection owner
    # records exact model ids.
    repo = _repo(tmp_path)
    lookup = FakeLookup({ACTIVATION: _record(model="Muse")})
    port = StubSessionPort()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(port, lookup).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "ACTIVATION_MODEL_MISMATCH"
    assert port.params == []


@pytest.mark.asyncio
async def test_backend_muse_route_partial_refs_refuse_without_fallback() -> None:
    legacy = _RecordingLegacyPort()
    session_port, lookup = StubSessionPort(), FakeLookup()
    preflight, _transport = _preflight_for()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=legacy,  # type: ignore[arg-type]
        muse_session_port=session_port,  # type: ignore[arg-type]
        muse_binding_lookup=lookup,  # type: ignore[arg-type]
        muse_preflight_verifier=preflight,
        framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    objective_id, cycle_id = await _admit_start_approve_muse_routed(backend)
    # Bind the authoritative fake record to the actual dynamic
    # objective/cycle/workflow while preserving the ref under test
    # (selection stays absent here); otherwise the identity check
    # correctly precedes the selection check and the wrong gate fires.
    lookup.records[ACTIVATION] = _record(
        objective_id=objective_id,
        cycle_id=cycle_id,
        workflow_id=objective_cycle_workflow_id(
            tenant_id=TENANT, objective_id=objective_id, cycle_id=cycle_id
        ),
    )
    body = {"activation_id": ACTIVATION, "workhub_attempt_id": WH_ATTEMPT}
    dispatched = await _drive_muse_route_to_refusal(
        backend, cycle_id, "muse-route-partial-refs", command_body=body
    )
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    assert legacy.requests == []
    assert session_port.params == []
    assert cycle.real_execute is None
    assert cycle.terminal_reason == "ACTIVATION_SELECTION_MISSING", (
        dispatched, cycle.dispatch_state, cycle.last_error_detail,
    )


@pytest.mark.asyncio
async def test_backend_muse_route_mismatched_ref_refuses_without_fallback() -> None:
    legacy = _RecordingLegacyPort()
    session_port, lookup = StubSessionPort(), FakeLookup()
    preflight, _transport = _preflight_for()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=legacy,  # type: ignore[arg-type]
        muse_session_port=session_port,  # type: ignore[arg-type]
        muse_binding_lookup=lookup,  # type: ignore[arg-type]
        muse_preflight_verifier=preflight,
        framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    objective_id, cycle_id = await _admit_start_approve_muse_routed(backend)
    # Bind the authoritative fake record to the actual dynamic
    # objective/cycle/workflow while preserving the ref under test (only
    # the body candidate is substituted); otherwise the identity check
    # correctly precedes the candidate check and the wrong gate fires.
    lookup.records[ACTIVATION] = _record(
        objective_id=objective_id,
        cycle_id=cycle_id,
        workflow_id=objective_cycle_workflow_id(
            tenant_id=TENANT, objective_id=objective_id, cycle_id=cycle_id
        ),
    )
    body = {**_owner_expected(), "candidate_sha": "deadbee2"}
    dispatched = await _drive_muse_route_to_refusal(
        backend, cycle_id, "muse-route-mismatch", command_body=body
    )
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    assert legacy.requests == []
    assert session_port.params == []
    assert cycle.real_execute is None
    assert cycle.terminal_reason == "ACTIVATION_CANDIDATE_MISMATCH", (
        dispatched, cycle.dispatch_state, cycle.last_error_detail,
    )


@pytest.mark.asyncio
async def test_owner_refs_threaded_starter_to_spawn_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full M1 trace on maintained seams: starter record -> workflow input
    -> dispatch payload_refs -> activity command body -> backend dispatch
    -> ExecuteStepRequest -> real adapter spawn, with the legacy executor
    never called.
    """
    from backend.session_factory.activation_runtime import (
        CanonicalTemporalMuseStarter,
    )
    from backend.workflows.temporal.objective_cycle_contracts import (
        ObjectiveCycleWorkflowInput,
        build_activity_input,
        muse_owner_refs_from_mapping,
    )
    from backend.workflows.temporal.objective_cycle_activities import (
        _execute_via_backend,
    )

    repo = _repo(tmp_path)
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"aix-frontend": str(repo)}),
    )
    legacy = _RecordingLegacyPort()
    session_port, lookup = StubSessionPort(), FakeLookup(records={})
    # The verifier handle threads through backend construction before the
    # record exists; the live mirror lands once admission fixes the ids
    # (shared transport reference, no caller-supplied authority).
    preflight_transport = FakeWorkHubTransport([])
    preflight = FakePreflight(preflight_transport)
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=legacy,  # type: ignore[arg-type]
        muse_session_port=session_port,  # type: ignore[arg-type]
        muse_binding_lookup=lookup,  # type: ignore[arg-type]
        muse_preflight_verifier=preflight,
        framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    objective_id, cycle_id = await _admit_start_approve_muse_routed(backend)
    # Settle the approve-time self-drive before driving the command path,
    # so the activity command below (with body, as production supplies it)
    # is the deterministic executor.
    await _settle_pending_execute_task(backend, cycle_id)
    traced_workflow_id = objective_cycle_workflow_id(
        tenant_id=TENANT, objective_id=objective_id, cycle_id=cycle_id
    )
    record = _record(
        objective_id=objective_id,
        cycle_id=cycle_id,
        workflow_id=traced_workflow_id,
        idempotency_key=traced_workflow_id,
    )
    lookup.records[ACTIVATION] = record
    preflight_transport.mirror(record["workhub_binding"])
    # Starter hop: canonical record -> raw workflow input, unchanged.
    captured_raw: dict[str, Any] = {}

    async def _fake_start(*, raw: dict[str, Any]) -> dict[str, Any]:
        captured_raw.update(raw)
        return {
            "workflow": "AixObjectiveCycleWorkflow",
            "workflow_id": record["workflow_id"],
        }

    receipt = await CanonicalTemporalMuseStarter(_fake_start)(record)
    assert receipt["muse_activation_receipt"] == f"muse:{ACTIVATION}"
    workflow_input = ObjectiveCycleWorkflowInput.model_validate(captured_raw)
    for key, expected in _owner_expected().items():
        assert captured_raw[key] == expected
        assert getattr(workflow_input, key) == expected
    # Workflow hop: input -> dispatch payload_refs, unchanged. Production
    # adds the approver actor attribution alongside (never in the owner
    # set); it travels under its own key and must not be conflated with
    # the accepted worker binding.
    payload_refs = {
        "ledger_ref": f"pg://cycle/{cycle_id}",
        **muse_owner_refs_from_mapping(workflow_input),
        "actor_id": "trace-approver",
    }
    activity_input = build_activity_input(
        workflow_input=workflow_input, activity_name="dispatch_step",
        aggregate_version=0, causation_id="cause-e2e-1",
        payload_refs=payload_refs,
        step_id="trace-step-1", attempt_id="trace-attempt-1",
    )
    for key, expected in _owner_expected().items():
        assert activity_input.payload_refs[key] == expected
    assert activity_input.payload_refs["actor_id"] == "trace-approver"
    # Backend hop: seed the ACCEPTED plan + STEP_READY through the same
    # maintained producers the dispatcher tests use, then run the real
    # activity function against the real backend.
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    step = backend._step_contracts[objective_id]  # noqa: SLF001
    backend._admit_plan_revision(  # noqa: SLF001
        PlanRevisionV1(
            objective_id=objective_id, cycle_id=cycle_id, revision=1,
            status="ACCEPTED", steps=(step,),
        )
    )
    ready = await backend._mark_dispatch_ready(  # noqa: SLF001
        cycle=cycle, state="STEP_READY", command_id="trace-ready",
        step=step, reason="muse-owner-trace",
    )
    assert ready.get("ok") is True, ready
    captured_requests: list[ExecuteStepRequest] = []
    real_execute_step = MuseExecuteAdapter.execute_step

    async def _spy(
        self: MuseExecuteAdapter, request: ExecuteStepRequest
    ) -> Any:
        captured_requests.append(request)
        return await real_execute_step(self, request)

    monkeypatch.setattr(MuseExecuteAdapter, "execute_step", _spy)
    envelope = await _execute_via_backend(
        activity_input=activity_input, operation="dispatch_ready_step",
        backend=backend,
    )
    assert envelope["ok"] is True, envelope
    task = backend._execute_tasks.get(cycle_id)  # noqa: SLF001
    if task is not None:
        await asyncio.wait_for(task, timeout=120.0)
    # Every ref survived into the executed request byte-for-byte; the
    # legacy executor never ran and the real adapter spawned exactly once.
    assert len(captured_requests) == 1
    assert len(preflight.calls) == 2, "two live owner verifications per effect"
    executed = captured_requests[0]
    for key, expected in _owner_expected().items():
        assert getattr(executed, key) == expected, key
    # Approver attribution propagated without displacing the worker binding.
    assert executed.actor_id == "trace-approver"
    assert executed.worker_id == "w-1"
    assert legacy.requests == []
    assert len(session_port.params) == 1
    assert ACTIVATION in session_port.params[0].prompt
    assert WH_ATTEMPT in session_port.params[0].prompt
    # The backend stores a dict projection of the executor result, not the
    # result object itself; assert that maintained stored shape.
    stored = cycle.real_execute
    assert isinstance(stored, dict), stored
    assert stored.get("ok") is True, stored
    assert stored.get("code") == "MUSE_EXECUTE_OK", stored
    assert stored.get("evidence_refs"), stored


# --- Lane-ops #23 M3 live assignment/lease preflight acceptance --------


@pytest.mark.asyncio
async def test_preflight_exact_tuple_passes_to_spawn_once(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    preflight, transport = _preflight_for()
    result = await _adapter(
        port, lookup, preflight_verifier=preflight
    ).execute_step(_request(tmp_path, repo))
    assert result.ok is True
    assert len(port.params) == 1, "exactly one model turn per effect"
    assert len(preflight.calls) == 2, "two live verifications per effect"
    # One coherent owner read per gate: task + assignment (captured for
    # extras) + evidence. The route/expiry liveness observes the same
    # snapshot within each gate; nothing is shared between gates.
    assert len(transport.calls) == 6, transport.calls
    # The verifier received the store-fresh binding only: no credentials,
    # no caller input, and the sentinel JWT never left the transport.
    sent = preflight.calls[0]
    assert set(sent) == set(_binding())
    assert _SENTINEL_JWT not in json.dumps(sent)


@pytest.mark.asyncio
async def test_preflight_moved_during_preparation_refuses_before_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authority moving mid-preparation is caught by the second gate.

    The first (pre-workspace) verification passes; owner state is then
    mutated deterministically inside the preparation seam (evidence
    directory setup, after materialization and before spawn), and the
    fresh pre-activation verification must refuse with zero model turns.
    Both gates performed live reads — no proof cache or reuse.
    """
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    preflight, transport = _preflight_for()
    adapter = _adapter(port, lookup, preflight_verifier=preflight)
    real_prepare = MuseExecuteAdapter._prepare_evidence_dir

    def _move_then_prepare(self: Any, *args: Any, **kwargs: Any) -> Any:
        transport.live_assignments[(PROJECT, 1)]["holding_principal"] = "holder-9"
        return real_prepare(self, *args, **kwargs)

    monkeypatch.setattr(
        MuseExecuteAdapter, "_prepare_evidence_dir", _move_then_prepare
    )
    with pytest.raises(RealExecuteError) as caught:
        await adapter.execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PREFLIGHT_BINDING_LEASE_HOLDER_MISMATCH"
    assert port.params == [], (
        "no model turn may spawn on authority moved mid-preparation"
    )
    assert len(preflight.calls) == 2, "both gates ran fresh live reads"


@pytest.mark.asyncio
async def test_preflight_second_read_outage_refuses_before_turn(
    tmp_path: Path,
) -> None:
    """An owner outage at the second gate refuses closed, with zero turns.

    The first verification succeeds on live reads; the owner read then
    goes down before session activation. The outage must refuse as
    unavailable — never fall back, never spawn.
    """
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    _, transport = _preflight_for()
    real = FakePreflight(transport)
    calls = 0

    async def _flaky(binding: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("owner read lost before spawn")
        return await real.verify_assignment_lease(binding)

    adapter = _adapter(
        port, lookup,
        preflight_verifier=SimpleNamespace(verify_assignment_lease=_flaky),
    )
    with pytest.raises(RealExecuteError) as caught:
        await adapter.execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PREFLIGHT_OWNER_UNAVAILABLE"
    assert port.params == []
    assert calls == 2, "outage hit the second gate after a live first gate"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,key,value,code",
    [
        ("assignment", "holding_principal", "holder-9",
         "MUSE_PREFLIGHT_BINDING_LEASE_HOLDER_MISMATCH"),
        ("assignment", "lease_epoch", 2,
         "MUSE_PREFLIGHT_BINDING_LEASE_MOVED"),
        ("assignment", "assignment_record_version", 2,
         "MUSE_PREFLIGHT_BINDING_ASSIGNMENT_MOVED"),
        ("assignment", "task_id", "task-9",
         "MUSE_PREFLIGHT_BINDING_ASSIGNMENT_TASK_MISMATCH"),
        ("assignment", "authority_ref", "auth-9",
         "MUSE_PREFLIGHT_BINDING_ROUTE_FIELD_MISMATCH"),
        ("assignment", "plan_digest", "p9",
         "MUSE_PREFLIGHT_BINDING_ROUTE_FIELD_MISMATCH"),
        ("assignment", "route_digest", "r9",
         "MUSE_PREFLIGHT_BINDING_ROUTE_FIELD_MISMATCH"),
        ("assignment", "executor_sub", "sub-9",
         "MUSE_PREFLIGHT_BINDING_EXECUTOR_MISMATCH"),
        ("assignment", "lease_expires_at", "2020-01-01T00:00:00+00:00",
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRED"),
        ("assignment", "lease_expires_at", "<ABSENT>",
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("assignment", "lease_expires_at", "not-a-time",
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("assignment", "lease_expires_at", float("nan"),
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("assignment", "lease_expires_at", float("inf"),
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("assignment", "lease_expires_at", float("-inf"),
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("assignment", "lease_expires_at", "nan",
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("assignment", "lease_expires_at", "inf",
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("assignment", "lease_expires_at", "+Infinity",
         "MUSE_PREFLIGHT_BINDING_LEASE_EXPIRY_UNPROVEN"),
        ("task", "tenant_id", "tenant-9",
         "MUSE_PREFLIGHT_BINDING_TENANT_MISMATCH"),
        ("task", "id", "task-9",
         "MUSE_PREFLIGHT_BINDING_TASK_MISMATCH"),
        ("task", "active_execution_contract_ref", "contract-9",
         "MUSE_PREFLIGHT_BINDING_CONTRACT_MOVED"),
        ("receipt", "attempt_version", 2,
         "MUSE_PREFLIGHT_BINDING_ATTEMPT_UNPROVEN"),
    ],
)
async def test_preflight_moved_tuple_refuses_before_spawn(
    tmp_path: Path, target: str, key: str, value: Any, code: str
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    preflight, transport = _preflight_for()
    if target == "assignment":
        if value == "<ABSENT>":
            transport.live_assignments[(PROJECT, 1)].pop(key, None)
        else:
            transport.live_assignments[(PROJECT, 1)][key] = value
    elif target == "task":
        transport.live_tasks[(PROJECT, 1)][key] = value
    else:
        transport.live_evidence[TASK][0]["evidence_json"][key] = value
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(
            port, lookup, preflight_verifier=preflight
        ).execute_step(_request(tmp_path, repo))
    assert caught.value.code == code
    assert port.params == [], "no model turn may spawn on a moved assignment"


@pytest.mark.asyncio
async def test_preflight_moved_project_scope_refuses_before_spawn(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    preflight, transport = _preflight_for()
    transport.live_tasks.pop((PROJECT, 1))
    transport.live_assignments.pop((PROJECT, 1))
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(
            port, lookup, preflight_verifier=preflight
        ).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PREFLIGHT_BINDING_TASK_MISSING"
    assert port.params == []


@pytest.mark.asyncio
async def test_preflight_owner_unavailable_refuses_without_fallback(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    preflight, transport = _preflight_for()
    transport.fail_with = ConnectionError("fake workhub unreachable")
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(
            port, lookup, preflight_verifier=preflight
        ).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PREFLIGHT_OWNER_UNAVAILABLE"
    assert port.params == []


@pytest.mark.asyncio
async def test_preflight_unbound_refuses_before_spawn(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    with pytest.raises(RealExecuteError) as caught:
        await _adapter(
            port, lookup, preflight_verifier=None
        ).execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PREFLIGHT_UNBOUND"
    assert port.params == []


@pytest.mark.asyncio
async def test_preflight_replay_after_movement_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    preflight, transport = _preflight_for()
    adapter = _adapter(port, lookup, preflight_verifier=preflight)
    first = await adapter.execute_step(_request(tmp_path, repo))
    assert first.ok is True
    reads_after_first = len(transport.calls)
    assert reads_after_first > 0
    # Authoritative tuple moves between effects: the earlier success must
    # not authorize the later spawn. Freshness is per effect, nothing cached.
    transport.live_assignments[(PROJECT, 1)]["holding_principal"] = "holder-9"
    with pytest.raises(RealExecuteError) as caught:
        await adapter.execute_step(_request(tmp_path, repo))
    assert caught.value.code == "MUSE_PREFLIGHT_BINDING_LEASE_HOLDER_MISMATCH"
    assert len(transport.calls) > reads_after_first, "second effect re-read live state"
    assert len(port.params) == 1


@pytest.mark.asyncio
async def test_preflight_secret_hygiene(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    port, lookup = StubSessionPort(), FakeLookup()
    preflight, transport = _preflight_for()
    sentinel = transport.credential
    adapter = _adapter(port, lookup, preflight_verifier=preflight)
    # The shared request contract may carry caller auth for the legacy
    # executor; the adapter must ignore a carried value end to end.
    request = _request(tmp_path, repo, caller_authorization=sentinel)
    result = await adapter.execute_step(request)
    assert result.ok is True
    assert request.caller_authorization == sentinel
    transport.live_assignments[(PROJECT, 1)]["holding_principal"] = "holder-9"
    with pytest.raises(RealExecuteError) as caught:
        await adapter.execute_step(
            _request(tmp_path, repo, caller_authorization=sentinel)
        )
    # The sentinel JWT appears nowhere outside the transport that holds it.
    for params in port.params:
        assert sentinel not in params.prompt
        assert sentinel not in str(params.workspace)
        assert sentinel not in str(params.evidence_dir)
    assert sentinel not in str(caught.value.detail)
    assert sentinel not in str(caught.value.code)
    for ref in result.evidence_refs:
        assert sentinel not in Path(ref).read_text(encoding="utf-8")
    for sent in preflight.calls:
        assert sentinel not in json.dumps(sent)
    assert sentinel not in json.dumps(transport.calls)
    # ``caller_authorization`` legitimately exists on the shared
    # ExecuteStepRequest for non-Muse execution paths, so its absence
    # cannot be asserted here. The Muse boundary is proven instead: the
    # backend hands the adapter a sanitized copy with the field cleared
    # (see test_backend_muse_route_strips_caller_credential), while this
    # test proves the adapter itself never leaks a carried value into
    # prompt, workspace, evidence, errors, or owner reads.


@pytest.mark.asyncio
async def test_backend_muse_route_strips_caller_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backend routing boundary: no credential crosses into the adapter.

    The dispatch command body may carry caller auth for the shared
    (legacy) execution surface. The Muse branch must hand the adapter a
    sanitized copy with ``caller_authorization`` cleared, even when the
    inbound request contained a credential value. Non-Muse behavior is
    untouched: the shared request construction keeps the field.
    """
    repo = _repo(tmp_path)
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"aix-frontend": str(repo)}),
    )
    legacy = _RecordingLegacyPort()
    session_port, lookup = StubSessionPort(), FakeLookup()
    preflight, _transport = _preflight_for()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False,
        execution_port=legacy,  # type: ignore[arg-type]
        muse_session_port=session_port,  # type: ignore[arg-type]
        muse_binding_lookup=lookup,  # type: ignore[arg-type]
        muse_preflight_verifier=preflight,
        framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    objective_id, cycle_id = await _admit_start_approve_muse_routed(backend)
    # Bind the authoritative fake record to the actual dynamic
    # objective/cycle/workflow (same pattern as the ref-gate tests); the
    # record binding is the static canonical tuple the preflight
    # transport already mirrors.
    lookup.records[ACTIVATION] = _record(
        objective_id=objective_id,
        cycle_id=cycle_id,
        workflow_id=objective_cycle_workflow_id(
            tenant_id=TENANT, objective_id=objective_id, cycle_id=cycle_id
        ),
    )
    captured: list[ExecuteStepRequest] = []
    real_execute_step = MuseExecuteAdapter.execute_step

    async def _spy(
        self: MuseExecuteAdapter, request: ExecuteStepRequest
    ) -> Any:
        captured.append(request)
        return await real_execute_step(self, request)

    monkeypatch.setattr(MuseExecuteAdapter, "execute_step", _spy)
    sentinel = "Bearer [REDACTED]"
    body = {**_owner_expected(), "caller_authorization": sentinel}
    await _drive_muse_route_to_refusal(
        backend, cycle_id, "muse-route-credential-strip", command_body=body
    )
    cycle = backend._cycles[cycle_id]  # noqa: SLF001
    # The maintained driver performs a missing-binding refusal (the
    # body-less approve-time self-drive carries no owner refs) followed
    # by the successful command-driven retry. The inbound credential
    # reached the shared surface on the retry, but every request crossing
    # into the adapter is a sanitized copy with the field cleared.
    assert len(captured) == 2
    assert all(r.caller_authorization is None for r in captured)
    assert legacy.requests == [], "Muse route never touches the legacy executor"
    stored = cycle.real_execute
    assert isinstance(stored, dict), stored
    assert stored.get("ok") is True, stored
    assert stored.get("code") == "MUSE_EXECUTE_OK", stored
    # The sentinel appears in no Muse artifact: prompt, evidence, or error.
    assert len(session_port.params) == 1
    assert sentinel not in session_port.params[0].prompt
    for ref in stored.get("evidence_refs") or ():
        assert sentinel not in Path(ref).read_text(encoding="utf-8")
    assert sentinel not in (cycle.last_error_detail or "")


@pytest.mark.asyncio
async def test_attach_muse_runtime_wiring() -> None:
    from backend.aix_core.runtime.objective.runtime_attach import _build_muse_runtime

    _, _, bound, reason = _build_muse_runtime(SimpleNamespace())
    assert (bound, reason) == (False, "MUSE_SESSION_DISABLED")
    _, _, bound, reason = _build_muse_runtime(SimpleNamespace(MUSE_SESSION_ENABLED=True))
    assert (bound, reason) == (False, "MUSE_SESSION_NO_ACTIVATION_STORE")
    bad_policy = SimpleNamespace(
        MUSE_SESSION_ENABLED=True, SESSION_ACTIVATION_STORE_ENABLED=True,
        DATABASE_URL="postgresql://u:p@h/db", SESSION_ACTIVATION_LEASE_OWNER="o",
        MUSE_SESSION_APPROVAL_MODE="allowAll",
    )
    _, _, bound, reason = _build_muse_runtime(bad_policy)
    assert bound is False
    assert reason.startswith("MUSE_SESSION_POLICY_REFUSED")
    no_binary = SimpleNamespace(
        MUSE_SESSION_ENABLED=True, SESSION_ACTIVATION_STORE_ENABLED=True,
        DATABASE_URL="postgresql://u:p@h/db", SESSION_ACTIVATION_LEASE_OWNER="o",
        MUSE_SESSION_BIN="/nonexistent/muse-xyz",
    )
    _, _, bound, reason = _build_muse_runtime(no_binary)
    assert (bound, reason) == (False, "MUSE_SESSION_NO_BINARY")
    full = SimpleNamespace(
        MUSE_SESSION_ENABLED=True, SESSION_ACTIVATION_STORE_ENABLED=True,
        DATABASE_URL="postgresql://u:p@h/db", SESSION_ACTIVATION_LEASE_OWNER="o",
        MUSE_SESSION_BIN=sys.executable,
    )
    port, lookup, bound, reason = _build_muse_runtime(full)
    assert (bound, reason) == (True, "MUSE_SESSION_BOUND")
    assert port is not None and lookup is not None


# --- Lane-ops #23 production preflight composition ------------------------


def _muse_composition_settings(**overrides: Any) -> Any:
    """Full Muse session settings plus the WorkHub service credentials."""
    values: dict[str, Any] = {
        "MUSE_SESSION_ENABLED": True,
        "SESSION_ACTIVATION_STORE_ENABLED": True,
        "DATABASE_URL": "postgresql://u:p@h/db",
        "SESSION_ACTIVATION_LEASE_OWNER": "o",
        "MUSE_SESSION_BIN": sys.executable,
        "OBJECTIVE_CYCLE_ALLOW_INPROCESS_LEDGER": True,
        "OBJECTIVE_CYCLE_PREFER_POSTGRES": False,
        "WORKHUB_API_URL": "http://workhub:8221",
        "WORKHUB_API_TOKEN": "service-token-abc",
        "WORKHUB_API_TIMEOUT_SECONDS": 12,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_preflight_verifier_composition_uses_settings_values() -> None:
    from backend.aix_core.framework.integration.workhub.client import (
        WorkHubTaskClient,
    )
    from backend.aix_core.runtime.objective.runtime_attach import (
        _build_muse_preflight_verifier,
    )
    from backend.session_factory.muse_preflight import WorkHubPreflightVerifier

    settings = _muse_composition_settings()
    verifier, bound, reason = _build_muse_preflight_verifier(settings)
    assert (bound, reason) == (True, "MUSE_PREFLIGHT_BOUND")
    assert isinstance(verifier, WorkHubPreflightVerifier)
    client = verifier._transport  # noqa: SLF001
    assert isinstance(client, WorkHubTaskClient)
    assert client.base_url == "http://workhub:8221"
    assert client.service_token == "service-token-abc"
    assert client.timeout_seconds == 12
    assert "service-token-abc" not in reason


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"WORKHUB_API_URL": ""}, "MUSE_PREFLIGHT_NO_WORKHUB_URL"),
        ({"WORKHUB_API_URL": "   "}, "MUSE_PREFLIGHT_NO_WORKHUB_URL"),
        ({"WORKHUB_API_TOKEN": ""}, "MUSE_PREFLIGHT_NO_WORKHUB_TOKEN"),
        ({"WORKHUB_API_TOKEN": "   "}, "MUSE_PREFLIGHT_NO_WORKHUB_TOKEN"),
        ({"WORKHUB_API_TIMEOUT_SECONDS": "not-a-number"},
         "MUSE_PREFLIGHT_BAD_WORKHUB_TIMEOUT"),
        ({"WORKHUB_API_TIMEOUT_SECONDS": -5},
         "MUSE_PREFLIGHT_BAD_WORKHUB_TIMEOUT"),
        ({"WORKHUB_API_TIMEOUT_SECONDS": float("nan")},
         "MUSE_PREFLIGHT_BAD_WORKHUB_TIMEOUT"),
    ],
)
def test_preflight_verifier_missing_or_bad_settings_stays_unbound(
    overrides: dict[str, Any], reason: str
) -> None:
    from backend.aix_core.runtime.objective.runtime_attach import (
        _build_muse_preflight_verifier,
    )

    settings = _muse_composition_settings(**overrides)
    verifier, bound, actual = _build_muse_preflight_verifier(settings)
    assert verifier is None
    assert bound is False
    assert actual == reason
    # Stable non-secret reasons: the token appears nowhere log-facing.
    assert "service-token-abc" not in actual


def test_preflight_verifier_construction_failure_stays_unbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.aix_core.runtime.objective.runtime_attach import (
        _build_muse_preflight_verifier,
    )

    def _explode(**kwargs: Any) -> Any:
        raise RuntimeError("client refused")

    monkeypatch.setattr(
        "backend.aix_core.framework.integration.workhub.client.WorkHubTaskClient",
        _explode,
    )
    verifier, bound, reason = _build_muse_preflight_verifier(
        _muse_composition_settings()
    )
    assert verifier is None
    assert bound is False
    assert reason == "MUSE_PREFLIGHT_CLIENT_REFUSED"


@pytest.mark.asyncio
async def test_inprocess_composition_hands_verifier_to_backend() -> None:
    from backend.aix_core.framework.integration.workhub.client import (
        WorkHubTaskClient,
    )
    from backend.aix_core.runtime.objective.runtime_attach import (
        build_objective_cycle_api_backend,
    )
    from backend.session_factory.muse_preflight import WorkHubPreflightVerifier

    backend = await build_objective_cycle_api_backend(_muse_composition_settings())
    assert backend is not None and backend.mode == "inprocess"
    adapter = backend._muse_adapter  # noqa: SLF001
    assert adapter is not None
    verifier = adapter._preflight_verifier  # noqa: SLF001
    assert isinstance(verifier, WorkHubPreflightVerifier)
    client = verifier._transport  # noqa: SLF001
    assert isinstance(client, WorkHubTaskClient)
    assert client.base_url == "http://workhub:8221"
    assert client.service_token == "service-token-abc"
    assert client.timeout_seconds == 12
    assert backend.muse_session_status["preflight_bound"] is True
    assert backend.muse_session_status["preflight_reason"] == "MUSE_PREFLIGHT_BOUND"


@pytest.mark.asyncio
async def test_postgres_composition_hands_verifier_to_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.aix_core.runtime.objective.runtime_attach as attach_mod
    from backend.session_factory.muse_preflight import WorkHubPreflightVerifier

    captured: dict[str, Any] = {}

    class _RecordingBackend:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    async def _noop_schema(conn: Any) -> None:
        return None

    class _Pool:
        def acquire(self) -> Any:
            class _Conn:
                async def __aenter__(self) -> Any:
                    return object()

                async def __aexit__(self, *args: Any) -> bool:
                    return False

            return _Conn()

    async def _fake_pool(*args: Any, **kwargs: Any) -> Any:
        return _Pool()

    monkeypatch.setattr(
        attach_mod, "DurableObjectiveCycleApiBackend", _RecordingBackend
    )
    monkeypatch.setattr(attach_mod, "resolve_test_database_url", lambda: "")
    monkeypatch.setattr(
        attach_mod, "ObjectiveCycleRepository", lambda *a, **k: object()
    )
    monkeypatch.setattr("asyncpg.create_pool", _fake_pool)
    monkeypatch.setattr(
        "backend.aix_core.runtime.verification.gap.ensure_gaps_schema",
        _noop_schema,
    )
    settings = _muse_composition_settings(OBJECTIVE_CYCLE_PREFER_POSTGRES=True)
    backend = await attach_mod.build_objective_cycle_api_backend(settings)
    assert captured.get("mode") == "postgres"
    verifier = captured.get("muse_preflight_verifier")
    assert isinstance(verifier, WorkHubPreflightVerifier)
    assert backend.muse_session_status["preflight_bound"] is True


@pytest.mark.asyncio
async def test_bootstrap_status_distinguishes_execution_binding() -> None:
    from backend.aix_core.runtime.objective.runtime_attach import (
        attach_objective_cycle_runtime,
        build_objective_cycle_api_backend,
    )

    # Complete binding: session transport/store plus credentialed preflight.
    full = await build_objective_cycle_api_backend(_muse_composition_settings())
    app = SimpleNamespace(state=SimpleNamespace())
    status = await attach_objective_cycle_runtime(app, _muse_composition_settings())
    assert full is not None
    assert status["muse_session_bound"] is True
    assert status["muse_preflight_bound"] is True
    assert status["muse_preflight_reason"] == "MUSE_PREFLIGHT_BOUND"
    assert status["muse_execution_bound"] is True
    # The service token appears in no status/log-facing value.
    assert "service-token-abc" not in str(status)
    # Session bound but verifier absent: execution must NOT read ready.
    lean = _muse_composition_settings(WORKHUB_API_TOKEN="")
    status = await attach_objective_cycle_runtime(
        SimpleNamespace(state=SimpleNamespace()), lean
    )
    assert status["muse_session_bound"] is True
    assert status["muse_preflight_bound"] is False
    assert status["muse_preflight_reason"] == "MUSE_PREFLIGHT_NO_WORKHUB_TOKEN"
    assert status["muse_execution_bound"] is False
