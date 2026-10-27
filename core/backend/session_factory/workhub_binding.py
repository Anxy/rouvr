"""Verify a submitted WorkHub binding against supported owner reads.

Lane-ops #23: the AIX ingress never trusts caller-supplied equality.
Every binding field is re-read through the supported transport
(task GET, assignment-binding GET, evidence list) and compared
exactly. No supported owner API exposes the attempt row, so attempt
identity/version plus the complete selection/candidate/plan are proven
by the lane's own exact handoff receipt and the live-contract
execution_spec row. No new WorkHub routes are introduced here.
"""

from __future__ import annotations

from typing import Any, Protocol


class BindingTransport(Protocol):
    async def get_task_by_number(
        self, *, project_id: str, task_number: int
    ) -> dict[str, Any] | None: ...
    async def get_assignment_binding(
        self, *, project_id: str, task_number: int
    ) -> dict[str, Any] | None: ...
    async def list_task_evidence(
        self, *, project_id: str, task_id: str
    ) -> list[dict[str, Any]]: ...


class BindingRefused(ValueError):
    pass


def _deny(code: str) -> None:
    raise BindingRefused(code)


# Required binding shape, mirroring activation.WORKHUB_BINDING_KEYS
# (pinned equal by unit test). Malformed bindings refuse as 409;
# nothing here may raise an unhandled KeyError/500.
_BINDING_REQUIRED_KEYS = frozenset({
    "schema_version", "tenant_id", "project_id", "task_id", "task_number",
    "assignment_id", "assignment_revision", "executor_sub",
    "workhub_attempt_id", "attempt_version", "controller_attempt_id",
    "lease_epoch", "lease_holder", "contract_ref", "authority_ref",
    "plan_digest", "route_digest", "selection",
})


def _require_shape(binding: Any) -> dict[str, Any]:
    if not isinstance(binding, dict) or not binding:
        _deny("BINDING_MISSING")
    assert isinstance(binding, dict)
    if binding.get("schema_version") != 1:
        _deny("BINDING_VERSION_UNSUPPORTED")
    missing = [key for key in _BINDING_REQUIRED_KEYS if key not in binding]
    if missing:
        _deny("BINDING_INCOMPLETE")
    try:
        int(binding["task_number"])
        int(binding["assignment_revision"])
        int(binding["attempt_version"])
        int(binding["lease_epoch"])
    except (TypeError, ValueError):
        _deny("BINDING_COUNTER_INVALID")
    if not isinstance(binding.get("selection"), dict):
        _deny("BINDING_SELECTION_MISSING")
    return binding


# Canonical handoff fence key, mirroring the controller's
# handoff_fence_key(contract): temporal-handoff-<controller_attempt>-e<epoch>.
# Reconstructed here from the binding and required by exact equality;
# no substring inference is accepted anywhere in this module.
_HANDOFF_KEY_TEMPLATE = "temporal-handoff-%s-e%s"

_HANDOFF_RECEIPT_TYPE = "temporal_handoff"

# Attempt fields the maintained handoff receipt must carry exactly.
_RECEIPT_ATTEMPT_KEYS = (
    "workhub_attempt_id",
    "attempt_version",
    "executor_sub",
    "controller_attempt_id",
)

# Selection keys proven against the live-contract execution_spec row.
_SELECTION_KEYS = (
    "tenant_id", "project_id", "task_id", "task_number", "objective_id",
    "cycle_id", "selection_id", "mandate_id", "session_id", "worker_id",
    "candidate_sha", "plan_sha256", "correlation_id",
)


def _expected_handoff_fence(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": str(binding["assignment_id"]),
        "fence_token": {
            "assignment_revision": int(binding["assignment_revision"]),
            "lease_epoch": int(binding["lease_epoch"]),
        },
        "idempotency_key": _HANDOFF_KEY_TEMPLATE % (
            binding["controller_attempt_id"], binding["lease_epoch"]),
    }


def _exact_attempt_receipt(
    rows: list[dict[str, Any]], binding: dict[str, Any]
) -> bool:
    """True when a maintained handoff receipt proves every attempt field.

    The receipt is the lane's own temporal_handoff evidence row, filed
    under prove_effect_claim at handoff time. Its fence must equal the
    binding-reconstructed fence exactly, and its payload attempt fields
    must equal the binding's exactly. A fabricated attempt id/version
    has no such receipt and refuses.
    """
    expected_fence = _expected_handoff_fence(binding)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("evidence_type") != _HANDOFF_RECEIPT_TYPE:
            continue
        payload = row.get("evidence_json")
        if not isinstance(payload, dict):
            continue
        if payload.get("effect_fence") != expected_fence:
            continue
        for key in _RECEIPT_ATTEMPT_KEYS:
            if payload.get(key) != binding.get(key):
                break
        else:
            return True
    return False


def _pinned_selection(
    rows: list[dict[str, Any]], binding: dict[str, Any]
) -> bool:
    """True when the binding selection equals the live-contract spec.

    The complete accepted selection/candidate/plan rides inside the
    binding; it must agree with the binding's own task fields and match
    exactly the aix_selection block of the single execution_spec row
    pinned to the live contract ref.
    """
    selection = binding.get("selection")
    if not isinstance(selection, dict):
        return False
    for key in ("tenant_id", "project_id", "task_id", "task_number"):
        if selection.get(key) != binding.get(key):
            return False
    live_ref = str(binding.get("contract_ref") or "")
    blocks = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("evidence_type") != "execution_spec":
            continue
        payload = row.get("evidence_json")
        if not isinstance(payload, dict):
            continue
        if str(payload.get("contract_ref") or "") != live_ref:
            continue
        block = payload.get("aix_selection")
        if isinstance(block, dict):
            blocks.append(block)
    if len(blocks) != 1:
        return False
    return all(
        blocks[0].get(key) == selection.get(key) for key in _SELECTION_KEYS
    )


