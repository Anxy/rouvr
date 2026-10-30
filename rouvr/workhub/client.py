"""rouvr/workhub/client.py — async WorkHub client. Every mutation carries Idempotency-Key."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from rouvr.errors import Code, RouvrError, from_http
from rouvr.ids import idempotency_key
from rouvr.workhub import routes as R


class TokenSource:
    """Fetches + caches a service token (POST /v1/auth/service-tokens). Credentials come from the
    persona file on the host, never from workflow inputs.

    Mint shape is the stage-proven one (lane/tools/wh_direct.py::service_login):
    {credential_id, client_secret, audience} + X-Hyeve-Tenant-Id header → {access_token}.
    The auth service lives on a different host than WorkHub, so auth_base is explicit."""

    def __init__(self, http: httpx.AsyncClient, credential_id: str, client_secret: str,
                 *, auth_base: str, tenant: str, audience: str) -> None:
        self._http, self._id, self._secret = http, credential_id, client_secret
        self._auth_base, self._tenant, self._audience = auth_base.rstrip("/"), tenant, audience
        self._token: str | None = None
        self._exp = 0.0

    async def get(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = await self._http.post(self._auth_base + R.AUTH_SERVICE_TOKEN,
                                  json={"credential_id": self._id, "client_secret": self._secret,
                                        "audience": self._audience},
                                  headers={"X-Hyeve-Tenant-Id": self._tenant})
        if r.status_code != 200:
            raise from_http(r.status_code, _json(r))
        body = r.json()
        self._token, self._exp = body["access_token"], time.time() + int(body.get("expires_in", 900))
        return self._token


def _json(r: httpx.Response) -> dict | None:
    try:
        v = r.json()
        return v if isinstance(v, dict) else {"data": v}
    except ValueError:
        return None


def canonical_task(t: dict) -> dict:
    """One task shape for stage + ledger rows. Stage-confirmed (Oct 2026): v2
    reads key the id as task_id, criteria are top-level free strings (ids are
    positional), and contract_rev is the record_version — the only revision
    field present on both v1 and v2 reads (claims/evidence don't bump it)."""
    md = t.get("metadata_json") or {}
    contract_body: dict = {}
    for c in t.get("contracts") or []:  # ledger rows: the done-contract carries criteria
        b = c.get("body")
        try:
            b = json.loads(b) if isinstance(b, str) else b
        except ValueError:
            continue
        if isinstance(b, dict):
            contract_body = b
            break
    raw = t.get("acceptance_criteria") or md.get("acceptance_criteria") or contract_body.get(
        "acceptance_criteria") or []
    criteria = [{"id": c["id"], "text": str(c.get("text", ""))} if isinstance(c, dict)
                else {"id": f"c{i + 1}", "text": str(c)} for i, c in enumerate(raw)]
    return {**t, "id": str(t.get("id") or t.get("task_id")), "record_version": int(t.get("record_version") or 0),
            "contract_rev": str(t.get("contract_rev") or t.get("record_version") or ""),
            "description": str(t.get("description") or t.get("intent") or contract_body.get("description") or ""),
            "project_id": str(t.get("project_id") or contract_body.get("project_id") or ""),
            "acceptance_criteria": criteria}


class WorkHubClient:
    def __init__(self, base_url: str, tenant_id: str, project_id: str, token: TokenSource | str,
                 *, transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 15.0) -> None:
        self.tenant_id, self.project_id = tenant_id, project_id
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport)
        self._token = token

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self, idem: str | None) -> dict[str, str]:
        tok = self._token if isinstance(self._token, str) else await self._token.get()
        h = {"Authorization": f"Bearer {tok}", "X-Hyeve-Tenant-Id": self.tenant_id,
             "X-Project-ID": self.project_id, "Accept": "application/json"}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    async def _call(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                    idem: str | None = None) -> dict:
        if method != "GET" and not idem:
            raise RouvrError(Code.CONTRACT, f"{method} {path} without Idempotency-Key")
        try:
            r = await self._http.request(method, path, json=json, params=params, headers=await self._headers(idem))
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise RouvrError(Code.TRANSPORT, type(e).__name__) from e
        if r.status_code >= 400:
            raise from_http(r.status_code, _json(r))
        body = _json(r) or {}
        if isinstance(body, dict) and body.get("success") is True and "data" in body:
            data = body["data"]  # stage envelope; list payloads keep a dict shell for list_* readers
            return data if isinstance(data, dict) else {"data": data}
        return body

    # ---- reads
    async def task_by_number(self, number: int) -> dict:
        return canonical_task(await self._call("GET", R.R1_TASK_BY_NUMBER.format(project=self.project_id,
                                                                                number=number)))

    async def get_task(self, task_id: str) -> dict:
        return canonical_task(await self._call("GET", R.R2_TASK.format(task_id=task_id)))

    async def get_binding_by_number(self, number: int) -> dict:
        try:
            b = await self._call("GET", R.R4_BINDING_BY_NUMBER.format(project=self.project_id, number=number))
        except RouvrError as e:
            if e.details.get("workhub_code") == "assignment_binding_not_found":
                return {}  # no active assignment yet: expected signal, not an error
            raise
        return {**b, "lease_epoch": b.get("expected_lease_epoch"), "holder": b.get("holding_principal")}

    async def list_evidence(self, task_id: str) -> list[dict]:
        body = await self._call("GET", R.R6_EVIDENCE_GET.format(task_id=task_id))
        rows = body.get("items") or body.get("data") or []
        rows = rows.get("items", []) if isinstance(rows, dict) else rows
        out = []
        for r in rows:
            ej = r.get("evidence_json") or {}
            out.append({"id": str(r.get("id")), "type": r.get("evidence_type"),
                        "fence": ej.get("evidence_ref") or r.get("evidence_ref"), "payload": ej})
        return out

    async def capabilities(self) -> dict:
        return await self._call("GET", R.R9_CAPABILITIES)

    # ---- mutations (bodies verified against stage Oct 2026; holder identity is auth-bound server-side)
    async def claim(self, task_id: str, principal: str, attempt_no: int, ttl_s: int) -> dict:
        a = await self._call("POST", R.R10_CLAIM.format(project=self.project_id),
                             json={"task_id": task_id, "persona": "coder", "plan_step": "build"},
                             idem=idempotency_key("claim", task_id, attempt_no))
        lease = await self._call("POST", R.R10_LEASE_CLAIM.format(project=self.project_id),
                                 json={"task_id": task_id, "ttl_seconds": ttl_s},
                                 idem=idempotency_key("lease-claim", task_id, attempt_no))
        return {"assignment_id": str(a.get("id") or a.get("assignment_id")), "lease_epoch": int(lease["epoch"]),
                "expires_at": str(lease["expires_at"]), "holder": str(lease["holder_sub"])}

    async def renew_lease(self, task_id: str, assignment_id: str, epoch: int, ttl_s: int, beat: int) -> dict:
        r = await self._call("POST", R.R10_LEASE_RENEW.format(project=self.project_id),
                             json={"task_id": task_id, "expected_epoch": epoch, "ttl_seconds": ttl_s},
                             idem=idempotency_key("renew", task_id, assignment_id, epoch, beat))
        return {"lease_epoch": int(r["epoch"]), "expires_at": str(r["expires_at"])}

    async def open_attempt(self, task_id: str, attempt_no: int, epoch: int, assignment_id: str = "") -> dict:
        r = await self._call("POST", R.R10_ATTEMPT.format(project=self.project_id),
                             json={"task_id": task_id, "assignment_id": assignment_id},
                             idem=idempotency_key("attempt", task_id, attempt_no))
        return {"attempt_id": str(r.get("id") or r.get("attempt_id")), "attempt_no": attempt_no,
                "lease_epoch": epoch}

    async def record_result(self, task_id: str, attempt_id: str, epoch: int, result: dict) -> dict:
        digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return await self._call("POST", R.R10_RESULT.format(project=self.project_id),
                                json={"task_id": task_id, "attempt_id": attempt_id, "artifact_digest": digest,
                                      "evidence_id": result.get("evidence_id")},
                                idem=idempotency_key("result", task_id, attempt_id))

    async def attach_evidence(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> dict:
        return await self._call("POST", R.R5_EVIDENCE_POST.format(task_id=task_id),
                                json={"evidence_type": evidence_type, "evidence_ref": fence,
                                      "evidence_json": payload},
                                idem=idempotency_key("evidence", task_id, evidence_type, fence))

    async def evaluate(self, task_id: str, contract_rev: str) -> dict:
        return await self._call("POST", R.R7_EVALUATE.format(task_id=task_id), json={"contract_rev": contract_rev},
                                idem=idempotency_key("evaluate", task_id, contract_rev))

    async def transition_batch(self, items: list[dict], key_parts: tuple) -> dict:
        """items: [{task_id, status, expected_record_version, reason}] — CAS on record_version."""
        return await self._call("POST", R.R8_TRANSITIONS.format(project=self.project_id), json={"items": items},
                                idem=idempotency_key("transition", *key_parts))

    async def reclaim_dead(self, task_id: str, attempt_no: int, holder: str = "") -> dict:
        """Executor-wide sweep (scoped mode needs an assignment id we don't have pre-claim)."""
        return await self._call("POST", R.R10_RECLAIM_DEAD.format(project=self.project_id),
                                json={"executor_sub": holder, "heartbeat_stale_after_s": 90},
                                idem=idempotency_key("reclaim-dead", task_id, attempt_no))

    # ---- helpers
    async def attach_evidence_exact(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> str:
        """Attach then read back; succeed only if exactly one item with (type, fence) exists."""
        try:
            await self.attach_evidence(task_id, evidence_type, fence, payload)
        except RouvrError as e:
            if e.code is not Code.MOVED:  # 409 duplicate fence => already attached, fall through to readback
                raise
        matches = [i for i in await self.list_evidence(task_id)
                   if i.get("type") == evidence_type and i.get("fence") == fence]
        if len(matches) != 1:
            raise RouvrError(Code.EVIDENCE, f"expected 1 {evidence_type}/{fence}, found {len(matches)}")
        return str(matches[0]["id"])
