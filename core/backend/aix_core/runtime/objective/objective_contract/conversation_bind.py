"""C025: conversation-to-objective binding and duplicate admission defense.

DoD: greeting/follow-up/status cannot become the wrong objective; reunderstanding preserves history.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

from backend.aix_core.runtime.objective.objective_contract import ObjectiveContractV1, understand_objective
from backend.aix_core.runtime.objective.objective_contract.clarification import reunderstand_on_objective_change

MessageKind = Literal["greeting", "follow_up", "status", "objective", "unknown"]


class ConversationBindError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


_GREETING = re.compile(r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening))\b", re.I)
_STATUS = re.compile(r"^\s*(status|what'?s\s+the\s+status|where\s+are\s+we|progress)\b", re.I)
_FOLLOW = re.compile(r"^\s*(also|and\s+then|follow\s*up|additionally)\b", re.I)


def classify_message(text: str) -> MessageKind:
    t = (text or "").strip()
    if not t:
        return "unknown"
    if _GREETING.match(t) and len(t) < 40:
        return "greeting"
    if _STATUS.match(t):
        return "status"
    if _FOLLOW.match(t):
        return "follow_up"
    # Heuristic: short chit-chat without verb of work
    if len(t) < 24 and "?" in t and not any(
        w in t.lower() for w in ("add", "fix", "implement", "create", "write", "update")
    ):
        return "greeting"
    return "objective"


@dataclass
class ConversationBinding:
    conversation_id: str
    tenant_id: str
    active_objective_id: str | None = None
    contract_history: list[str] = field(default_factory=list)  # content hashes
    admitted_fingerprints: set[str] = field(default_factory=set)
    created_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "active_objective_id": self.active_objective_id,
            "contract_history": list(self.contract_history),
            "admitted_fingerprints": sorted(self.admitted_fingerprints),
            "created_at": self.created_at,
        }


def _fingerprint(tenant_id: str, objective_text: str, dod: tuple[str, ...]) -> str:
    payload = f"{tenant_id}|{objective_text.strip().lower()}|{'||'.join(x.strip().lower() for x in dod)}"
    return "fp_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class BindResult:
    action: Literal["ignore", "status", "follow_up", "admit", "reunderstand", "duplicate"]
    message_kind: MessageKind
    objective_id: str | None = None
    contract: ObjectiveContractV1 | None = None
    detail: str = ""


class ConversationObjectiveBinder:
    def __init__(self) -> None:
        self._by_conversation: dict[tuple[str, str], ConversationBinding] = {}

    def get_or_create(self, *, tenant_id: str, conversation_id: str) -> ConversationBinding:
        key = (tenant_id, conversation_id)
        if key not in self._by_conversation:
            self._by_conversation[key] = ConversationBinding(
                conversation_id=conversation_id, tenant_id=tenant_id
            )
        return self._by_conversation[key]

    def handle_message(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        text: str,
        definition_of_done: list[str] | None = None,
        objective_id: str | None = None,
    ) -> BindResult:
        if not tenant_id.strip() or not conversation_id.strip():
            raise ConversationBindError("INVALID_CONVERSATION_BIND")
        binding = self.get_or_create(tenant_id=tenant_id, conversation_id=conversation_id)
        kind = classify_message(text)

        if kind == "greeting":
            return BindResult(action="ignore", message_kind=kind, detail="greeting_not_objective")
        if kind == "unknown":
            return BindResult(action="ignore", message_kind=kind, detail="non_objective_message")
        if kind == "status":
            return BindResult(
                action="status",
                message_kind=kind,
                objective_id=binding.active_objective_id,
                detail="status_query",
            )
        if kind == "follow_up":
            if not binding.active_objective_id:
                raise ConversationBindError("FOLLOW_UP_WITHOUT_OBJECTIVE")
            return BindResult(
                action="follow_up",
                message_kind=kind,
                objective_id=binding.active_objective_id,
                detail="follow_up_bound_to_active",
            )

        # objective candidate
        dod = tuple(definition_of_done or ())
        fp = _fingerprint(tenant_id, text, dod)
        if fp in binding.admitted_fingerprints:
            return BindResult(
                action="duplicate",
                message_kind="objective",
                objective_id=binding.active_objective_id,
                detail="duplicate_admission_defense",
            )

        oid = objective_id or f"obj_{new_ulid()[:12]}"
        if binding.active_objective_id and binding.active_objective_id == oid:
            prev_hash = binding.contract_history[-1] if binding.contract_history else None
            fresh, disposition = reunderstand_on_objective_change(
                tenant_id=tenant_id,
                objective_id=oid,
                new_objective_text=text,
                definition_of_done=list(dod),
                previous=None,
            )
            binding.contract_history.append(fresh.content_hash)
            binding.admitted_fingerprints.add(fp)
            return BindResult(
                action="reunderstand" if prev_hash and prev_hash != fresh.content_hash else "admit",
                message_kind="objective",
                objective_id=oid,
                contract=fresh,
                detail=disposition,
            )

        contract = understand_objective(
            tenant_id=tenant_id,
            raw_objective=text,
            definition_of_done=list(dod),
            objective_id=oid,
        )
        binding.active_objective_id = oid
        binding.contract_history.append(contract.content_hash)
        binding.admitted_fingerprints.add(fp)
        return BindResult(
            action="admit",
            message_kind="objective",
            objective_id=oid,
            contract=contract,
            detail="admitted",
        )


__all__ = [
    "MessageKind",
    "ConversationBindError",
    "ConversationBinding",
    "BindResult",
    "ConversationObjectiveBinder",
    "classify_message",
]
