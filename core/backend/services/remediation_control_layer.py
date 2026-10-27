from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

OPEN_BLOCKER_STATUSES = ("queued", "running", "blocked", "needs_review", "waiting_on_human")
LEDGER_SCAN_STATUSES = OPEN_BLOCKER_STATUSES + ("done", "failed", "cancelled")
SHAPING_DEFECT_CLASSES = {"shaping", "task_shaping", "planner_shaping"}
UNKNOWN_DEFECT_CLASSES = {"", "unknown", "unclassified"}
REMEDIATION_CONTROL_METADATA_KEY = "remediation_control_current"


@dataclass(frozen=True, slots=True)
class DefectFingerprint:
    family_key: str
    family_root_task_number: int
    defect_class: str
    reason_code: str
    phase: str
    title_key: str
    target_files: tuple[str, ...] = ()
    remedial_depth: int = 0
    auto_create_kind: str = "remedial_child"


@dataclass(frozen=True, slots=True)
class RemediationPolicy:
    max_remedial_depth: int = 1
    max_open_child_per_defect_class_per_family: int = 1
    max_repeated_same_class_auto_remediations_before_pause: int = 2
    allow_shaping_class_auto_creation: bool = False
    allow_unknown_class_auto_creation: bool = False


@dataclass(slots=True)
class FamilyBlockerEntry:
    task_number: int
    task_id: str | None
    status: str
    title: str
    title_key: str
    family_key: str
    family_root_task_number: int
    defect_class: str
    reason_code: str
    phase: str
    fingerprint_key: str
    remedial_depth: int
    metadata: dict[str, Any]
    raw_task: dict[str, Any]


@dataclass(slots=True)
class FamilyBlockerLedger:
    family_key: str
    family_root_task_number: int
    open_entries: list[FamilyBlockerEntry]
    all_entries: list[FamilyBlockerEntry]


def default_remediation_policy() -> RemediationPolicy:
    return RemediationPolicy()


def normalize_title_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def build_defect_fingerprint(
    *,
    family_root_task_number: int,
    family_key: str,
    defect_class: str,
    reason_code: str,
    phase: str,
    title: str,
    target_files: list[str] | tuple[str, ...] | None = None,
    remedial_depth: int = 0,
    auto_create_kind: str = "remedial_child",
) -> DefectFingerprint:
    normalized_targets = tuple(
        str(item).strip().replace("\\", "/") for item in (target_files or []) if str(item).strip()
    )
    return DefectFingerprint(
        family_key=str(family_key or "").strip(),
        family_root_task_number=max(0, int(family_root_task_number or 0)),
        defect_class=str(defect_class or "").strip().lower() or "unknown",
        reason_code=str(reason_code or "").strip() or "UNKNOWN_REASON",
        phase=str(phase or "").strip().lower() or "unknown",
        title_key=normalize_title_key(title),
        target_files=normalized_targets,
        remedial_depth=max(0, int(remedial_depth or 0)),
        auto_create_kind=str(auto_create_kind or "").strip() or "remedial_child",
    )


