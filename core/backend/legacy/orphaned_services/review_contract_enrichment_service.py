from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

try:
    from aix.contracts.ayrpack_current import ActorRefCurrent, ContractRefCurrent, SpineEntryKind
    from aix.contracts.task_lifecycle_state_machine_current import (
        TaskLifecycleState,
        append_packet_event,
        transition_task_state,
    )
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    _AIX_SRC = Path(__file__).resolve().parents[3] / "src"
    if str(_AIX_SRC) not in sys.path:
        sys.path.insert(0, str(_AIX_SRC))
    from aix.contracts.ayrpack_current import ActorRefCurrent, ContractRefCurrent, SpineEntryKind
    from aix.contracts.task_lifecycle_state_machine_current import (
        TaskLifecycleState,
        append_packet_event,
        transition_task_state,
    )

from backend.infrastructure.telemetry.hyeve_aix_service_telemetry import observe_manual_intervention, observe_review_ready_conversion
from hyeve_core.task_contract_validation import validate_task_ticket_contract

try:
    from hyeve_task_contract_engine import sync_task_readiness_metadata
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    _HYEVE_ROOT = Path(__file__).resolve().parents[5]
    _ENGINE_PACKAGE_ROOT = _HYEVE_ROOT / "libs" / "hyeve_task_contract_engine"
    if str(_ENGINE_PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(_ENGINE_PACKAGE_ROOT))
    from hyeve_task_contract_engine import sync_task_readiness_metadata

ReviewTaskFetcher = Callable[[int], Awaitable[list[dict[str, Any]]]]
ReviewTaskPersister = Callable[[dict[str, Any], dict[str, Any]], Awaitable[tuple[bool, str | None]]]
ReviewContractEnricher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ReviewTaskClaimer = Callable[[dict[str, Any], dict[str, Any]], Awaitable[tuple[bool, str | None]]]
ReviewApprovalMode = Literal["human_confirmation", "designated_approval"]


