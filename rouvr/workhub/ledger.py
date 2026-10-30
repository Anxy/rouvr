"""rouvr/workhub/ledger.py — LedgerWorkHub: WorkHubPort over ledger/app.py.

Task ids are str(int). Responses are normalized to the contract asserted by
tests/test_ledger_contract.py."""
from __future__ import annotations

import json
import uuid

import httpx

from rouvr.errors import Code, RouvrError, from_http
from rouvr.ids import idempotency_key
from rouvr.workhub.client import canonical_task


class LedgerWorkHub:
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout_s: float = 15.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, body: dict | None = None):
        try:
            r = await self._http.request(method, path, json=body)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise RouvrError(Code.TRANSPORT, type(e).__name__) from e
        payload = r.json() if r.content else {}
        if r.status_code == 404:
            raise RouvrError(Code.CONTRACT, f"not found: {path}")
        if r.status_code >= 400:
            code = payload.get("code") if isinstance(payload, dict) else None
            raise from_http(r.status_code, {"code": code or "", "message": str(payload.get("detail", payload))})
        return payload

    async def create_task(self, title: str, intent: str) -> dict:
        return await self._call("POST", "/v1/tasks", {"title": title, "intent": intent})

    async def get_task(self, task_id: str) -> dict:
        return canonical_task(await self._call("GET", f"/v1/tasks/{task_id}"))

    async def task_by_number(self, number: int) -> dict:
        return await self.get_task(str(number))  # ledger rows: the id is the number

    async def get_binding_by_number(self, number: int) -> dict:
        try:
            b = await self._call("GET", f"/v1/tasks/{number}/binding")
        except RouvrError as e:
            if e.code is Code.CONTRACT:  # no active assignment: expected signal, not an error
                return {}
            raise
        return {"assignment_id": str(b["assignment"]["id"]), "lease_epoch": int(b["lease"]["lease_epoch"]),
                "holder": b["lease"]["holder"]}

    async def claim(self, task_id: str, principal: str, attempt_no: int, ttl_s: int) -> dict:
        r = await self._call("POST", f"/v1/tasks/{task_id}/assignments/claim",
                             {"principal": principal, "attempt_no": attempt_no, "ttl_s": ttl_s,
                              "key": f"rouvr-ledger-{uuid.uuid4().hex}"})
        return {"assignment_id": str(r["assignment"]["id"]), "lease_epoch": int(r["lease_epoch"]),
                "expires_at": str(r["lease"]["expires_at"])}

    async def open_attempt(self, task_id: str, attempt_no: int, epoch: int, assignment_id: str = "") -> dict:
        r = await self._call("POST", f"/v1/tasks/{task_id}/attempts",
                             {"attempt_no": attempt_no, "lease_epoch": epoch, "assignment_id": assignment_id,
                              "key": f"rouvr-ledger-{uuid.uuid4().hex}"})
        return {"attempt_id": str(r["attempt"]["id"]), "attempt_no": int(r["attempt"]["attempt_no"]),
                "lease_epoch": int(r["attempt"]["lease_epoch"])}

    async def renew_lease(self, task_id: str, assignment_id: str, epoch: int, ttl_s: int, beat: int) -> dict:
        binding = await self.get_binding_by_number(int(task_id))
        if not binding:
            raise RouvrError(Code.CONTRACT, "renew with no active lease")
        r = await self._call("POST", f"/v1/tasks/{task_id}/leases/renew",
                             {"holder": binding["holder"], "expected_lease_epoch": epoch, "ttl_s": ttl_s,
                              "key": f"rouvr-ledger-{uuid.uuid4().hex}"})
        return {"lease_epoch": int(r["lease"]["lease_epoch"]), "expires_at": r["lease"]["expires_at"]}

    async def reclaim_dead(self, task_id: str, attempt_no: int, holder: str = "") -> dict:
        r = await self._call("POST", f"/v1/tasks/{task_id}/assignments/reclaim",
                             {"key": f"rouvr-ledger-{uuid.uuid4().hex}"})
        return {"reclaimed": bool(r["reclaimed"])}

    async def attach_evidence(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> dict:
        return await self._call("POST", f"/v1/tasks/{task_id}/evidence",
                                {"kind": evidence_type, "body": {"fence": fence, "payload": payload}})

    async def list_evidence(self, task_id: str) -> list[dict]:
        task = await self.get_task(task_id)
        out = []
        for row in task.get("evidence", []):
            body = row.get("body", {})
            body = json.loads(body) if isinstance(body, str) else body
            out.append({"id": str(row["id"]), "type": row.get("kind"), "fence": body.get("fence"),
                        "payload": body.get("payload")})
        return out

    async def attach_evidence_exact(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> str:
        # Ledger has no server-side fence key: dedupe here so the observable
        # contract matches stage (409 duplicate fence => same readback row).
        matches = [i for i in await self.list_evidence(task_id)
                   if i["type"] == evidence_type and i["fence"] == fence]
        if len(matches) == 1:
            return matches[0]["id"]
        if not matches:
            await self.attach_evidence(task_id, evidence_type, fence, payload)
            matches = [i for i in await self.list_evidence(task_id)
                       if i["type"] == evidence_type and i["fence"] == fence]
        if len(matches) != 1:
            raise RouvrError(Code.EVIDENCE, f"expected 1 {evidence_type}/{fence}, found {len(matches)}")
        return matches[0]["id"]

    async def record_result(self, task_id: str, attempt_id: str, epoch: int, result: dict) -> dict:
        verdict = result.get("verdict") or result.get("status") or "recorded"
        r = await self._call("POST", f"/v1/tasks/{task_id}/results",
                             {"attempt_id": int(attempt_id), "lease_epoch": epoch,
                              "verdict": verdict, "result": result,
                              "key": f"rouvr-ledger-{uuid.uuid4().hex}"})
        return {"result_id": str(r["result"]["id"])}

    async def evaluate(self, task_id: str, contract_rev: str) -> dict:
        raise NotImplementedError("R9: reviewer evaluation")

    async def transition_batch(self, items: list[dict], key_parts: tuple) -> dict:
        moved = []
        for i, it in enumerate(items):
            r = await self._call("POST", f"/v1/tasks/{it['task_id']}/transition",
                                 {"to": it["status"], "note": it.get("reason"),
                                  "idempotency_key": idempotency_key("transition", *key_parts, i)})
            moved.append(r.get("replayed", False))
        return {"moved": moved}