async def verify_binding(
    binding: dict[str, Any], *, transport: BindingTransport
) -> dict[str, Any]:
    """Re-read and exactly compare every binding field. Fail closed."""
    binding = _require_shape(binding)
    project = str(binding["project_id"])
    number = int(binding["task_number"])
    task = await transport.get_task_by_number(
        project_id=project, task_number=number
    )
    if not isinstance(task, dict) or not task:
        _deny("BINDING_TASK_MISSING")
    assert isinstance(task, dict)
    if str(task.get("id") or "") != str(binding["task_id"]):
        _deny("BINDING_TASK_MISMATCH")
    if str(task.get("tenant_id") or "") != str(binding["tenant_id"]):
        _deny("BINDING_TENANT_MISMATCH")
    if str(task.get("active_execution_contract_ref") or "") != str(
        binding["contract_ref"]
    ):
        _deny("BINDING_CONTRACT_MOVED")
    live = await transport.get_assignment_binding(
        project_id=project, task_number=number
    )
    if not isinstance(live, dict) or not live:
        _deny("BINDING_ASSIGNMENT_MISSING")
    assert isinstance(live, dict)
    if str(live.get("task_id") or "") != str(binding["task_id"]):
        _deny("BINDING_ASSIGNMENT_TASK_MISMATCH")
    if str(live.get("assignment_status") or "") != "active":
        _deny("BINDING_ASSIGNMENT_NOT_ACTIVE")
    if str(live.get("executor_sub") or "") != str(binding["executor_sub"]):
        _deny("BINDING_EXECUTOR_MISMATCH")
    if int(live.get("assignment_record_version") or 0) != int(
        binding["assignment_revision"]
    ):
        _deny("BINDING_ASSIGNMENT_MOVED")
    if str(live.get("assignment_id") or "") != str(binding["assignment_id"]):
        _deny("BINDING_ASSIGNMENT_ID_MISMATCH")
    for key in ("authority_ref", "plan_digest"):
        if not live.get(key):
            _deny("BINDING_ROUTE_FIELD_ABSENT")
        if str(live.get(key)) != str(binding[key]):
            _deny("BINDING_ROUTE_FIELD_MISMATCH")
    epoch = live.get("lease_epoch")
    if not isinstance(epoch, int) or epoch < 1:
        _deny("BINDING_LEASE_LAPSED")
    if epoch != int(binding["lease_epoch"]):
        _deny("BINDING_LEASE_MOVED")
    if str(live.get("holding_principal") or "") != str(
        binding["lease_holder"]
    ):
        _deny("BINDING_LEASE_HOLDER_MISMATCH")
    rows = await transport.list_task_evidence(
        project_id=project, task_id=str(binding["task_id"])
    )
    # No supported owner API exposes the attempt row (only task reads,
    # assignment-binding and evidence list exist), so attempt identity
    # and version are proven by the lane's own exact handoff receipt.
    if not _exact_attempt_receipt(rows or [], binding):
        _deny("BINDING_ATTEMPT_UNPROVEN")
    if not _pinned_selection(rows or [], binding):
        _deny("BINDING_SELECTION_UNPROVEN")
    verified_selection = binding.get("selection")
    if not isinstance(verified_selection, dict):
        _deny("BINDING_SELECTION_MISSING")
    return {
        "task_id": str(binding["task_id"]),
        "assignment_id": str(binding["assignment_id"]),
        "workhub_attempt_id": str(binding["workhub_attempt_id"]),
        "lease_epoch": epoch,
        "authority_ref": str(binding["authority_ref"]),
        "plan_digest": str(binding["plan_digest"]),
        # Canonical verified selection: the exact inner selection proven
        # against the live-contract execution_spec row above. Ingress and
        # activation bind every effect-driving outer field to this dict;
        # nothing about the selection is trusted from the outer payload.
        "selection": dict(verified_selection),
    }


def build_verifier(client: Any) -> Any:
    """Production verifier over the supported WorkHub client reads."""

    async def _verify(binding: dict[str, Any]) -> dict[str, Any]:
        return await verify_binding(binding, transport=client)

    return _verify


def build_binding(
    *,
    tenant_id: str,
    project_id: str,
    task_id: str,
    task_number: int,
    assignment_id: str,
    assignment_revision: int,
    executor_sub: str,
    workhub_attempt_id: str,
    attempt_version: int,
    controller_attempt_id: str,
    lease_epoch: int,
    lease_holder: str,
    contract_ref: str,
    authority_ref: str,
    plan_digest: str,
    route_digest: str,
    selection: dict[str, Any],
) -> dict[str, Any]:
    """Closed constructor mirroring GovernanceBindingV1 field discipline."""
    from backend.session_factory.activation import WORKHUB_BINDING_VERSION

    return {
        "schema_version": WORKHUB_BINDING_VERSION,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "task_id": task_id,
        "task_number": task_number,
        "assignment_id": assignment_id,
        "assignment_revision": assignment_revision,
        "executor_sub": executor_sub,
        "workhub_attempt_id": workhub_attempt_id,
        "attempt_version": attempt_version,
        "controller_attempt_id": controller_attempt_id,
        "lease_epoch": lease_epoch,
        "lease_holder": lease_holder,
        "contract_ref": contract_ref,
        "authority_ref": authority_ref,
        "plan_digest": plan_digest,
        "route_digest": route_digest,
        "selection": dict(selection),
    }
