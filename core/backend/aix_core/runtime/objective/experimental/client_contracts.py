"""Typed CLI/SDK/operator request contracts with no extra authority."""

from __future__ import annotations

from enum import StrEnum
from string import Formatter
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

CLIENT_CONTRACT_VERSION = "aix.objective-cycle.client.v1"


class ClientOperation(StrEnum):
    LIST_OBJECTIVES = "list-objectives"
    GET_OBJECTIVE = "get-objective"
    START_CYCLE = "start-cycle"
    LIST_CYCLES = "list-cycles"
    GET_CYCLE = "get-cycle"
    GET_PLAN = "get-plan"
    GET_STEPS = "get-steps"
    GET_ATTEMPTS = "get-attempts"
    GET_BUDGETS = "get-budgets"
    GET_EVENTS = "get-events"
    GET_AGENTS = "get-agents"
    GET_EVIDENCE = "get-evidence"
    GET_REVIEW_PACKET = "get-review-packet"
    CLARIFY = "clarify"
    CANCEL = "cancel"
    RESUME = "resume"
    RETRY_STEP = "retry-step"
    REPLAN = "replan"


class OperationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: ClientOperation
    method: str
    path_template: str
    command: bool
    requires_if_match: bool = False


OPERATION_CONTRACTS: dict[ClientOperation, OperationContract] = {
    ClientOperation.LIST_OBJECTIVES: OperationContract(
        operation=ClientOperation.LIST_OBJECTIVES, method="GET", path_template="/aix/objectives", command=False
    ),
    ClientOperation.GET_OBJECTIVE: OperationContract(
        operation=ClientOperation.GET_OBJECTIVE,
        method="GET",
        path_template="/aix/objectives/{objective_id}",
        command=False,
    ),
    ClientOperation.START_CYCLE: OperationContract(
        operation=ClientOperation.START_CYCLE,
        method="POST",
        path_template="/aix/objectives/{objective_id}/cycles",
        command=True,
    ),
    ClientOperation.LIST_CYCLES: OperationContract(
        operation=ClientOperation.LIST_CYCLES, method="GET", path_template="/aix/cycles", command=False
    ),
    ClientOperation.GET_CYCLE: OperationContract(
        operation=ClientOperation.GET_CYCLE, method="GET", path_template="/aix/cycles/{cycle_id}", command=False
    ),
    **{
        operation: OperationContract(
            operation=operation,
            method="GET",
            path_template=f"/aix/cycles/{{cycle_id}}/{suffix}",
            command=False,
        )
        for operation, suffix in (
            (ClientOperation.GET_PLAN, "plan"),
            (ClientOperation.GET_STEPS, "steps"),
            (ClientOperation.GET_ATTEMPTS, "attempts"),
            (ClientOperation.GET_BUDGETS, "budgets"),
            (ClientOperation.GET_EVENTS, "events"),
            (ClientOperation.GET_AGENTS, "agents"),
            (ClientOperation.GET_EVIDENCE, "evidence"),
            (ClientOperation.GET_REVIEW_PACKET, "review-packet"),
        )
    },
    **{
        operation: OperationContract(
            operation=operation,
            method="POST",
            path_template=f"/aix/cycles/{{cycle_id}}/{suffix}",
            command=True,
            requires_if_match=True,
        )
        for operation, suffix in (
            (ClientOperation.CLARIFY, "clarify"),
            (ClientOperation.CANCEL, "cancel"),
            (ClientOperation.RESUME, "resume"),
            (ClientOperation.RETRY_STEP, "retry-step"),
            (ClientOperation.REPLAN, "replan"),
        )
    },
}


class CycleClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = CLIENT_CONTRACT_VERSION
    operation: ClientOperation
    method: str
    url: str
    headers: dict[str, str]
    json_body: dict | None = None


class CycleClientContractError(ValueError):
    pass


class CycleClientRequestBuilder:
    def __init__(self, *, gateway_base_url: str, bearer_token: str, tenant_id: str) -> None:
        base_url = gateway_base_url.rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise CycleClientContractError("GATEWAY_HTTPS_REQUIRED")
        if not bearer_token.strip() or not tenant_id.strip():
            raise CycleClientContractError("CLIENT_AUTH_AND_TENANT_REQUIRED")
        self._base_url = base_url
        self._token = bearer_token.strip()
        self._tenant_id = tenant_id.strip()

    def build(
        self,
        operation: ClientOperation,
        *,
        path_parameters: dict[str, str] | None = None,
        body: dict | None = None,
        idempotency_key: str | None = None,
        expected_version: int | None = None,
    ) -> CycleClientRequest:
        contract = OPERATION_CONTRACTS[operation]
        parameters = dict(path_parameters or {})
        required_parameters = {
            field_name
            for _, field_name, _, _ in Formatter().parse(contract.path_template)
            if field_name
        }
        if required_parameters != set(parameters) or any(not str(value).strip() for value in parameters.values()):
            raise CycleClientContractError("CLIENT_PATH_PARAMETERS_INVALID")
        path = contract.path_template.format(**parameters)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Tenant-ID": self._tenant_id,
            "Accept": "application/json",
        }
        if contract.command:
            if not str(idempotency_key or "").strip():
                raise CycleClientContractError("CLIENT_IDEMPOTENCY_KEY_REQUIRED")
            headers["Idempotency-Key"] = str(idempotency_key).strip()
            headers["Content-Type"] = "application/json"
        if contract.requires_if_match:
            if expected_version is None or expected_version < 0:
                raise CycleClientContractError("CLIENT_EXPECTED_VERSION_REQUIRED")
            headers["If-Match"] = f'W/"{expected_version}"'
        return CycleClientRequest(
            operation=operation,
            method=contract.method,
            url=f"{self._base_url}{path}",
            headers=headers,
            json_body=dict(body or {}) if contract.command else None,
        )
