"""Explicit operator recovery of a completed task's abandoned controller slot.

This does not resume or approve an attempt. WorkHub completion and absence of an
active assignment are required. Original journals remain archived; the old task
is recorded as externally completed and never counted as unattended success.
"""

import hashlib
import json
import os


def recover(lane, continuation):
    require = continuation.require
    config = continuation.settings(lane)
    require(config is not None, "recovery-continuous-disabled")
    root = lane.STATE_DIR / "completed-halt-recovery"
    intent_path = root / "intent.json"
    lease_path = lane.LEASE_FILE
    slot_path = continuation.state_path(lane)
    if intent_path.exists():
        intent = json.loads(intent_path.read_text())
        lease_bytes = (root / "lease.json").read_bytes()
        slot_bytes = (root / "slot.json").read_bytes()
        require(
            hashlib.sha256(lease_bytes).hexdigest() == intent["lease_sha256"]
            and hashlib.sha256(slot_bytes).hexdigest() == intent["slot_sha256"],
            "recovery-archive-drift",
        )
    else:
        lease_bytes, slot_bytes = lease_path.read_bytes(), slot_path.read_bytes()
        intent = {
            "schema": "CompletedHaltRecoveryV1",
            "lease_sha256": hashlib.sha256(lease_bytes).hexdigest(),
            "slot_sha256": hashlib.sha256(slot_bytes).hexdigest(),
        }
    lease, value = json.loads(lease_bytes), json.loads(slot_bytes)
    journal = lease.get("canonical_review_pending") or {}
    slot = value.get("slot") or {}
    halt = "verifier-material-missing:reconciliation-readback.json"
    require(
        journal.get("halted") == halt
        and value.get("halted") == "canonical-review-pending:" + halt,
        "recovery-halt-not-supported",
    )
    tid = lease.get("task_id")
    require(
        tid
        and slot.get("root_task_id") == tid
        and slot.get("phase") == "await-parent"
        and slot.get("operations") == {}
        and not slot.get("parent")
        and not slot.get("parent_lease"),
        "recovery-slot-effects-or-lineage-invalid",
    )
    require(
        journal.get("task", {}).get("id") == tid
        and journal.get("attempt_id") == lease.get("attempt_id")
        and lease.get("workhub_attempt_id")
        and lease.get("assignment_id")
        and lease.get("result_filed") is True,
        "recovery-lease-lineage-invalid",
    )
    require(
        not any(k in journal.get("operations", {}) for k in ("review", "integration"))
        and not journal.get("complete")
        and not journal.get("next_job_readback"),
        "recovery-review-already-advanced",
    )
    pid, ticks = lease.get("worker_pid"), lease.get("worker_ticks")
    require(
        type(pid) is int and pid > 0 and isinstance(ticks, str) and ticks,
        "recovery-worker-identity-missing",
    )
    require(not lane.alive(pid, ticks), "recovery-worker-live")
    _, cfg, _, _ = lane.recipe_session(journal["route"])
    jwt, principal = continuation.session(lane, cfg, config, "coordinator")
    require(
        journal.get("cfg")
        == {k: cfg.get(k) for k in ("wh_base", "auth_base", "project")},
        "recovery-project-drift",
    )
    response, task = lane.WH.get_task(
        cfg["wh_base"], jwt, lane.TENANT, lane.cfg_project(cfg), tid
    )
    require(
        not lane.WH.refusal_reason(response)
        and task.get("id") == tid
        and task.get("status") == "done"
        and task.get("task_number") == lease.get("task_number"),
        "recovery-task-not-completed",
    )
    response = lane.WH.wh_call(
        cfg["wh_base"],
        jwt,
        "GET",
        f"/api/v1/projects/{lane.cfg_project(cfg)}/tasks/by-number/"
        f"{task['task_number']}/assignment-binding",
        lane.TENANT,
        lane.cfg_project(cfg),
    )
    body = response.get("body")
    body = json.loads(body) if isinstance(body, str) else body
    require(
        response.get("http") == 404
        and isinstance(body, dict)
        and body.get("detail", {}).get("code") == "assignment_binding_not_found",
        "recovery-active-assignment-or-read-refused",
    )
    audit = {
        **{k: intent[k] for k in ("schema", "lease_sha256", "slot_sha256")},
        "task_id": tid,
        "task_number": task["task_number"],
        "attempt_id": lease["attempt_id"],
        "workhub_attempt_id": lease["workhub_attempt_id"],
        "assignment_id": lease["assignment_id"],
        "formal_result_id": lease.get("formal_result_id"),
        "task_record_version": task["record_version"],
        "task_status": "done",
        "active_assignment": None,
        "worker_absent": True,
        "resolver": principal,
        "reason": "owner-recovery-after-external-completion",
        "unattended_success": False,
    }
    if intent_path.exists():
        # Stable evidence bytes make replay safe even across an interrupted reset.
        require(intent["audit"] == audit, "recovery-authority-readback-drift")
    else:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path, raw in (
            (root / "lease.json", lease_bytes),
            (root / "slot.json", slot_bytes),
        ):
            if path.exists():
                require(path.read_bytes() == raw, "recovery-archive-collision")
            else:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
        continuation.persist(intent_path, {**intent, "audit": audit})
    ref = "completed-halt-recovery:" + intent["lease_sha256"]
    # Use the maintained client's exact replay/readback helper.
    receipt = lane.file_evidence_or_exact_replay(
        cfg, jwt, tid, "controller_terminal_recovery_v1", ref, audit, ref
    )
    require(receipt.get("id"), "recovery-evidence-missing")
    read, rows = lane.WH.list_evidence(
        cfg["wh_base"], jwt, lane.TENANT, lane.cfg_project(cfg), tid
    )
    require(
        not lane.WH.refusal_reason(read)
        and any(
            row.get("id") == receipt["id"]
            and row.get("task_id") == tid
            and row.get("evidence_type") == "controller_terminal_recovery_v1"
            and row.get("evidence_ref") == ref
            and lane.WH.json_contains(row.get("evidence_json", {}), audit)
            for row in rows or []
        ),
        "recovery-evidence-readback-invalid",
    )
    marker = {
        "task_id": tid,
        "task_number": task["task_number"],
        "attempt_id": lease["attempt_id"],
        "reason": audit["reason"],
        "recovery_evidence_id": receipt["id"],
        "archive": str(root),
        "unattended_success": False,
    }
    new_slot = dict(value)
    new_slot.pop("halted")
    new_slot["slot"] = None
    new_slot.setdefault("external_recoveries", []).append(marker)
    require(
        lease_path.read_bytes() == lease_bytes
        or lane.load_json(lease_path, {}) == marker,
        "recovery-local-lease-changed",
    )
    require(
        slot_path.read_bytes() == slot_bytes
        or lane.load_json(slot_path, {}) == new_slot,
        "recovery-local-slot-changed",
    )
    continuation.persist(lease_path, marker)
    continuation.save(lane, new_slot)
    return {"action": "completed-halt-recovered", **marker}
