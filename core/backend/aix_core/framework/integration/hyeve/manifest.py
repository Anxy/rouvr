"""AIX Framework Hyeve Capability Manifest.

Sprint: F9 Hyeve Capability Layer (F9-P1)
Grounded in: manifest.yaml, Hyeve Fleet Service Architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HyeveCapabilityDefinition:
    """Descriptor for an external platform capability hosted by Hyeve Fleet."""

    capability_id: str
    service_name: str
    endpoint: str
    auth_type: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "service_name": self.service_name,
            "endpoint": self.endpoint,
            "auth_type": self.auth_type,
            "description": self.description,
        }


CANONICAL_HYEVE_CAPABILITIES: dict[str, HyeveCapabilityDefinition] = {
    "llm.chat_completion": HyeveCapabilityDefinition(
        capability_id="llm.chat_completion",
        service_name="hyeve-integration-service",
        endpoint="/api/v1/chat/completions",
        auth_type="bearer",
        description="Governed multi-model LLM chat completion gateway.",
    ),
    "codeintel.query": HyeveCapabilityDefinition(
        capability_id="codeintel.query",
        service_name="hyeve-codeintel-service",
        endpoint="/api/v1/query",
        auth_type="bearer",
        description="Codebase semantic and structural symbol indexing and search.",
    ),
    "opsbus.telemetry": HyeveCapabilityDefinition(
        capability_id="opsbus.telemetry",
        service_name="hyeve-opsbus-service",
        endpoint="/api/v1/runs/{run_id}/logs",
        auth_type="bearer",
        description="Distributed execution logging and telemetry bus.",
    ),
    "auth.token_validation": HyeveCapabilityDefinition(
        capability_id="auth.token_validation",
        service_name="hyeve-auth-service",
        endpoint="/api/v1/tokens/validate",
        auth_type="service_token",
        description="Central authentication and JWT token validation.",
    ),
    "workhub.task_context": HyeveCapabilityDefinition(
        capability_id="workhub.task_context",
        service_name="hyeve-workhub-service",
        endpoint="/api/v1/projects/{project_id}/tasks",
        auth_type="bearer",
        description="System of Record for work item lifecycle and briefing.",
    ),
}
