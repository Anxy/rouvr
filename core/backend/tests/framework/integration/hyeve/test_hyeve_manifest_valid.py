"""Tests for F9-P1 Hyeve Service Capability Manifest."""

from __future__ import annotations

import os
import pytest

from backend.aix_core.framework.integration.hyeve.manifest import (
    CANONICAL_HYEVE_CAPABILITIES,
    HyeveCapabilityDefinition,
)
from backend.aix_core.framework.integration.hyeve.resolver import (
    HyeveCapabilityResolver,
    get_hyeve_capability_resolver,
)


def test_canonical_manifest_resolves_all_platform_services():
    resolver = get_hyeve_capability_resolver()
    for cap_id in (
        "llm.chat_completion",
        "codeintel.query",
        "opsbus.telemetry",
        "auth.token_validation",
        "workhub.task_context",
    ):
        cap = resolver.resolve(cap_id)
        assert cap is not None
        assert cap.capability_id == cap_id
        assert len(cap.service_name) > 0
        assert cap.endpoint.startswith("/api/v1/")


def test_manifest_yaml_file_exists():
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    yaml_path = os.path.join(backend_dir, "aix_core", "framework", "integration", "hyeve", "manifest.yaml")
    assert os.path.exists(yaml_path)


def test_hyeve_capability_definition_immutability():
    cap = CANONICAL_HYEVE_CAPABILITIES["llm.chat_completion"]
    data = cap.to_dict()
    assert data["service_name"] == "hyeve-integration-service"
    assert data["auth_type"] == "bearer"