def build_fingerprint_key(fingerprint: DefectFingerprint) -> str:
    payload = {
        "family_key": fingerprint.family_key,
        "family_root_task_number": fingerprint.family_root_task_number,
        "defect_class": fingerprint.defect_class,
        "reason_code": fingerprint.reason_code,
        "phase": fingerprint.phase,
        "title_key": fingerprint.title_key,
        "target_files": list(fingerprint.target_files),
        "auto_create_kind": fingerprint.auto_create_kind,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def child_task_contract_validator(
    *,
    title: str,
    description: str,
    metadata_json: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(metadata_json or {})
    ticket_contract = dict(metadata.get("ticket_contract_current") or {})
    execution_slice = dict(ticket_contract.get("execution_slice") or {})
    target_surface = dict(ticket_contract.get("target_surface") or {})
    target_files = [
        str(item).strip().replace("\\", "/")
        for item in (
            metadata.get("target_files")
            or execution_slice.get("target_files")
            or target_surface.get("target_files")
            or []
        )
        if str(item).strip()
    ]
    validation_commands = [
        str(item).strip()
        for item in (
            metadata.get("validation_commands")
            or metadata.get("allowed_validation_commands")
            or execution_slice.get("validation_commands")
            or []
        )
        if str(item).strip()
    ]
    missing: list[str] = []
    if not str(title or "").strip():
        missing.append("title")
    if not str(description or "").strip():
        missing.append("description")
    if not isinstance(metadata, dict) or not metadata:
        missing.append("metadata_json")
    task_intent = str(metadata.get("task_intent") or metadata.get("change_intent") or "").strip().lower()
    if task_intent not in {"system_issue_auto_submit", "system_issue"} and not target_files:
        missing.append("target_files")
    if task_intent in {"mutation_required", "recovery_task_auto_submit"} and not validation_commands:
        missing.append("validation_commands")
    return {
        "valid": len(missing) == 0,
        "missing_fields": missing,
        "target_files": target_files,
        "validation_commands": validation_commands,
    }


def build_family_blocker_ledger(
    *,
    task_client: Any,
    project_id: str,
    family_root_task_number: int,
    family_key: str,
) -> FamilyBlockerLedger:
    rows_by_status: dict[str, list[dict[str, Any]]] = {}
    for status in LEDGER_SCAN_STATUSES:
        try:
            rows = task_client._list_tasks(project_id=project_id, limit=200, status=status)
        except Exception:
            continue
        rows_by_status[status] = [row for row in rows if isinstance(row, dict)]
    return build_family_blocker_ledger_from_rows(
        rows_by_status=rows_by_status,
        family_root_task_number=family_root_task_number,
        family_key=family_key,
    )


def build_family_blocker_ledger_from_rows(
    *,
    rows_by_status: dict[str, list[dict[str, Any]]] | None,
    family_root_task_number: int,
    family_key: str,
) -> FamilyBlockerLedger:
    entries: list[FamilyBlockerEntry] = []
    for status in LEDGER_SCAN_STATUSES:
        rows = list((rows_by_status or {}).get(status) or [])
        for row in rows:
            entry = _ledger_entry_from_task(
                row=row,
                expected_family_root_task_number=family_root_task_number,
                expected_family_key=family_key,
            )
            if entry is not None:
                entries.append(entry)
    open_entries = [entry for entry in entries if entry.status in OPEN_BLOCKER_STATUSES]
    return FamilyBlockerLedger(
        family_key=str(family_key or "").strip(),
        family_root_task_number=max(0, int(family_root_task_number or 0)),
        open_entries=open_entries,
        all_entries=entries,
    )


def find_equivalent_open_blocker(
    *,
    fingerprint: DefectFingerprint,
    ledger: FamilyBlockerLedger,
) -> FamilyBlockerEntry | None:
    expected_key = build_fingerprint_key(fingerprint)
    for entry in ledger.open_entries:
        if entry.fingerprint_key and entry.fingerprint_key == expected_key:
            return entry
    for entry in ledger.open_entries:
        if entry.defect_class != fingerprint.defect_class:
            continue
        if entry.phase != fingerprint.phase:
            continue
        if str(entry.reason_code or "") != str(fingerprint.reason_code or ""):
            continue
        if entry.title_key and fingerprint.title_key and entry.title_key != fingerprint.title_key:
            continue
        return entry
    return None


def remediation_budget_check(
    *,
    fingerprint: DefectFingerprint,
    ledger: FamilyBlockerLedger,
    policy: RemediationPolicy,
) -> dict[str, Any]:
    open_count = sum(1 for entry in ledger.open_entries if entry.defect_class == fingerprint.defect_class)
    allowed = open_count < int(policy.max_open_child_per_defect_class_per_family)
    return {
        "allowed": allowed,
        "open_count": open_count,
        "limit": int(policy.max_open_child_per_defect_class_per_family),
        "reason_code": None if allowed else "REMEDIATION_OPEN_CHILD_BUDGET_EXHAUSTED",
    }


def circuit_breaker_check(
    *,
    fingerprint: DefectFingerprint,
    ledger: FamilyBlockerLedger,
    policy: RemediationPolicy,
) -> dict[str, Any]:
    repeated_count = sum(1 for entry in ledger.all_entries if entry.defect_class == fingerprint.defect_class)
    allowed = repeated_count < int(policy.max_repeated_same_class_auto_remediations_before_pause)
    return {
        "allowed": allowed,
        "repeated_count": repeated_count,
        "limit": int(policy.max_repeated_same_class_auto_remediations_before_pause),
        "reason_code": None if allowed else "REMEDIATION_CIRCUIT_BREAKER_OPEN",
    }


def should_create_remedial_task(
    *,
    fingerprint: DefectFingerprint,
    contract_validation: dict[str, Any],
    ledger: FamilyBlockerLedger,
    policy: RemediationPolicy,
) -> dict[str, Any]:
    if not bool(contract_validation.get("valid")):
        return {
            "action": "suppress",
            "reason_code": "CHILD_TASK_CONTRACT_INVALID",
            "audit_event": "child_suppressed_contract_invalid",
            "existing_blocker": None,
        }
    if fingerprint.remedial_depth > int(policy.max_remedial_depth):
        return {
            "action": "suppress",
            "reason_code": "REMEDIATION_DEPTH_EXHAUSTED",
            "audit_event": "child_suppressed_budget",
            "existing_blocker": None,
        }
    if fingerprint.defect_class in SHAPING_DEFECT_CLASSES and not bool(policy.allow_shaping_class_auto_creation):
        return {
            "action": "suppress",
            "reason_code": "REMEDIATION_SHAPING_AUTO_CREATE_DISABLED",
            "audit_event": "child_suppressed_policy",
            "existing_blocker": None,
        }
    if fingerprint.defect_class in UNKNOWN_DEFECT_CLASSES and not bool(policy.allow_unknown_class_auto_creation):
        return {
            "action": "suppress",
            "reason_code": "REMEDIATION_UNKNOWN_CLASS_AUTO_CREATE_DISABLED",
            "audit_event": "child_suppressed_policy",
            "existing_blocker": None,
        }
    existing_blocker = find_equivalent_open_blocker(fingerprint=fingerprint, ledger=ledger)
    if existing_blocker is not None:
        return {
            "action": "attach_existing",
            "reason_code": "EQUIVALENT_OPEN_BLOCKER_EXISTS",
            "audit_event": "attached_to_existing_blocker",
            "existing_blocker": existing_blocker,
        }
    budget = remediation_budget_check(fingerprint=fingerprint, ledger=ledger, policy=policy)
    if not bool(budget.get("allowed")):
        return {
            "action": "suppress",
            "reason_code": str(budget.get("reason_code") or "REMEDIATION_OPEN_CHILD_BUDGET_EXHAUSTED"),
            "audit_event": "child_suppressed_budget",
            "existing_blocker": None,
        }
    circuit = circuit_breaker_check(fingerprint=fingerprint, ledger=ledger, policy=policy)
    if not bool(circuit.get("allowed")):
        return {
            "action": "suppress",
            "reason_code": str(circuit.get("reason_code") or "REMEDIATION_CIRCUIT_BREAKER_OPEN"),
            "audit_event": "child_suppressed_circuit_breaker",
            "existing_blocker": None,
        }
    return {
        "action": "create",
        "reason_code": None,
        "audit_event": "child_created",
        "existing_blocker": None,
    }


def stamp_remediation_metadata(
    *,
    metadata_json: dict[str, Any] | None,
    fingerprint: DefectFingerprint,
    policy: RemediationPolicy,
    event_name: str,
) -> dict[str, Any]:
    metadata = dict(metadata_json or {})
    control = dict(metadata.get(REMEDIATION_CONTROL_METADATA_KEY) or {})
    control.update(
        {
            "family_key": fingerprint.family_key,
            "family_root_task_number": fingerprint.family_root_task_number,
            "defect_class": fingerprint.defect_class,
            "reason_code": fingerprint.reason_code,
            "phase": fingerprint.phase,
            "fingerprint_key": build_fingerprint_key(fingerprint),
            "auto_created": True,
            "auto_create_kind": fingerprint.auto_create_kind,
            "remedial_depth": fingerprint.remedial_depth,
            "policy": {
                "max_remedial_depth": int(policy.max_remedial_depth),
                "max_open_child_per_defect_class_per_family": int(policy.max_open_child_per_defect_class_per_family),
                "max_repeated_same_class_auto_remediations_before_pause": int(
                    policy.max_repeated_same_class_auto_remediations_before_pause
                ),
                "allow_shaping_class_auto_creation": bool(policy.allow_shaping_class_auto_creation),
                "allow_unknown_class_auto_creation": bool(policy.allow_unknown_class_auto_creation),
            },
        }
    )
    events = [dict(item) for item in control.get("events") or [] if isinstance(item, dict)]
    events.append(build_remediation_audit_event(event_name=event_name, fingerprint=fingerprint))
    control["events"] = events[-10:]
    metadata[REMEDIATION_CONTROL_METADATA_KEY] = control
    return metadata


def build_remediation_audit_event(
    *,
    event_name: str,
    fingerprint: DefectFingerprint,
    reason_code: str | None = None,
    attached_task_number: int | None = None,
) -> dict[str, Any]:
    return {
        "event": str(event_name or "").strip(),
        "family_key": fingerprint.family_key,
        "family_root_task_number": fingerprint.family_root_task_number,
        "defect_class": fingerprint.defect_class,
        "reason_code": str(reason_code or fingerprint.reason_code or "").strip() or None,
        "phase": fingerprint.phase,
        "fingerprint_key": build_fingerprint_key(fingerprint),
        "attached_task_number": int(attached_task_number or 0) or None,
    }


def _ledger_entry_from_task(
    *,
    row: dict[str, Any],
    expected_family_root_task_number: int,
    expected_family_key: str,
) -> FamilyBlockerEntry | None:
    metadata_value = row.get("metadata_json")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    control = dict(metadata.get(REMEDIATION_CONTROL_METADATA_KEY) or {})
    family_root_task_number = int(
        control.get("family_root_task_number")
        or metadata.get("recovery_root_task_number")
        or metadata.get("decomposition_parent_task_number")
        or metadata.get("planning_parent_task_number")
        or metadata.get("source_task_number")
        or metadata.get("parent_task_number")
        or 0
    )
    if family_root_task_number != int(expected_family_root_task_number):
        return None
    family_key = str(control.get("family_key") or expected_family_key).strip()
    if expected_family_key and family_key != str(expected_family_key).strip():
        return None
    title = str(row.get("title") or "").strip()
    title_key = normalize_title_key(title)
    defect_class = str(control.get("defect_class") or "").strip().lower()
    if not defect_class:
        change_intent = str(metadata.get("change_intent") or "").strip().lower()
        if change_intent == "recovery_task_auto_submit":
            defect_class = (
                f"recovery:{str(metadata.get('recovery_source_failure_code') or '').strip().lower() or 'unknown'}"
            )
        elif change_intent == "system_issue_auto_submit":
            defect_class = f"system_issue:{str(metadata.get('failure_code') or '').strip().lower() or 'unknown'}"
        elif title.lower().startswith("child "):
            defect_class = (
                f"admission_decomposition:{str(metadata.get('execution_phase') or '').strip().lower() or 'unknown'}"
            )
        else:
            defect_class = "unknown"
    reason_code = str(
        control.get("reason_code")
        or metadata.get("failure_code")
        or metadata.get("recovery_source_failure_code")
        or metadata.get("reason_code")
        or "UNKNOWN_REASON"
    ).strip()
    phase = str(control.get("phase") or metadata.get("execution_phase") or "unknown").strip().lower() or "unknown"
    fingerprint_key = str(control.get("fingerprint_key") or "").strip()
    if not fingerprint_key:
        fingerprint = build_defect_fingerprint(
            family_root_task_number=family_root_task_number,
            family_key=family_key,
            defect_class=defect_class,
            reason_code=reason_code,
            phase=phase,
            title=title,
            target_files=[
                str(item).strip()
                for item in (metadata.get("target_files") or metadata.get("recovery_target_files") or [])
                if str(item).strip()
            ],
            remedial_depth=int(control.get("remedial_depth") or 0),
            auto_create_kind=str(control.get("auto_create_kind") or "").strip() or "remedial_child",
        )
        fingerprint_key = build_fingerprint_key(fingerprint)
    return FamilyBlockerEntry(
        task_number=int(row.get("task_number") or 0),
        task_id=str(row.get("id") or "").strip() or None,
        status=str(row.get("status") or "").strip().lower(),
        title=title,
        title_key=title_key,
        family_key=family_key,
        family_root_task_number=family_root_task_number,
        defect_class=defect_class,
        reason_code=reason_code,
        phase=phase,
        fingerprint_key=fingerprint_key,
        remedial_depth=int(control.get("remedial_depth") or 0),
        metadata=metadata,
        raw_task=dict(row),
    )
