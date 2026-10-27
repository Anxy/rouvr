"""001P-W13: after successful objective-cycle execute, mark WorkHub task done.

Legal path (evidence_required + verifier_required):
  queued -> running -> needs_review -> attach evidence -> verification pass -> done

Exception only; production remains NO_GO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
import httpx

from backend.aix_core.framework.integration.workhub.client import WorkHubTaskClient
from backend.aix_core.runtime.objective.ulid_compat import new_ulid


class MarkWorkHubDoneError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class MarkWorkHubDoneResult:
    ok: bool
    code: str
    task_id: str
    from_status: str | None = None
    to_status: str | None = None
    steps: list[str] = field(default_factory=list)
    detail: str = ""
    exception: str = "001P-W13"


def _idem(prefix: str, cycle_id: str) -> str:
    return f"{prefix}-{cycle_id}-{new_ulid()[:10]}"


async def mark_workhub_task_done(
    *,
    client: WorkHubTaskClient,
    project_id: str,
    task_id: str,
    cycle_id: str,
    evidence_ref: str,
    evidence_json: dict[str, Any] | None = None,
    artifact_ref: str | None = None,
    reason: str | None = None,
    tenant_id: str | None = None,
    task_number: int | None = None,
) -> MarkWorkHubDoneResult:
    """Drive WorkHub task to done after a successful AIX cycle execute."""
    project_id = str(project_id or "").strip()
    task_id = str(task_id or "").strip()
    if not project_id or not task_id:
        raise MarkWorkHubDoneError("WORKHUB_TASK_CONTEXT_MISSING", "project_id and task_id required")

    steps: list[str] = []
    reason_text = str(reason or f"AIX objective cycle {cycle_id} completed successfully").strip()
    artifact = str(artifact_ref or evidence_ref or "").strip() or f"aix://cycles/{cycle_id}"

    try:
        task = await client.get_task(
            project_id=project_id,
            task_id=task_id,
            tenant_id=tenant_id,
            task_number=task_number,
        )
    except httpx.HTTPError as exc:
        raise MarkWorkHubDoneError("WORKHUB_TASK_LOAD_FAILED", str(exc)[:400]) from exc
    if not task:
        raise MarkWorkHubDoneError("WORKHUB_TASK_NOT_FOUND", task_id)

    current = str(task.get("status") or "").strip().lower()
    from_status = current
    if current == "done":
        return MarkWorkHubDoneResult(
            ok=True,
            code="ALREADY_DONE",
            task_id=task_id,
            from_status=from_status,
            to_status="done",
            steps=["noop:already_done"],
            detail="Task already done",
        )

    # Walk toward needs_review before evidence/verification gates.
    path_to_review = {
        "queued": ["running", "needs_review"],
        "running": ["needs_review"],
        "waiting_on_human": ["running", "needs_review"],
        "needs_enrichment": ["running", "needs_review"],
        "needs_human_intervention": ["running", "needs_review"],
        "execution_failed": ["running", "needs_review"],
        "blocked": ["running", "needs_review"],
        "submitted_for_review": [],
        "needs_review": [],
    }
    if current not in path_to_review and current != "needs_review":
        raise MarkWorkHubDoneError("UNSUPPORTED_STATUS", f"cannot mark done from status={current}")

    for target in path_to_review.get(current, []):
        try:
            updated = await client.transition_task(
                project_id=project_id,
                task_id=task_id,
                status_value=target,
                reason=reason_text,
                tenant_id=tenant_id,
                idempotency_key=_idem(f"aix-w13-{target}", cycle_id),
            )
        except httpx.HTTPError as exc:
            raise MarkWorkHubDoneError("TRANSITION_FAILED", f"{current}->{target}: {exc}"[:500]) from exc
        current = str(updated.get("status") or target).strip().lower()
        steps.append(f"transition:{target}")

    try:
        await client.add_task_evidence(
            project_id=project_id,
            task_id=task_id,
            evidence_ref=str(evidence_ref or artifact),
            evidence_type="aix_objective_cycle",
            evidence_json={
                "source": "aix.objective_cycle.mark_workhub_done",
                "exception": "001P-W13",
                "cycle_id": cycle_id,
                "recorded_at": _utc(),
                **dict(evidence_json or {}),
            },
            tenant_id=tenant_id,
            idempotency_key=_idem("aix-w13-evidence", cycle_id),
        )
        steps.append("evidence:attached")
    except httpx.HTTPError as exc:
        raise MarkWorkHubDoneError("EVIDENCE_ATTACH_FAILED", str(exc)[:500]) from exc

    try:
        verification = await client.evaluate_task_verification(
            project_id=project_id,
            task_id=task_id,
            decision="pass",
            reasons=[reason_text],
            checks=[
                {
                    "check_id": "aix_objective_cycle_completed",
                    "passed": True,
                    "note": "AIX real execute completed with evidence artifacts",
                    "artifact_ref": artifact,
                }
            ],
            tenant_id=tenant_id,
            idempotency_key=_idem("aix-w13-verify", cycle_id),
        )
        steps.append("verification:pass")
        if verification.get("eligible_for_done") is False:
            raise MarkWorkHubDoneError("NOT_ELIGIBLE_FOR_DONE", str(verification)[:400])
    except MarkWorkHubDoneError:
        raise
    except httpx.HTTPError as exc:
        raise MarkWorkHubDoneError("VERIFICATION_FAILED", str(exc)[:500]) from exc

    try:
        done_task = await client.transition_task(
            project_id=project_id,
            task_id=task_id,
            status_value="done",
            reason=reason_text,
            tenant_id=tenant_id,
            idempotency_key=_idem("aix-w13-done", cycle_id),
        )
    except httpx.HTTPError as exc:
        raise MarkWorkHubDoneError("DONE_TRANSITION_FAILED", str(exc)[:500]) from exc

    to_status = str(done_task.get("status") or "done").strip().lower()
    steps.append("transition:done")
    if to_status != "done":
        raise MarkWorkHubDoneError("DONE_STATUS_MISMATCH", f"expected done got {to_status}")

    return MarkWorkHubDoneResult(
        ok=True,
        code="MARKED_DONE",
        task_id=task_id,
        from_status=from_status,
        to_status=to_status,
        steps=steps,
        detail=reason_text,
    )
