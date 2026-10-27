"""Typed contracts for the AIX model gateway."""
from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ModelPolicy(str, Enum):
    """Executable policy mode for model selection."""

    SLAVX_ONLY = "slavx_only"
    ALLOW_ESCALATE = "allow_escalate"
    ONLINEAI_ONLY = "onlineai_only"


class ModelProvider(str, Enum):
    """Supported model providers for M5."""

    SLAVX = "slavx"
    ONLINEAI = "onlineai"


class ModelGatewayRequest(BaseModel):
    """Canonical request for model gateway resolution."""

    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = None
    task_type: str = Field(..., min_length=1)
    risk_level: str = Field(..., min_length=1)
    model_policy: ModelPolicy
    correlation_id: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RegistryModel(BaseModel):
    """BH-backed model record used by AIX gateway."""

    id: str
    tenant_id: str
    project_id: str | None = None
    provider: ModelProvider
    model_id: str
    name: str
    is_active: bool = True
    max_tokens: int | None = None
    temperature: float | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class ModelGatewayResponse(BaseModel):
    """Canonical gateway selection response contract."""

    tenant_id: str
    correlation_id: str
    task_type: str
    risk_level: str
    model_policy: ModelPolicy
    selected_model: RegistryModel | None = None
    blocked: bool = False
    block_reason: str | None = None

    model_config = ConfigDict(extra="forbid")


class ModelGatewayProviderClient(Protocol):
    """Provider-agnostic gateway invocation contract (SG1 stub)."""

    async def invoke(self, request: ModelGatewayRequest, model: RegistryModel) -> dict[str, Any]:
        """Invoke provider model for a request and return provider payload."""