@dataclass(slots=True)
class ReviewContractEnrichmentService:
    fetch_review_tasks: ReviewTaskFetcher
    persist_task_metadata: ReviewTaskPersister
    enrich_contract: ReviewContractEnricher
    claim_review_task: ReviewTaskClaimer | None = None
    poll_seconds: float = 5.0
    actor_id: str = "review_contract_agent"
    actor_display_name: str = "Review Contract Agent"

    async def poll_once(self, *, limit: int = 20) -> list[dict[str, Any]]:
        tasks = list(await self.fetch_review_tasks(limit))
        results: list[dict[str, Any]] = []
        for task in tasks:
            metadata = dict(task.get("metadata_json") or {})
            packet = metadata.get("ayrpack")
            status = str(((packet or {}).get("active_frame") or {}).get("status") or "").strip().lower()
            if status != "review":
                continue
            results.append(await self.process_task(task))
        return results

    async def run_forever(self, *, stop_event: asyncio.Event | None = None, limit: int = 20) -> None:
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            await self.poll_once(limit=limit)
            await asyncio.sleep(self.poll_seconds)

    async def process_task(self, task: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(task.get("metadata_json") or {})
        packet = deepcopy(metadata.get("ayrpack") or {})
        if not isinstance(packet, dict):
            return {"ok": False, "reason": "AYRPACK_MISSING", "task_id": task.get("id")}
        actor = _review_agent_actor(self.actor_id, self.actor_display_name)
        claim_packet = _claim_review_packet(packet=packet, actor=actor)
        metadata["ayrpack"] = claim_packet
        claim_result, claim_error = await self._persist_claim(task, metadata)
        if not claim_result:
            return {"ok": False, "reason": "CLAIM_FAILED", "task_id": task.get("id"), "error": claim_error}
        packet = deepcopy(claim_packet)

        task_context = {
            "task_id": str(task.get("id") or ""),
            "task_number": task.get("task_number"),
            "project_id": str(task.get("project_id") or ""),
            "title": str(task.get("title") or ""),
            "description": task.get("description"),
            "metadata": deepcopy(metadata),
            "current_contract": deepcopy(metadata.get("ticket_contract_current") or {}),
            "contract_ref": deepcopy(
                packet.get("active_frame", {}).get("contract_ref") or packet.get("contract_ref") or {}
            ),
            "ayrpack": deepcopy(packet),
        }
        try:
            enriched_contract = dict(await self.enrich_contract(task_context) or {})
        except Exception as exc:
            updated_packet = append_packet_event(
                packet=packet,
                actor=actor,
                title="review enrichment failed",
                summary=str(exc),
                status=TaskLifecycleState.REVIEW.value,
                contract_ref=dict(
                    packet.get("active_frame", {}).get("contract_ref") or packet.get("contract_ref") or {}
                ),
            )["packet"]
            metadata["ayrpack"] = updated_packet
            persisted, error = await self.persist_task_metadata(task, metadata)
            return {"ok": False, "reason": "ENRICHMENT_FAILED", "persisted": persisted, "error": error or str(exc)}

        candidate_metadata = dict(metadata)
        candidate_metadata["ticket_contract_current"] = enriched_contract
        validation = validate_task_ticket_contract(
            title=str(task.get("title") or ""),
            description=task.get("description"),
            metadata=candidate_metadata,
        )
        if not bool(validation.get("valid")):
            gaps = list(validation.get("missing_fields") or [])
            updated_packet = append_packet_event(
                packet=packet,
                actor=actor,
                title="review enrichment incomplete",
                summary=f"gaps={','.join(gaps)}" if gaps else "enriched contract still incomplete",
                status=TaskLifecycleState.REVIEW.value,
                contract_ref=dict(
                    packet.get("active_frame", {}).get("contract_ref") or packet.get("contract_ref") or {}
                ),
            )["packet"]
            metadata["ayrpack"] = updated_packet
            metadata = sync_task_readiness_metadata(
                task_status=str(task.get("status") or "").strip() or "queued",
                metadata=metadata,
            )
            persisted, error = await self.persist_task_metadata(task, metadata)
            return {
                "ok": False,
                "reason": "ENRICHMENT_INCOMPLETE",
                "persisted": persisted,
                "error": error,
                "missing_fields": gaps,
            }

        current_contract = dict(metadata.get("ticket_contract_current") or {})
        previous_ref = dict(packet.get("active_frame", {}).get("contract_ref") or packet.get("contract_ref") or {})
        new_ref = _build_versioned_contract_ref(previous_ref=previous_ref, contract=enriched_contract)
        contract_diff = _build_contract_diff(previous=current_contract, current=enriched_contract)
        versions = list(metadata.get("ticket_contract_versions") or [])
        versions.append(
            {
                "contract_ref": dict(new_ref),
                "base_contract_ref": dict(previous_ref),
                "ticket_contract_current": deepcopy(enriched_contract),
                "changed_paths": list(contract_diff["changed_paths"]),
                "summary": contract_diff["summary"],
                "diff": contract_diff,
                "created_at_utc": _utcnow(),
                "source": "review_contract_agent",
            }
        )
        metadata["ticket_contract_versions"] = versions
        updated_packet = _set_review_enriched(
            packet=packet,
            contract_ref=new_ref,
            actor_id=self.actor_id,
            actor_name=self.actor_display_name,
            diff_summary=str(contract_diff["summary"]),
        )
        metadata["ayrpack"] = updated_packet
        metadata = sync_task_readiness_metadata(
            task_status=str(task.get("status") or "").strip() or "queued",
            metadata=metadata,
        )
        persisted, error = await self.persist_task_metadata(task, metadata)
        return {
            "ok": persisted,
            "reason": "REVIEW_ENRICHED" if persisted else "PERSIST_FAILED",
            "error": error,
            "contract_ref": new_ref,
            "contract_diff": contract_diff,
        }

    async def _persist_claim(self, task: dict[str, Any], metadata: dict[str, Any]) -> tuple[bool, str | None]:
        if self.claim_review_task is not None:
            return await self.claim_review_task(task, metadata)
        return await self.persist_task_metadata(task, metadata)


def _review_agent_actor(actor_id: str, actor_name: str) -> ActorRefCurrent:
    return {"kind": "agent", "id": actor_id, "display_name": actor_name}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _append_spine_entry(
    *,
    packet: dict[str, Any],
    kind: SpineEntryKind,
    phase: str,
    status: str,
    actor: dict[str, str],
    title: str,
    summary: str,
    contract_ref: dict[str, Any],
) -> dict[str, Any]:
    packet_copy = deepcopy(packet)
    spine = list(packet_copy.get("spine") or [])
    next_index = max((int(item.get("index", 0)) for item in spine), default=-1) + 1
    timestamp_utc = _utcnow()
    spine.append(
        {
            "entry_id": f"spine-{uuid4().hex}",
            "index": next_index,
            "kind": kind,
            "timestamp_utc": timestamp_utc,
            "phase": phase,
            "status": status,
            "actor": dict(actor),
            "title": title,
            "summary": summary,
            "contract_ref": dict(contract_ref or {}),
        }
    )
    frame = dict(packet_copy.get("active_frame") or {})
    frame["updated_at_utc"] = timestamp_utc
    frame["actor"] = dict(actor)
    frame["summary"] = summary
    packet_copy["active_frame"] = frame
    packet_copy["spine"] = spine
    return packet_copy


def _claim_review_packet(*, packet: dict[str, Any], actor: ActorRefCurrent) -> dict[str, Any]:
    return append_packet_event(
        packet=packet,
        actor=actor,
        title="review task claimed",
        summary="review task claimed by review agent",
        status=TaskLifecycleState.REVIEW.value,
        contract_ref=dict(packet.get("active_frame", {}).get("contract_ref") or packet.get("contract_ref") or {}),
    )["packet"]


def _normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip()


def _build_contract_diff(*, previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous_paths = _flatten_contract_paths(previous)
    current_paths = _flatten_contract_paths(current)
    all_paths = sorted(set(previous_paths) | set(current_paths))
    changed_paths: list[str] = []
    added_paths: list[str] = []
    removed_paths: list[str] = []
    changed_values: list[dict[str, Any]] = []
    for path in all_paths:
        before_exists = path in previous_paths
        after_exists = path in current_paths
        before_value = previous_paths.get(path)
        after_value = current_paths.get(path)
        if before_exists and not after_exists:
            removed_paths.append(path)
            changed_paths.append(path)
        elif after_exists and not before_exists:
            added_paths.append(path)
            changed_paths.append(path)
        elif before_value != after_value:
            changed_paths.append(path)
        if before_value != after_value:
            changed_values.append({"path": path, "before": before_value, "after": after_value})
    summary = (
        f"contract version changed: {len(changed_paths)} path(s); "
        f"added={len(added_paths)} removed={len(removed_paths)}"
    )
    return {
        "changed_paths": changed_paths,
        "added_paths": added_paths,
        "removed_paths": removed_paths,
        "changes": changed_values,
        "summary": summary,
    }


def _flatten_contract_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        if not value and prefix:
            flattened[prefix] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten_contract_paths(child, path))
        return flattened
    if isinstance(value, list):
        if not value and prefix:
            flattened[prefix] = []
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            flattened.update(_flatten_contract_paths(child, path))
        return flattened
    if prefix:
        flattened[prefix] = value
    return flattened


def _build_versioned_contract_ref(*, previous_ref: dict[str, Any], contract: dict[str, Any]) -> ContractRefCurrent:
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()
    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base_kind = str(previous_ref.get("kind") or "task_ticket").strip() or "task_ticket"
    base_ref = (
        str(previous_ref.get("ref") or "local://task_ticket_contract_current").strip()
        or "local://task_ticket_contract_current"
    )
    return {
        "kind": base_kind,
        "ref": f"{base_ref}@review-enriched-{version}-{digest[:12]}",
        "version": version,
        "digest": f"sha256:{digest}",
        "summary": "review enriched contract candidate",
    }


def _set_review_enriched(
    *,
    packet: dict[str, Any],
    contract_ref: ContractRefCurrent,
    actor_id: str,
    actor_name: str,
    diff_summary: str,
) -> dict[str, Any]:
    summary = "contract enriched by review agent, pending human confirmation"
    return transition_task_state(
        packet=packet,
        to_state=TaskLifecycleState.REVIEW_ENRICHED.value,
        actor=_review_agent_actor(actor_id, actor_name),
        title="review -> review_enriched",
        summary=f"{summary}; {diff_summary}",
        entry_kind=SpineEntryKind.DECISION,
        contract_ref=dict(contract_ref),
    )["packet"]


def confirm_review_enriched_contract(
    *,
    packet: dict[str, Any],
    actor: ActorRefCurrent,
    task_title: str,
    task_description: str | None,
    task_metadata: dict[str, Any],
    approval_mode: ReviewApprovalMode = "human_confirmation",
) -> dict[str, Any]:
    current_state = str(((packet or {}).get("active_frame") or {}).get("status") or "")
    if current_state != TaskLifecycleState.REVIEW_ENRICHED.value:
        raise ValueError(
            f"confirm_review_enriched_contract requires current state 'review_enriched', got '{current_state}'"
        )
    if approval_mode == "human_confirmation" and actor.get("kind") != "human":
        raise PermissionError("review_enriched -> ready requires explicit human confirmation")
    transition = transition_task_state(
        packet=packet,
        to_state=TaskLifecycleState.READY.value,
        actor=actor,
        title="review_enriched -> ready",
        summary="review-enriched contract approved for readiness",
        entry_kind=SpineEntryKind.HANDOFF,
        contract_ref=dict(packet.get("active_frame", {}).get("contract_ref") or packet.get("contract_ref") or {}),
        task_title=task_title,
        task_description=task_description,
        task_metadata=task_metadata,
    )
    if not transition["applied"]:
        raise ValueError(str((transition.get("validation") or {}).get("missing_fields") or "contract approval blocked"))
    observe_review_ready_conversion(actor_kind=str(actor.get("kind") or "unknown"))
    observe_manual_intervention(intervention_kind="review_ready_confirmation")
    return transition["packet"]


__all__ = ["ReviewContractEnrichmentService", "confirm_review_enriched_contract"]
