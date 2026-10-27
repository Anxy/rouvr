"""Muse execution adapter at the canonical execution-port seam.

``MuseExecuteAdapter`` implements ``ExecutionPort`` for steps whose accepted
model route selects the Muse family (see ``is_muse_model_id``). It performs
one bounded, neutral session turn through an injected ``MuseSessionPort`` and
converts the terminal turn evidence into the existing ``RealExecuteResult`` —
never a synthetic success, and never success from agent text alone: tool
failures observed on the turn and token-budget overruns become explicit
``ok=False`` results that keep the full evidence.

Pre-effect failure order (each refuses before any later effect runs):

1. ``CANCELLED`` — already cancelled before spawn.
2. ``MODEL_ROUTE_NOT_MUSE`` — this adapter must not act for foreign routes.
3. ``MUSE_DEMO_CAPABILITY_UNSUPPORTED`` — content-generation proposals are
   not repository executions and have no turn-evidence shape here.
4. ``WORKHUB_BINDING_MISSING`` — no activation/workhub attempt refs carried.
5. ``WORKHUB_BINDING_LOOKUP_FAILED`` / ``WORKHUB_BINDING_UNKNOWN_ACTIVATION``
   — the maintained owner has no such activation at effect time.
6. ``MUSE_ACTIVATION_TERMINAL`` / ``MUSE_ACTIVATION_STATUS_UNKNOWN`` /
   ``MUSE_ACTIVATION_NOT_ACCEPTED`` — the fresh owner read shows a terminal,
   unknown, or unaccepted activation.
7. ``ACTIVATION_IDENTITY_MISMATCH`` / ``ACTIVATION_SELECTION_MISSING`` /
   ``ACTIVATION_SELECTION_MISMATCH`` / ``ACTIVATION_CANDIDATE_MISSING`` /
   ``ACTIVATION_CANDIDATE_MISMATCH`` / ``ACTIVATION_PLAN_MISSING`` /
   ``ACTIVATION_PLAN_MISMATCH`` / ``ACTIVATION_MODEL_MISSING`` /
   ``ACTIVATION_MODEL_MISMATCH`` / ``ACTIVATION_WORKER_MISSING`` /
   ``ACTIVATION_WORKER_MISMATCH`` / ``ACTIVATION_EXECUTOR_MISMATCH`` — a
   carried owner ref is absent or disagrees with the canonical stored
   record (recomputed workflow identity, exact normalized accepted
   model on both route and threaded id, and exact worker binding
   included). A caller-supplied dictionary is never authority on its own.
8. ``WORKHUB_BINDING_REFUSED`` — the maintained ``require_workhub_binding``
   validator rejects the stored binding (original code preserved in detail).
9. ``WORKHUB_BINDING_*_MISMATCH`` / ``WORKHUB_BINDING_ATTEMPT_FOREIGN``.
10. ``MUSE_PREFLIGHT_UNBOUND`` / ``MUSE_PREFLIGHT_BINDING_*`` /
    ``MUSE_PREFLIGHT_OWNER_UNAVAILABLE`` / ``MUSE_PREFLIGHT_PROOF_INVALID``
    / ``MUSE_PREFLIGHT_TUPLE_MISMATCH`` — the credentialed owner-side
    assignment/lease preflight (lane-ops #23 M3) is unbound, the live
    tuple moved/expired, the owner read failed, the proof is malformed,
    or the proof does not match this exact request/binding. Every call
    performs live owner reads; nothing is cached, so a moved assignment
    cannot ride a previous success. The first gate runs before workspace
    staging, so a refusal leaves no spawn-adjacent effect behind; a
    second fresh gate runs immediately before session activation, after
    all workspace/materialization/isolation preparation, so authority
    that moves during preparation still refuses with zero model turns.
11. ``MUSE_SCOPE_UNPROVEN`` / ``MUSE_REPOSITORY_UNKNOWN`` /
    ``MUSE_REPOSITORY_MISSING`` / ``MUSE_MATERIALIZE_*`` — no exact admitted
    candidate can be materialized through the maintained owners.
12. ``WORKSPACE_ISOLATION_FAILED`` — the step workspace cannot be resolved.

Post-turn explicit failures (``ok=False`` with evidence intact):

- ``EXECUTOR_MODEL_MISMATCH`` — provider-echoed model differs from the bound
  route (same semantics as the durable backend enforcement).
- ``MUSE_TOOL_FAILED`` — a turn item reached a non-completed terminal status.
- ``MUSE_BUDGET_EXCEEDED`` — validated turn usage overruns the step budget.
- ``MUSE_PATH_ESCAPES_WORKSPACE`` / ``MUSE_PATH_OUT_OF_SCOPE`` — worker
  output outside the workspace or the accepted file scope.

Credentials never enter the turn: ``caller_authorization`` is not read here
at all, and only binding *refs* travel. Capability posture
(approval mode / permission profile) comes from an explicit owner-supplied
``MuseCapabilityPolicy`` — never from caller request fields — and defaults
to the safe ``denyUnmatched`` + ``:read-only`` posture; ``allowAll`` and
``:unrestricted`` are refused at wiring time, never silently selected.

Missing owner contracts (named, not invented — each needs its owner slice):

- M1 (activation selection refs): ``activation_id``, ``selection_id``,
  ``candidate_sha``, ``plan_sha256``, ``workhub_attempt_id`` must travel
  ``CanonicalTemporalMuseStarter`` record → ``ObjectiveCycleWorkflowInput``
  / activity ``payload_refs`` → ``_execute_via_backend`` command body →
  ``command()`` / ``dispatch_ready_step`` → ``ExecuteStepRequest``.
  Owners: Temporal contracts, activities, backend command seam. Until then
  the seam carries empty refs and this adapter refuses unbound execution.
- M2 (plan-owned capability profile): no accepted plan/selection record
  carries an approved approval-mode/profile pair, so posture comes from
  deployer settings validated at startup. Binding posture to the accepted
  plan is a plan-owner decision.
- M3 (live lease/assignment revalidation): the injected owner-side
  ``MusePreflightVerifier`` (``backend.session_factory.muse_preflight``)
  re-reads the canonical assignment/lease through the maintained binding
  transport on every effect and compares the complete tuple; credentials
  stay in the verifier. Deployment must bind the credentialed transport;
  until then Muse routes refuse ``MUSE_PREFLIGHT_UNBOUND``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from backend.aix_core.framework.execution.muse_session import (
    APPROVAL_MODES,
    FileChange,
    MuseSessionError,
    MuseSessionPort,
    MuseTurnParams,
    MuseTurnResult,
    is_muse_model_id,
    normalize_model_id,
    write_evidence_file,
)
from backend.aix_core.framework.execution.port import ExecuteStepRequest
from backend.aix_core.intelligence.repository.repository_location import (
    RepositoryLocationError,
    resolve_repository_root,
)
from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteError,
    RealExecuteResult,
    default_workspace_base,
)
from backend.aix_core.runtime.executor.workspace_isolation import (
    WorkspaceIsolationBoundary,
    WorkspaceIsolationError,
    WorkspaceScopeV1,
    resolve_sticky_date_key,
)
from backend.aix_core.runtime.governance.secret_boundary import redact_objective_text
from backend.session_factory.activation import require_workhub_binding
from backend.session_factory.muse_preflight import MusePreflightVerifier
from backend.session_factory.workhub_binding import BindingRefused
from backend.workflows.temporal.objective_cycle_contracts import (
    objective_cycle_workflow_id,
)

MUSE_EXECUTOR_ID = "objective_cycle.muse_execute.v1"

# Upper bound for one turn: the dispatch_step activity contract (headroom
# under its 1800s start_to_close). A step budget may only narrow it.
_MAX_TURN_SECONDS = 1800.0

# Materialization guards: the admitted scope is human-approved, but an
# unbounded copy is still refused explicitly rather than attempted blindly.
_MATERIALIZE_MAX_FILES = 10000
_MATERIALIZE_MAX_BYTES = 256 * 1024 * 1024


class ActivationBindingLookup(Protocol):
    """Effect-time read subset of the maintained activation store.

    Structurally satisfied by the canonical ``ActivationStore`` protocol
    (``backend.session_factory.activation``); only reads are used here, so a
    narrower dependency is declared instead of the full claim-capable store.
    """

    async def get_activation(self, activation_id: str) -> dict[str, Any] | None: ...
    async def find_attempt_activation(
        self, tenant_id: str, project_id: str, workhub_attempt_id: str
    ) -> list[str]: ...
    async def get_repair_count(self, activation_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class MuseCapabilityPolicy:
    """Owner-supplied session capability posture (never caller-supplied).

    Selects a preconfigured host approval mode and a native-catalog
    permission profile; the transport additionally requires the profile to
    be catalog-admitted and available. ``allowAll`` and ``:unrestricted``
    are refused at construction — this path never runs unguarded.
    """

    approval_mode: str = "denyUnmatched"
    permission_profile: str = ":read-only"


def validate_capability_policy(policy: MuseCapabilityPolicy) -> MuseCapabilityPolicy:
    """Validate an owner-supplied capability posture (fail-closed wiring).

    Unknown modes, ``allowAll``, empty profiles, and ``:unrestricted`` are
    refused here so a widened posture can never reach first dispatch.
    """
    if policy.approval_mode not in APPROVAL_MODES:
        raise ValueError(
            f"MUSE_CAPABILITY_POLICY_INVALID: unknown approval mode {policy.approval_mode!r}"
        )
    if policy.approval_mode == "allowAll":
        raise ValueError("MUSE_POLICY_FORBIDDEN: allowAll disables approval gating")
    if not str(policy.permission_profile or "").strip():
        raise ValueError("MUSE_CAPABILITY_POLICY_INVALID: empty permission profile")
    if str(policy.permission_profile).strip() == ":unrestricted":
        raise ValueError("MUSE_POLICY_FORBIDDEN: :unrestricted removes permission gating")
    return policy


@dataclass(frozen=True, slots=True)
class _AuthoritativeBinding:
    binding: dict[str, Any]
    record: dict[str, Any]
    activation_id: str
    current_round: int


def _refuse(code: str, detail: str = "") -> RealExecuteError:
    return RealExecuteError(code, detail)


def _effective_file_scope(request: ExecuteStepRequest) -> tuple[str, ...] | None:
    """Accepted file scope with the legacy P6 freeze semantics.

    A caller-supplied tuple — including empty — is frozen scope. Only an
    omitted (None) scope inherits the accepted step contract target_scope.
    """
    if request.allowed_files is not None:
        return tuple(request.allowed_files)
    step_contract = request.step_contract
    if step_contract is not None:
        return tuple(getattr(step_contract, "target_scope", None) or ())
    return None


def _norm_rel(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def _scope_covers(scope: tuple[str, ...], rel: str) -> bool:
    """Canonical relative coverage: exact entry or strictly beneath a dir entry.

    No suffix guessing in either direction: a worker path counts iff it
    equals an admitted entry or sits under one (``entry/`` prefix). Entries
    that are absolute, empty, or traversal attempts cover nothing.
    """
    target = _norm_rel(rel)
    if not target or target.startswith("/") or target in (".", ".."):
        return False
    parts = target.split("/")
    if ".." in parts:
        return False
    for entry in scope:
        admitted = _norm_rel(entry)
        if not admitted or admitted.startswith("/") or ".." in admitted.split("/"):
            continue
        if target == admitted or target.startswith(admitted.rstrip("/") + "/"):
            return True
    return False


class MuseExecuteAdapter:
    """Execute one accepted Muse step as a single bounded session turn."""

    def __init__(
        self,
        *,
        session_port: MuseSessionPort,
        binding_lookup: ActivationBindingLookup,
        preflight_verifier: MusePreflightVerifier | None = None,
        capability_policy: MuseCapabilityPolicy | None = None,
        executor_id: str = MUSE_EXECUTOR_ID,
        default_turn_timeout_seconds: float = 300.0,
    ) -> None:
        if session_port is None or binding_lookup is None:
            raise ValueError("MUSE_ADAPTER_DEPS_MISSING")
        if preflight_verifier is not None and not callable(
            getattr(preflight_verifier, "verify_assignment_lease", None)
        ):
            raise ValueError("MUSE_PREFLIGHT_VERIFIER_INVALID")
        self._session_port = session_port
        self._binding_lookup = binding_lookup
        # Lane-ops #23 M3: credentialed owner-side assignment/lease
        # preflight, holding the WorkHub transport. The adapter sends it
        # the store-fresh record binding and binds the returned non-secret
        # proof to the exact request; credentials never enter this module.
        # Unbound means no live lease proof is possible, so a Muse route
        # refuses rather than inferring entitlement from stored binding
        # alone; deployment binds the credentialed transport.
        self._preflight_verifier = preflight_verifier
        self._capability_policy = validate_capability_policy(
            capability_policy or MuseCapabilityPolicy()
        )
        self._executor_id = executor_id or MUSE_EXECUTOR_ID
        self._default_turn_timeout_seconds = min(
            max(float(default_turn_timeout_seconds or 0) or 300.0, 1.0),
            _MAX_TURN_SECONDS,
        )

    async def execute_step(self, request: ExecuteStepRequest) -> RealExecuteResult:
        if request.is_cancelled is not None and request.is_cancelled():
            raise _refuse("CANCELLED", "cancelled before Muse spawn")
        model_id = str(request.model_override or "").strip()
        if not is_muse_model_id(model_id):
            raise _refuse(
                "MODEL_ROUTE_NOT_MUSE",
                "Muse adapter requires the accepted route to select a Muse model",
            )
        if request.demo_capability:
            raise _refuse(
                "MUSE_DEMO_CAPABILITY_UNSUPPORTED",
                "content-generation proposals have no Muse turn-evidence shape",
            )
        activation_id = str(request.activation_id or "").strip()
        workhub_attempt_id = str(request.workhub_attempt_id or "").strip()
        if not activation_id or not workhub_attempt_id:
            raise _refuse(
                "WORKHUB_BINDING_MISSING",
                "Muse execution needs activation_id + workhub_attempt_id refs",
            )
        authoritative = await self._resolve_binding(
            request, activation_id=activation_id, workhub_attempt_id=workhub_attempt_id
        )
        # Lane-ops #23 M3 (first gate): live assignment/lease preflight
        # after the store-fresh binding resolution and before any
        # spawn-adjacent effect (workspace staging included), so
        # unauthorized work cannot stage. Every call performs live owner
        # reads; nothing is cached, so a moved assignment cannot ride a
        # previous success. A second fresh verification runs immediately
        # before session activation below: authority can move DURING
        # workspace preparation, and the pre-workspace check alone cannot
        # cover that window.
        await self._verify_assignment_lease(request, authoritative)
        workspace, boundary, scope = self._resolve_workspace(request)
        try:
            boundary.assert_no_foreign_symlinks(scope)
        except WorkspaceIsolationError as exc:
            raise _refuse("MUSE_WORKSPACE_TAINTED", str(exc)) from exc
        self._assert_no_symlinks(workspace)
        materialized = self._materialize_candidate(request, boundary, scope, workspace)
        try:
            boundary.assert_no_foreign_symlinks(scope)
        except WorkspaceIsolationError as exc:
            raise _refuse("MUSE_WORKSPACE_TAINTED", str(exc)) from exc
        evidence_dir = self._prepare_evidence_dir(request, boundary)
        prompt = self._build_prompt(request, authoritative=authoritative)
        timeout, token_budget = self._turn_limits(request)
        params = MuseTurnParams(
            model_id=model_id,
            workspace=workspace,
            prompt=prompt,
            evidence_dir=evidence_dir,
            timeout_seconds=timeout,
            approval_mode=self._capability_policy.approval_mode,
            permission_profile=self._capability_policy.permission_profile,
            token_budget=token_budget,
            is_cancelled=request.is_cancelled,
        )
        # Lane-ops #23 M3 (second gate): fresh owner verification
        # immediately before session activation, after ALL
        # workspace/materialization/isolation preparation. No proof cache
        # or reuse — this performs a full new set of live owner reads. If
        # assignment/lease/expiry moved during preparation, this refuses
        # before session_port.run_turn, so zero model turns run.
        await self._verify_assignment_lease(request, authoritative)
        try:
            turn = await self._session_port.run_turn(params)
        except MuseSessionError as exc:
            raise _refuse(exc.code, exc.detail) from exc
        try:
            boundary.assert_no_foreign_symlinks(scope)
        except WorkspaceIsolationError as exc:
            raise _refuse("MUSE_WORKSPACE_TAINTED", str(exc)) from exc
        return self._to_result(
            request, turn, workspace, model_id, timeout, token_budget,
            authoritative=authoritative, materialized=materialized,
            evidence_dir=evidence_dir, prompt=prompt,
        )

    async def _resolve_binding(
        self, request: ExecuteStepRequest, *, activation_id: str, workhub_attempt_id: str
    ) -> _AuthoritativeBinding:
        """Fresh owner verification at effect time, then exact comparison."""
        try:
            record = await self._binding_lookup.get_activation(activation_id)
        except Exception as exc:  # noqa: BLE001 - transport failure refuses closed
            raise _refuse("WORKHUB_BINDING_LOOKUP_FAILED", str(exc)) from exc
        if not isinstance(record, dict):
            raise _refuse(
                "WORKHUB_BINDING_UNKNOWN_ACTIVATION",
                f"no activation record for {activation_id}",
            )
        status = str(record.get("status") or "")
        if status in ("SUCCEEDED", "FAILED"):
            raise _refuse(
                "MUSE_ACTIVATION_TERMINAL",
                f"activation {activation_id} already holds terminal status {status}",
            )
        if status not in ("STARTING", "STARTED"):
            raise _refuse(
                "MUSE_ACTIVATION_STATUS_UNKNOWN",
                f"activation {activation_id} status {status!r} is not dispatchable",
            )
        if record.get("accepted") is not True:
            raise _refuse(
                "MUSE_ACTIVATION_NOT_ACCEPTED",
                f"activation {activation_id} was never accepted",
            )
        # Exact identity: the stored record must name this tenant/objective/
        # cycle/selection, and its workflow identity must recompute through
        # the maintained workflow-id function.
        for key, carried in (
            ("tenant_id", request.tenant_id),
            ("objective_id", request.objective_id),
            ("cycle_id", request.cycle_id),
        ):
            if str(record.get(key) or "") != str(carried or ""):
                raise _refuse(
                    "ACTIVATION_IDENTITY_MISMATCH",
                    f"activation {key} disagrees with the dispatch request",
                )
        # Lane-ops #23 M1: every carried selection ref is required
        # non-empty for a Muse route and must equal the authoritative
        # record exactly. A caller-supplied dictionary is never authority
        # on its own; absence or substitution refuses before spawn.
        for key, carried, missing_code, mismatch_code in (
            ("selection_id", request.selection_id,
             "ACTIVATION_SELECTION_MISSING", "ACTIVATION_SELECTION_MISMATCH"),
            ("candidate_sha", request.candidate_sha,
             "ACTIVATION_CANDIDATE_MISSING", "ACTIVATION_CANDIDATE_MISMATCH"),
            ("plan_sha256", request.plan_sha256,
             "ACTIVATION_PLAN_MISSING", "ACTIVATION_PLAN_MISMATCH"),
        ):
            if carried is None or not str(carried).strip():
                raise _refuse(
                    missing_code,
                    f"dispatch carried no {key} for activation {activation_id}",
                )
            if str(record.get(key) or "") != str(carried).strip():
                raise _refuse(
                    mismatch_code,
                    f"activation {key} disagrees with the carried selection ref",
                )
        expected_workflow = objective_cycle_workflow_id(
            tenant_id=request.tenant_id,
            objective_id=request.objective_id,
            cycle_id=request.cycle_id,
        )
        if str(record.get("workflow_id") or "") != expected_workflow:
            raise _refuse(
                "ACTIVATION_IDENTITY_MISMATCH",
                "stored workflow identity does not recompute for this cycle",
            )
        # Exact accepted-model binding: the dispatch route model and the
        # starter-threaded accepted model id must both equal the stored
        # selection model after shared normalization. Family-only matching
        # would let a different Muse model id pass; provider echo stays a
        # second post-turn check. Legacy family-only records ("Muse")
        # fail closed here until the selection owner records exact ids.
        record_model = normalize_model_id(record.get("model"))
        if not record_model:
            raise _refuse(
                "ACTIVATION_MODEL_MISMATCH",
                "stored selection carries no usable model identity",
            )
        carried_accepted = normalize_model_id(request.accepted_model_id)
        if not carried_accepted:
            raise _refuse(
                "ACTIVATION_MODEL_MISSING",
                f"dispatch carried no accepted model id for activation {activation_id}",
            )
        if carried_accepted != record_model:
            raise _refuse(
                "ACTIVATION_MODEL_MISMATCH",
                "carried accepted model differs from the stored selection model",
            )
        if normalize_model_id(request.model_override) != record_model:
            raise _refuse(
                "ACTIVATION_MODEL_MISMATCH",
                "dispatch route model differs from the stored selection model",
            )
        if str(record.get("executor") or "") != "muse-lane":
            raise _refuse(
                "ACTIVATION_EXECUTOR_MISMATCH",
                "stored selection executor is not the muse lane",
            )
        # Owner-threaded actor/worker binding: the carried worker id must
        # be non-empty and equal the authoritative record exactly. The
        # dispatch attribution in ``request.actor_id`` is intentionally
        # not equated with the worker binding here (approver attribution
        # versus accepted worker are distinct principals); the binding
        # proof is carried-worker versus stored-worker.
        carried_worker = str(request.worker_id or "").strip()
        if not carried_worker:
            raise _refuse(
                "ACTIVATION_WORKER_MISSING",
                f"dispatch carried no worker binding for activation {activation_id}",
            )
        if str(record.get("worker_id") or "").strip() != carried_worker:
            raise _refuse(
                "ACTIVATION_WORKER_MISMATCH",
                "carried worker binding differs from the stored activation worker",
            )
        stored = record.get("workhub_binding")
        if not isinstance(stored, dict) or not stored:
            raise _refuse(
                "WORKHUB_BINDING_UNKNOWN_ACTIVATION",
                "activation record carries no workhub binding",
            )
        try:
            binding = require_workhub_binding(stored, tenant_id=request.tenant_id)
        except ValueError as exc:
            raise _refuse("WORKHUB_BINDING_REFUSED", str(exc)) from exc
        if str(binding.get("workhub_attempt_id") or "") != workhub_attempt_id:
            raise _refuse(
                "WORKHUB_BINDING_ATTEMPT_MISMATCH",
                "carried workhub attempt disagrees with the canonical binding",
            )
        if request.project_id and str(binding.get("project_id") or "") != request.project_id:
            raise _refuse(
                "WORKHUB_BINDING_PROJECT_MISMATCH",
                "carried project disagrees with the canonical binding",
            )
        if request.workhub_task_id and str(binding.get("task_id") or "") != request.workhub_task_id:
            raise _refuse(
                "WORKHUB_BINDING_TASK_MISMATCH",
                "carried task disagrees with the canonical binding",
            )
        try:
            owners = await self._binding_lookup.find_attempt_activation(
                str(binding.get("tenant_id") or ""),
                str(binding.get("project_id") or ""),
                str(binding.get("workhub_attempt_id") or ""),
            )
            current_round = await self._binding_lookup.get_repair_count(activation_id)
        except Exception as exc:  # noqa: BLE001 - transport failure refuses closed
            raise _refuse("WORKHUB_BINDING_LOOKUP_FAILED", str(exc)) from exc
        if activation_id not in list(owners or []):
            raise _refuse(
                "WORKHUB_BINDING_ATTEMPT_FOREIGN",
                "activation does not own the bound workhub attempt",
            )
        if isinstance(current_round, bool) or not isinstance(current_round, int) or current_round < 0:
            raise _refuse(
                "WORKHUB_BINDING_LOOKUP_FAILED",
                "owner returned no usable current repair round",
            )
        return _AuthoritativeBinding(
            binding=binding, record=dict(record),
            activation_id=activation_id, current_round=current_round,
        )

    async def _verify_assignment_lease(
        self, request: ExecuteStepRequest, authoritative: _AuthoritativeBinding
    ) -> None:
        """Live WorkHub assignment/lease preflight (lane-ops #23 M3).

        Backs BOTH gates: the pre-workspace check and the fresh check
        immediately before session activation. The store-fresh record
        binding — never caller input — goes to the owner verifier, which
        performs live reads on every call (nothing cached, no proof
        reuse); the returned non-secret proof is then bound to this exact
        request. Credentials/JWTs and raw WorkHub responses never enter
        this module.
        """
        verifier = self._preflight_verifier
        if verifier is None:
            raise _refuse(
                "MUSE_PREFLIGHT_UNBOUND",
                "no owner assignment/lease verifier bound; refusing Muse "
                "spawn rather than inferring entitlement from stored "
                "binding alone",
            )
        binding = authoritative.binding
        try:
            proof = await verifier.verify_assignment_lease(dict(binding))
        except BindingRefused as exc:
            raise _refuse(
                f"MUSE_PREFLIGHT_{exc}",
                f"owner assignment/lease preflight refused: {exc}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - owner read failure refuses closed
            raise _refuse(
                "MUSE_PREFLIGHT_OWNER_UNAVAILABLE",
                "owner assignment/lease read unavailable: "
                f"{type(exc).__name__}",
            ) from exc
        if not isinstance(proof, dict) or not proof:
            raise _refuse(
                "MUSE_PREFLIGHT_PROOF_INVALID",
                "owner preflight returned no usable proof",
            )
        try:
            proof_epoch = int(proof.get("lease_epoch"))
            record_epoch = int(binding.get("lease_epoch"))
        except (TypeError, ValueError):
            raise _refuse(
                "MUSE_PREFLIGHT_PROOF_INVALID",
                "owner preflight proof carries no usable lease epoch",
            ) from None
        bound = (
            str(proof.get("workhub_attempt_id") or "")
            == str(request.workhub_attempt_id or "").strip()
            and str(proof.get("assignment_id") or "")
            == str(binding.get("assignment_id") or "")
            and str(proof.get("lease_holder") or "")
            == str(binding.get("lease_holder") or "")
            and str(proof.get("executor_sub") or "")
            == str(binding.get("executor_sub") or "")
            and proof_epoch == record_epoch
        )
        if not bound:
            raise _refuse(
                "MUSE_PREFLIGHT_TUPLE_MISMATCH",
                "owner preflight proof does not match this exact "
                "request/binding tuple",
            )

    def _resolve_workspace(
        self, request: ExecuteStepRequest
    ) -> tuple[Path, WorkspaceIsolationBoundary, WorkspaceScopeV1]:
        """Resolve the step workspace through the maintained boundary owner."""
        base = request.workspace_base
        if base is None and request.settings is not None:
            configured = str(
                getattr(request.settings, "OBJECTIVE_CYCLE_WORKSPACE_BASE", "") or ""
            ).strip()
            if configured:
                base = Path(configured)
        if base is None:
            base = default_workspace_base()
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            boundary = WorkspaceIsolationBoundary(base)
            session = str(request.session_id or "").strip()
            resolved_session = session if session.lower().startswith("disc_") else ""
            sticky_day = resolve_sticky_date_key(
                boundary.base_root,
                tenant_id=request.tenant_id,
                cycle_id=request.cycle_id,
                session_id=resolved_session,
            )
            scope = WorkspaceScopeV1(
                tenant_id=request.tenant_id,
                cycle_id=request.cycle_id,
                step_id=str(request.workspace_step_id or "step-1").strip() or "step-1",
                attempt_id=str(request.workspace_attempt_id or "attempt-1").strip()
                or "attempt-1",
                date_key=sticky_day,
                session_id=resolved_session,
            )
            return boundary.create_workspace(scope), boundary, scope
        except WorkspaceIsolationError as exc:
            raise _refuse("WORKSPACE_ISOLATION_FAILED", str(exc)) from exc

    def _materialize_candidate(
        self,
        request: ExecuteStepRequest,
        boundary: WorkspaceIsolationBoundary,
        scope: WorkspaceScopeV1,
        workspace: Path,
    ) -> list[str]:
        """Copy exactly the admitted scope from the repo root into the workspace.

        Sources resolve through the maintained repository registry; every
        destination resolves through the maintained workspace boundary. No
        symlinks are reproduced as links (resolved content is copied when it
        stays inside the repo); anything unsafe, missing, or oversized
        refuses explicitly instead of being skipped silently.
        """
        admitted = _effective_file_scope(request)
        if admitted is None:
            raise _refuse(
                "MUSE_SCOPE_UNPROVEN",
                "no admitted file scope: refusing blind full-tree materialization",
            )
        if not admitted:
            return []  # frozen empty scope: read-only turn, nothing to stage
        try:
            repo_root = resolve_repository_root(
                request.repository_id, settings=request.settings
            )
        except RepositoryLocationError as exc:
            raise _refuse(exc.code, exc.detail) from exc
        staged: list[str] = []
        total_bytes = 0
        for entry in admitted:
            rel = _norm_rel(entry)
            if not rel or rel.startswith("/") or rel in (".", "..") or ".." in rel.split("/"):
                raise _refuse("MUSE_SCOPE_INVALID", f"admitted entry is not relative: {entry!r}")
            sources = self._expand_source(repo_root, rel)
            for src, target_rel in sources:
                if len(staged) >= _MATERIALIZE_MAX_FILES:
                    raise _refuse(
                        "MUSE_MATERIALIZE_TOO_LARGE",
                        f"admitted scope exceeds {_MATERIALIZE_MAX_FILES} files",
                    )
                try:
                    size = src.stat().st_size
                except OSError as exc:
                    raise _refuse("MUSE_MATERIALIZE_FAILED", f"cannot stat {target_rel}: {exc}") from exc
                total_bytes += size
                if total_bytes > _MATERIALIZE_MAX_BYTES:
                    raise _refuse(
                        "MUSE_MATERIALIZE_TOO_LARGE",
                        f"admitted scope exceeds {_MATERIALIZE_MAX_BYTES} bytes",
                    )
                try:
                    dest = boundary.resolve_target(scope, target_rel)
                except WorkspaceIsolationError as exc:
                    raise _refuse("MUSE_MATERIALIZE_FAILED", str(exc)) from exc
                self._assert_no_symlink_on_path(workspace, target_rel)
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with open(src, "rb") as handle_in, open(dest, "wb") as handle_out:
                        while True:
                            chunk = handle_in.read(65536)
                            if not chunk:
                                break
                            handle_out.write(chunk)
                except OSError as exc:
                    raise _refuse("MUSE_MATERIALIZE_FAILED", f"cannot stage {target_rel}: {exc}") from exc
                staged.append(target_rel)
        staged.sort()
        return staged

    def _expand_source(self, repo_root: Path, rel: str) -> list[tuple[Path, str]]:
        """Resolve one admitted entry to (source file, workspace rel) pairs.

        Symlinks resolve to their target content only when the target stays
        inside the repository; anything else refuses instead of leaking
        worker effects outside the workspace later.
        """
        candidate = repo_root / rel
        try:
            is_link = candidate.is_symlink()
            exists = candidate.exists()
        except OSError as exc:
            raise _refuse("MUSE_MATERIALIZE_FAILED", f"cannot inspect {rel}: {exc}") from exc
        if is_link:
            candidate = self._resolve_link_source(repo_root, candidate, rel)
        if not exists and not is_link:
            raise _refuse("MUSE_MATERIALIZE_MISSING", f"admitted path absent from repo: {rel}")
        if candidate.is_dir():
            pairs: list[tuple[Path, str]] = []
            self._stage_tree(repo_root, candidate, rel, pairs)
            pairs.sort(key=lambda pair: pair[1])
            return pairs
        if candidate.is_file():
            return [(candidate, rel)]
        raise _refuse("MUSE_MATERIALIZE_UNSUPPORTED", f"scoped non-file: {rel}")

    def _resolve_link_source(self, repo_root: Path, link: Path, display_rel: str) -> Path:
        """Resolve a scoped symlink to in-repo content, never reproducing links."""
        try:
            resolved = link.resolve(strict=True)
        except OSError as exc:
            raise _refuse(
                "MUSE_MATERIALIZE_UNSAFE_SOURCE", f"dead symlink in scope: {display_rel}"
            ) from exc
        try:
            resolved.relative_to(repo_root)
        except ValueError as exc:
            raise _refuse(
                "MUSE_MATERIALIZE_UNSAFE_SOURCE",
                f"scoped symlink escapes the repository: {display_rel}",
            ) from exc
        return resolved

    def _stage_tree(
        self, repo_root: Path, tree: Path, display_rel: str, pairs: list[tuple[Path, str]]
    ) -> None:
        """Stage every file under ``tree``; symlinks resolve to in-repo content.

        Display relpaths mirror the admitted layout (through link names),
        so scope checks keep working on worker-visible paths while all bytes
        come from inside the repository.
        """
        for dirpath, dirnames, filenames in os.walk(tree, followlinks=False):
            dirnames.sort()
            filenames.sort()
            here = Path(dirpath)
            try:
                sub = here.relative_to(tree).as_posix()
            except ValueError as exc:
                raise _refuse("MUSE_MATERIALIZE_FAILED", f"escape during staging: {here}") from exc
            prefix = display_rel if sub == "." else f"{display_rel}/{sub}"
            for name in dirnames:
                full = here / name
                if not full.is_symlink():
                    continue
                resolved = self._resolve_link_source(repo_root, full, f"{prefix}/{name}")
                if resolved.is_dir():
                    self._stage_tree(repo_root, resolved, f"{prefix}/{name}", pairs)
                elif resolved.is_file():
                    pairs.append((resolved, f"{prefix}/{name}"))
                else:
                    raise _refuse(
                        "MUSE_MATERIALIZE_UNSUPPORTED", f"scoped non-file: {prefix}/{name}"
                    )
            for name in filenames:
                full = here / name
                if full.is_symlink():
                    resolved = self._resolve_link_source(
                        repo_root, full, f"{prefix}/{name}"
                    )
                    if not resolved.is_file():
                        raise _refuse(
                            "MUSE_MATERIALIZE_UNSUPPORTED",
                            f"scoped non-file: {prefix}/{name}",
                        )
                    pairs.append((resolved, f"{prefix}/{name}"))
                elif full.is_file():
                    pairs.append((full, f"{prefix}/{name}"))
                else:
                    raise _refuse(
                        "MUSE_MATERIALIZE_UNSUPPORTED", f"scoped non-file: {prefix}/{name}"
                    )

    @staticmethod
    def _assert_no_symlinks(workspace: Path) -> None:
        """Refuse when the pre-turn workspace already contains any symlink.

        A retried attempt reuses its workspace; a worker-planted link from
        the previous turn must never silently redirect materialization or
        observation. Fresh workspaces contain none, so this never misfires.
        """
        for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
            for name in (*dirnames, *filenames):
                if Path(dirpath, name).is_symlink():
                    raise _refuse(
                        "MUSE_WORKSPACE_TAINTED",
                        f"pre-turn workspace contains a symlink: {name}",
                    )

    @staticmethod
    def _assert_no_symlink_on_path(workspace: Path, rel: str) -> None:
        """Refuse when any destination component is already a symlink."""
        current = workspace
        for part in _norm_rel(rel).split("/")[:-1]:
            current = current / part
            try:
                if current.is_symlink():
                    raise _refuse(
                        "MUSE_WORKSPACE_TAINTED",
                        f"destination component is a symlink: {current}",
                    )
            except OSError as exc:
                raise _refuse("MUSE_MATERIALIZE_FAILED", f"cannot inspect {current}: {exc}") from exc

    def _prepare_evidence_dir(
        self, request: ExecuteStepRequest, boundary: WorkspaceIsolationBoundary
    ) -> Path:
        """Securely create the supervisor evidence dir level by level.

        The directory lives beside (never inside) the step workspaces under
        the boundary base root. Each component is created with mode ``0700``
        (a permissive umask can only strip bits, never add group/world
        write) and opened relative to a held parent descriptor
        (``mkdirat``/``openat`` with ``O_NOFOLLOW | O_DIRECTORY``), so no
        path re-resolution can redirect creation and a planted symlink or
        non-directory fails closed as ``MUSE_EVIDENCE_REDIRECTED``.

        A component this call newly created is owned by us: when a host
        default ACL leaves group/world write bits set despite mode
        ``0700``, the bits are cleared on the held descriptor before the
        model turn. A pre-existing component is never chmodded, adopted,
        deleted, or replaced — an unowned, shared-mode, symlinked, or
        non-directory ancestor fails closed here, before any turn, under
        the same codes the descriptor writer enforces downstream.
        """
        parts = [
            ".aix-muse-evidence",
            str(request.tenant_id or "").strip(),
            str(request.cycle_id or "").strip(),
            str(request.workspace_step_id or "step-1").strip() or "step-1",
            str(request.workspace_attempt_id or "attempt-1").strip() or "attempt-1",
        ]
        for part in parts:
            if (
                not part or part in (".", "..") or "/" in part or "\\" in part or "\x00" in part
            ):
                raise _refuse("MUSE_EVIDENCE_WRITE_FAILED", f"unsafe evidence component: {part!r}")
        current = boundary.base_root
        try:
            parent_fd = os.open(
                current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except OSError as exc:
            raise _refuse(
                "MUSE_EVIDENCE_WRITE_FAILED", f"cannot open evidence base: {exc}"
            ) from exc
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent_fd)
                    created = True
                except FileExistsError:
                    created = False
                except OSError as exc:
                    raise _refuse(
                        "MUSE_EVIDENCE_WRITE_FAILED",
                        f"cannot create evidence component {part!r}: {exc}",
                    ) from exc
                try:
                    child_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent_fd,
                    )
                except OSError as exc:
                    raise _refuse(
                        "MUSE_EVIDENCE_REDIRECTED",
                        f"evidence component is not a real directory: "
                        f"{current / part}: {exc}",
                    ) from exc
                try:
                    st = os.fstat(child_fd)
                except OSError as exc:
                    os.close(child_fd)
                    raise _refuse(
                        "MUSE_EVIDENCE_WRITE_FAILED",
                        f"cannot stat evidence component {part!r}: {exc}",
                    ) from exc
                if st.st_uid != os.geteuid():
                    os.close(child_fd)
                    raise _refuse(
                        "MUSE_EVIDENCE_DIR_UNOWNED",
                        f"evidence dir not owned by the current user: {current / part}",
                    )
                if st.st_mode & 0o022:
                    if not created:
                        os.close(child_fd)
                        raise _refuse(
                            "MUSE_EVIDENCE_DIR_SHARED",
                            f"evidence dir is group/world writable: {current / part}",
                        )
                    try:
                        os.fchmod(child_fd, 0o700)
                        st = os.fstat(child_fd)
                    except OSError as exc:
                        os.close(child_fd)
                        raise _refuse(
                            "MUSE_EVIDENCE_WRITE_FAILED",
                            f"cannot secure new evidence dir {part!r}: {exc}",
                        ) from exc
                    if st.st_mode & 0o022:
                        os.close(child_fd)
                        raise _refuse(
                            "MUSE_EVIDENCE_DIR_SHARED",
                            f"new evidence dir remains shared after secure create: "
                            f"{current / part}",
                        )
                os.close(parent_fd)
                parent_fd = child_fd
                current = current / part
        finally:
            os.close(parent_fd)
        return current

    def _turn_limits(self, request: ExecuteStepRequest) -> tuple[float, int | None]:
        timeout = self._default_turn_timeout_seconds
        token_budget: int | None = None
        step_budget = getattr(request.step_contract, "step_budget", None)
        configured_time = getattr(step_budget, "time_seconds", None)
        if isinstance(configured_time, (int, float)) and not isinstance(configured_time, bool):
            if configured_time > 0:
                timeout = min(float(configured_time), _MAX_TURN_SECONDS)
        configured_tokens = getattr(step_budget, "tokens", None)
        if isinstance(configured_tokens, int) and not isinstance(configured_tokens, bool):
            if configured_tokens > 0:
                token_budget = configured_tokens
        return max(timeout, 1.0), token_budget

    def _build_prompt(
        self, request: ExecuteStepRequest, *, authoritative: _AuthoritativeBinding
    ) -> str:
        objective, _ = redact_objective_text(str(request.objective or ""))
        lines = [f"Objective: {objective}"]
        step_contract = request.step_contract
        if step_contract is not None:
            purpose = str(getattr(step_contract, "purpose", "") or "").strip()
            if purpose:
                lines.append(f"Step: {purpose}")
            criteria = tuple(getattr(step_contract, "acceptance_criteria", None) or ())
            if criteria:
                lines.append("Acceptance criteria: " + "; ".join(str(c) for c in criteria))
            scope = tuple(getattr(step_contract, "target_scope", None) or ())
            if scope:
                lines.append("Target scope: " + ", ".join(str(s) for s in scope))
        if request.allowed_files is not None:
            if request.allowed_files:
                lines.append("Allowed files: " + ", ".join(str(p) for p in request.allowed_files))
            else:
                lines.append("Allowed files: none — read-only exploration, change no files")
        directive = request.repair_directive
        if directive is not None:
            required = str(getattr(directive, "required_correction", "") or "").strip()
            if required:
                lines.append(f"Required correction: {required}")
            for label in ("remaining_requirements", "constraints", "verification_requirements"):
                values = tuple(getattr(directive, label, None) or ())
                if values:
                    lines.append(
                        f"{label.replace('_', ' ').title()}: " + "; ".join(str(v) for v in values)
                    )
        lines.append(
            "Attempt: "
            f"{request.workspace_step_id or 'step-1'}/{request.workspace_attempt_id or 'attempt-1'} "
            f"(activation {authoritative.activation_id}, "
            f"workhub attempt {authoritative.binding.get('workhub_attempt_id')}, "
            f"authorized round {authoritative.current_round})"
        )
        lines.append(
            f"Capability: approval {self._capability_policy.approval_mode}, "
            f"profile {self._capability_policy.permission_profile}"
        )
        lines.append(
            "Rules: work only inside the current directory; access no network, "
            "no credentials; report changed files relative to the workspace root."
        )
        return "\n".join(lines)

    def _to_result(
        self,
        request: ExecuteStepRequest,
        turn: MuseTurnResult,
        workspace: Path,
        model_id: str,
        timeout: float,
        token_budget: int | None,
        *,
        authoritative: _AuthoritativeBinding,
        materialized: list[str],
        evidence_dir: Path,
        prompt: str,
    ) -> RealExecuteResult:
        if not turn.session_id or not turn.turn_id:
            raise _refuse(
                "MUSE_SESSION_IDENTITY_MISMATCH",
                "terminal turn carries no session/turn identity",
            )
        echo = str(turn.model_echo or "").strip()
        if echo and normalize_model_id(echo) != normalize_model_id(model_id):
            raise RealExecuteError(
                "EXECUTOR_MODEL_MISMATCH",
                f"expected {model_id}, provider reported {echo}",
                events=[self._evidence_event(turn, "muse.model_mismatch")],
            )
        kept = self._check_changes(workspace, turn.changes, request, turn)
        usage_total = self._usage_total(turn.token_usage)
        budget_exceeded = (
            token_budget is not None
            and usage_total is not None
            and usage_total > token_budget
        )
        summary = {
            "executor": self._executor_id,
            "session_id": turn.session_id,
            "turn_id": turn.turn_id,
            "model_id": model_id,
            "model_echo": echo,
            "capability": {
                "approval_mode": self._capability_policy.approval_mode,
                "permission_profile": self._capability_policy.permission_profile,
            },
            "activation_id": authoritative.activation_id,
            "workhub_attempt_id": str(request.workhub_attempt_id or ""),
            "authorized_round": authoritative.current_round,
            "prompt": prompt,
            "agent_text": turn.agent_text,
            "tool_failures": [
                {"item_id": item.item_id, "kind": item.kind, "status": item.status}
                for item in turn.tool_failures
            ],
            "approval_roundtrips": turn.approval_roundtrips,
            "input_echo_observed": turn.input_echo_observed,
            "token_budget": token_budget,
            "token_usage": turn.token_usage,
            "usage_total": usage_total,
            "materialized": materialized,
            "changed_files": [
                {
                    "path": item.path, "change": item.change, "kind": item.kind,
                    "mode": oct(item.mode) if item.mode is not None else None,
                    "sha256": item.sha256, "size": item.size,
                }
                for item in kept
            ],
        }
        summary_path = write_evidence_file(
            evidence_dir,
            f"summary-{turn.turn_id}",
            json.dumps(summary, indent=2, sort_keys=True).encode("utf-8"),
        )
        evidence_refs = [str(path) for path in turn.evidence_files] + [str(summary_path)]
        token_usage = dict(turn.token_usage or {})
        token_usage.setdefault("ok", True)
        token_usage.setdefault("provider", "meta")
        # No echo fallback: an absent provider echo stays absent so the
        # backend enforcement skips instead of passing vacuously.
        token_usage.setdefault("model", echo)
        if turn.tool_failures:
            failures = "; ".join(
                f"{item.item_id}/{item.kind}/{item.status}" for item in turn.tool_failures
            )
            return self._explicit_failure(
                workspace, turn, kept, evidence_refs, token_usage, timeout, token_budget,
                code="MUSE_TOOL_FAILED",
                detail=f"turn items failed: {failures}",
            )
        if budget_exceeded:
            return self._explicit_failure(
                workspace, turn, kept, evidence_refs, token_usage, timeout, token_budget,
                code="MUSE_BUDGET_EXCEEDED",
                detail=f"turn usage {usage_total} exceeds step budget {token_budget}",
            )
        return RealExecuteResult(
            ok=True,
            code="MUSE_EXECUTE_OK",
            workspace=str(workspace),
            evidence_refs=evidence_refs,
            workhub_loaded=False,
            workhub_lifecycle_authority=False,
            detail=(
                f"muse turn {turn.turn_id} completed in session {turn.session_id}; "
                f"{len(kept)} file change(s) observed"
            ),
            artifacts={
                "muse_turn_transcript": str(turn.evidence_files[0])
                if turn.evidence_files
                else "",
                "muse_turn_summary": str(summary_path),
            },
            token_usage=token_usage,
            changed_source_files=[
                {
                    "path": item.path, "change": item.change,
                    "sha256": item.sha256, "size": item.size,
                }
                for item in kept
            ],
            executor=self._executor_id,
            execution_status="COMPLETED",
            objective_status="INCOMPLETE",
            events=[
                {
                    "event": "muse.turn_completed",
                    "payload_refs": {
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "model_id": model_id,
                        "changed_files": len(kept),
                    },
                }
            ],
            budget_summary={
                "turn_timeout_seconds": timeout,
                "step_token_budget": token_budget,
                "turn_usage_total": usage_total,
                "transport": "muse-session-turn-v1",
            },
        )

    def _explicit_failure(
        self,
        workspace: Path,
        turn: MuseTurnResult,
        kept: list[FileChange],
        evidence_refs: list[str],
        token_usage: dict[str, Any],
        timeout: float,
        token_budget: int | None,
        *,
        code: str,
        detail: str,
    ) -> RealExecuteResult:
        """Post-effect explicit failure: ok=False with the evidence intact.

        The turn ran and left real proof; success is refused, but the proof
        stays reviewable instead of being converted into an exception.
        """
        return RealExecuteResult(
            ok=False,
            code=code,
            workspace=str(workspace),
            evidence_refs=evidence_refs,
            workhub_loaded=False,
            workhub_lifecycle_authority=False,
            detail=f"{code}: {detail}",
            artifacts={
                "muse_turn_transcript": str(turn.evidence_files[0])
                if turn.evidence_files
                else "",
            },
            token_usage=token_usage,
            changed_source_files=[
                {
                    "path": item.path, "change": item.change,
                    "sha256": item.sha256, "size": item.size,
                }
                for item in kept
            ],
            executor=self._executor_id,
            execution_status="COMPLETED",
            objective_status="INCOMPLETE",
            reason_code=code,
            events=[
                {
                    "event": "muse.turn_failed",
                    "payload_refs": {
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "reason_code": code,
                        "changed_files": len(kept),
                    },
                }
            ],
            budget_summary={
                "turn_timeout_seconds": timeout,
                "step_token_budget": token_budget,
                "turn_usage_total": self._usage_total(turn.token_usage),
                "transport": "muse-session-turn-v1",
            },
        )

    @staticmethod
    def _usage_total(usage: dict[str, Any] | None) -> int | None:
        if not isinstance(usage, dict):
            return None
        total = 0
        for key in ("inputTokens", "outputTokens"):
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            total += value
        return total

    def _check_changes(
        self,
        workspace: Path,
        changes: tuple[FileChange, ...],
        request: ExecuteStepRequest,
        turn: MuseTurnResult,
    ) -> list[FileChange]:
        scope = _effective_file_scope(request)
        if scope is None:
            raise RealExecuteError(
                "MUSE_SCOPE_UNPROVEN",
                "no admitted file scope covers the observed worker output",
            )
        workspace_resolved = workspace.resolve(strict=False)
        kept: list[FileChange] = []
        for item in changes:
            rel = _norm_rel(item.path)
            candidate = (workspace / rel).resolve(strict=False)
            try:
                candidate.relative_to(workspace_resolved)
            except ValueError:
                raise RealExecuteError(
                    "MUSE_PATH_ESCAPES_WORKSPACE",
                    f"worker output escapes the workspace: {item.path}",
                    events=[self._evidence_event(turn, "muse.path_escape")],
                ) from None
            if item.kind == "symlink":
                target = str(item.link_target or "")
                anchor = (workspace / rel).parent
                resolved_target = (anchor / target).resolve(strict=False) if not os.path.isabs(target) else Path(target)
                try:
                    resolved_target.relative_to(workspace_resolved)
                except ValueError:
                    raise RealExecuteError(
                        "MUSE_PATH_ESCAPES_WORKSPACE",
                        f"worker symlink escapes the workspace: {item.path} -> {target}",
                        events=[self._evidence_event(turn, "muse.path_escape")],
                    ) from None
            if not _scope_covers(scope, rel):
                raise RealExecuteError(
                    "MUSE_PATH_OUT_OF_SCOPE",
                    f"worker output outside the accepted scope: {item.path} ({item.change})",
                    events=[self._evidence_event(turn, "muse.out_of_scope")],
                )
            kept.append(item)
        return kept

    @staticmethod
    def _evidence_event(turn: MuseTurnResult, name: str) -> dict[str, Any]:
        return {
            "event": name,
            "payload_refs": {
                "session_id": turn.session_id,
                "turn_id": turn.turn_id,
                **{
                    f"evidence_ref_{i}": ref
                    for i, ref in enumerate(turn.evidence_files)
                },
            },
        }


__all__ = [
    "MUSE_EXECUTOR_ID",
    "ActivationBindingLookup",
    "MuseCapabilityPolicy",
    "MuseExecuteAdapter",
    "validate_capability_policy",
]
