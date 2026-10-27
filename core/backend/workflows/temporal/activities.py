from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

ActivityFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def _default_activity_impl(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "accepted",
        "payload": payload,
    }


@dataclass
class AixTemporalActivities:
    dispatch_plan_fn: ActivityFn = _default_activity_impl
    transition_task_fn: ActivityFn = _default_activity_impl
    verifier_handoff_fn: ActivityFn = _default_activity_impl
    evidence_write_fn: ActivityFn = _default_activity_impl
    _idempotency_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def dispatch_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._execute("dispatch_plan", payload, self.dispatch_plan_fn)

    async def transition_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._execute("transition_task", payload, self.transition_task_fn)

    async def verifier_handoff(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._execute("verifier_handoff", payload, self.verifier_handoff_fn)

    async def write_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._execute("write_evidence", payload, self.evidence_write_fn)

    async def _execute(
        self,
        action: str,
        payload: dict[str, Any],
        fn: ActivityFn,
    ) -> dict[str, Any]:
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key and idempotency_key in self._idempotency_cache:
            cached = dict(self._idempotency_cache[idempotency_key])
            cached["idempotent_replay"] = True
            return cached

        body = dict(payload)
        body["action"] = action
        result = await fn(body)
        normalized = {
            "ok": bool(result.get("ok", True)),
            "status": str(result.get("status") or ("completed" if result.get("ok", True) else "failed")),
            "action": action,
            "result": result,
            "idempotent_replay": False,
        }
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = dict(normalized)
        return normalized
