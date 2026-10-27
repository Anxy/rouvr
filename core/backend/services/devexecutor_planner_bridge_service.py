from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import socket
import subprocess
import time
from concurrent.futures import Future
from pathlib import Path
from threading import Thread
from typing import Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import ValidationError

from backend.clients.ayrpack_client import ayrpack_client
from backend.hyeve_aix_service.config import settings
from backend.models.planner_slice_packet_models import PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES, PlannerSlicePacketCurrent
from backend.services.aix_model_ingress_artifact_service import persist_model_ingress_presend_artifacts
from backend.services.aix_model_ingress_gate_service import (
    ModelIngressGateError,
    compile_model_ingress_request,
    enforce_model_ingress_gate,
)
from backend.services.aix_quality_enforcement_service import validate as validate_quality_enforcement
from backend.services.aix_run_ledger_service import get_latest_runtime_memory_capsule
from backend.services.aix_target_file_resolution_service import resolve_target_file
from backend.services.aix_v2_executable_plan_protocol_service import (
    V2ExecutablePlanProtocolError,
    is_packet_bound_single_symbol_replace_function_lane,
    validate_v2_executable_plan_protocol,
)
from backend.services.ayrpack_trace_packet_service import build_planner_trace_packet_payload
from backend.services.devexecutor_planner_request_envelope_service import (
    build_planner_request_envelope_with_accounting,
    serialize_planner_request_envelope,
)
from backend.services.devexecutor_runlog_resolver_service import get_planner_trace_root
from backend.services.devexecutor_tool_contract_registry_service import (
    build_planner_contract_text,
    get_allowed_tool_names,
)
from hyeve_core.task_execution_phase_contract import phase_timeout_seconds

# AIX autonomous lane changes must run through scripts/run_aix_lane_gauntlet.sh before deploy.
_ALLOWED_TOOLS = get_allowed_tool_names()
_IGNORABLE_STEP_STRINGS = {"tool.split"}
LOCAL_TRACE_ROOT = get_planner_trace_root()
_LOCAL_PLANNER_HOST_ALIASES = {"api-gateway", "hyeve-api-gateway-service"}
_MANAGED_PLANNER_SERVICE_HOST_ALIASES = {"integration", "hyeve-integration-service"}
_DEFAULT_RUNTIME_PLANNER_API_URL = "http://integration:8020"
_MANAGED_PLANNER_SERVICE_CONTAINER_NAMES = ("hyeve-integration-service",)
_MANAGED_PLANNER_SERVICE_COMPOSE_NAME = "integration"
_MANAGED_PLANNER_SERVICE_COMPOSE_FILE = os.environ.get(
    "AIX_MANAGED_PLANNER_COMPOSE_FILE",
    "/srv/rouvr/repos/hyeve/deploy/hostinger/docker-compose.aix-min.yml",
)
_MANAGED_PLANNER_SERVICE_COMPOSE_DIR = str(Path(_MANAGED_PLANNER_SERVICE_COMPOSE_FILE).parent)
_MANAGED_PLANNER_SERVICE_ENV_FILE = f"{_MANAGED_PLANNER_SERVICE_COMPOSE_DIR}/.env.aix-min"
_MANAGED_PLANNER_SERVICE_PROBE_ATTEMPTS = 10
_MANAGED_PLANNER_SERVICE_PROBE_SLEEP_SECONDS = 2.0
SHADOW_TRACE_ROLE = "debug_forensic_mirror"
SHADOW_TRACE_AUTHORITY = "non_canonical"
CANONICAL_OPERATOR_SOURCES = ["workhub", "run_ledger", "execution_digest"]
PLANNER_SOCKET_PREFLIGHT_TIMEOUT_SECONDS = 2.0
PLANNER_HARD_PAYLOAD_CAP_BYTES = 96_000
MUTATION_PLANNER_PACKET_CAP_BYTES = 32_000
PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION = "packet_bound_replace_function"
PLANNER_MODE_GENERIC = "generic"
logger = logging.getLogger(__name__)
MAX_JSON_REPAIRS = 2
MAX_SEMANTIC_REPAIRS = 1
PLANNER_JSON_MAX_TOKENS = 4096
PLANNER_DEFAULT_TIMEOUT_SECONDS = max(1, int(math.ceil(float(phase_timeout_seconds("plan")))))
_PLANNER_DISALLOWED_MODEL_MARKERS = ("nemotron",)
_PLANNER_DISALLOWED_PROVIDER_MARKERS = ("observer",)
_PLANNER_SEARCH_GROUNDED_TOTAL = 0
_PLANNER_SEARCH_GROUNDED_FALSE = 0
EDIT_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "args": {"type": "object"},
                        },
                        "required": ["tool", "args"],
                        "additionalProperties": True,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "read_range"},
                            "file": {"type": "string"},
                            "symbol": {"type": "string"},
                            "context_lines": {"type": "integer"},
                            "step_idx": {"type": "integer"},
                            "output_content": {"type": "string"},
                        },
                        "required": ["type", "file"],
                        "additionalProperties": True,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "edit_file"},
                            "file": {"type": "string"},
                            "grounding_source": {"type": "string"},
                            "read_step_idx": {"type": "integer"},
                            "search_range": {
                                "type": "object",
                                "properties": {
                                    "start_line": {"type": "integer", "minimum": 1},
                                    "end_line": {"type": "integer", "minimum": 1},
                                },
                                "required": ["start_line", "end_line"],
                                "additionalProperties": False,
                            },
                            "edit_range_within_read": {
                                "type": "object",
                                "properties": {
                                    "start_line": {"type": "integer", "minimum": 1},
                                    "end_line": {"type": "integer", "minimum": 1},
                                },
                                "required": ["start_line", "end_line"],
                                "additionalProperties": False,
                            },
                            "replacement_text": {"type": "string"},
                        },
                        "required": ["type", "file", "grounding_source", "replacement_text"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "replace_function"},
                            "file": {"type": "string"},
                            "qualified_symbol": {"type": "string"},
                            "target_symbol": {"type": "string"},
                            "edit_intent": {"type": "string"},
                            "preserve_constraints": {"type": "array", "items": {"type": "string"}},
                            "line_targets": {"type": "array", "items": {"type": "string"}},
                            "replacement": {"type": "string"},
                        },
                        "required": ["type", "file"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "failure_analysis"},
                            "reason_code": {"const": "INSUFFICIENT_CONTEXT_FOR_MUTATION"},
                            "detail": {"type": "object"},
                        },
                        "required": ["type", "reason_code", "detail"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "run_validation_command"},
                            "command": {"type": "string"},
                        },
                        "required": ["type", "command"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "tool": {"const": "cmd.run"},
                            "args": {
                                "type": "object",
                                "properties": {
                                    "command_text": {"type": "string"},
                                    "timeout_seconds": {"type": "number"},
                                },
                                "required": ["command_text"],
                                "additionalProperties": True,
                            },
                        },
                        "required": ["tool", "args"],
                        "additionalProperties": True,
                    },
                ]
            },
        }
    },
    "required": ["steps"],
    "additionalProperties": False,
}


class PlannerBridgeError(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class PlannerModelGateway(Protocol):
    def request_payload(
        self,
        *,
        tenant_id: str,
        project_id: str,
        correlation_id: str,
        workspace_root: str,
        body: dict[str, Any],
        authorization: str | None,
        timeout_seconds: int | None,
        max_retries: int | None,
        retry_backoff_ms: int | None,
        require_explicit_runtime_base: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        ...


class HttpPlannerModelGateway:
    def request_payload(
        self,
        *,
        tenant_id: str,
        project_id: str,
        correlation_id: str,
        workspace_root: str,
        body: dict[str, Any],
        authorization: str | None,
        timeout_seconds: int | None,
        max_retries: int | None,
        retry_backoff_ms: int | None,
        require_explicit_runtime_base: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        planner_base_resolution = _planner_base_resolution(
            workspace_root=workspace_root,
            require_explicit_runtime_base=require_explicit_runtime_base,
        )
        base_url = str(planner_base_resolution.get("resolved_base_url") or "").rstrip("/")
        target_url = f"{base_url}/api/integrations/llm/chat"
        fallback_target_url = _planner_local_retry_target_url(target_url=target_url, workspace_root=workspace_root)
        headers = {
            "Authorization": _planner_auth_header(authorization=authorization),
            "X-Tenant-ID": tenant_id,
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/json",
            "Connection": "close",
        }
        resolved_timeout_seconds = _resolve_planner_timeout_seconds(timeout_seconds)
        resolved_max_retries = max(
            0,
            int(max_retries if max_retries is not None else settings.RUNTIME_PLANNER_MAX_RETRIES),
        )
        backoff_ms = max(
            0,
            int(retry_backoff_ms if retry_backoff_ms is not None else settings.RUNTIME_PLANNER_RETRY_BACKOFF_MS),
        )
        try:
            body = enforce_model_ingress_gate(
                body,
                source="aix.planner_bridge",
                allow_tool_calls=False,
            )
        except ModelIngressGateError as exc:
            raise PlannerBridgeError(str(exc), diagnostics=dict(exc.diagnostics)) from exc

        request_build_started = time.perf_counter()
        payload_json = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
        payload_bytes = len(payload_json.encode("utf-8"))
        request_build_ms = max(0, int(round((time.perf_counter() - request_build_started) * 1000)))
        if payload_bytes > PLANNER_HARD_PAYLOAD_CAP_BYTES:
            raise PlannerBridgeError(
                "PLANNER_PAYLOAD_CAP_EXCEEDED"
                f" payload_bytes={payload_bytes}"
                f" cap_bytes={PLANNER_HARD_PAYLOAD_CAP_BYTES}"
                f" provider={body.get('provider')}"
                f" model={body.get('model')}",
                diagnostics={
                    "target_url": target_url,
                    **planner_base_resolution,
                    "project_id": project_id,
                    "payload_bytes": payload_bytes,
                    "payload_cap_bytes": PLANNER_HARD_PAYLOAD_CAP_BYTES,
                    "request_build_ms": request_build_ms,
                    "transport_attempts": [],
                    "transport_phase_timings": {
                        "request_build_ms": request_build_ms,
                        "socket_preflight_ms_total": 0,
                        "http_request_ms_total": 0,
                        "response_parse_ms_total": 0,
                        "retry_sleep_ms_total": 0,
                    },
                },
            )
        payload: dict[str, Any] | None = None
        attempts_used = 0
        configured_retries_used = 0
        fresh_connect_retry_used = False
        local_route_retry_used = False
        planner_service_recovery_used = False
        preflight_ok = False
        preflight_error: str | None = None
        preflight_attempted = False
        preflight_skipped = False
        last_transport_code = ""
        last_failure_phase: str | None = None
        transport_attempts: list[dict[str, Any]] = []
        planner_service_recovery: dict[str, Any] | None = None
        socket_preflight_ms_total = 0
        http_request_ms_total = 0
        response_parse_ms_total = 0
        retry_sleep_ms_total = 0

        def _transport_diag_snapshot() -> dict[str, Any]:
            body_metadata = dict(body.get("metadata") or {})
            return {
                "target_url": target_url,
                **planner_base_resolution,
                "timeout_seconds": resolved_timeout_seconds,
                "max_retries": resolved_max_retries,
                "retry_backoff_ms": backoff_ms,
                "attempts": attempts_used,
                "project_id": project_id,
                "payload_bytes": payload_bytes,
                "payload_cap_bytes": PLANNER_HARD_PAYLOAD_CAP_BYTES,
                "configured_retries_used": configured_retries_used,
                "fresh_connect_retry_used": fresh_connect_retry_used,
                "local_route_retry_used": local_route_retry_used,
                "planner_service_recovery_used": planner_service_recovery_used,
                "planner_service_recovery": dict(planner_service_recovery or {}) if planner_service_recovery else None,
                "socket_preflight_attempted": preflight_attempted,
                "socket_preflight_skipped": preflight_skipped,
                "socket_preflight_ok": preflight_ok,
                "socket_preflight_error": preflight_error,
                "transport_failure_code": last_transport_code or None,
                "failure_phase": last_failure_phase,
                "request_build_ms": request_build_ms,
                "model_ingress_artifact_refs": dict(body_metadata.get("model_ingress_artifact_refs") or {}),
                "prompt_token_breakdown": dict(body_metadata.get("prompt_token_breakdown") or {}),
                "transport_attempts": list(transport_attempts),
                "transport_phase_timings": {
                    "request_build_ms": request_build_ms,
                    "socket_preflight_ms_total": socket_preflight_ms_total,
                    "http_request_ms_total": http_request_ms_total,
                    "response_parse_ms_total": response_parse_ms_total,
                    "retry_sleep_ms_total": retry_sleep_ms_total,
                },
            }

        if bool(planner_base_resolution.get("guard_blocked")):
            last_transport_code = "PLANNER_RUNTIME_BASE_URL_REQUIRED"
            last_failure_phase = "base_url_resolution"
            preflight_error = str(planner_base_resolution.get("guard_reason") or "hybrid_core_fallback_blocked")
            raise PlannerBridgeError(
                "PLANNER_RUNTIME_BASE_URL_REQUIRED "
                "detail=hybrid_host_workspace_requires_explicit_runtime_planner_api_url",
                diagnostics=_transport_diag_snapshot(),
            )

        if not bool(planner_base_resolution.get("planner_base_fallback_used")):
            planner_service_recovery = _ensure_managed_planner_service_ready(
                target_url=target_url,
                timeout_seconds=min(5.0, float(resolved_timeout_seconds)),
            )
            planner_service_recovery_used = bool((planner_service_recovery or {}).get("recovered"))

        while True:
            attempts_used += 1
            attempt_diag: dict[str, Any] = {
                "attempt": attempts_used,
                "preflight_phase": None,
                "socket_preflight_ms": 0,
                "http_request_ms": 0,
                "response_parse_ms": 0,
                "retry_sleep_ms": 0,
                "failure_phase": None,
                "transport_failure_code": None,
            }
            try:
                preflight_ok = False
                preflight_error = None
                preflight_attempted = False
                preflight_skipped = False
                if _should_skip_socket_preflight(workspace_root=workspace_root, target_url=target_url):
                    preflight_skipped = True
                    preflight_error = "skipped_internal_planner_transport"
                    attempt_diag["preflight_phase"] = "skipped"
                else:
                    preflight_attempted = True
                    preflight_started = time.perf_counter()
                    _planner_socket_preflight(
                        target_url=target_url,
                        timeout_seconds=PLANNER_SOCKET_PREFLIGHT_TIMEOUT_SECONDS,
                    )
                    preflight_elapsed_ms = max(0, int(round((time.perf_counter() - preflight_started) * 1000)))
                    attempt_diag["preflight_phase"] = "completed"
                    attempt_diag["socket_preflight_ms"] = preflight_elapsed_ms
                    socket_preflight_ms_total += preflight_elapsed_ms
                    preflight_ok = True
                with _planner_http_client(timeout_seconds=resolved_timeout_seconds) as client:
                    request_started = time.perf_counter()
                    response = client.post(target_url, headers=headers, json=body)
                    response.raise_for_status()
                    request_elapsed_ms = max(0, int(round((time.perf_counter() - request_started) * 1000)))
                    attempt_diag["http_request_ms"] = request_elapsed_ms
                    http_request_ms_total += request_elapsed_ms
                    parse_started = time.perf_counter()
                    payload = response.json()
                    parse_elapsed_ms = max(0, int(round((time.perf_counter() - parse_started) * 1000)))
                    attempt_diag["response_parse_ms"] = parse_elapsed_ms
                    response_parse_ms_total += parse_elapsed_ms
                    last_transport_code = ""
                    last_failure_phase = None
                    transport_attempts.append(attempt_diag)
                    break
            except PlannerBridgeError as exc:
                last_transport_code = _planner_transport_error_code(exc)
                preflight_error = str(exc)
                last_failure_phase = _planner_transport_failure_phase(exc)
                attempt_diag["failure_phase"] = last_failure_phase
                attempt_diag["transport_failure_code"] = last_transport_code or None
                transport_attempts.append(attempt_diag)
                if (
                    _is_transient_name_resolution_error(exc)
                    and not local_route_retry_used
                    and fallback_target_url != target_url
                ):
                    local_route_retry_used = True
                    target_url = fallback_target_url
                    continue
                recovered_service = _ensure_managed_planner_service_ready(
                    target_url=target_url,
                    timeout_seconds=min(5.0, float(resolved_timeout_seconds)),
                )
                if bool((recovered_service or {}).get("recovered")):
                    planner_service_recovery = recovered_service
                    planner_service_recovery_used = True
                    continue
                if last_transport_code == "PLANNER_TRANSPORT_STALE_SOCKET" and not fresh_connect_retry_used:
                    fresh_connect_retry_used = True
                    continue
                if configured_retries_used >= resolved_max_retries:
                    raise
                configured_retries_used += 1
                sleep_seconds = (backoff_ms * configured_retries_used) / 1000.0
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                retry_sleep_ms = max(0, int(round(sleep_seconds * 1000)))
                attempt_diag["retry_sleep_ms"] = retry_sleep_ms
                retry_sleep_ms_total += retry_sleep_ms
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                transport_code = _planner_transport_error_code(exc)
                last_transport_code = transport_code
                last_failure_phase = _planner_transport_failure_phase(exc)
                attempt_diag["failure_phase"] = last_failure_phase
                attempt_diag["transport_failure_code"] = transport_code
                transport_attempts.append(attempt_diag)
                if (
                    _is_transient_name_resolution_error(exc)
                    and not local_route_retry_used
                    and fallback_target_url != target_url
                ):
                    local_route_retry_used = True
                    target_url = fallback_target_url
                    continue
                recovered_service = _ensure_managed_planner_service_ready(
                    target_url=target_url,
                    timeout_seconds=min(5.0, float(resolved_timeout_seconds)),
                )
                if bool((recovered_service or {}).get("recovered")):
                    planner_service_recovery = recovered_service
                    planner_service_recovery_used = True
                    continue
                if transport_code == "PLANNER_TRANSPORT_STALE_SOCKET" and not fresh_connect_retry_used:
                    fresh_connect_retry_used = True
                    continue
                if configured_retries_used >= resolved_max_retries:
                    raise PlannerBridgeError(
                        f"{transport_code} error={exc} attempts={attempts_used}",
                        diagnostics=_transport_diag_snapshot(),
                    ) from exc
                configured_retries_used += 1
                sleep_seconds = (backoff_ms * configured_retries_used) / 1000.0
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                retry_sleep_ms = max(0, int(round(sleep_seconds * 1000)))
                attempt_diag["retry_sleep_ms"] = retry_sleep_ms
                retry_sleep_ms_total += retry_sleep_ms
            except Exception as exc:
                raise PlannerBridgeError(
                    f"PLANNER_UPSTREAM_REQUEST_FAILED error={exc}",
                    diagnostics=_transport_diag_snapshot(),
                ) from exc

        if not isinstance(payload, dict):
            raise PlannerBridgeError(
                "PLANNER_UPSTREAM_REQUEST_FAILED error=empty_payload",
                diagnostics=_transport_diag_snapshot(),
            )
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            raise PlannerBridgeError(
                "PLANNER_INVALID_RESPONSE data must be an object",
                diagnostics=_transport_diag_snapshot(),
            )
        return data, _transport_diag_snapshot()


def build_planner_model_gateway() -> PlannerModelGateway:
    return HttpPlannerModelGateway()


def _planner_http_client(*, timeout_seconds: int) -> httpx.Client:
    resolved_timeout_seconds = max(1.0, float(timeout_seconds))
    connect_timeout_seconds = min(10.0, resolved_timeout_seconds)
    write_timeout_seconds = min(30.0, resolved_timeout_seconds)
    try:
        return httpx.Client(
            timeout=httpx.Timeout(
                resolved_timeout_seconds,
                connect=connect_timeout_seconds,
                read=resolved_timeout_seconds,
                write=write_timeout_seconds,
                pool=connect_timeout_seconds,
            ),
            http2=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0, keepalive_expiry=0.0),
            trust_env=False,
        )
    except TypeError:
        return httpx.Client(timeout=resolved_timeout_seconds)


def _resolve_planner_timeout_seconds(timeout_seconds: int | float | None) -> int:
    if timeout_seconds is None:
        return PLANNER_DEFAULT_TIMEOUT_SECONDS
    try:
        numeric = float(timeout_seconds)
    except (TypeError, ValueError):
        numeric = float(settings.RUNTIME_PLANNER_TIMEOUT_SECONDS)
    if numeric <= 0:
        return 1
    return max(1, int(math.ceil(numeric)))


def _planner_model_guard(*, provider: str | None, model: str | None) -> None:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip().lower()
    if any(marker in normalized_provider for marker in _PLANNER_DISALLOWED_PROVIDER_MARKERS):
        raise PlannerBridgeError(
            f"PLANNER_MODEL_ROUTE_FORBIDDEN provider={provider} model={model} reason=provider_not_allowed_for_planner"
        )
    if any(marker in normalized_model for marker in _PLANNER_DISALLOWED_MODEL_MARKERS):
        raise PlannerBridgeError(
            f"PLANNER_MODEL_ROUTE_FORBIDDEN provider={provider} model={model} reason=model_not_allowed_for_planner"
        )


def _extract_normalized_usage(data: dict[str, Any]) -> dict[str, Any]:
    response_json = data.get("response_json")
    usage = data.get("usage")
    if not isinstance(usage, dict) and isinstance(response_json, dict):
        usage = response_json.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    completion_details = usage.get("completion_tokens_details")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    reasoning_tokens = completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    finish_reason = None
    if isinstance(response_json, dict):
        choices = response_json.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = str(choices[0].get("finish_reason") or "").strip() or None
    return {
        "prompt_tokens": int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
        "completion_tokens": int(completion_tokens) if isinstance(completion_tokens, int) else None,
        "total_tokens": int(total_tokens) if isinstance(total_tokens, int) else None,
        "reasoning_tokens": int(reasoning_tokens) if isinstance(reasoning_tokens, int) else None,
        "finish_reason": finish_reason,
    }


def _validate_inbound_planner_packet(planner_packet: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(planner_packet, dict) or not planner_packet:
        return None
    try:
        return PlannerSlicePacketCurrent.model_validate(planner_packet).model_dump(mode="json")
    except ValidationError as exc:
        raise PlannerBridgeError(f"PLANNER_PACKET_INVALID error={exc}") from exc


def _resolve_planner_mode(
    *,
    planner_mode: str | None = None,
    planner_packet: dict[str, Any] | None = None,
) -> str:
    explicit = str(planner_mode or "").strip()
    if explicit in {PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION, PLANNER_MODE_GENERIC}:
        return explicit
    packet_mode = str((dict(planner_packet or {})).get("planner_mode") or "").strip()
    if packet_mode in {PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION, PLANNER_MODE_GENERIC}:
        return packet_mode
    return PLANNER_MODE_GENERIC


def _assert_v2_planner_packet_boundary(planner_packet: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], int]:
    raw_packet = dict(planner_packet or {})
    if not raw_packet:
        raise PlannerBridgeError("PLANNER_PACKET_REQUIRED")
    if str(raw_packet.get("schema_name") or "").strip() != "planner_slice.current":
        raise PlannerBridgeError("BUILDER_CURRENT_PACKET_REQUIRED")
    if any(
        field in raw_packet
        for field in (
            "grounded_symbol_source",
            "codeintel_anchor",
            "implementation_hints",
            "mutation_instructions",
            "hard_invariants",
            "stop_conditions",
            "constraints",
            "capability_contract",
        )
    ):
        raise PlannerBridgeError("BUILDER_RICH_CONTEXT_CONTAMINATION")
    validated = _validate_inbound_planner_packet(planner_packet)
    if not isinstance(validated, dict) or not validated:
        raise PlannerBridgeError("PLANNER_PACKET_REQUIRED")
    if str(validated.get("schema_name") or "").strip() != "planner_slice.current":
        raise PlannerBridgeError("BUILDER_CURRENT_PACKET_REQUIRED")
    audit = PlannerSlicePacketCurrent.model_validate(validated).build_audit()
    if list(audit.get("forbidden_fields") or []):
        raise PlannerBridgeError("BUILDER_RICH_CONTEXT_CONTAMINATION")
    anchor = dict((validated.get("allowed_surface") or {}).get("anchor") or {})
    compact_snippet = str(anchor.get("compact_snippet") or "")
    if not compact_snippet.strip():
        raise PlannerBridgeError("PLANNER_PACKET_INVALID error=allowed_surface.anchor.compact_snippet_missing")
    if len(compact_snippet.encode("utf-8")) > 800 or len(compact_snippet.splitlines()) > 10:
        raise PlannerBridgeError("PLANNER_PACKET_INVALID error=allowed_surface.anchor.compact_snippet_out_of_bounds")
    serialized_bytes = PlannerSlicePacketCurrent.model_validate(validated).serialized_bytes()
    if serialized_bytes > PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES:
        raise PlannerBridgeError(
            f"PLANNER_PACKET_INVALID error=planner_packet_exceeds_{PLANNER_SLICE_PACKET_CURRENT_MAX_BYTES}_bytes"
        )
    return validated, audit, serialized_bytes


def _planner_packet_fingerprint(planner_packet: dict[str, Any] | None) -> str | None:
    if not isinstance(planner_packet, dict) or not planner_packet:
        return None
    normalized = json.dumps(planner_packet, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _contains_legacy_edit_guidance(text: Any) -> bool:
    normalized = str(text or "").lower()
    return (
        "args.search_replace_blocks" in normalized
        or "legacy tool-and-args edit payload" in normalized
        or "raw search" in normalized
    )


def _sanitize_planner_packet_for_ast_contract(planner_packet: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(planner_packet, ensure_ascii=True))
    if str(sanitized.get("schema_name") or "").strip() == "planner_slice.current":
        return sanitized
    implementation_hints = sanitized.get("implementation_hints")
    filtered_hints: list[str] = []
    if isinstance(implementation_hints, list):
        filtered_hints = [str(hint) for hint in implementation_hints if not _contains_legacy_edit_guidance(hint)]
    ast_hint = (
        "Mutation: Read before you write on existing files with read_range, then emit only structured JSON steps. "
        "Use a grounded structural patch for edits: emit "
        '{"type":"edit_file","file":str,"grounding_source":"anchor","search_range":{"start_line":int,"end_line":int},"replacement_text":str} '
        "so the bridge can derive the exact preimage directly from the live file. Textual SEARCH/REPLACE payloads are forbidden. "
        "For exact anchor windows, the search_range must cover the full read_range.output_content and the plan must end with run_validation_command copied from planner_packet.validation_command."
    )
    if ast_hint not in filtered_hints:
        filtered_hints.insert(0, ast_hint)
    sanitized["implementation_hints"] = filtered_hints
    mutation_instructions = sanitized.get("mutation_instructions")
    if _contains_legacy_edit_guidance(mutation_instructions):
        sanitized["mutation_instructions"] = (
            "Emit JSON-only structured planner steps. Use search_range plus replacement_text for anchor-grounded edits, or read_range plus edit_range_within_read plus replacement_text for read-grounded edits. "
            "End with run_validation_command copied from planner_packet.validation_command. "
            "If the change cannot be grounded safely, return failure_analysis with INSUFFICIENT_CONTEXT_FOR_MUTATION."
        )
    return sanitized


def _canonicalize_generic_packet_for_request_envelope(planner_packet: dict[str, Any]) -> dict[str, Any]:
    packet = json.loads(json.dumps(planner_packet, ensure_ascii=True))
    grounded_symbol_source = str(packet.get("grounded_symbol_source") or "")
    if grounded_symbol_source.strip():
        codeintel_anchor = (
            dict(packet.get("codeintel_anchor") or {}) if isinstance(packet.get("codeintel_anchor"), dict) else {}
        )
        if codeintel_anchor and not str(codeintel_anchor.get("output_content") or "").strip():
            codeintel_anchor["output_content"] = grounded_symbol_source
            packet["codeintel_anchor"] = codeintel_anchor
        packet.pop("grounded_symbol_source", None)
    return packet


def _shadow_trace_contract() -> dict[str, Any]:
    return {
        "role": SHADOW_TRACE_ROLE,
        "authority": SHADOW_TRACE_AUTHORITY,
        "required_for_control_plane": False,
        "canonical_operator_sources": list(CANONICAL_OPERATOR_SOURCES),
    }


def _is_hybrid_host_workspace(workspace_root: str) -> bool:
    root = str(workspace_root or "").strip().replace("\\", "/")
    return bool(re.match(r"^[A-Za-z]:/", root))


def _planner_base_resolution(*, workspace_root: str, require_explicit_runtime_base: bool = False) -> dict[str, Any]:
    override_raw = str(settings.RUNTIME_PLANNER_API_URL or "").strip()
    raw_base = str(override_raw or _DEFAULT_RUNTIME_PLANNER_API_URL).rstrip("/")
    if raw_base.lower().endswith("/api/v1"):
        raw_base = raw_base[: -len("/api/v1")]
    parsed = urlsplit(raw_base)
    host = (parsed.hostname or "").strip().lower()
    is_hybrid_workspace = _is_hybrid_host_workspace(workspace_root)
    fallback_used = not bool(override_raw)
    local_alias_rewritten = is_hybrid_workspace and host in _LOCAL_PLANNER_HOST_ALIASES
    if local_alias_rewritten:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        netloc = f"127.0.0.1:{port}"
        resolved_base_url = urlunsplit(
            (parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment)
        ).rstrip("/")
    else:
        resolved_base_url = raw_base
    guard_blocked = bool(local_alias_rewritten and fallback_used and require_explicit_runtime_base)
    return {
        "planner_base_url_raw": raw_base,
        "planner_base_url_resolved": resolved_base_url,
        "planner_base_override_set": not fallback_used,
        "planner_base_fallback_used": fallback_used,
        "planner_base_fallback_source": (
            "DEFAULT_RUNTIME_PLANNER_API_URL" if fallback_used else "RUNTIME_PLANNER_API_URL"
        ),
        "planner_base_host": host,
        "planner_base_local_alias_rewritten": local_alias_rewritten,
        "guard_blocked": guard_blocked,
        "guard_reason": ("hybrid_core_fallback_blocked" if guard_blocked else None),
        "resolved_base_url": resolved_base_url,
    }


def _resolve_planner_base_url(*, workspace_root: str) -> str:
    return str(_planner_base_resolution(workspace_root=workspace_root).get("resolved_base_url") or "").rstrip("/")


def _planner_local_retry_target_url(*, target_url: str, workspace_root: str) -> str:
    parsed = urlsplit(str(target_url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    if host not in _LOCAL_PLANNER_HOST_ALIASES or not _is_hybrid_host_workspace(workspace_root):
        return str(target_url or "").strip()
    port = parsed.port or (443 if (parsed.scheme or "http").lower() == "https" else 80)
    netloc = f"127.0.0.1:{port}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment)).rstrip("/")


def _planner_target_host_port(*, target_url: str) -> tuple[str, int]:
    parsed = urlsplit(str(target_url or "").strip())
    scheme = str(parsed.scheme or "http").lower()
    host = str(parsed.hostname or "").strip() or "127.0.0.1"
    if parsed.port is not None:
        return host, int(parsed.port)
    return host, (443 if scheme == "https" else 80)


def _planner_service_health_url(*, target_url: str) -> str:
    parsed = urlsplit(str(target_url or "").strip())
    scheme = str(parsed.scheme or "http").lower() or "http"
    host = str(parsed.hostname or "").strip() or "127.0.0.1"
    port = parsed.port or (443 if scheme == "https" else 80)
    return urlunsplit((scheme, f"{host}:{port}", "/health", "", "")).rstrip("/")


def _probe_planner_service_health(*, target_url: str, timeout_seconds: float) -> dict[str, Any]:
    health_url = _planner_service_health_url(target_url=target_url)
    try:
        with httpx.Client(timeout=max(1.0, float(timeout_seconds)), trust_env=False) as client:
            response = client.get(health_url)
        payload: dict[str, Any] | None = None
        try:
            if response.headers.get("content-type", "").lower().startswith("application/json"):
                decoded = response.json()
                payload = decoded if isinstance(decoded, dict) else None
        except Exception:
            payload = None
        return {
            "healthy": response.status_code == 200,
            "status_code": int(response.status_code),
            "health_url": health_url,
            "payload": payload,
        }
    except Exception as exc:
        return {
            "healthy": False,
            "status_code": None,
            "health_url": health_url,
            "error": str(exc),
        }


def _run_planner_service_recovery_command(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "command": list(command),
            "returncode": int(completed.returncode),
            "stdout": str(completed.stdout or "").strip() or None,
            "stderr": str(completed.stderr or "").strip() or None,
        }
    except Exception as exc:
        return {
            "command": list(command),
            "returncode": None,
            "stdout": None,
            "stderr": str(exc),
        }


def _probe_planner_service_until_ready(*, target_url: str, timeout_seconds: float) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    for attempt in range(_MANAGED_PLANNER_SERVICE_PROBE_ATTEMPTS):
        probe = _probe_planner_service_health(target_url=target_url, timeout_seconds=timeout_seconds)
        probe["attempt"] = attempt + 1
        probes.append(probe)
        if bool(probe.get("healthy")):
            return {
                "healthy": True,
                "attempts": probes,
                "final_probe": probe,
            }
        if attempt + 1 < _MANAGED_PLANNER_SERVICE_PROBE_ATTEMPTS:
            time.sleep(_MANAGED_PLANNER_SERVICE_PROBE_SLEEP_SECONDS)
    return {
        "healthy": False,
        "attempts": probes,
        "final_probe": probes[-1] if probes else None,
    }


def _ensure_managed_planner_service_ready(*, target_url: str, timeout_seconds: float) -> dict[str, Any] | None:
    if os.name == "nt":
        return None
    host, _port = _planner_target_host_port(target_url=target_url)
    if host not in _MANAGED_PLANNER_SERVICE_HOST_ALIASES:
        return None
    before_probe = _probe_planner_service_health(target_url=target_url, timeout_seconds=timeout_seconds)
    if bool(before_probe.get("healthy")):
        return {
            "managed": True,
            "target_url": target_url,
            "recovered": False,
            "before_probe": before_probe,
            "recovery_steps": [],
            "after_probe": before_probe,
        }
    recovery_steps = []
    for container_name in _MANAGED_PLANNER_SERVICE_CONTAINER_NAMES:
        recovery_steps.append(_run_planner_service_recovery_command(["docker", "start", container_name]))
        after_start_probe = _probe_planner_service_until_ready(target_url=target_url, timeout_seconds=timeout_seconds)
        if bool(after_start_probe.get("healthy")):
            return {
                "managed": True,
                "target_url": target_url,
                "recovered": True,
                "before_probe": before_probe,
                "recovery_steps": recovery_steps,
                "after_probe": after_start_probe.get("final_probe"),
                "probe_attempts": after_start_probe.get("attempts"),
            }
        recovery_steps.append(_run_planner_service_recovery_command(["docker", "restart", container_name]))
        after_restart_probe = _probe_planner_service_until_ready(target_url=target_url, timeout_seconds=timeout_seconds)
        if bool(after_restart_probe.get("healthy")):
            return {
                "managed": True,
                "target_url": target_url,
                "recovered": True,
                "before_probe": before_probe,
                "recovery_steps": recovery_steps,
                "after_probe": after_restart_probe.get("final_probe"),
                "probe_attempts": after_restart_probe.get("attempts"),
            }
    compose_command = [
        "docker",
        "compose",
        "--project-directory",
        _MANAGED_PLANNER_SERVICE_COMPOSE_DIR,
        "-f",
        _MANAGED_PLANNER_SERVICE_COMPOSE_FILE,
        "--env-file",
        _MANAGED_PLANNER_SERVICE_ENV_FILE,
        "up",
        "-d",
        _MANAGED_PLANNER_SERVICE_COMPOSE_NAME,
    ]
    recovery_steps.append(_run_planner_service_recovery_command(compose_command))
    after_compose_probe = _probe_planner_service_until_ready(target_url=target_url, timeout_seconds=timeout_seconds)
    return {
        "managed": True,
        "target_url": target_url,
        "recovered": bool(after_compose_probe.get("healthy")),
        "before_probe": before_probe,
        "recovery_steps": recovery_steps,
        "after_probe": after_compose_probe.get("final_probe"),
        "probe_attempts": after_compose_probe.get("attempts"),
    }


def _should_skip_socket_preflight(*, workspace_root: str, target_url: str) -> bool:
    host, _port = _planner_target_host_port(target_url=target_url)
    if host in _LOCAL_PLANNER_HOST_ALIASES:
        return True
    if os.name == "nt" and host in _MANAGED_PLANNER_SERVICE_HOST_ALIASES:
        return True
    if _is_hybrid_host_workspace(workspace_root) and host in {"127.0.0.1", "localhost"}:
        return True
    if os.name == "nt" and host in {"127.0.0.1", "localhost"}:
        return True
    return False


def _planner_transport_error_code(exc: Exception) -> str:
    text = str(exc or "").strip().lower()
    if isinstance(exc, PlannerBridgeError):
        code = str(exc).split(" ", 1)[0].split(":", 1)[0].strip()
        if code.startswith("PLANNER_") or code.startswith("PROVIDER_"):
            return code
    if isinstance(exc, (httpx.TimeoutException, TimeoutError, socket.timeout)):
        return "PLANNER_TRANSPORT_TIMEOUT"
    err_no = getattr(exc, "errno", None)
    if err_no == 115 or "operation now in progress" in text:
        return "PLANNER_TRANSPORT_STALE_SOCKET"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "PLANNER_TRANSPORT_CONNECT_FAILED"
    if isinstance(exc, httpx.TransportError):
        return "PLANNER_TRANSPORT_CONNECT_FAILED"
    if "timed out" in text or "timeout" in text:
        return "PLANNER_TRANSPORT_TIMEOUT"
    return "PLANNER_UPSTREAM_REQUEST_FAILED"


def _planner_transport_failure_phase(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool"
    if isinstance(exc, httpx.ReadTimeout):
        return "read"
    if isinstance(exc, httpx.WriteTimeout):
        return "write"
    if isinstance(exc, PlannerBridgeError):
        if "PLANNER_TRANSPORT_STALE_SOCKET" in str(exc):
            return "connect"
    if isinstance(exc, httpx.ConnectError):
        return "connect"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "transport"
    return "unknown"


def _is_transient_name_resolution_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    return "temporary failure in name resolution" in text or "name resolution" in text


def _planner_socket_preflight(*, target_url: str, timeout_seconds: float) -> None:
    host, port = _planner_target_host_port(target_url=target_url)
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return None
    except OSError as exc:
        code = _planner_transport_error_code(exc)
        raise PlannerBridgeError(f"{code} error={exc} host={host} port={port}") from exc


def _coerce_planner_step(
    item: Any,
    *,
    step_idx: int = 0,
    planner_packet: dict[str, Any] | None = None,
    planner_mode: str | None = None,
) -> dict[str, Any] | None:
    candidate = item
    if isinstance(candidate, str):
        text = candidate.strip()
        if not text:
            raise PlannerBridgeError("PLANNER_INVALID_RESPONSE empty step entry")
        if text in _IGNORABLE_STEP_STRINGS:
            return None
        if text.startswith("{") and text.endswith("}"):
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PlannerBridgeError(f"PLANNER_INVALID_RESPONSE step_json_parse_failed error={exc}") from exc
        elif text in _ALLOWED_TOOLS:
            candidate = {"tool": text, "args": {}}
        else:
            raise PlannerBridgeError(f"PLANNER_INVALID_RESPONSE unsupported_step_string={text!r}")

    if not isinstance(candidate, dict):
        raise PlannerBridgeError(
            f"PLANNER_INVALID_RESPONSE each step must be an object step_type={type(candidate).__name__}"
        )

    nested = candidate.get("step")
    if isinstance(nested, dict):
        candidate = nested

    explicit_type = str(candidate.get("type") or candidate.get("tool") or candidate.get("action") or "").strip()
    if explicit_type == "edit_file":
        args_raw = candidate.get("args")
        if not isinstance(args_raw, dict):
            args_raw = candidate.get("parameters")
        args = dict(args_raw) if isinstance(args_raw, dict) else {}
        for key in (
            "file",
            "relative_path",
            "path",
            "search",
            "replace",
            "replacement",
            "search_replace_blocks",
            "read_source",
            "grounding_source",
            "search_range",
            "read_step_idx",
            "edit_range_within_read",
            "replacement_text",
            "replace_text",
        ):
            if key not in args and candidate.get(key) not in (None, "", [], {}):
                args[key] = candidate.get(key)
        if not str(args.get("relative_path") or "").strip():
            relative_path = str(args.get("file") or args.get("path") or "").strip()
            if relative_path:
                args["relative_path"] = relative_path
        has_structured_grounding = bool(
            str(args.get("grounding_source") or "").strip()
            or isinstance(args.get("search_range"), dict)
            or isinstance(args.get("edit_range_within_read"), dict)
            or args.get("read_step_idx") not in (None, "")
        )
        if has_structured_grounding:
            replacement_text = str(
                args.get("replacement_text")
                or args.get("replace_text")
                or args.get("replacement")
                or args.get("replace")
                or ""
            )
            if replacement_text:
                args["replacement_text"] = replacement_text
            for key in ("replace_text", "replacement", "replace"):
                args.pop(key, None)
            return {"tool": "edit_file", "args": args}
        has_legacy_edit_payload = any(
            str(args.get(key) or "").strip() for key in ("search", "replace", "replacement", "search_replace_blocks")
        )
        if has_legacy_edit_payload:
            normalize_edit_file_args(args)
            return {"tool": "edit_file", "args": args}

    structured_step = _normalize_structured_step(
        candidate,
        step_idx=step_idx,
        planner_packet=planner_packet,
        planner_mode=planner_mode,
    )
    if structured_step is not None:
        return structured_step

    tool = str(
        candidate.get("tool") or candidate.get("tool_name") or candidate.get("action") or candidate.get("name") or ""
    ).strip()
    if tool not in _ALLOWED_TOOLS:
        raise PlannerBridgeError(f"PLANNER_INVALID_TOOL tool={tool!r}")

    args_raw = candidate.get("args")
    if not isinstance(args_raw, dict):
        args_raw = candidate.get("parameters")
    if not isinstance(args_raw, dict):
        args_raw = candidate.get("input")
    args = dict(args_raw) if isinstance(args_raw, dict) else {}
    if tool == "evidence.attach" and not str(args.get("evidence_ref") or "").strip():
        return None
    if tool == "edit_file":
        normalize_edit_file_args(args)
    return {"tool": tool, "args": args}


def _coerce_steps_container(raw: Any) -> list[Any]:
    candidate = raw
    if isinstance(candidate, dict):
        error_code = str(candidate.get("error") or "").strip()
        if error_code == "PLANNER_INSUFFICIENT_CONTEXT":
            details = {
                str(key): value
                for key, value in candidate.items()
                if str(key).strip() and str(key).strip() != "error" and value not in (None, "", [], {}, ())
            }
            if details:
                raise PlannerBridgeError(
                    f"PLANNER_INSUFFICIENT_CONTEXT:{json.dumps(details, sort_keys=True, ensure_ascii=True)}"
                )
            raise PlannerBridgeError("PLANNER_INSUFFICIENT_CONTEXT")
        candidate = candidate.get("steps")
    if isinstance(candidate, str):
        text = candidate.strip()
        if not text:
            raise PlannerBridgeError("PLANNER_INVALID_RESPONSE steps payload is empty")
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError as exc:
            repaired = _repair_plan_output_text(text)
            if repaired:
                try:
                    candidate = json.loads(repaired)
                except json.JSONDecodeError:
                    candidate = _parse_python_literal_output(repaired)
            if candidate is None:
                candidate = _parse_python_literal_output(text)
            if candidate is None:
                if not repaired or repaired == text:
                    raise PlannerBridgeError("PLANNER_INVALID_RESPONSE steps must be a list") from exc
                raise PlannerBridgeError(f"PLANNER_INVALID_RESPONSE steps_json_parse_failed error={exc}") from exc
    if isinstance(candidate, dict):
        nested_steps = candidate.get("steps")
        if nested_steps is not None:
            return _coerce_steps_container(nested_steps)
        return [candidate]
    if isinstance(candidate, list):
        return candidate
    raise PlannerBridgeError("PLANNER_INVALID_RESPONSE steps must be a list")


def normalize_edit_file_args(args: dict[str, Any]) -> None:
    candidate_payload = args.get("search_replace_blocks")
    if candidate_payload in (None, "", [], {}):
        candidate_payload = args
    canonical = _canonicalize_edit_file_blocks(candidate_payload)
    if canonical:
        args["search_replace_blocks"] = canonical
    parse_diag = _build_edit_file_parse_diagnostic(candidate_payload)
    if parse_diag:
        args["__aix_edit_file_parse_diag"] = parse_diag


def _canonicalize_edit_file_blocks(payload: Any) -> str | None:
    blocks = _extract_edit_file_blocks(payload, depth=0)
    if not blocks:
        return None
    rendered_blocks: list[str] = []
    for search_text, replace_text in blocks:
        _validate_edit_candidate(search_text=search_text, replace_text=replace_text)
        rendered_blocks.append(_render_search_replace_block(search_text, replace_text))
    return "\n".join(block.rstrip("\n") for block in rendered_blocks if block.strip()) or None


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_edit_candidate_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return normalized.strip()


def _coerce_search_range(
    value: Any,
    *,
    step_idx: int,
    relative_path: str | None,
) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    raw_start = value.get("start_line", value.get("line_start"))
    raw_end = value.get("end_line", value.get("line_end"))
    if raw_start in (None, "") or raw_end in (None, ""):
        return None
    start_line = _safe_int(raw_start)
    end_line = _safe_int(raw_end)
    if start_line is None or end_line is None:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_SEARCH_RANGE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative_path,
                    "search_range": value,
                    "reason": "search_range lines must be integers",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if start_line < 1 or end_line < start_line:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_SEARCH_RANGE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative_path,
                    "search_range": {"start_line": start_line, "end_line": end_line},
                    "reason": "search_range must be 1-based and end_line >= start_line",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    return {"start_line": start_line, "end_line": end_line}


def _coerce_edit_range_within_read(
    value: Any,
    *,
    step_idx: int,
    relative_path: str,
) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        start_line = int(str(value.get("start_line")))
        end_line = int(str(value.get("end_line")))
    except Exception as exc:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_EDIT_RANGE_WITHIN_READ detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative_path,
                    "edit_range_within_read": value,
                    "reason": "edit_range_within_read lines must be integers",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        ) from exc
    if start_line < 1 or end_line < start_line:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_EDIT_RANGE_WITHIN_READ detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative_path,
                    "edit_range_within_read": {"start_line": start_line, "end_line": end_line},
                    "reason": "edit_range_within_read must be 1-based and end_line >= start_line",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    return {"start_line": start_line, "end_line": end_line}


def _slice_read_buffer_by_search_range(
    *,
    buffer_text: str,
    search_range: dict[str, int],
    step_idx: int,
    relative_path: str | None,
) -> str:
    lines = str(buffer_text or "").splitlines(keepends=True)
    start_line = int(search_range["start_line"])
    end_line = int(search_range["end_line"])
    if end_line > len(lines):
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_SEARCH_RANGE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative_path,
                    "search_range": search_range,
                    "buffer_line_count": len(lines),
                    "reason": "search_range exceeds cited read_source buffer",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    selected = "".join(lines[start_line - 1 : end_line])
    if not selected:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_SEARCH_RANGE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative_path,
                    "search_range": search_range,
                    "reason": "search_range resolved to an empty buffer slice",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    return selected


def _slice_workspace_file_by_search_range(
    *,
    workspace_root: str | None,
    relative_path: str | None,
    search_range: dict[str, int],
    step_idx: int,
) -> str:
    relative = str(relative_path or "").strip().replace("\\", "/")
    if not relative or not str(workspace_root or "").strip():
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_SEARCH_RANGE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative_path,
                    "search_range": search_range,
                    "reason": "workspace_root and relative_path are required for search_range hydration",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    resolution = resolve_target_file(workspace_root=str(workspace_root or ""), input_path=relative)
    if not resolution.exists or not resolution.resolved_absolute_path:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_SEARCH_RANGE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": relative,
                    "search_range": search_range,
                    "reason": "search_range target resolution failed",
                    "target_resolution": resolution.as_dict(),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    try:
        buffer_text = Path(str(resolution.resolved_absolute_path)).read_text(encoding="utf-8")
    except Exception as exc:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_SEARCH_RANGE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": resolution.normalized_repo_relative_path or relative,
                    "search_range": search_range,
                    "reason": f"search_range file read failed: {exc}",
                    "target_resolution": resolution.as_dict(),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        ) from exc
    return _slice_read_buffer_by_search_range(
        buffer_text=buffer_text,
        search_range=search_range,
        step_idx=step_idx,
        relative_path=resolution.normalized_repo_relative_path or relative,
    )


def _diff_token_count(search_text: str, replace_text: str) -> int:
    search_tokens = re.findall(r"\S+", _normalize_edit_candidate_text(search_text))
    replace_tokens = re.findall(r"\S+", _normalize_edit_candidate_text(replace_text))
    shared = sum(1 for left, right in zip(search_tokens, replace_tokens, strict=False) if left == right)
    return max(len(search_tokens), len(replace_tokens)) - shared


def _validate_edit_candidate(*, search_text: str, replace_text: str) -> None:
    normalized_search = _normalize_edit_candidate_text(search_text)
    normalized_replace = _normalize_edit_candidate_text(replace_text)
    diff_tokens = _diff_token_count(search_text, replace_text)
    logger.warning(
        "EDIT_CANDIDATE search_hash=%s replace_hash=%s diff=%d",
        hashlib.sha1(normalized_search.encode("utf-8")).hexdigest()[:12],
        hashlib.sha1(normalized_replace.encode("utf-8")).hexdigest()[:12],
        diff_tokens,
    )
    if normalized_search != normalized_replace:
        return
    detail = {
        "reason": "edit_file_identity_delta",
        "evidence": {
            "search": str(search_text or "")[:200],
            "replace": str(replace_text or "")[:200],
            "diff_tokens": diff_tokens,
        },
    }
    raise PlannerBridgeError(
        "MUTATION_REQUIRED_BUT_ABSENT detail="
        + json.dumps(
            {
                "reason": "edit_file_identity_delta",
                **detail,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )


def _validate_multiline_search_anchor(*, search_text: str, step_idx: int, relative_path: str | None) -> None:
    search_lines = str(search_text or "").splitlines()
    if len(search_lines) < 2:
        return
    if str(search_lines[-1]).strip():
        return
    detail = {
        "step_idx": step_idx,
        "detail": "multiline SEARCH ends with a whitespace-only line. Anchor on complete source lines and include the closing delimiter or following non-whitespace line.",
        "reason": "incomplete_multiline_search_anchor",
        "grounded_symbol": relative_path,
    }
    raise PlannerBridgeError(
        "PLANNER_EDIT_INCOMPLETE_SEARCH_ANCHOR detail=" + json.dumps(detail, ensure_ascii=True, sort_keys=True)
    )


def _lint_semantic_noop(steps: list[dict[str, Any]], grounded_source: str | None = None) -> None:
    for idx, step in enumerate(steps):
        if str(step.get("tool") or "").strip() != "edit_file":
            continue
        args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
        search_text = _extract_edit_search_text(step)
        replace_text = str(
            args.get("replacement_text")
            or args.get("replace_text")
            or args.get("replace")
            or args.get("replacement")
            or ""
        )
        payload = args.get("search_replace_blocks")
        canonical = _canonicalize_edit_file_blocks(payload)
        pairs = _extract_canonical_search_replace_pairs(canonical) if canonical else []
        if pairs:
            replace_text = str(pairs[0][1] or "")
        if (
            isinstance(args.get("edit_range_within_read"), dict)
            and str(args.get("grounding_source") or "").strip() == "read_range"
        ):
            if str(grounded_source or "").strip() and replace_text.strip() == str(grounded_source or "").strip():
                detail = {
                    "step_idx": idx,
                    "detail": "replacement_text is identical to the compact grounded source. You must change the code or return failure_analysis.",
                    "grounded_symbol": _planner_step_relative_path(step),
                    "grounded_symbol_source_present": True,
                    "retry_hint": (
                        "Do not target an unchanged enclosing wrapper call, constructor call, or return block. "
                        "Mutate the smallest behavior-bearing nested block or return failure_analysis."
                    ),
                }
                raise PlannerBridgeError(
                    "MUTATION_REQUIRED_BUT_ABSENT detail="
                    + json.dumps({**detail, "reason": "edit_file_identity_delta"}, ensure_ascii=True, sort_keys=True)
                )
            if replace_text.strip():
                continue
        normalized_search = str(search_text or "").strip()
        normalized_replace = str(replace_text or "").strip()
        if normalized_search == normalized_replace:
            detail = {
                "step_idx": idx,
                "detail": "search and replace are identical after strip(). You must change the code or return failure_analysis.",
                "grounded_symbol": _planner_step_relative_path(step),
                "grounded_symbol_source_present": bool(str(grounded_source or "").strip()),
                "retry_hint": (
                    "Do not target an unchanged enclosing wrapper call, constructor call, or return block. "
                    "Mutate the smallest behavior-bearing nested block or return failure_analysis."
                ),
            }
            raise PlannerBridgeError(
                "MUTATION_REQUIRED_BUT_ABSENT detail="
                + json.dumps({**detail, "reason": "edit_file_identity_delta"}, ensure_ascii=True, sort_keys=True)
            )
        if search_text == replace_text and search_text:
            detail = {
                "step_idx": idx,
                "detail": "replace is byte-identical to search",
                "grounded_symbol": _planner_step_relative_path(step),
                "grounded_symbol_source_present": bool(str(grounded_source or "").strip()),
                "retry_hint": (
                    "Do not target an unchanged enclosing wrapper call, constructor call, or return block. "
                    "Mutate the smallest behavior-bearing nested block or return failure_analysis."
                ),
            }
            raise PlannerBridgeError(
                "MUTATION_REQUIRED_BUT_ABSENT detail="
                + json.dumps({**detail, "reason": "edit_file_identity_delta"}, ensure_ascii=True, sort_keys=True)
            )
        relative_path = str(_planner_step_relative_path(step) or "").strip().lower()
        search_shape = _python_semantic_shape(search_text)
        replace_shape = _python_semantic_shape(replace_text)
        if (
            relative_path.endswith(".py")
            and search_shape is not None
            and replace_shape is not None
            and search_shape == replace_shape
        ):
            detail = {
                "step_idx": idx,
                "detail": (
                    "replace changes only comments, docstrings, or formatting. You must change executable Python "
                    "behavior or return failure_analysis."
                ),
                "reason": "edit_file_non_executable_delta",
                "grounded_symbol": _planner_step_relative_path(step),
                "grounded_symbol_source_present": bool(str(grounded_source or "").strip()),
                "retry_hint": (
                    "Do not satisfy acceptance criteria with comments, docstrings, or formatting-only edits. "
                    "Mutate executable test inputs, assertions, or behavior-bearing data instead."
                ),
            }
            raise PlannerBridgeError(
                "MUTATION_REQUIRED_BUT_ABSENT detail=" + json.dumps(detail, ensure_ascii=True, sort_keys=True)
            )
        _ = grounded_source


def _python_semantic_shape(text: str) -> str | None:
    source = str(text or "")
    if not source.strip():
        return None
    try:
        tree = ast.parse(source)
    except Exception:
        return None
    _strip_docstrings(tree)
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def _strip_docstrings(node: ast.AST) -> None:
    for child in ast.iter_child_nodes(node):
        _strip_docstrings(child)
    body = getattr(node, "body", None)
    if not isinstance(body, list) or not body:
        return
    first = body[0]
    if not isinstance(first, ast.Expr):
        return
    value = first.value
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        body.pop(0)


def _planner_step_relative_path(step: dict[str, Any]) -> str | None:
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    for key in ("relative_path", "path", "file", "filename"):
        value = str(args.get(key) or step.get(key) or "").strip()
        if value:
            return value.replace("\\", "/")
    return None


def _planner_step_read_source(step: dict[str, Any]) -> dict[str, Any]:
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    value = args.get("read_source")
    return dict(value) if isinstance(value, dict) else {}


def _prefer_latest_explicit_read_source(step: dict[str, Any], prior_steps: list[dict[str, Any]]) -> dict[str, Any]:
    if str(step.get("tool") or "").strip() != "edit_file":
        return step
    relative_path = _planner_step_relative_path(step)
    if not relative_path:
        return step
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    read_source = dict(args.get("read_source") or {}) if isinstance(args.get("read_source"), dict) else {}
    cited_step_idx = read_source.get("step_idx")
    explicit_reads = [
        prior
        for prior in prior_steps
        if str(prior.get("tool") or "").strip() in {"read_range", "read_file"}
        and _planner_step_relative_path(prior) == relative_path
        and isinstance(prior.get("step_idx"), int)
        and bool(_extract_read_buffer(prior))
    ]
    if not explicit_reads:
        return step
    latest_explicit = explicit_reads[-1]
    latest_step_idx = _safe_int(latest_explicit.get("step_idx"))
    if latest_step_idx is None:
        return step
    updated = dict(step)
    if cited_step_idx != latest_step_idx:
        read_source["step_idx"] = latest_step_idx
        read_source["file"] = relative_path
        args["read_source"] = read_source
    updated["args"] = args
    if bool(latest_explicit.get("synthetic")):
        updated["planner_grounded_buffer"] = str(_extract_read_buffer(latest_explicit) or "").rstrip("\n")
        updated["planner_grounded_source"] = "grounded_symbol_source"
    return updated


def _extract_edit_search_text(step: dict[str, Any]) -> str:
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    explicit_search = str(args.get("search") or step.get("search") or "")
    if explicit_search:
        return explicit_search
    payload = args.get("search_replace_blocks")
    canonical = _canonicalize_edit_file_blocks(payload)
    if canonical:
        pairs = _extract_canonical_search_replace_pairs(canonical) or []
        if pairs:
            return str(pairs[0][0] or "")
    text = str(payload or "")
    pairs = _extract_canonical_search_replace_pairs(text) or _extract_edit_file_blocks_from_string(text, depth=0) or []
    if pairs:
        return str(pairs[0][0] or "")
    return ""


def _extract_read_buffer(step: dict[str, Any]) -> str | None:
    top_level_content = step.get("output_content")
    if isinstance(top_level_content, str) and top_level_content:
        return top_level_content
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    for key in (
        "content",
        "output",
        "read_buffer",
        "buffer",
        "text",
        "result_text",
        "file_content",
    ):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _validate_search_anchor(*, search_text: str, source_text: str) -> dict[str, Any]:
    if not search_text or not source_text:
        return {"valid": False, "reason": "ANCHOR_NOT_FOUND", "match_count": 0, "match_spans": []}
    match_spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = source_text.find(search_text, start)
        if index < 0:
            break
        match_spans.append((index, index + len(search_text)))
        start = index + 1
    if len(match_spans) == 1:
        return {"valid": True, "reason": None, "match_count": 1, "match_spans": match_spans}
    if len(match_spans) > 1:
        return {
            "valid": False,
            "reason": "ANCHOR_NOT_UNIQUE",
            "match_count": len(match_spans),
            "match_spans": match_spans,
        }
    return {"valid": False, "reason": "ANCHOR_NOT_FOUND", "match_count": 0, "match_spans": []}


def validate_edit_grounding(
    edit_step: dict[str, Any],
    prior_steps: list[dict[str, Any]],
    read_buffers: dict[str, dict[str, Any]],
    *,
    planner_mode: str | None = None,
) -> None:
    resolved_planner_mode = _resolve_planner_mode(planner_mode=planner_mode)
    relative_path = _planner_step_relative_path(edit_step)
    search_text = _extract_edit_search_text(edit_step)
    read_source = _planner_step_read_source(edit_step)
    edit_args = dict(edit_step.get("args") or {}) if isinstance(edit_step.get("args"), dict) else {}
    grounding_source = str(edit_args.get("grounding_source") or "").strip().lower()
    read_step_idx = _safe_int(edit_args.get("read_step_idx"))
    if not relative_path:
        raise PlannerBridgeError("PLANNER_EDIT_MISSING_READ detail=" + json.dumps({"file": None}, ensure_ascii=True))
    if not search_text and edit_args.get("payload") not in (None, "", {}, []):
        raise PlannerBridgeError(
            "PLANNER_EDIT_MISSING_SEARCH detail="
            + json.dumps(
                {
                    "file": relative_path,
                    "rule": "edit_file payload must expose a non-empty SEARCH source",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if not search_text and str(edit_args.get("search_replace_blocks") or "").strip():
        raise PlannerBridgeError(
            "PLANNER_EDIT_MISSING_SEARCH detail="
            + json.dumps(
                {
                    "file": relative_path,
                    "rule": "edit_file textual payload must include a non-empty SEARCH block",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if read_source and resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
        raise PlannerBridgeError(
            "PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN detail="
            + json.dumps(
                {"file": relative_path, "rule": "edit_file must be self-contained and may not declare read_source"},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    cited_read_buffer = ""
    matched_cited_step = False
    cited_read_tool = ""
    if isinstance(read_source, dict) and read_source:
        source_file = str(read_source.get("file") or relative_path).strip().replace("\\", "/")
        cited_step_idx = _safe_int(read_source.get("step_idx"))
        if source_file:
            candidate_buffer = read_buffers.get(source_file) or {}
            candidate_step_idx = _safe_int(candidate_buffer.get("step_idx"))
            if candidate_buffer and (cited_step_idx is None or cited_step_idx == candidate_step_idx):
                matched_cited_step = True
                cited_read_buffer = str(candidate_buffer.get("text") or "")
                for prior_step in reversed(prior_steps):
                    if str(prior_step.get("tool") or "").strip() not in {"read_range", "read_file"}:
                        continue
                    if _planner_step_relative_path(prior_step) != source_file:
                        continue
                    prior_step_idx = _safe_int(prior_step.get("step_idx"))
                    if cited_step_idx is not None and prior_step_idx != cited_step_idx:
                        continue
                    cited_read_tool = str(prior_step.get("tool") or "").strip()
                    break
            if not cited_read_buffer:
                for prior_step in reversed(prior_steps):
                    if str(prior_step.get("tool") or "").strip() not in {"read_range", "read_file"}:
                        continue
                    if _planner_step_relative_path(prior_step) != source_file:
                        continue
                    prior_step_idx = _safe_int(prior_step.get("step_idx"))
                    if cited_step_idx is not None and prior_step_idx != cited_step_idx:
                        continue
                    matched_cited_step = True
                    cited_read_tool = str(prior_step.get("tool") or "").strip()
                    cited_read_buffer = str(_extract_read_buffer(prior_step) or "")
                    if cited_read_buffer:
                        break
        if not cited_read_buffer:
            if matched_cited_step:
                raise PlannerBridgeError(
                    "PLANNER_EDIT_MISSING_READ detail="
                    + json.dumps(
                        {
                            "file": relative_path,
                            "reason": "cited_read_missing_buffer",
                            "read_source": read_source,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            raise PlannerBridgeError(
                "PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN detail="
                + json.dumps(
                    {
                        "file": relative_path,
                        "read_source": read_source,
                        "rule": "edit_file may only cite an observed same-file read_range/read_file output from this plan",
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
    if grounding_source not in {"anchor", "read_range"}:
        derived_search_range = edit_args.get("__aix_derived_search_range")
        if isinstance(derived_search_range, dict) or isinstance(edit_args.get("search_range"), dict):
            return
        latest_read_buffer = cited_read_buffer or str((read_buffers.get(relative_path) or {}).get("text") or "")
        if not latest_read_buffer:
            for prior_step in reversed(prior_steps):
                if str(prior_step.get("tool") or "").strip() not in {"read_range", "read_file"}:
                    continue
                if _planner_step_relative_path(prior_step) != relative_path:
                    continue
                latest_read_buffer = str(_extract_read_buffer(prior_step) or "")
                if latest_read_buffer:
                    break
        if not latest_read_buffer:
            raise PlannerBridgeError(
                "PLANNER_EDIT_MISSING_READ_SOURCE detail="
                + json.dumps(
                    {
                        "file": relative_path,
                        "rule": "legacy edit_file requires a preceding same-file read_range/read_file or explicit grounding_source",
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        if not search_text:
            return
        _validate_multiline_search_anchor(
            search_text=search_text,
            step_idx=int(edit_step.get("step_idx") or 0),
            relative_path=relative_path,
        )
        if (
            isinstance(read_source, dict)
            and read_source
            and cited_read_tool == "read_file"
            and search_text in latest_read_buffer
        ):
            return
        anchor_validation = _validate_search_anchor(search_text=search_text, source_text=latest_read_buffer)
        if bool(anchor_validation.get("valid")):
            return
        if str(anchor_validation.get("reason") or "") == "ANCHOR_NOT_UNIQUE":
            raise PlannerBridgeError(
                "PLANNER_EDIT_ANCHOR_NOT_UNIQUE detail="
                + json.dumps(
                    {
                        "file": relative_path,
                        "match_count": int(anchor_validation.get("match_count") or 0),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        raise PlannerBridgeError(
            "PLANNER_EDIT_SEARCH_NOT_FROM_READ detail="
            + json.dumps(
                {
                    "file": relative_path,
                    "rule": "SEARCH must be copied from the latest same-file read output",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if grounding_source == "anchor" and read_step_idx is not None:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_READ_PROVENANCE detail="
            + json.dumps(
                {
                    "file": relative_path,
                    "grounding_source": grounding_source,
                    "read_step_idx": read_step_idx,
                    "rule": "anchor-grounded edit_file may not declare read_step_idx",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if grounding_source == "read_range" and read_step_idx is None:
        raise PlannerBridgeError(
            "PLANNER_EDIT_MISSING_READ_PROVENANCE detail="
            + json.dumps(
                {
                    "file": relative_path,
                    "grounding_source": grounding_source,
                    "rule": "read-grounded edit_file must declare read_step_idx for the same-file read_range source",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if not search_text:
        return
    planner_anchor_window = dict(edit_step.get("planner_anchor_window") or {})
    anchor_relative_path = str(planner_anchor_window.get("relative_path") or "").strip().replace("\\", "/")
    anchor_start_line = _safe_int(planner_anchor_window.get("start_line"))
    anchor_end_line = _safe_int(planner_anchor_window.get("end_line"))
    if grounding_source == "read_range":
        raise PlannerBridgeError(
            "PLANNER_EDIT_READ_RANGE_SEARCH_FORBIDDEN detail="
            + json.dumps(
                {
                    "file": relative_path,
                    "grounding_source": grounding_source,
                    "rule": "read_range grounded edit_file must use replacement_text plus edit_range_within_read only",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if (
        anchor_relative_path
        and anchor_relative_path == relative_path
        and anchor_start_line is not None
        and anchor_end_line is not None
    ):
        exact_anchor_buffer = str(edit_args.get("__aix_exact_anchor_buffer") or search_text or "")
        if search_text not in {exact_anchor_buffer, exact_anchor_buffer.rstrip("\n")}:
            raise PlannerBridgeError(
                "PLANNER_EDIT_SEARCH_NOT_EXACT_ANCHOR_BUFFER detail="
                + json.dumps(
                    {
                        "file": relative_path,
                        "reason": "SEARCH must exactly equal the cited anchor read_range.output_content",
                        "anchor_start_line": anchor_start_line,
                        "anchor_end_line": anchor_end_line,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        return
    return


def _normalize_structured_step(
    candidate: dict[str, Any],
    *,
    step_idx: int,
    planner_packet: dict[str, Any] | None = None,
    planner_mode: str | None = None,
) -> dict[str, Any] | None:
    explicit_type = str(candidate.get("type") or "").strip()
    step_type = explicit_type or str(candidate.get("tool") or candidate.get("action") or "").strip()
    planner_packet_dict = dict(planner_packet or {}) if isinstance(planner_packet, dict) else {}
    replace_function_spec_lane = (
        _resolve_planner_mode(planner_mode=planner_mode, planner_packet=planner_packet_dict)
        == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
    )
    if replace_function_spec_lane and step_type in _RAW_MUTATION_STEP_KINDS and step_type != "replace_function":
        raise PlannerBridgeError(
            "PLANNER_CONTRACT_VIOLATION detail="
            + json.dumps(
                {
                    "reason": "packet_bound_single_symbol_replace_function_lane_forbids_non_replace_function_mutation_steps",
                    "step_idx": step_idx,
                    "step_kind": step_type,
                    "allowed_mutation_step": "replace_function",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if explicit_type in {"read_range", "read_file"}:
        if candidate.get("step_idx") == 0:
            raise PlannerBridgeError(
                "PLANNER_INVALID_SYNTHETIC_READ detail="
                + json.dumps(
                    {
                        "step_idx": 0,
                        "tool": explicit_type,
                        "detail": "planner-emitted step_idx=0 grounded read is forbidden",
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        file_value = str(candidate.get("file") or candidate.get("relative_path") or "").strip().replace("\\", "/")
        args: dict[str, Any] = {"relative_path": file_value}
        if step_type == "read_range":
            if candidate.get("symbol") not in (None, ""):
                args["symbol"] = str(candidate.get("symbol"))
            if candidate.get("context_lines") is not None:
                args["context_lines"] = candidate.get("context_lines")
            output_content = candidate.get("output_content")
            if output_content not in (None, ""):
                normalized_output = str(output_content).strip().lower()
                if normalized_output in {"to be populated by read", "placeholder", "<placeholder>"}:
                    raise PlannerBridgeError(
                        "PLANNER_INVALID_GROUNDED_READ detail="
                        + json.dumps(
                            {
                                "tool": "read_range",
                                "detail": "read_range output_content cannot be a placeholder",
                                "step_idx": candidate.get("step_idx"),
                            },
                            ensure_ascii=True,
                            sort_keys=True,
                        )
                    )
        for key in ("start_line", "end_line"):
            value = candidate.get(key)
            if value not in (None, ""):
                args[key] = value
        normalized_step = {"tool": step_type, "args": args, "step_idx": step_idx, "type": step_type}
        if candidate.get("output_content") not in (None, ""):
            normalized_step["output_content"] = candidate.get("output_content")
        if candidate.get("content") not in (None, ""):
            normalized_step["content"] = candidate.get("content")
        for key in ("source", "read_buffer", "buffer", "result_text"):
            value = candidate.get(key)
            if value not in (None, ""):
                normalized_step[key] = value
        return normalized_step
    if explicit_type in {"failure_analysis", "report_failure"} or step_type in {"failure_analysis", "report_failure"}:
        reason_code = str(candidate.get("reason_code") or "").strip() or "INSUFFICIENT_CONTEXT_FOR_MUTATION"
        detail = dict(candidate.get("detail") or {}) if isinstance(candidate.get("detail"), dict) else {}
        failure_analysis = candidate.get("failure_analysis")
        if isinstance(failure_analysis, dict):
            detail.update({k: v for k, v in failure_analysis.items() if k not in detail})
            failure_analysis = failure_analysis.get("detail") or failure_analysis.get("message") or json.dumps(detail)
        failure_text = str(failure_analysis or detail.get("detail") or detail.get("message") or "").strip()
        return {
            "tool": "failure_analysis",
            "args": {
                "reason_code": reason_code,
                "detail": detail,
                "failure_analysis": failure_text,
            },
            "type": "failure_analysis",
            "step_idx": step_idx,
            "reason_code": reason_code,
            "detail": detail,
            "failure_analysis": failure_text,
        }
    if explicit_type in {"cmd.run", "run_validation_command"} or step_type in {"cmd.run", "run_validation_command"}:
        args_raw = candidate.get("args")
        if not isinstance(args_raw, dict):
            args_raw = candidate.get("parameters")
        args = dict(args_raw) if isinstance(args_raw, dict) else {}
        command_text = str(
            args.get("command_text")
            or args.get("command")
            or candidate.get("command_text")
            or candidate.get("command")
            or candidate.get("validation_command")
            or ""
        ).strip()
        if not command_text:
            raise PlannerBridgeError(
                "PLANNER_CMD_RUN_MISSING_COMMAND detail="
                + json.dumps({"step_idx": step_idx}, ensure_ascii=True, sort_keys=True)
            )
        args["command_text"] = command_text
        return {"tool": "cmd.run", "args": args, "step_idx": step_idx, "type": "cmd.run"}
    if explicit_type == "replace_function" or step_type == "replace_function":
        args_raw = dict(candidate.get("args") or {}) if isinstance(candidate.get("args"), dict) else {}
        file_value = (
            str(candidate.get("file") or candidate.get("relative_path") or args_raw.get("relative_path") or "")
            .strip()
            .replace("\\", "/")
        )
        qualified_symbol = str(
            candidate.get("qualified_symbol")
            or candidate.get("target_symbol")
            or args_raw.get("qualified_symbol")
            or args_raw.get("function_name")
            or args_raw.get("target_symbol")
            or ""
        ).strip()
        replacement = str(
            candidate.get("replacement")
            or args_raw.get("replacement")
            or candidate.get("replacement_text")
            or args_raw.get("replacement_text")
            or ""
        )
        edit_intent = str(candidate.get("edit_intent") or args_raw.get("edit_intent") or "").strip()
        preserve_constraints = candidate.get("preserve_constraints")
        if not isinstance(preserve_constraints, list):
            preserve_constraints = args_raw.get("preserve_constraints")
        if not isinstance(preserve_constraints, list):
            preserve_constraints = []
        line_targets = candidate.get("line_targets")
        if not isinstance(line_targets, list):
            line_targets = args_raw.get("line_targets")
        if not isinstance(line_targets, list):
            line_targets = []
        if replace_function_spec_lane:
            if replacement.strip():
                raise PlannerBridgeError(
                    "PLANNER_REPLACE_FUNCTION_DIRECT_BODY_FORBIDDEN detail="
                    + json.dumps(
                        {"step_idx": step_idx, "file": file_value, "qualified_symbol": qualified_symbol},
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            if not file_value or not qualified_symbol or not edit_intent:
                raise PlannerBridgeError(
                    "PLANNER_REPLACE_FUNCTION_SPEC_INVALID detail="
                    + json.dumps(
                        {
                            "step_idx": step_idx,
                            "file": file_value,
                            "qualified_symbol": qualified_symbol,
                            "edit_intent": edit_intent,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            return {
                "tool": "replace_function",
                "args": {
                    "relative_path": file_value,
                    "function_name": qualified_symbol,
                    "qualified_symbol": qualified_symbol,
                    "edit_intent": edit_intent,
                    "preserve_constraints": [str(item).strip() for item in preserve_constraints if str(item).strip()],
                    "line_targets": [str(item).strip() for item in line_targets if str(item).strip()],
                },
                "step_idx": step_idx,
                "type": "replace_function",
            }
        if not file_value or not qualified_symbol or not replacement.strip():
            raise PlannerBridgeError(
                "PLANNER_REPLACE_FUNCTION_INVALID detail="
                + json.dumps(
                    {"step_idx": step_idx, "file": file_value, "qualified_symbol": qualified_symbol},
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        return {
            "tool": "replace_function",
            "args": {
                "relative_path": file_value,
                "function_name": qualified_symbol,
                "qualified_symbol": qualified_symbol,
                "replacement": replacement,
            },
            "step_idx": step_idx,
            "type": "replace_function",
        }
    args_raw = dict(candidate.get("args") or {}) if isinstance(candidate.get("args"), dict) else {}
    is_structured_edit = (
        explicit_type == "edit_file"
        or step_type == "edit_file"
        or (
            (
                "search_range" in candidate
                or "edit_range_within_read" in candidate
                or "search_range" in args_raw
                or "edit_range_within_read" in args_raw
            )
            and (
                "replacement_text" in candidate
                or "replacement_text" in args_raw
                or "replace_text" in candidate
                or "replace_text" in args_raw
            )
            and ("file" in candidate or "relative_path" in candidate or "relative_path" in args_raw)
        )
    )
    if not is_structured_edit:
        return None
    file_value = (
        str(candidate.get("file") or candidate.get("relative_path") or args_raw.get("relative_path") or "")
        .strip()
        .replace("\\", "/")
    )
    search_range = _coerce_search_range(
        candidate.get("search_range") or args_raw.get("search_range"),
        step_idx=step_idx,
        relative_path=file_value,
    )
    edit_range_within_read = _coerce_edit_range_within_read(
        candidate.get("edit_range_within_read") or args_raw.get("edit_range_within_read"),
        step_idx=step_idx,
        relative_path=file_value,
    )
    search_text = ""
    replace_text = str(
        candidate.get("replacement_text")
        or args_raw.get("replacement_text")
        or candidate.get("replace_text")
        or args_raw.get("replace_text")
        or ""
    )
    if any(
        str(candidate.get(key) or args_raw.get(key) or "")
        for key in ("search", "replace", "replacement", "search_replace_blocks")
    ):
        raise PlannerBridgeError(
            "PLANNER_TEXTUAL_PATCH_BLOCKS_FORBIDDEN detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": file_value,
                    "rule": "edit_file must use structured ranges plus replacement_text only",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    read_source_value = candidate.get("read_source")
    if not isinstance(read_source_value, dict):
        read_source_value = args_raw.get("read_source")
    if isinstance(read_source_value, dict) and read_source_value:
        raise PlannerBridgeError(
            "PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN detail="
            + json.dumps(
                {"step_idx": step_idx, "file": file_value, "rule": "edit_file may not declare read_source"},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if args_raw.get("payload") not in (None, "", {}, []):
        raise PlannerBridgeError(
            "PLANNER_EDIT_MISSING_SEARCH detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": file_value,
                    "rule": "edit_file payload must expose a non-empty SEARCH source",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    grounding_source = str(candidate.get("grounding_source") or args_raw.get("grounding_source") or "").strip().lower()
    if grounding_source not in {"anchor", "read_range"}:
        raise PlannerBridgeError(
            "PLANNER_EDIT_MISSING_GROUNDING_SOURCE detail="
            + json.dumps({"step_idx": step_idx, "file": file_value}, ensure_ascii=True, sort_keys=True)
        )
    read_step_idx_value = candidate.get("read_step_idx")
    if read_step_idx_value in (None, ""):
        read_step_idx_value = args_raw.get("read_step_idx")
    read_step_idx = _safe_int(read_step_idx_value)
    if grounding_source == "anchor" and read_step_idx is not None:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_READ_PROVENANCE detail="
            + json.dumps(
                {
                    "step_idx": step_idx,
                    "file": file_value,
                    "grounding_source": grounding_source,
                    "read_step_idx": read_step_idx,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if grounding_source == "read_range" and read_step_idx is None:
        raise PlannerBridgeError(
            "PLANNER_EDIT_MISSING_READ_PROVENANCE detail="
            + json.dumps(
                {"step_idx": step_idx, "file": file_value, "grounding_source": grounding_source},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if grounding_source == "anchor" and edit_range_within_read is not None:
        raise PlannerBridgeError(
            "PLANNER_EDIT_INVALID_EDIT_RANGE_WITHIN_READ detail="
            + json.dumps(
                {"step_idx": step_idx, "file": file_value, "grounding_source": grounding_source},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    if grounding_source == "read_range":
        if search_text:
            raise PlannerBridgeError(
                "PLANNER_EDIT_READ_RANGE_SEARCH_FORBIDDEN detail="
                + json.dumps(
                    {"step_idx": step_idx, "file": file_value, "grounding_source": grounding_source},
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        if search_range is not None:
            raise PlannerBridgeError(
                "PLANNER_EDIT_READ_RANGE_SEARCH_RANGE_FORBIDDEN detail="
                + json.dumps(
                    {"step_idx": step_idx, "file": file_value, "grounding_source": grounding_source},
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        if edit_range_within_read is None:
            raise PlannerBridgeError(
                "PLANNER_EDIT_MISSING_EDIT_RANGE_WITHIN_READ detail="
                + json.dumps(
                    {"step_idx": step_idx, "file": file_value, "grounding_source": grounding_source},
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
    elif search_range is None:
        raise PlannerBridgeError(
            "PLANNER_EDIT_MISSING_SEARCH detail="
            + json.dumps({"step_idx": step_idx, "file": file_value}, ensure_ascii=True, sort_keys=True)
        )
    if search_range is None and search_text == replace_text and search_text:
        raise PlannerBridgeError(
            "MUTATION_REQUIRED_BUT_ABSENT detail="
            + json.dumps(
                {
                    "reason": "edit_file_identity_delta",
                    "detail": "edit_file must contain a semantic delta (search != replace)",
                    "step_idx": step_idx,
                    "file": file_value,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    args = {"relative_path": file_value}
    if grounding_source == "read_range":
        args["edit_range_within_read"] = edit_range_within_read
        args["replace_text"] = replace_text
    elif search_range is not None:
        args["search_range"] = search_range
        args["replace_text"] = replace_text
    args["grounding_source"] = grounding_source
    if read_step_idx is not None:
        args["read_step_idx"] = read_step_idx
    return {"tool": "edit_file", "args": args, "step_idx": step_idx, "type": "edit_file"}


def _extract_edit_file_blocks(payload: Any, *, depth: int) -> list[tuple[str, str]] | None:
    if depth > 4 or payload in (None, "", [], {}):
        return None
    if isinstance(payload, str):
        return _extract_edit_file_blocks_from_string(payload, depth=depth)
    if isinstance(payload, list):
        pairs: list[tuple[str, str]] = []
        for item in payload:
            item_pairs = _extract_edit_file_blocks(item, depth=depth + 1)
            if not item_pairs:
                return None
            pairs.extend(item_pairs)
        return pairs or None
    if isinstance(payload, dict):
        direct_pair = _extract_semantic_search_replace_pair(payload)
        if direct_pair is not None:
            return [direct_pair]
        wrapper_keys = (
            "search_replace_blocks",
            "blocks",
            "items",
            "pairs",
            "edits",
            "changes",
            "payload",
            "value",
            "data",
            "arguments",
            "args",
        )
        for key in wrapper_keys:
            nested = payload.get(key)
            nested_pairs = _extract_edit_file_blocks(nested, depth=depth + 1)
            if nested_pairs:
                return nested_pairs
        text_fields = (
            "text",
            "content",
            "patch_text",
            "block",
            "search_replace_text",
            "search_replace",
        )
        for key in text_fields:
            text_value = str(payload.get(key) or "")
            nested_pairs = _extract_edit_file_blocks_from_string(text_value, depth=depth + 1)
            if nested_pairs:
                return nested_pairs
        ignored_keys = {
            "relative_path",
            "path",
            "file",
            "filename",
            "tool",
            "action",
            "name",
            "__aix_edit_file_parse_diag",
        }
        for key, value in payload.items():
            if str(key) in ignored_keys:
                continue
            nested_pairs = _extract_edit_file_blocks(value, depth=depth + 1)
            if nested_pairs:
                return nested_pairs
        return None
    return None


def _extract_edit_file_blocks_from_string(text: str, *, depth: int) -> list[tuple[str, str]] | None:
    value = str(text or "")
    if not value.strip():
        return None
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(stripped)
            except Exception:
                parsed = None
        if parsed is not None:
            parsed_pairs = _extract_edit_file_blocks(parsed, depth=depth + 1)
            if parsed_pairs:
                return parsed_pairs
    canonical_pairs = _extract_canonical_search_replace_pairs(value)
    if canonical_pairs:
        return canonical_pairs
    bare_headers = re.fullmatch(
        r"\s*SEARCH\s*:?\s*\n(?P<search>.*?)(?:\r?\n)?REPLACE\s*:?\s*\n(?P<replace>.*)\s*",
        value,
        flags=re.DOTALL,
    )
    if bare_headers:
        search_text = str(bare_headers.group("search") or "")
        replace_text = str(bare_headers.group("replace") or "")
        if search_text.strip():
            return [(search_text, replace_text)]
        return None
    if value.count("=======") == 1 and "<<<<<<< SEARCH" not in value and ">>>>>>> REPLACE" not in value:
        search_text, replace_text = value.split("=======", 1)
        if str(search_text or "").strip():
            return [(search_text, replace_text)]
        return None
    return None


def _extract_canonical_search_replace_pairs(text: str) -> list[tuple[str, str]] | None:
    pattern = re.compile(
        r"<<<<<<< SEARCH\s*\n(?P<search>.*?)\n=======\s*\n(?P<replace>.*?)\n>>>>>>> REPLACE",
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(str(text or "")))
    if not matches:
        return None
    pairs: list[tuple[str, str]] = []
    for match in matches:
        pairs.append((str(match.group("search") or ""), str(match.group("replace") or "")))
    return pairs or None


def _extract_semantic_search_replace_pair(item: dict[str, Any]) -> tuple[str, str] | None:
    key_map = {str(key): item.get(key) for key in item.keys()}
    normalized_map = {_normalize_edit_file_semantic_key(key): key for key in key_map.keys()}
    search_exact = (
        "search",
        "searchtext",
        "old",
        "before",
        "find",
        "findtext",
        "match",
        "source",
        "original",
        "existing",
    )
    replace_exact = (
        "replace",
        "replacetext",
        "replacement",
        "new",
        "after",
        "target",
        "updated",
        "newtext",
    )
    search_key = next((normalized_map[key] for key in search_exact if key in normalized_map), None)
    replace_key = next((normalized_map[key] for key in replace_exact if key in normalized_map), None)
    if search_key and replace_key:
        search_text = str(item.get(search_key) or "")
        replace_text = str(item.get(replace_key) or "")
        if search_text.strip():
            return search_text, replace_text
        return None
    string_fields: list[tuple[str, str]] = []
    for key, value in item.items():
        if isinstance(value, str) and str(value).strip():
            string_fields.append((str(key), str(value)))
    if len(string_fields) == 2:
        left_key = _normalize_edit_file_semantic_key(string_fields[0][0])
        right_key = _normalize_edit_file_semantic_key(string_fields[1][0])
        if _looks_like_search_key(left_key) and _looks_like_replace_key(right_key):
            if string_fields[0][1].strip():
                return string_fields[0][1], string_fields[1][1]
            return None
        if _looks_like_search_key(right_key) and _looks_like_replace_key(left_key):
            if string_fields[1][1].strip():
                return string_fields[1][1], string_fields[0][1]
            return None
    return None


def _normalize_edit_file_semantic_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key or "").casefold())


def _looks_like_search_key(normalized_key: str) -> bool:
    markers = ("search", "old", "before", "find", "match", "source", "original", "existing")
    return any(marker in normalized_key for marker in markers)


def _looks_like_replace_key(normalized_key: str) -> bool:
    markers = ("replace", "new", "after", "target", "updated", "replacement")
    return any(marker in normalized_key for marker in markers)


def _build_edit_file_parse_diagnostic(payload: Any) -> dict[str, Any] | None:
    pairs = _extract_edit_file_blocks(payload, depth=0)
    payload_text = _stringify_edit_file_payload(payload)
    if pairs:
        return {
            "parse_class": "canonicalized",
            "pair_count": len(pairs),
            "payload_snippet": payload_text[:240] or None,
        }
    if not payload_text:
        return None
    return {
        "parse_class": "unrecoverable",
        "payload_snippet": payload_text[:240],
    }


def _stringify_edit_file_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    try:
        return json.dumps(payload, ensure_ascii=True)
    except Exception:
        return str(payload or "").strip()


def _planner_packet_requires_real_anchor_read(packet: dict[str, Any] | None) -> bool:
    payload = dict(packet or {})
    codeintel_anchor = dict(payload.get("codeintel_anchor") or {})
    anchor_file = str(codeintel_anchor.get("file_path") or "").strip()
    anchor_start_line = _safe_int(codeintel_anchor.get("start_line"))
    anchor_end_line = _safe_int(codeintel_anchor.get("end_line"))
    if anchor_start_line is None or anchor_end_line is None:
        return False
    if not anchor_file or anchor_start_line < 1 or anchor_end_line < anchor_start_line:
        return False
    if bool(payload.get("enforce_read_before_write")):
        return True
    execution_lane = str((dict(payload.get("tool_policy") or {})).get("execution_lane") or "").strip().lower()
    return execution_lane == "planning_safe"


def _build_virtual_grounded_read(packet: dict[str, Any] | None) -> dict[str, Any] | None:
    payload = dict(packet or {})
    if _planner_packet_requires_real_anchor_read(payload):
        return None
    grounded_source = str(payload.get("grounded_symbol_source") or "").replace("\r\n", "\n").rstrip("\n")
    target_symbol = str(payload.get("target_symbol") or "").strip()
    target_file = str(payload.get("target_file") or "").strip().replace("\\", "/")
    if not (grounded_source.strip() and target_symbol and target_file):
        return None
    line_start = int(payload.get("grounded_line_start") or 1)
    line_end = int(payload.get("grounded_line_end") or max(line_start, len(grounded_source.splitlines())))
    return {
        "tool": "read_range",
        "type": "read_range",
        "args": {
            "relative_path": target_file,
            "start_line": line_start,
            "end_line": line_end,
            "output_content": grounded_source,
        },
        "file": target_file,
        "symbol": target_symbol,
        "step_idx": 0,
        "output_content": grounded_source,
        "synthetic": True,
    }


def _planner_packet_anchor_window(packet: dict[str, Any] | None) -> dict[str, Any] | None:
    payload = dict(packet or {})
    codeintel_anchor = dict(payload.get("codeintel_anchor") or {})
    relative_path = str(codeintel_anchor.get("file_path") or "").strip().replace("\\", "/")
    start_line = _safe_int(codeintel_anchor.get("start_line"))
    end_line = _safe_int(codeintel_anchor.get("end_line"))
    if start_line is None or end_line is None:
        return None
    if not relative_path or start_line < 1 or end_line < start_line:
        return None
    output_content = str(codeintel_anchor.get("output_content") or payload.get("grounded_symbol_source") or "")
    return {
        "relative_path": relative_path,
        "start_line": start_line,
        "end_line": end_line,
        "output_content": output_content,
    }


def _splice_replace_into_anchor_buffer(
    *, anchor_output_content: str, search_text: str, replace_text: str
) -> str | None:
    anchor_validation = _validate_search_anchor(search_text=search_text, source_text=anchor_output_content)
    if not bool(anchor_validation.get("valid")):
        return None
    match_spans = list(anchor_validation.get("match_spans") or [])
    if len(match_spans) != 1:
        return None
    start_offset, end_offset = match_spans[0]
    return f"{anchor_output_content[:start_offset]}{replace_text}{anchor_output_content[end_offset:]}"


def _hydrate_edit_step_from_search_range(
    edit_step: dict[str, Any],
    *,
    prior_steps: list[dict[str, Any]],
    planner_packet: dict[str, Any] | None,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    args = dict(edit_step.get("args") or {}) if isinstance(edit_step.get("args"), dict) else {}
    relative_path = _planner_step_relative_path(edit_step)
    search_range = _coerce_search_range(
        args.get("search_range"),
        step_idx=int(edit_step.get("step_idx") or 0),
        relative_path=relative_path,
    )
    if search_range is None:
        return edit_step
    packet_bound_lane = (
        _resolve_planner_mode(planner_packet=planner_packet) == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
    )
    if packet_bound_lane and _planner_step_read_source(edit_step):
        raise PlannerBridgeError(
            "PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN detail="
            + json.dumps(
                {
                    "step_idx": edit_step.get("step_idx"),
                    "file": relative_path,
                    "reason": "search_range edits must be self-contained and may not declare read_source",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    search_text = ""
    read_source = _planner_step_read_source(edit_step)
    if read_source:
        source_file = str(read_source.get("file") or relative_path).strip().replace("\\", "/")
        cited_step_idx = _safe_int(read_source.get("step_idx"))
        for prior_step in reversed(prior_steps):
            if str(prior_step.get("tool") or "").strip() not in {"read_range", "read_file"}:
                continue
            if _planner_step_relative_path(prior_step) != source_file:
                continue
            prior_step_idx = _safe_int(prior_step.get("step_idx"))
            if cited_step_idx is not None and prior_step_idx != cited_step_idx:
                continue
            read_buffer = str(_extract_read_buffer(prior_step) or "")
            if read_buffer:
                search_text = _slice_read_buffer_by_search_range(
                    buffer_text=read_buffer,
                    search_range=search_range,
                    step_idx=int(edit_step.get("step_idx") or 0),
                    relative_path=relative_path,
                )
                break
    if not search_text:
        search_text = _slice_workspace_file_by_search_range(
            workspace_root=workspace_root,
            relative_path=relative_path,
            search_range=search_range,
            step_idx=int(edit_step.get("step_idx") or 0),
        )
    hydrated_step = dict(edit_step)
    hydrated_args = dict(args)
    hydrated_args["__aix_derived_search_range"] = {
        "mode": "workspace_file_lines",
        "start_line": search_range["start_line"],
        "end_line": search_range["end_line"],
    }
    replacement_text = str(
        hydrated_args.get("replacement_text")
        or hydrated_args.get("replace_text")
        or hydrated_args.get("replacement")
        or hydrated_args.get("replace")
        or ""
    )
    if replacement_text:
        hydrated_args["replacement_text"] = replacement_text
        hydrated_args["search_replace_blocks"] = _render_verbatim_search_replace_block(search_text, replacement_text)
    hydrated_step["args"] = hydrated_args
    return hydrated_step


def _hydrate_anchor_window_edit_from_read_output(
    edit_step: dict[str, Any],
    *,
    prior_steps: list[dict[str, Any]],
    planner_packet: dict[str, Any] | None,
) -> dict[str, Any]:
    if not _planner_packet_requires_real_anchor_read(planner_packet):
        return edit_step
    anchor_window = _planner_packet_anchor_window(planner_packet)
    if anchor_window is None:
        return edit_step
    relative_path = _planner_step_relative_path(edit_step)
    if relative_path != anchor_window["relative_path"]:
        return edit_step
    anchor_output_content = str(anchor_window.get("output_content") or "")
    if not anchor_output_content:
        return edit_step
    pairs = _extract_edit_file_blocks(dict(edit_step.get("args") or {}).get("search_replace_blocks"), depth=0)
    if not pairs or len(pairs) != 1:
        return edit_step
    search_text, replace_text = pairs[0]
    if search_text == anchor_output_content or search_text == anchor_output_content.rstrip("\n"):
        hydrated_step = dict(edit_step)
        hydrated_step["planner_anchor_window"] = anchor_window
        hydrated_args = dict(hydrated_step.get("args") or {})
        hydrated_args["__aix_exact_anchor_buffer"] = anchor_output_content
        hydrated_step["args"] = hydrated_args
        hydrated_step["grounding_contract_validation"] = {
            "status": "hydrated",
            "mode": "exact_anchor_buffer",
            "relative_path": relative_path,
            "anchor_start_line": anchor_window["start_line"],
            "anchor_end_line": anchor_window["end_line"],
            "contract_rule": "edit_file.search must exactly equal the anchor-window content",
        }
        return hydrated_step
    # Preserve bounded subrange edits as-is. Promoting them to a full anchor-buffer
    # rewrite widens the mutation surface and can turn a valid local edit into a
    # structurally invalid whole-function replacement.
    return edit_step


def _hydrate_anchor_window_read_output_content(raw: Any, planner_packet: dict[str, Any] | None) -> Any:
    if not _planner_packet_requires_real_anchor_read(planner_packet):
        return raw
    anchor_window = _planner_packet_anchor_window(planner_packet)
    if anchor_window is None:
        return raw
    if not str(anchor_window.get("output_content") or "").strip():
        return raw
    if not isinstance(raw, dict):
        return raw
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        return raw
    first_step = steps[0]
    if not isinstance(first_step, dict):
        return raw
    step_type = str(first_step.get("type") or first_step.get("tool") or first_step.get("action") or "").strip()
    step_file = str(first_step.get("file") or first_step.get("relative_path") or "").strip().replace("\\", "/")
    if step_type != "read_range" or step_file != anchor_window["relative_path"]:
        return raw
    start_line = _safe_int(first_step.get("start_line"))
    end_line = _safe_int(first_step.get("end_line"))
    if start_line is None or end_line is None:
        return raw
    if start_line != anchor_window["start_line"] or end_line != anchor_window["end_line"]:
        return raw
    if str(first_step.get("output_content") or "").strip():
        return raw
    hydrated = dict(raw)
    hydrated_steps = list(steps)
    hydrated_first_step = dict(first_step)
    hydrated_first_step["output_content"] = anchor_window["output_content"]
    hydrated_steps[0] = hydrated_first_step
    hydrated["steps"] = hydrated_steps
    return hydrated


def _attach_anchor_window_metadata_to_read_step(
    step: dict[str, Any],
    *,
    planner_packet: dict[str, Any] | None,
) -> dict[str, Any]:
    anchor_window = _planner_packet_anchor_window(planner_packet)
    if anchor_window is None or str(step.get("tool") or "").strip() != "read_range":
        return step
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    relative_path = str(args.get("relative_path") or "").replace("\\", "/").strip()
    start_line = _safe_int(args.get("start_line"))
    end_line = _safe_int(args.get("end_line"))
    if start_line is None or end_line is None:
        return step
    if (
        relative_path != anchor_window["relative_path"]
        or start_line != anchor_window["start_line"]
        or end_line != anchor_window["end_line"]
    ):
        return step
    updated = dict(step)
    updated["planner_anchor_window"] = anchor_window
    return updated


def _render_search_replace_block(search_text: str, replace_text: str) -> str:
    return (
        "<<<<<<< SEARCH\n"
        f"{str(search_text or '').rstrip()}\n"
        "=======\n"
        f"{str(replace_text or '').rstrip()}\n"
        ">>>>>>> REPLACE"
    )


def _render_verbatim_search_replace_block(search_text: str, replace_text: str) -> str:
    rendered_search = str(search_text or "")
    rendered_replace = str(replace_text or "")
    if not rendered_search.endswith("\n"):
        rendered_search = f"{rendered_search}\n"
    if not rendered_replace.endswith("\n"):
        rendered_replace = f"{rendered_replace}\n"
    return "<<<<<<< SEARCH\n" f"{rendered_search}" "=======\n" f"{rendered_replace}" ">>>>>>> REPLACE"


def _planner_failure_analysis_step(
    *, reason_code: str, detail: dict[str, Any], failure_analysis: str
) -> dict[str, Any]:
    return {
        "tool": "failure_analysis",
        "args": {
            "reason_code": str(reason_code or "INSUFFICIENT_CONTEXT_FOR_MUTATION").strip()
            or "INSUFFICIENT_CONTEXT_FOR_MUTATION",
            "detail": dict(detail or {}),
            "failure_analysis": str(failure_analysis or "").strip(),
        },
        "type": "failure_analysis",
        "reason_code": str(reason_code or "INSUFFICIENT_CONTEXT_FOR_MUTATION").strip()
        or "INSUFFICIENT_CONTEXT_FOR_MUTATION",
        "detail": dict(detail or {}),
        "failure_analysis": str(failure_analysis or "").strip(),
    }


def _bounded_edit_surface_failure_text() -> str:
    return (
        "Cannot satisfy 'executable mutation' within bounded_edit_surface. The test only asserts True on current inputs. "
        "Changing assertion to False would be test invalidation, not fix. Changing metadata dict does not affect "
        "_should_require_strict_planner_packet return value per static analysis. Recommend expanding target_surface "
        "to the v2 bootstrap/runtime owner or close task as infeasible."
    )


def _guard_test_only_assertion_inversion(step: dict[str, Any]) -> dict[str, Any]:
    if str(step.get("tool") or "").strip() != "edit_file":
        return step
    relative_path = str(_planner_step_relative_path(step) or "")
    normalized_path = relative_path.replace("\\", "/").lower()
    if "/tests/" not in normalized_path and not Path(normalized_path).name.startswith("test_"):
        return step
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    pairs = _extract_edit_file_blocks(args.get("search_replace_blocks"), depth=0) or []
    if len(pairs) != 1:
        return step
    search_text, replace_text = pairs[0]
    assert_pattern = re.compile(
        r"^(?P<indent>\s*)assert\s+(?P<expr>.+?)\s+is\s+(?P<bool>True|False)\s*$",
        flags=re.MULTILINE,
    )
    search_match = assert_pattern.search(search_text)
    if not search_match:
        return step
    replace_match = assert_pattern.search(replace_text)
    search_lines = [line.rstrip() for line in search_text.splitlines()]
    replace_lines = [line.rstrip() for line in replace_text.splitlines()]
    changed_pairs = [
        (before, after) for before, after in zip(search_lines, replace_lines, strict=False) if before != after
    ]
    if len(replace_lines) != len(search_lines):
        extra_search = search_lines[len(replace_lines) :]
        changed_pairs.extend((line, "") for line in extra_search)
        extra_replace = replace_lines[len(search_lines) :]
        changed_pairs.extend(("", line) for line in extra_replace)
    significant_pairs = [(before, after) for before, after in changed_pairs if before.strip() or after.strip()]
    if not significant_pairs:
        return step
    if any("assert " not in before and "assert " not in after for before, after in significant_pairs):
        return step
    if replace_match and replace_match.group("expr").strip() == search_match.group("expr").strip():
        if replace_match.group("bool") == search_match.group("bool"):
            return step
    detail = {
        "detail": "Planner proposed a test-only expectation inversion instead of a behavior-bearing bounded fix",
        "file": relative_path,
        "reason": "test_expectation_inversion",
        "search_preview": search_text[:160],
        "replace_preview": replace_text[:160],
        "remediation": "Return failure_analysis when the bounded surface only supports invalidating the test assertion.",
    }
    return _planner_failure_analysis_step(
        reason_code="PLANNER_INVALID_TEST_ONLY_MUTATION",
        detail=detail,
        failure_analysis=_bounded_edit_surface_failure_text(),
    )


def _step_edit_search_preview(step: dict[str, Any]) -> str:
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    payload = str(args.get("search_replace_blocks") or "")
    match = re.search(
        r"<<<<<<< SEARCH\n(?P<search>.*?)\n=======",
        payload,
        flags=re.DOTALL,
    )
    if match:
        return str(match.group("search") or "")
    return str(step.get("search") or "")


def _enforce_planner_search_grounding(
    *,
    steps: list[dict[str, Any]],
    planner_packet: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    global _PLANNER_SEARCH_GROUNDED_TOTAL, _PLANNER_SEARCH_GROUNDED_FALSE
    packet = dict(planner_packet or {})
    anchor = dict(packet.get("codeintel_anchor") or {})
    preloaded = str(packet.get("grounded_symbol_source") or anchor.get("output_content") or "")
    grounded = True
    hallucinated_search = ""
    if preloaded:
        for step in steps:
            step_type = str(step.get("tool") or step.get("type") or "").strip()
            if step_type != "edit_file":
                continue
            args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
            grounded_buffer = str(
                args.get("__aix_exact_anchor_buffer") or step.get("planner_grounded_buffer") or preloaded or ""
            )
            search = _step_edit_search_preview(step)
            if search and search not in grounded_buffer:
                grounded = False
                hallucinated_search = search
                break
    _PLANNER_SEARCH_GROUNDED_TOTAL += 1
    if not grounded:
        _PLANNER_SEARCH_GROUNDED_FALSE += 1
    false_rate = _PLANNER_SEARCH_GROUNDED_FALSE / max(_PLANNER_SEARCH_GROUNDED_TOTAL, 1)
    logger.info("PLANNER_SEARCH_GROUNDED=%s false_rate=%.3f", str(grounded).lower(), false_rate)
    if false_rate > 0.05:
        logger.warning("PLANNER_SEARCH_GROUNDED_ALERT rate=%.3f threshold=0.05", false_rate)
    if grounded:
        return steps
    detail = {
        "detail": "Planner search block not found in pre_loaded_read_result.content",
        "search_preview": hallucinated_search[:120],
        "buffer_sha": hashlib.sha256(preloaded.encode()).hexdigest()[:8] if preloaded else None,
        "remediation": "Regenerate with search_range coordinates or search copied byte-for-byte from grounded_symbol_source",
    }
    return [
        _planner_failure_analysis_step(
            reason_code="PLANNER_HALLUCINATED_SEARCH",
            detail=detail,
            failure_analysis=_bounded_edit_surface_failure_text(),
        )
    ]


def _declared_validation_command_text(planner_packet: dict[str, Any] | None) -> str:
    if not isinstance(planner_packet, dict):
        return ""
    command_text = str(planner_packet.get("validation_command") or "").strip()
    if command_text:
        return command_text
    validation_commands = planner_packet.get("validation_commands")
    if isinstance(validation_commands, list) and validation_commands:
        command_text = str(validation_commands[0] or "").strip()
        if command_text:
            return command_text
    allowed_validation_commands = planner_packet.get("allowed_validation_commands")
    if isinstance(allowed_validation_commands, list) and allowed_validation_commands:
        command_text = str(allowed_validation_commands[0] or "").strip()
        if command_text:
            return command_text
    execution_slice = dict(planner_packet.get("execution_slice") or {}) if isinstance(planner_packet, dict) else {}
    validation_commands = execution_slice.get("validation_commands")
    if isinstance(validation_commands, list) and validation_commands:
        return str(validation_commands[0] or "").strip()
    return ""


def _enforce_declared_validation_step(
    steps: list[dict[str, Any]],
    *,
    planner_packet: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    command_text = _declared_validation_command_text(planner_packet)
    if not command_text:
        return steps
    if any(str(step.get("tool") or "").strip() == "failure_analysis" for step in steps):
        return [step for step in steps if str(step.get("tool") or "").strip() != "cmd.run"]
    if not any(
        str(step.get("tool") or "").strip() in {"edit_file", "write_file", "apply_patch", "replace_function"}
        for step in steps
    ):
        return steps

    normalized_steps = [dict(step) for step in steps if str(step.get("tool") or "").strip() != "cmd.run"]
    command_step = {
        "tool": "cmd.run",
        "args": {"command_text": command_text},
        "validation_contract_validation": {
            "status": "hydrated",
            "mode": "declared_validation_command",
            "command_text": command_text,
            "contract_rule": "terminal cmd.run must exactly equal planner_packet.validation_command",
        },
    }
    normalized_steps.append(command_step)
    return normalized_steps


def _normalize_planner_steps(
    raw: Any,
    *,
    max_steps: int,
    grounded_source: str | None = None,
    planner_packet: dict[str, Any] | None = None,
    workspace_root: str | None = None,
    planner_mode: str | None = None,
) -> list[dict[str, Any]]:
    resolved_planner_mode = _resolve_planner_mode(planner_mode=planner_mode, planner_packet=planner_packet)
    found_legacy_keys = _find_legacy_edit_shape_keys(raw)
    if found_legacy_keys:
        raise PlannerBridgeError(
            "PLANNER_LEGACY_SHAPE detail="
            + json.dumps(
                {
                    "detail": "Output contains deprecated keys. Emit self-contained edit_file steps only.",
                    "found_keys": sorted(set(found_legacy_keys)),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    items = _coerce_steps_container(raw)
    normalized: list[dict[str, Any]] = []
    executable_steps: list[dict[str, Any]] = []
    read_buffers: dict[str, dict[str, Any]] = {}
    step_outputs: dict[int, dict[str, Any]] = {}
    virtual_read = _build_virtual_grounded_read(planner_packet)
    start_idx = 0
    if virtual_read is not None:
        normalized.append(virtual_read)
        read_buffers[_planner_step_relative_path(virtual_read) or ""] = {
            "text": str(virtual_read.get("output_content") or ""),
            "step_idx": 0,
        }
        step_outputs[0] = dict(virtual_read)
        start_idx = 1
    for idx, item in enumerate(items, start=start_idx):
        if idx >= max_steps:
            break
        normalized_step = _coerce_planner_step(
            item,
            step_idx=idx,
            planner_packet=planner_packet,
            planner_mode=resolved_planner_mode,
        )
        if normalized_step is None:
            continue
        step_args = dict(normalized_step.get("args") or {}) if isinstance(normalized_step.get("args"), dict) else {}
        step_relative_path = str(step_args.get("relative_path") or "").strip()
        if workspace_root and step_relative_path:
            target_resolution = resolve_target_file(workspace_root=workspace_root, input_path=step_relative_path)
            normalized_relative_path = str(target_resolution.normalized_repo_relative_path or "").strip()
            if normalized_relative_path and normalized_relative_path != step_relative_path:
                step_args["relative_path"] = normalized_relative_path
                normalized_step = dict(normalized_step)
                normalized_step["args"] = step_args
        tool = str(normalized_step.get("tool") or "").strip()
        if tool == "edit_file":
            if resolved_planner_mode == PLANNER_MODE_GENERIC:
                normalized_step = _prefer_latest_explicit_read_source(normalized_step, normalized)
            if (
                resolved_planner_mode == PLANNER_MODE_GENERIC
                and _planner_packet_requires_real_anchor_read(planner_packet)
                and _planner_step_read_source(normalized_step)
                and not any(str(step.get("tool") or "").strip() in {"read_range", "read_file"} for step in normalized)
            ):
                raise PlannerBridgeError(
                    "PLANNER_EDIT_MISSING_READ detail="
                    + json.dumps(
                        {
                            "file": _planner_step_relative_path(normalized_step),
                            "reason": "no_prior_same_file_read",
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            if resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION and _planner_step_read_source(
                normalized_step
            ):
                raise PlannerBridgeError("PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN")
            normalized_step = _hydrate_edit_step_from_search_range(
                normalized_step,
                prior_steps=normalized,
                planner_packet=planner_packet,
                workspace_root=workspace_root,
            )
            normalized_step = _hydrate_anchor_window_edit_from_read_output(
                normalized_step,
                prior_steps=normalized,
                planner_packet=planner_packet,
            )
            preflight_steps = _enforce_planner_search_grounding(
                steps=[normalized_step],
                planner_packet=planner_packet,
            )
            if preflight_steps:
                candidate_step = dict(preflight_steps[0])
                candidate_tool = str(candidate_step.get("tool") or candidate_step.get("type") or "").strip()
                if candidate_tool == "failure_analysis":
                    normalized_step = candidate_step
                    normalized.append(normalized_step)
                    if not bool(normalized_step.get("synthetic")):
                        executable_steps.append(normalized_step)
                    continue
                normalized_step = candidate_step
            guarded_step = _guard_test_only_assertion_inversion(normalized_step)
            guarded_tool = str(guarded_step.get("tool") or guarded_step.get("type") or "").strip()
            if guarded_tool == "failure_analysis":
                normalized_step = guarded_step
                normalized.append(normalized_step)
                if not bool(normalized_step.get("synthetic")):
                    executable_steps.append(normalized_step)
                continue
            normalized_step = guarded_step
            validate_edit_grounding(
                normalized_step,
                normalized,
                read_buffers,
                planner_mode=resolved_planner_mode,
            )
        elif tool in {"read_range", "read_file"}:
            normalized_step["step_idx"] = int(idx)
            normalized_step = _attach_anchor_window_metadata_to_read_step(
                normalized_step,
                planner_packet=planner_packet,
            )
            relative_path = _planner_step_relative_path(normalized_step)
            read_buffer = _extract_read_buffer(normalized_step)
            if relative_path and read_buffer is not None:
                read_buffers[relative_path] = {
                    "text": read_buffer,
                    "step_idx": int(normalized_step.get("step_idx") or idx),
                }
        normalized.append(normalized_step)
        if not bool(normalized_step.get("synthetic")):
            executable_steps.append(normalized_step)
        if isinstance(normalized_step.get("step_idx"), int):
            step_outputs[int(normalized_step["step_idx"])] = dict(normalized_step)
    executable_steps = _enforce_planner_search_grounding(
        steps=executable_steps,
        planner_packet=planner_packet,
    )
    try:
        protocol = validate_v2_executable_plan_protocol(steps=executable_steps, planner_packet=planner_packet)
    except V2ExecutablePlanProtocolError as exc:
        raise PlannerBridgeError(
            f"{exc.code} detail=" + json.dumps(exc.detail, ensure_ascii=True, sort_keys=True)
        ) from exc
    if protocol is not None:
        protocol_form = protocol.form
        for step in executable_steps:
            if str(step.get("tool") or step.get("type") or "").strip() == "edit_file":
                step_args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
                step_args["__aix_v2_protocol_form"] = protocol_form
                step["args"] = step_args
    normalized = [step for step in normalized if bool(step.get("synthetic"))] + [
        dict(step) for step in executable_steps
    ]
    _lint_semantic_noop(normalized, grounded_source)
    effective_task_intent = str((planner_packet or {}).get("task_intent") or "").strip() or "analysis_only"
    validate_quality_enforcement(
        "PLANNER_OUTPUT",
        {"steps": normalized},
        {
            "read_buffers": read_buffers,
            "step_outputs": step_outputs,
            "task": {"task_intent": effective_task_intent},
            "grounded_symbol_source": grounded_source,
            "planner_packet": dict(planner_packet or {}),
            "workspace_root": workspace_root,
        },
    )
    if not executable_steps:
        raise PlannerBridgeError("PLANNER_EMPTY_PLAN")
    return _enforce_declared_validation_step(executable_steps, planner_packet=planner_packet)


def _find_legacy_edit_shape_keys(raw: Any) -> list[str]:
    found: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            explicit_type = str(value.get("type") or "").strip()
            tool_name = str(value.get("tool") or value.get("action") or "").strip()
            if explicit_type == "edit_file":
                if "args" in value:
                    found.add("args")
                if "search_replace_blocks" in value:
                    found.add("search_replace_blocks")
            elif tool_name == "edit_file" and "search_replace_blocks" in value:
                found.add("search_replace_blocks")
            for nested in value.values():
                _walk(nested)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(raw)
    return sorted(found)


def _parse_plan_output(output_text: str) -> Any:
    text = str(output_text or "").strip()
    if not text:
        raise PlannerBridgeError("PLANNER_EMPTY_OUTPUT")
    text = _strip_markdown_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        repaired = _repair_plan_output_text(text)
        if repaired and repaired != text:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        literal_parsed = _parse_python_literal_output(repaired or text)
        if literal_parsed is not None:
            return literal_parsed
        raise PlannerBridgeError(f"PLANNER_OUTPUT_NOT_JSON error={exc}") from exc


def _strip_markdown_code_fence(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _repair_plan_output_text(text: str) -> str | None:
    value = str(text or "").strip()
    if not value:
        return None

    start_candidates = [idx for idx in (value.find("{"), value.find("[")) if idx >= 0]
    if start_candidates:
        value = value[min(start_candidates) :]

    last_object = value.rfind("}")
    last_array = value.rfind("]")
    end = max(last_object, last_array)
    if end >= 0:
        value = value[: end + 1]

    in_string = False
    escape = False
    quote_count = 0
    nesting: list[str] = []
    for ch in value:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            quote_count += 1
            continue
        if in_string:
            continue
        if ch == "{":
            nesting.append("}")
        elif ch == "}":
            if nesting and nesting[-1] == "}":
                nesting.pop()
        elif ch == "[":
            nesting.append("]")
        elif ch == "]":
            if nesting and nesting[-1] == "]":
                nesting.pop()

    repaired = value
    if in_string or (quote_count % 2 == 1):
        repaired += '"'
    if nesting:
        repaired += "".join(reversed(nesting))
    return repaired


def _parse_python_literal_output(text: str) -> Any | None:
    value = str(text or "").strip()
    if not value:
        return None
    try:
        parsed = ast.literal_eval(value)
    except Exception:
        return None
    if isinstance(parsed, dict | list):
        return parsed
    return None


def _contract_fingerprint(contract_text: str) -> str:
    return hashlib.sha256(str(contract_text or "").encode("utf-8")).hexdigest()[:16]


def _resolve_planner_capsule_identity(*, planner_packet: dict[str, Any]) -> tuple[str | None, int | None]:
    capsule_task_id = str(planner_packet.get("task_id") or "").strip() or None
    capsule_task_number = _safe_int(planner_packet.get("task_number"))
    return capsule_task_id, capsule_task_number


def _inject_runtime_capsule_into_envelope(
    *,
    planner_request_envelope: dict[str, Any],
    planner_packet: dict[str, Any],
    raw_task_id: str,
    raw_slice_id: str,
    project_id: str,
) -> dict[str, Any] | None:
    capsule_task_id, capsule_task_number = _resolve_planner_capsule_identity(planner_packet=planner_packet)
    try:
        runtime_capsule = get_latest_runtime_memory_capsule(
            task_id=capsule_task_id,
            project_id=project_id,
            task_number=capsule_task_number,
        )
        if isinstance(runtime_capsule, dict) and runtime_capsule:
            print(
                "RECOVERY_PLANNER_SPAWN_ENTERED "
                f"task={capsule_task_id or capsule_task_number or raw_task_id} "
                "reason=executor_terminal_step_failed "
                f"slice={str(planner_packet.get('slice_id') or raw_slice_id or 'unknown')} "
                f"capsule_id={runtime_capsule.get('capsule_id')}"
            )
            return runtime_capsule
        print(
            "RECOVERY_PLANNER_SPAWN_ENTERED "
            f"task={capsule_task_id or capsule_task_number or raw_task_id} "
            "reason=executor_terminal_step_failed "
            f"slice={str(planner_packet.get('slice_id') or raw_slice_id or 'unknown')} "
            "capsule_id=MISSING"
        )
    except Exception as exc:
        logger.warning("Capsule injection failed (safe fallback): %s", exc)
    return None


def _build_planner_messages(
    *,
    planner_system: str,
    planner_user: str,
    runtime_capsule: dict[str, Any] | None,
    capsule_task_ref: str,
) -> list[dict[str, Any]]:
    if isinstance(runtime_capsule, dict) and runtime_capsule:
        capsule_slice_id = (
            str((runtime_capsule.get("run_ledger_facts") or {}).get("slice_id") or "").strip() or "unknown"
        )
        logger.info("Capsule reference retained for task %s (slice %s)", capsule_task_ref, capsule_slice_id)
        logger.info(
            "CAPSULE_REFERENCE task=%s capsule_id=%s first_200_chars=%s",
            capsule_task_ref,
            runtime_capsule.get("capsule_id"),
            str(runtime_capsule)[:200],
        )
    return [
        {"role": "system", "content": planner_system},
        {"role": "user", "content": planner_user},
    ]


def _build_planner_request_metadata(
    *,
    planner_packet: dict[str, Any],
    packet_fingerprint: str,
    contract_fingerprint: str,
    runtime_capsule: dict[str, Any] | None,
    planner_system_bytes: int,
    planner_user_packet_bytes: int,
    planner_packet_accounting: dict[str, Any],
) -> dict[str, Any]:
    return {
        "planner_request_shape": "aix.utf_task.current",
        "planner_packet_schema_name": str(planner_packet.get("schema_name") or "").strip() or None,
        "planner_packet_schema_version": str(planner_packet.get("schema_version") or "").strip() or None,
        "planner_packet_fingerprint": packet_fingerprint,
        "planner_json_mode": "content_json_object",
        "planner_prompt_authority": "tool_contract_registry",
        "contract_fingerprint": contract_fingerprint,
        "planner_user_packet_authority": "planner_request_envelope.current",
        "planner_secondary_prompt_injected": False,
        "runtime_capsule_injected": bool(runtime_capsule),
        "runtime_capsule_id": str((runtime_capsule or {}).get("capsule_id") or "").strip() or None,
        "planner_system_prompt_bytes": planner_system_bytes,
        "planner_user_packet_bytes": planner_user_packet_bytes,
        "planner_packet_accounting": dict(planner_packet_accounting or {}),
    }


def _build_planner_request_body_preview(
    *,
    provider: str,
    model: str | None,
    messages: list[dict[str, Any]],
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    request_metadata: dict[str, Any],
    timeout_seconds: int | None,
) -> dict[str, Any]:
    return _compile_planner_model_request_body(
        provider=provider,
        model=model,
        messages=messages,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=workspace_root,
        request_metadata=request_metadata,
        timeout_seconds=timeout_seconds,
    )


def _message_content_by_role(messages: list[dict[str, Any]], role: str) -> str:
    for message in messages:
        if str(message.get("role") or "").strip() == role:
            return str(message.get("content") or "").strip()
    return ""


def _compile_planner_model_request_body(
    *,
    provider: str,
    model: str | None,
    messages: list[dict[str, Any]],
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    request_metadata: dict[str, Any],
    timeout_seconds: int | None,
) -> dict[str, Any]:
    system_instruction = _message_content_by_role(messages, "system") or "Return compact JSON only."
    user_instruction = _message_content_by_role(messages, "user")
    repair_context = [
        {
            "role": str(message.get("role") or "").strip(),
            "content": str(message.get("content") or "").strip(),
        }
        for message in messages[2:]
        if str(message.get("role") or "").strip() and str(message.get("content") or "").strip()
    ]
    params: dict[str, Any] = {
        "project_id": project_id,
        "workspace_root": Path(workspace_root).as_posix(),
        "planner_user_request": user_instruction,
    }
    if repair_context:
        params["repair_context"] = repair_context
    ingress_kind = "repair" if repair_context else "planner"
    context = {
        "correlation_id": correlation_id,
        "planner_bridge": "devexecutor_runtime",
        "planner_request_shape": str(request_metadata.get("planner_request_shape") or "aix.utf_task.current"),
    }
    compiled = compile_model_ingress_request(
        kind=ingress_kind,
        task_id=correlation_id,
        task_type="devexecutor_repair_request" if ingress_kind == "repair" else "devexecutor_planner_request",
        params=params,
        actions=["repair_and_return_planner_steps_json"] if ingress_kind == "repair" else ["return_planner_steps_json"],
        context=context,
        provider=provider,
        model=model,
        system_instruction=system_instruction,
        max_tokens=PLANNER_JSON_MAX_TOKENS,
        temperature=0,
        allow_tool_calls=False,
        metadata={
            "planner_bridge": "devexecutor_runtime",
            "project_id": project_id,
            "correlation_id": correlation_id,
            "workspace_root": Path(workspace_root).as_posix(),
            **dict(request_metadata or {}),
        },
    )
    body = dict(compiled.body)
    body["timeout"] = _resolve_planner_timeout_seconds(timeout_seconds)
    body["response_format"] = {"type": "json_object"}
    metadata = dict(body.get("metadata") or {})
    metadata["model_ingress_request_id"] = compiled.request_id
    metadata["model_ingress_fingerprint"] = compiled.fingerprint
    metadata["model_ingress_schema_validation"] = "passed"
    body["metadata"] = metadata
    artifact_refs = persist_model_ingress_presend_artifacts(
        source="planner",
        request_id=compiled.request_id,
        request_body=body,
        artifact=compiled.artifact,
    )
    metadata = dict(body.get("metadata") or {})
    metadata["model_ingress_artifact_refs"] = artifact_refs
    body["metadata"] = metadata
    return body


def _build_planner_trace_packet_id(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    provider: str,
    workspace_root: str,
    prompt: str,
    attempts_used: int = 0,
) -> str:
    digest = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()[:16]
    stable_key = "|".join([tenant_id, project_id, correlation_id, provider, workspace_root, digest, str(attempts_used)])
    return str(uuid5(NAMESPACE_URL, stable_key))


def _workspace_backed_planner_trace_root(*, workspace_root: str) -> Path | None:
    root_text = str(workspace_root or "").strip()
    if not root_text:
        return None
    try:
        root = Path(root_text)
    except Exception:
        return None
    if not root.is_absolute():
        return None
    candidate = root / "projects" / "AIX" / "hyeve-aix-service" / "backend" / "_runlogs" / "planner-traces"
    if candidate.parent.exists() and candidate.parent.is_dir():
        return candidate
    return None


def _resolve_planner_trace_root(*, workspace_root: str) -> Path:
    explicit_root = str(settings.RUNTIME_PLANNER_TRACE_ROOT or "").strip()
    if explicit_root:
        return Path(explicit_root)
    return get_planner_trace_root()


async def _shadow_write_planner_trace_packet(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    provider: str,
    model: str | None,
    prompt: str,
    attempts_used: int,
    raw_output_text: str,
    parsed_output: Any,
    normalized_steps: list[dict[str, Any]],
    transport_diagnostics: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    packet_id = _build_planner_trace_packet_id(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        provider=provider,
        workspace_root=workspace_root,
        prompt=prompt,
        attempts_used=attempts_used,
    )
    packet_payload = build_planner_trace_packet_payload(
        packet_id=packet_id,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=workspace_root,
        provider=provider,
        model=model,
        prompt=prompt,
        attempts_used=attempts_used,
        raw_output_text=raw_output_text,
        parsed_output=parsed_output,
        normalized_steps=normalized_steps,
        transport_diagnostics=transport_diagnostics,
    )
    packet_response, packet_error = await ayrpack_client.submit_packet(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        packet_payload=packet_payload,
        idempotency_key=packet_id,
    )
    if packet_error:
        return None, packet_error
    persisted_packet_id = (
        ((packet_response or {}).get("data") or {}).get("packet_id") if isinstance(packet_response, dict) else None
    )
    trace_packet_id = str(persisted_packet_id or packet_id)
    _, audit_error = await ayrpack_client.append_audit(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        packet_id=trace_packet_id,
        audit_payload={
            "event_type": "planner_trace_shadow_write",
            "event_data_json": {
                "packet_id": trace_packet_id,
                "provider": provider,
                "model": model,
                "attempts_used": attempts_used,
                "normalized_step_count": len(normalized_steps),
            },
            "actor_id": "aix-v2-runtime",
            "status": "recorded",
        },
    )
    if audit_error:
        return trace_packet_id, f"AYRPACK_AUDIT_{audit_error}"
    return trace_packet_id, None


def _maybe_shadow_write_planner_trace(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    provider: str,
    model: str | None,
    prompt: str,
    attempts_used: int,
    raw_output_text: str,
    parsed_output: Any,
    normalized_steps: list[dict[str, Any]],
    transport_diagnostics: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    if not bool(settings.AYRPACK_SHADOW_WRITE_ENABLED):
        return None, "AYRPACK_SHADOW_WRITE_DISABLED"
    if not str(settings.AYRPACK_API_TOKEN or "").strip():
        return None, "AYRPACK_API_TOKEN_MISSING"
    coroutine = _shadow_write_planner_trace_packet(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=workspace_root,
        provider=provider,
        model=model,
        prompt=prompt,
        attempts_used=attempts_used,
        raw_output_text=raw_output_text,
        parsed_output=parsed_output,
        normalized_steps=normalized_steps,
        transport_diagnostics=transport_diagnostics,
    )
    try:
        asyncio.get_running_loop()
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            try:
                return asyncio.run(coroutine)
            except Exception as inner_exc:
                return None, str(inner_exc)
        return None, str(exc)
    result: Future[tuple[str | None, str | None]] = Future()

    def _runner() -> None:
        try:
            result.set_result(asyncio.run(coroutine))
        except Exception as exc:  # pragma: no cover - exercised via result propagation
            result.set_exception(exc)

    thread = Thread(target=_runner, name="ayrpack-planner-shadow-write", daemon=True)
    thread.start()
    thread.join()
    try:
        return result.result()
    except Exception as exc:
        return None, str(exc)


def _persist_planner_trace_local(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    provider: str,
    model: str | None,
    prompt: str,
    attempts_used: int,
    raw_output_text: str,
    parsed_output: Any | None,
    normalized_steps: list[dict[str, Any]] | None,
    planner_packet_snapshot: dict[str, Any] | None = None,
    planner_request_shape: str | None = None,
    planner_packet_fingerprint: str | None = None,
    request_payload_bytes: int | None = None,
    request_metadata: dict[str, Any] | None = None,
    transport_diagnostics: dict[str, Any] | None = None,
    routing_trace: dict[str, Any] | None = None,
    parse_error: str | None = None,
    upstream_payload: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    packet_id = _build_planner_trace_packet_id(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        provider=provider,
        workspace_root=workspace_root,
        prompt=prompt,
        attempts_used=attempts_used,
    )
    payload = {
        "packet_id": packet_id,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id,
        "provider": provider,
        "model": model,
        "workspace_root": workspace_root,
        "attempts_used": attempts_used,
        "planner_prompt": prompt,
        "planner_packet_snapshot": dict(planner_packet_snapshot or {}),
        "planner_request_shape": planner_request_shape,
        "planner_packet_fingerprint": planner_packet_fingerprint,
        "request_payload_bytes": request_payload_bytes,
        "request_metadata": dict(request_metadata or {}),
        "transport_diagnostics": dict(transport_diagnostics or {}),
        "routing_trace": dict(routing_trace or {}),
        "raw_output_text": raw_output_text,
        "parsed_output": parsed_output,
        "normalized_steps": normalized_steps or [],
        "parse_error": parse_error,
        "upstream_payload": upstream_payload,
    }
    try:
        trace_root = _resolve_planner_trace_root(workspace_root=workspace_root)
        trace_root.mkdir(parents=True, exist_ok=True)
        out_path = trace_root / f"{packet_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        latest_path = trace_root / "latest.json"
        latest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        return str(out_path), None
    except Exception as exc:
        return None, str(exc)


def _extract_openai_choice_content(response_json: dict[str, Any]) -> str | None:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    chunks.append(text_value.strip())
        if chunks:
            return "".join(chunks).strip()
    return None


def _extract_openai_tool_call_arguments(response_json: dict[str, Any]) -> str | None:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        first_call = tool_calls[0]
        if isinstance(first_call, dict):
            function = first_call.get("function")
            if isinstance(function, dict):
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments.strip():
                    return arguments.strip()
    function_call = message.get("function_call")
    if isinstance(function_call, dict):
        arguments = function_call.get("arguments")
        if isinstance(arguments, str) and arguments.strip():
            return arguments.strip()
    return None


def _classify_provider_empty_response(data: dict[str, Any]) -> str | None:
    response_json = data.get("response_json")
    if not isinstance(response_json, dict):
        return None
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if content not in (None, ""):
        return None
    if message.get("tool_calls") is not None or message.get("function_call") is not None:
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = response_json.get("usage")
    completion_details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    reasoning_tokens = completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    if isinstance(reasoning_tokens, int) and reasoning_tokens > 0:
        return f"PROVIDER_REASONING_EXHAUSTION reasoning_tokens={reasoning_tokens}"
    return "PROVIDER_EMPTY_RESPONSE"


def _extract_plan_output_text(data: dict[str, Any]) -> str:
    status_value = str(data.get("status") or "").strip().lower()
    error_value = str(data.get("error") or "").strip()
    provider_value = str(data.get("provider") or "").strip()
    resolved_provider_value = str(data.get("resolved_provider") or "").strip()
    if status_value == "failed" or error_value:
        details = [
            "PLANNER_UPSTREAM_FAILED",
            f"status={status_value or 'unknown'}",
            f"error={error_value or 'unknown'}",
        ]
        if provider_value:
            details.append(f"provider={provider_value}")
        if resolved_provider_value:
            details.append(f"resolved_provider={resolved_provider_value}")
        raise PlannerBridgeError(" ".join(details))

    response_json = data.get("response_json")
    if isinstance(response_json, dict):
        if isinstance(response_json.get("steps"), list):
            return json.dumps(response_json, ensure_ascii=True)
        nested = _extract_openai_choice_content(response_json)
        if nested:
            return nested
        tool_arguments = _extract_openai_tool_call_arguments(response_json)
        if tool_arguments:
            return tool_arguments
        empty_response_code = _classify_provider_empty_response(data)
        if empty_response_code:
            raise PlannerBridgeError(empty_response_code)
        return json.dumps(response_json, ensure_ascii=True)

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    # Some integrations return content directly on data payload instead of output_text.
    content = data.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    raise PlannerBridgeError("PLANNER_EMPTY_OUTPUT")


def _looks_like_closed_json_object(raw_output: str) -> bool:
    text = str(raw_output or "").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return False
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in "{[":
            depth += 1
            continue
        if char in "}]":
            depth -= 1
            if depth < 0:
                return False
    return not in_string and depth == 0


def _extract_top_level_json_field_names(raw_output: str, *, limit: int = 2) -> list[str]:
    text = str(raw_output or "").lstrip()
    if not text.startswith("{"):
        return []
    decoder = json.JSONDecoder()
    index = 1
    fields: list[str] = []
    length = len(text)
    while index < length and len(fields) < max(1, int(limit)):
        while index < length and text[index] in " \r\n\t,":
            index += 1
        if index >= length or text[index] == "}":
            break
        try:
            key, next_index = decoder.raw_decode(text, index)
        except Exception:
            break
        if not isinstance(key, str):
            break
        fields.append(key)
        index = next_index
        while index < length and text[index] in " \r\n\t":
            index += 1
        if index >= length or text[index] != ":":
            break
        index += 1
        while index < length and text[index] in " \r\n\t":
            index += 1
        try:
            _value, next_index = decoder.raw_decode(text, index)
        except Exception:
            break
        index = next_index
    return fields


def _build_planner_completion_evidence(
    *,
    raw_output: str,
    transport_diag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = str(raw_output or "")
    normalized_usage = dict((transport_diag or {}).get("normalized_usage") or {})
    evidence = {
        "raw_output_chars": len(text),
        "raw_output_bytes": len(text.encode("utf-8")),
        "finish_reason": (transport_diag or {}).get("finish_reason"),
        "reasoning_tokens": (transport_diag or {}).get("reasoning_tokens"),
        "completion_tokens": normalized_usage.get("completion_tokens"),
        "prompt_tokens": normalized_usage.get("prompt_tokens"),
        "json_closed_cleanly": _looks_like_closed_json_object(text),
        "first_top_level_fields": _extract_top_level_json_field_names(text, limit=2),
        "response_endswith": text[-24:] if text else "",
    }
    stripped = text.rstrip()
    if not stripped:
        evidence["completion_state"] = "empty"
    elif evidence["json_closed_cleanly"]:
        evidence["completion_state"] = "closed_json_object"
    else:
        evidence["completion_state"] = "incomplete_json_object"
    return evidence


def _planner_packet_bound_single_symbol_target(planner_packet: dict[str, Any] | None) -> bool:
    packet = dict(planner_packet or {})
    target_files = [
        str(item).strip().replace("\\", "/").strip("/")
        for item in (packet.get("admitted_target_files") or packet.get("target_files") or [])
        if str(item).strip()
    ]
    if len(target_files) != 1:
        return False
    target_symbol = str(packet.get("target_symbol") or "").strip()
    admitted_symbols = [str(item).strip() for item in packet.get("admitted_symbol_targets") or [] if str(item).strip()]
    return bool(target_symbol or len(admitted_symbols) == 1)


def _planner_schema_validation_errors(
    parsed: Any,
    planner_packet: dict[str, Any] | None = None,
    *,
    planner_mode: str | None = None,
) -> list[str]:
    if not isinstance(parsed, dict):
        return ["top_level_object_required"]
    if "steps" not in parsed:
        return ["missing_required_key:steps"]
    if not isinstance(parsed.get("steps"), list):
        return ["steps_must_be_array"]
    errors: list[str] = []
    resolved_planner_mode = _resolve_planner_mode(planner_mode=planner_mode, planner_packet=planner_packet)
    generic_packet_flow = (
        resolved_planner_mode == PLANNER_MODE_GENERIC and isinstance(planner_packet, dict) and bool(planner_packet)
    )
    packet_bound_target = (
        resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
        or _planner_packet_bound_single_symbol_target(planner_packet)
    )
    for index, step in enumerate(parsed.get("steps") or []):
        if not isinstance(step, dict):
            errors.append(f"step[{index}]_must_be_object")
            continue
        tool_name = str(step.get("tool") or "").strip()
        if tool_name:
            if tool_name == "replace_function":
                args = step.get("args")
                if not isinstance(args, dict):
                    errors.append(f"step[{index}]_tool_args_must_be_object")
                    continue
                if not packet_bound_target and not str(args.get("relative_path") or "").strip():
                    errors.append(f"step[{index}]_replace_function_missing_relative_path")
                if (
                    not packet_bound_target
                    and not str(args.get("function_name") or args.get("qualified_symbol") or "").strip()
                ):
                    errors.append(f"step[{index}]_replace_function_missing_qualified_symbol")
                replacement = str(args.get("replacement") or "").strip()
                edit_intent = str(args.get("edit_intent") or "").strip()
                if not replacement and not edit_intent:
                    errors.append(f"step[{index}]_replace_function_missing_replacement_or_edit_intent")
                continue
            if tool_name == "edit_file":
                args = step.get("args")
                if not isinstance(args, dict):
                    errors.append(f"step[{index}]_tool_args_must_be_object")
                    continue
                if not str(args.get("relative_path") or "").strip():
                    errors.append(f"step[{index}]_edit_file_missing_relative_path")
                grounding_source = str(args.get("grounding_source") or "").strip()
                has_textual_patch_fields = any(
                    str(args.get(key) or "").strip()
                    for key in ("search", "replace", "replacement", "search_replace_blocks")
                )
                has_structured_grounding = bool(
                    grounding_source
                    or isinstance(args.get("search_range"), dict)
                    or isinstance(args.get("edit_range_within_read"), dict)
                    or args.get("read_step_idx") not in (None, "")
                )
                if has_textual_patch_fields:
                    if generic_packet_flow:
                        raw_blocks = str(args.get("search_replace_blocks") or "").strip()
                        if raw_blocks:
                            try:
                                parsed_blocks = _extract_edit_file_blocks(raw_blocks, depth=0)
                            except PlannerBridgeError:
                                parsed_blocks = None
                            if parsed_blocks is None or not parsed_blocks:
                                errors.append(f"step[{index}]_edit_file_malformed_search_replace_blocks")
                        continue
                    if isinstance(args.get("search_range"), dict):
                        continue
                    if args.get("read_source") not in (None, "", {}) and not has_structured_grounding:
                        errors.append(f"step[{index}]_edit_file_read_source_forbidden")
                        continue
                    raw_blocks = str(args.get("search_replace_blocks") or "").strip()
                    if raw_blocks:
                        try:
                            parsed_blocks = _extract_edit_file_blocks(raw_blocks, depth=0)
                        except PlannerBridgeError:
                            parsed_blocks = None
                        if parsed_blocks is None or not parsed_blocks:
                            errors.append(f"step[{index}]_edit_file_malformed_search_replace_blocks")
                            continue
                    if grounding_source not in {"anchor", "read_range"}:
                        errors.append(f"step[{index}]_edit_file_missing_grounding_source")
                        continue
                    if grounding_source == "read_range":
                        edit_range_within_read = args.get("edit_range_within_read")
                        has_valid_range = (
                            isinstance(edit_range_within_read, dict)
                            and isinstance(edit_range_within_read.get("start_line"), int)
                            and isinstance(edit_range_within_read.get("end_line"), int)
                        )
                        if not has_valid_range:
                            errors.append(f"step[{index}]_edit_file_missing_edit_range_within_read")
                        if not isinstance(args.get("read_step_idx"), int):
                            errors.append(f"step[{index}]_edit_file_missing_read_step_idx")
                        if (
                            str(args.get("search") or "").strip()
                            or str(args.get("search_replace_blocks") or "").strip()
                        ):
                            errors.append(f"step[{index}]_edit_file_read_range_search_forbidden")
                    else:
                        if raw_blocks and (
                            str(args.get("search") or "").strip() or str(args.get("replace") or "").strip()
                        ):
                            errors.append(f"step[{index}]_edit_file_textual_patch_fields_forbidden")
                    continue
                if grounding_source == "read_range":
                    edit_range_within_read = args.get("edit_range_within_read")
                    has_valid_range = (
                        isinstance(edit_range_within_read, dict)
                        and isinstance(edit_range_within_read.get("start_line"), int)
                        and isinstance(edit_range_within_read.get("end_line"), int)
                    )
                    if not has_valid_range:
                        errors.append(f"step[{index}]_edit_file_missing_edit_range_within_read")
                    if args.get("search") not in (None, "") or str(args.get("search_replace_blocks") or "").strip():
                        errors.append(f"step[{index}]_edit_file_read_range_search_forbidden")
                else:
                    search_range = args.get("search_range")
                    has_search_range = (
                        isinstance(search_range, dict)
                        and isinstance(search_range.get("start_line"), int)
                        and isinstance(search_range.get("end_line"), int)
                    )
                    if not has_search_range:
                        errors.append(f"step[{index}]_edit_file_missing_search_range")
                if grounding_source not in {"anchor", "read_range"}:
                    errors.append(f"step[{index}]_edit_file_missing_grounding_source")
                if not isinstance(args.get("replacement_text") or args.get("replace_text"), str):
                    errors.append(f"step[{index}]_edit_file_missing_replacement_text")
                if grounding_source == "read_range" and not isinstance(args.get("read_step_idx"), int):
                    errors.append(f"step[{index}]_edit_file_missing_read_step_idx")
                if grounding_source == "anchor" and args.get("read_step_idx") not in (None, ""):
                    errors.append(f"step[{index}]_edit_file_anchor_read_step_idx_forbidden")
                if grounding_source == "anchor" and args.get("edit_range_within_read") not in (None, "", {}):
                    errors.append(f"step[{index}]_edit_file_anchor_edit_range_within_read_forbidden")
                if args.get("read_source") not in (None, "", {}) and not has_search_range:
                    errors.append(f"step[{index}]_edit_file_read_source_forbidden")
            elif tool_name == "cmd.run":
                args = step.get("args")
                if not isinstance(args, dict):
                    errors.append(f"step[{index}]_tool_args_must_be_object")
                elif not str(args.get("command_text") or "").strip():
                    errors.append(f"step[{index}]_cmd_run_missing_command_text")
            elif not isinstance(step.get("args"), dict):
                errors.append(f"step[{index}]_tool_args_must_be_object")
            continue
        step_type = str(step.get("type") or "").strip()
        if step_type == "read_range":
            if not str(step.get("file") or "").strip():
                errors.append(f"step[{index}]_read_range_missing_file")
            continue
        if step_type == "edit_file":
            if not str(step.get("file") or "").strip():
                errors.append(f"step[{index}]_edit_file_missing_file")
            grounding_source = str(step.get("grounding_source") or "").strip()
            has_search_range = isinstance(step.get("search_range"), dict)
            has_textual_patch_fields = any(
                str(step.get(key) or "").strip()
                for key in ("search", "replace", "replacement", "search_replace_blocks")
            )
            has_structured_grounding = bool(
                grounding_source
                or isinstance(step.get("search_range"), dict)
                or isinstance(step.get("edit_range_within_read"), dict)
                or step.get("read_step_idx") not in (None, "")
            )
            if has_textual_patch_fields:
                if generic_packet_flow:
                    raw_blocks = str(step.get("search_replace_blocks") or "").strip()
                    if raw_blocks:
                        try:
                            parsed_blocks = _extract_edit_file_blocks(raw_blocks, depth=0)
                        except PlannerBridgeError:
                            parsed_blocks = None
                        if parsed_blocks is None or not parsed_blocks:
                            errors.append(f"step[{index}]_edit_file_malformed_search_replace_blocks")
                    continue
                if has_search_range:
                    continue
                if step.get("read_source") not in (None, "", {}) and not has_structured_grounding:
                    errors.append(f"step[{index}]_edit_file_read_source_forbidden")
                    continue
                raw_blocks = str(step.get("search_replace_blocks") or "").strip()
                if raw_blocks:
                    try:
                        parsed_blocks = _extract_edit_file_blocks(raw_blocks, depth=0)
                    except PlannerBridgeError:
                        parsed_blocks = None
                    if parsed_blocks is None or not parsed_blocks:
                        errors.append(f"step[{index}]_edit_file_malformed_search_replace_blocks")
                        continue
                if grounding_source not in {"anchor", "read_range"} and not has_search_range:
                    errors.append(f"step[{index}]_edit_file_missing_grounding_source")
                    continue
                if grounding_source == "read_range":
                    if not isinstance(step.get("read_step_idx"), int):
                        errors.append(f"step[{index}]_edit_file_missing_read_step_idx")
                    edit_range_within_read = step.get("edit_range_within_read")
                    has_edit_range_within_read = (
                        isinstance(edit_range_within_read, dict)
                        and isinstance(edit_range_within_read.get("start_line"), int)
                        and isinstance(edit_range_within_read.get("end_line"), int)
                    )
                    if not has_edit_range_within_read:
                        errors.append(f"step[{index}]_edit_file_missing_edit_range_within_read")
                    if str(step.get("search") or "").strip() or str(step.get("search_replace_blocks") or "").strip():
                        errors.append(f"step[{index}]_edit_file_read_range_search_forbidden")
                else:
                    if raw_blocks and (str(step.get("search") or "").strip() or str(step.get("replace") or "").strip()):
                        errors.append(f"step[{index}]_edit_file_textual_patch_fields_forbidden")
                continue
            if grounding_source not in {"anchor", "read_range"} and not has_search_range:
                errors.append(f"step[{index}]_edit_file_missing_grounding_source")
            if grounding_source == "read_range" and not isinstance(step.get("read_step_idx"), int):
                errors.append(f"step[{index}]_edit_file_missing_read_step_idx")
            if grounding_source == "anchor" and step.get("read_step_idx") not in (None, ""):
                errors.append(f"step[{index}]_edit_file_anchor_read_step_idx_forbidden")
            if grounding_source == "anchor" and step.get("edit_range_within_read") not in (None, "", {}):
                errors.append(f"step[{index}]_edit_file_anchor_edit_range_within_read_forbidden")
            search_range = step.get("search_range")
            has_search_range = (
                isinstance(search_range, dict)
                and isinstance(search_range.get("start_line"), int)
                and isinstance(search_range.get("end_line"), int)
            )
            edit_range_within_read = step.get("edit_range_within_read")
            has_edit_range_within_read = (
                isinstance(edit_range_within_read, dict)
                and isinstance(edit_range_within_read.get("start_line"), int)
                and isinstance(edit_range_within_read.get("end_line"), int)
            )
            if grounding_source == "read_range":
                if not has_edit_range_within_read:
                    errors.append(f"step[{index}]_edit_file_missing_edit_range_within_read")
                if isinstance(step.get("search"), str):
                    errors.append(f"step[{index}]_edit_file_read_range_search_forbidden")
                if has_search_range:
                    errors.append(f"step[{index}]_edit_file_read_range_search_range_forbidden")
            elif not has_search_range:
                errors.append(f"step[{index}]_edit_file_missing_search_range")
            if not isinstance(step.get("replacement_text") or step.get("replace_text"), str):
                errors.append(f"step[{index}]_edit_file_missing_replacement_text")
            if step.get("read_source") not in (None, "", {}) and not has_search_range:
                errors.append(f"step[{index}]_edit_file_read_source_forbidden")
            continue
        if step_type == "replace_function":
            if not packet_bound_target and not str(step.get("file") or "").strip():
                errors.append(f"step[{index}]_replace_function_missing_file")
            if (
                not packet_bound_target
                and not str(step.get("qualified_symbol") or step.get("target_symbol") or "").strip()
            ):
                errors.append(f"step[{index}]_replace_function_missing_qualified_symbol")
            replacement = str(step.get("replacement") or "").strip()
            edit_intent = str(step.get("edit_intent") or "").strip()
            if not replacement and not edit_intent:
                errors.append(f"step[{index}]_replace_function_missing_replacement_or_edit_intent")
            continue
        if step_type in {"cmd.run", "run_validation_command"}:
            args = step.get("args")
            command_text = ""
            if isinstance(args, dict):
                command_text = str(args.get("command_text") or "").strip()
            if not command_text:
                command_text = str(step.get("command_text") or step.get("command") or "").strip()
            if not command_text:
                errors.append(f"step[{index}]_cmd_run_missing_command_text")
            continue
        if "args" in step or "search_replace_blocks" in step:
            errors.append(f"step[{index}]_uses_legacy_edit_shape")
            continue
        if step_type == "failure_analysis":
            if str(step.get("reason_code") or "").strip() != "INSUFFICIENT_CONTEXT_FOR_MUTATION":
                errors.append(f"step[{index}]_failure_analysis_invalid_reason_code")
            if not isinstance(step.get("detail"), dict):
                errors.append(f"step[{index}]_failure_analysis_missing_detail")
            continue
        errors.append(f"step[{index}]_unsupported_type:{step_type or 'missing'}")
    return errors


def _planner_v2_protocol_validation_errors(parsed: Any, planner_packet: dict[str, Any] | None) -> list[str]:
    if not isinstance(parsed, dict):
        return []
    raw_steps = parsed.get("steps")
    if not isinstance(raw_steps, list):
        return []
    steps = [dict(step) for step in raw_steps if isinstance(step, dict)]
    try:
        validate_v2_executable_plan_protocol(steps=steps, planner_packet=planner_packet)
    except V2ExecutablePlanProtocolError as exc:
        reason = str(exc.detail.get("reason") or exc.code).strip() or exc.code
        return [reason]
    return []


def _symbol_for_packet_bound_replace_function_step(
    step: dict[str, Any],
    *,
    planner_packet: dict[str, Any] | None,
) -> str:
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    symbol = str(
        step.get("qualified_symbol")
        or step.get("target_symbol")
        or args.get("qualified_symbol")
        or args.get("function_name")
        or args.get("target_symbol")
        or ""
    ).strip()
    expected = str(dict(planner_packet or {}).get("target_symbol") or "").strip()
    return symbol if symbol and (not expected or symbol == expected) else ""


def _file_for_packet_bound_step(step: dict[str, Any]) -> str:
    args = dict(step.get("args") or {}) if isinstance(step.get("args"), dict) else {}
    return str(args.get("relative_path") or step.get("file") or args.get("file") or "").strip().replace("\\", "/")


def _repair_packet_bound_read_range_symbol(
    parsed: Any,
    *,
    planner_packet: dict[str, Any] | None,
    planner_mode: str | None,
) -> Any:
    if (
        _resolve_planner_mode(planner_mode=planner_mode, planner_packet=planner_packet)
        != PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
    ):
        return parsed
    if not isinstance(parsed, dict) or not isinstance(parsed.get("steps"), list):
        return parsed
    steps = [dict(step) if isinstance(step, dict) else step for step in parsed.get("steps") or []]
    if len(steps) < 2 or not all(isinstance(step, dict) for step in steps[:2]):
        return parsed
    read_step = dict(steps[0])
    replace_step = dict(steps[1])
    if _raw_step_kind(read_step) != "read_range" or _raw_step_kind(replace_step) != "replace_function":
        return parsed
    read_args = dict(read_step.get("args") or {}) if isinstance(read_step.get("args"), dict) else {}
    if str(read_step.get("symbol") or read_args.get("symbol") or "").strip():
        return parsed
    read_file = _file_for_packet_bound_step(read_step)
    replace_file = _file_for_packet_bound_step(replace_step)
    target_file = str(dict(planner_packet or {}).get("target_file") or "").strip().replace("\\", "/")
    if not read_file or read_file != replace_file or (target_file and read_file != target_file):
        return parsed
    symbol = _symbol_for_packet_bound_replace_function_step(replace_step, planner_packet=planner_packet)
    if not symbol:
        return parsed
    if read_args:
        read_args.pop("start_line", None)
        read_args.pop("end_line", None)
        read_args["symbol"] = symbol
        read_step["args"] = read_args
    else:
        read_step.pop("start_line", None)
        read_step.pop("end_line", None)
        read_step["symbol"] = symbol
    repaired = dict(parsed)
    steps[0] = read_step
    repaired["steps"] = steps
    return repaired


_RAW_MUTATION_STEP_KINDS = {
    "apply_patch",
    "create_file",
    "delete_file",
    "edit_file",
    "edit_symbol",
    "extend_python_signature",
    "insert_at_anchor",
    "rename_file",
    "replace_function",
    "replace_in_function",
    "replace_range",
    "update_call_kwarg",
    "write_file",
}


def _raw_step_kind(candidate: dict[str, Any]) -> str:
    return str(candidate.get("type") or candidate.get("tool") or candidate.get("action") or "").strip()


def _planner_contract_violation_for_lane(
    parsed: Any,
    planner_packet: dict[str, Any] | None,
    *,
    planner_mode: str | None = None,
) -> str | None:
    if (
        _resolve_planner_mode(planner_mode=planner_mode, planner_packet=planner_packet)
        != PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
    ):
        return None
    if not isinstance(parsed, dict):
        return None
    raw_steps = parsed.get("steps")
    if not isinstance(raw_steps, list):
        return None
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            continue
        step_kind = _raw_step_kind(raw_step)
        if step_kind not in _RAW_MUTATION_STEP_KINDS:
            continue
        if step_kind == "replace_function":
            continue
        return "PLANNER_CONTRACT_VIOLATION detail=" + json.dumps(
            {
                "reason": "packet_bound_single_symbol_replace_function_lane_forbids_non_replace_function_mutation_steps",
                "step_idx": index,
                "step_kind": step_kind,
                "allowed_mutation_step": "replace_function",
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    return None


def _append_json_repair_messages(
    *,
    messages: list[dict[str, Any]],
    raw_output: str,
    error_text: str,
    completion_evidence: dict[str, Any] | None = None,
    planner_packet: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    repaired_messages = list(map(dict, messages))
    completion = dict(completion_evidence or {})
    anchor = _planner_packet_anchor_window(planner_packet)
    preview = str(raw_output or "")[:800]
    if anchor is not None and completion.get("completion_state") == "incomplete_json_object":
        if is_packet_bound_single_symbol_replace_function_lane(planner_packet):
            repaired_messages.append(
                {
                    "role": "assistant",
                    "content": (
                        '{"steps":[{"type":"read_range","file":"...","symbol":"<grounded qualified symbol>"}, '
                        '{"type":"replace_function","file":"...","qualified_symbol":"<grounded qualified symbol>","edit_intent":"..."}'
                    ),
                }
            )
        else:
            repaired_messages.append(
                {
                    "role": "assistant",
                    "content": '{"steps":[{"type":"read_range","file":"...","start_line":1,"end_line":2}, "...truncated..."]}',
                }
            )
    else:
        repaired_messages.append({"role": "assistant", "content": preview})
    compact_anchor_rule = ""
    if is_packet_bound_single_symbol_replace_function_lane(planner_packet):
        compact_anchor_rule = (
            " For this packet-bound single-symbol lane, start over with a fresh compact JSON object with exactly 3 steps total: "
            "step 1 one same-file read_range on the grounded target symbol using file plus symbol only; "
            "step 2 one replace_function spec with file/qualified_symbol/edit_intent plus preserve_constraints and optional line_targets only; "
            "step 3 one run_validation_command, or emit a single failure_analysis instead. "
            "The read_range symbol and replace_function qualified_symbol must both equal the exact fully qualified grounded target symbol. "
            "Do not emit start_line/end_line on that preread step."
        )
    elif anchor is not None:
        compact_anchor_rule = (
            " For this exact anchor-window lane, start over with a fresh compact JSON object with at most 3 steps total: "
            "step 1 read_range using only type/file/start_line/end_line; "
            "step 2 one edit_file with file/grounding_source/read_step_idx/edit_range_within_read/replace; "
            "step 3 one run_validation_command, or emit a single failure_analysis instead."
        )
    repaired_messages.append(
        {
            "role": "user",
            "content": (
                f"Your last output failed planner JSON validation: {error_text}. "
                "Output ONLY corrected JSON that matches the required schema. "
                "No prose. No markdown. "
                f"Completion evidence: {json.dumps(completion, ensure_ascii=True, sort_keys=True)}. "
                "For exact anchor-window read_range steps, emit file/start_line/end_line only and DO NOT inline output_content; "
                "the bridge hydrates it from planner_packet.codeintel_anchor.output_content. "
                "Do NOT emit symbol, context_lines, step_idx, or output_content on that first read_range. "
                "Do not repeat or continue the truncated fragment; regenerate the full JSON object from scratch. "
                "Preserve the grounded structured mutation primitive and terminal cmd.run."
                f"{compact_anchor_rule}"
            ),
        }
    )
    return repaired_messages


def _append_semantic_repair_messages(
    *, messages: list[dict[str, Any]], raw_output: str, error_text: str, planner_packet: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    repaired_messages = list(map(dict, messages))
    repaired_messages.append({"role": "assistant", "content": str(raw_output or "")})
    anchor = dict((planner_packet or {}).get("codeintel_anchor") or {}) if isinstance(planner_packet, dict) else {}
    anchor_file = str(
        anchor.get("file_path") or (planner_packet or {}).get("target_file") or (planner_packet or {}).get("file") or ""
    ).strip()
    anchor_start = _safe_int(anchor.get("start_line"))
    anchor_end = _safe_int(anchor.get("end_line"))
    anchor_guidance = ""
    if is_packet_bound_single_symbol_replace_function_lane(planner_packet):
        anchor_guidance = (
            " For this packet-bound single-symbol retry, emit read_range first and then replace_function. "
            "The read_range preread must use file plus symbol only, and the symbol must exactly equal the fully qualified grounded target symbol. "
            "qualified_symbol must equal the grounded target symbol and replace_function must carry only a bounded rewrite spec: "
            "edit_intent plus preserve_constraints and optional line_targets derived directly from the read_range(symbol) source. "
            "Do not emit start_line/end_line in place of that symbol-scoped preread. "
            "Do not emit replacement, edit_file, SEARCH/REPLACE blocks, placeholder comments, partial body fragments, prose, fences, or extra helper definitions."
        )
    elif anchor_file and anchor_start is not None and anchor_end is not None:
        anchor_guidance = (
            f" For this retry, keep step 1 as read_range on {anchor_file} lines {anchor_start}-{anchor_end}. "
            "The subsequent Form B edit_file.edit_range_within_read must stay inside that exact read output, "
            "must cover only the smallest behavior-bearing lines that actually change, and must not rewrite the full "
            "anchored window or collapse into a one-line identity replacement. "
            "If the required fix cannot be made safely inside that exact window, emit failure_analysis instead."
        )
    repaired_messages.append(
        {
            "role": "user",
            "content": (
                f"Your last planner JSON parsed but failed mutation semantics: {error_text}. "
                "Output ONLY corrected JSON that matches the required schema. "
                "No prose. No markdown. "
                "You must produce a real mutating step for the cited code-change task. "
                "Do not emit an identity edit, unchanged wrapper edit, comment-only/docstring-only edit, "
                "or formatting-only edit. "
                "Mutate the smallest behavior-bearing code block. "
                "If a safe bounded mutation is not possible from the provided evidence, emit "
                'a terminal failure_analysis with reason_code "INSUFFICIENT_CONTEXT_FOR_MUTATION". '
                "The replace text must be byte-different from the current selected grounded content."
                f"{anchor_guidance} "
                "Preserve the grounded structured mutation primitive and terminal cmd.run."
            ),
        }
    )
    return repaired_messages


def _planner_auth_header(*, authorization: str | None) -> str:
    if authorization:
        return authorization
    service_token = str(settings.RUNTIME_PLANNER_AUTH_TOKEN or "").strip()
    if service_token:
        return service_token if service_token.lower().startswith("bearer ") else f"Bearer {service_token}"
    raise PlannerBridgeError("PLANNER_AUTH_REQUIRED")


def _planner_request_payload(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    body: dict[str, Any],
    authorization: str | None,
    timeout_seconds: int | None,
    max_retries: int | None,
    retry_backoff_ms: int | None,
    require_explicit_runtime_base: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    gateway = build_planner_model_gateway()
    return gateway.request_payload(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=workspace_root,
        body=body,
        authorization=authorization,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_ms=retry_backoff_ms,
        require_explicit_runtime_base=require_explicit_runtime_base,
    )


def request_planner_json_via_llm(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    system_prompt: str,
    user_prompt: str,
    provider: str = "openrouter",
    model: str | None = None,
    temperature: float | None = None,
    timeout_seconds: int | None = None,
    max_retries: int | None = None,
    retry_backoff_ms: int | None = None,
    authorization: str | None = None,
    routing_trace: dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    ingress_kind: str = "planner",
    task_type: str = "planner_json_request",
    actions: list[str] | None = None,
    params_override: dict[str, Any] | None = None,
    context_override: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _planner_model_guard(provider=provider, model=model)
    params = dict(
        params_override
        or {
            "project_id": project_id,
            "workspace_root": Path(workspace_root).as_posix(),
            "instruction": str(user_prompt or "").strip(),
        }
    )
    context = dict(
        context_override
        or {
            "correlation_id": correlation_id,
            "planner_bridge": "planner_json_request",
        }
    )
    compiled = compile_model_ingress_request(
        kind=ingress_kind,
        task_id=correlation_id,
        task_type=task_type,
        params=params,
        actions=list(actions or ["return_json_object"]),
        context=context,
        provider=provider,
        model=model,
        system_instruction=system_prompt,
        max_tokens=PLANNER_JSON_MAX_TOKENS,
        temperature=temperature,
        allow_tool_calls=False,
        metadata={
            "planner_bridge": "planner_json_request",
            "project_id": project_id,
            "correlation_id": correlation_id,
            "workspace_root": Path(workspace_root).as_posix(),
        },
    )
    body: dict[str, Any] = dict(compiled.body)
    body["timeout"] = _resolve_planner_timeout_seconds(timeout_seconds)
    body["response_format"] = dict(response_format or {"type": "json_object"})
    metadata = dict(body.get("metadata") or {})
    metadata.update(
        {
            "model_ingress_request_id": compiled.request_id,
            "model_ingress_fingerprint": compiled.fingerprint,
            "model_ingress_schema_validation": "passed",
            "project_id": project_id,
            "correlation_id": correlation_id,
            "workspace_root": Path(workspace_root).as_posix(),
        }
    )
    body["metadata"] = metadata
    artifact_refs = persist_model_ingress_presend_artifacts(
        source=ingress_kind,
        request_id=compiled.request_id,
        request_body=body,
        artifact=compiled.artifact,
    )
    metadata = dict(body.get("metadata") or {})
    metadata["model_ingress_artifact_refs"] = artifact_refs
    body["metadata"] = metadata
    data, transport_diag = _planner_request_payload(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=workspace_root,
        body=body,
        authorization=authorization,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_ms=retry_backoff_ms,
    )
    output_text = _extract_plan_output_text(data)
    normalized_usage = _extract_normalized_usage(data)
    try:
        parsed = json.loads(str(output_text or ""))
    except json.JSONDecodeError as exc:
        raise PlannerBridgeError(f"PLANNER_INVALID_RESPONSE json_parse_failed error={exc}") from exc
    if not isinstance(parsed, dict):
        raise PlannerBridgeError("PLANNER_INVALID_RESPONSE data must decode to an object")
    diagnostics = {
        "planner_used": True,
        "provider": provider,
        "model": model,
        "normalized_usage": normalized_usage,
        "finish_reason": normalized_usage.get("finish_reason"),
        "reasoning_tokens": normalized_usage.get("reasoning_tokens"),
        "routing_trace": dict(routing_trace or {}),
        "planner_raw_output_snippet": str(output_text or "").strip()[:300] or None,
        "model_ingress_artifact_refs": metadata.get("model_ingress_artifact_refs"),
        "prompt_token_breakdown": metadata.get("prompt_token_breakdown"),
        "model_ingress_schema": metadata.get("ingress_schema"),
        **transport_diag,
    }
    return parsed, diagnostics


def _get_structured_plan(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    provider: str,
    model: str | None,
    messages: list[dict[str, Any]],
    request_metadata: dict[str, Any],
    planner_packet: dict[str, Any] | None,
    timeout_seconds: int | None,
    max_retries: int | None,
    retry_backoff_ms: int | None,
    authorization: str | None,
    planner_mode: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str, int]:
    planner_mode = _resolve_planner_mode(planner_mode=planner_mode, planner_packet=planner_packet)
    _planner_model_guard(provider=provider, model=model)
    active_messages = list(map(dict, messages))
    last_raw_output = ""
    transport_diag: dict[str, Any] = {}
    require_explicit_runtime_base = planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
    for attempt in range(MAX_JSON_REPAIRS + 1):
        body = _compile_planner_model_request_body(
            provider=provider,
            model=model,
            messages=active_messages,
            project_id=project_id,
            correlation_id=correlation_id,
            workspace_root=workspace_root,
            request_metadata=request_metadata,
            timeout_seconds=timeout_seconds,
        )
        data, transport_diag = _planner_request_payload(
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            workspace_root=workspace_root,
            body=body,
            authorization=authorization,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff_ms,
            require_explicit_runtime_base=require_explicit_runtime_base,
        )
        normalized_usage = _extract_normalized_usage(data)
        transport_diag["normalized_usage"] = dict(normalized_usage)
        transport_diag["finish_reason"] = normalized_usage.get("finish_reason")
        transport_diag["reasoning_tokens"] = normalized_usage.get("reasoning_tokens")
        try:
            last_raw_output = _extract_plan_output_text(data)
        except PlannerBridgeError as exc:
            raise PlannerBridgeError(
                str(exc),
                diagnostics={"upstream_payload": data, "request_body": body, "raw_output_text": ""},
            ) from exc
        completion_evidence = _build_planner_completion_evidence(
            raw_output=last_raw_output,
            transport_diag=transport_diag,
        )
        transport_diag["planner_completion_evidence"] = dict(completion_evidence)
        try:
            parsed = json.loads(str(last_raw_output or ""))
        except json.JSONDecodeError as exc:
            if attempt < MAX_JSON_REPAIRS:
                active_messages = _append_json_repair_messages(
                    messages=active_messages,
                    raw_output=last_raw_output,
                    error_text=f"invalid_json:{exc}",
                    completion_evidence=completion_evidence,
                    planner_packet=planner_packet,
                )
                continue
            raise PlannerBridgeError(
                "PLANNER_JSON_INVALID detail="
                + json.dumps(
                    {
                        "raw": str(last_raw_output or "")[:200],
                        "error": str(exc),
                        "completion_evidence": completion_evidence,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                diagnostics={
                    "upstream_payload": data,
                    "request_body": body,
                    "raw_output_text": last_raw_output,
                    "planner_completion_evidence": completion_evidence,
                },
            ) from exc
        packet_bound_lane = planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
        parsed = _repair_packet_bound_read_range_symbol(
            parsed,
            planner_packet=planner_packet,
            planner_mode=planner_mode,
        )
        raw_step_kinds = (
            [
                _raw_step_kind(step)
                for step in parsed.get("steps") or []
                if isinstance(step, dict) and _raw_step_kind(step)
            ]
            if isinstance(parsed, dict)
            else []
        )
        transport_diag["packet_bound_lane"] = packet_bound_lane
        transport_diag["real_output_tool_types"] = list(raw_step_kinds)
        transport_diag["lane_contract_expected"] = "replace_function_only" if packet_bound_lane else "generic"
        contract_violation = _planner_contract_violation_for_lane(
            parsed,
            planner_packet,
            planner_mode=planner_mode,
        )
        if contract_violation:
            transport_diag["lane_contract_accepted"] = False
            transport_diag["planner_contract_rejection_reason"] = contract_violation
            transport_diag["validation_stage_of_rejection"] = "planner_contract"
            raise PlannerBridgeError(
                contract_violation,
                diagnostics={
                    "upstream_payload": data,
                    "request_body": body,
                    "raw_output_text": last_raw_output,
                    "planner_parsed_output": parsed,
                    "planner_contract_violation": contract_violation,
                    **transport_diag,
                },
            )
        validation_errors = _planner_schema_validation_errors(
            parsed,
            planner_packet=planner_packet,
            planner_mode=planner_mode,
        )
        validation_errors.extend(_planner_v2_protocol_validation_errors(parsed, planner_packet))
        if validation_errors:
            transport_diag["lane_contract_accepted"] = False if packet_bound_lane else None
            transport_diag["planner_contract_rejection_reason"] = "; ".join(validation_errors)
            transport_diag["validation_stage_of_rejection"] = "planner_schema"
            if attempt < MAX_JSON_REPAIRS:
                active_messages = _append_json_repair_messages(
                    messages=active_messages,
                    raw_output=last_raw_output,
                    error_text=f"schema_invalid:{'; '.join(validation_errors)}",
                )
                continue
            raise PlannerBridgeError(
                "PLANNER_SCHEMA_INVALID detail="
                + json.dumps({"errors": validation_errors}, ensure_ascii=True, sort_keys=True),
                diagnostics={
                    "upstream_payload": data,
                    "request_body": body,
                    "raw_output_text": last_raw_output,
                    "planner_parsed_output": parsed,
                    "planner_validation_errors": list(validation_errors),
                    **transport_diag,
                },
            )
        transport_diag["planner_parsed_output"] = parsed
        transport_diag["planner_validation_errors"] = []
        transport_diag["lane_contract_accepted"] = True if packet_bound_lane else None
        transport_diag["planner_contract_rejection_reason"] = None
        transport_diag["validation_stage_of_rejection"] = None
        return parsed, transport_diag, last_raw_output, attempt
    raise PlannerBridgeError("PLANNER_JSON_INVALID detail=" + json.dumps({"raw": last_raw_output[:200]}))


def _is_semantic_repairable_planner_error(exc: PlannerBridgeError) -> bool:
    return str(exc).startswith("MUTATION_REQUIRED_BUT_ABSENT")


def plan_steps_via_llm(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    prompt: str,
    planner_packet: dict[str, Any] | None = None,
    planner_mode: str | None = None,
    provider: str = "openrouter",
    model: str | None = None,
    temperature: float | None = None,
    max_steps: int | None = None,
    timeout_seconds: int | None = None,
    max_retries: int | None = None,
    retry_backoff_ms: int | None = None,
    authorization: str | None = None,
    planner_attempt_number: int | None = None,
    routing_trace: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_task_id = str((planner_packet or {}).get("task_id") or (planner_packet or {}).get("task_number") or "unknown")
    raw_slice_id = str((planner_packet or {}).get("slice_id") or "unknown")
    print(f"DEBUG_PLANNER_ENTRY task={raw_task_id} lane=planning_safe capsule_present=False")
    user_prompt = str(prompt or "").strip()
    if not user_prompt:
        raise PlannerBridgeError("PLANNER_PROMPT_REQUIRED")
    _planner_model_guard(provider=provider, model=model)
    resolved_planner_mode = _resolve_planner_mode(planner_mode=planner_mode, planner_packet=planner_packet)
    planner_packet_audit: dict[str, Any] = {}
    planner_packet_serialized_bytes = 0
    if resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
        (
            validated_planner_packet,
            planner_packet_audit,
            planner_packet_serialized_bytes,
        ) = _assert_v2_planner_packet_boundary(planner_packet or {})
        validated_planner_packet = _sanitize_planner_packet_for_ast_contract(validated_planner_packet)
    else:
        raw_packet = dict(planner_packet or {}) if isinstance(planner_packet, dict) else {}
        validated_planner_packet = _sanitize_planner_packet_for_ast_contract(raw_packet) if raw_packet else {}
        if raw_packet:
            cleaned_packet = _canonicalize_generic_packet_for_request_envelope(validated_planner_packet)
            planner_packet_audit = {
                "total_bytes": len(
                    json.dumps(cleaned_packet, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                ),
                "field_bytes": {
                    str(key): len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
                    for key, value in cleaned_packet.items()
                    if str(key).strip()
                },
                "forbidden_fields": [],
                "duplicate_fields": [],
            }
            planner_packet_serialized_bytes = int(planner_packet_audit["total_bytes"])
    limit = max(1, min(int(max_steps or settings.RUNTIME_PLANNER_MAX_STEPS), 64))
    planner_system = build_planner_contract_text(validated_planner_packet)
    contract_fingerprint = _contract_fingerprint(planner_system)
    packet_fingerprint = _planner_packet_fingerprint(validated_planner_packet)
    capsule_task_id, capsule_task_number = _resolve_planner_capsule_identity(planner_packet=validated_planner_packet)
    planner_packet_accounting: dict[str, Any] = {}
    runtime_capsule: dict[str, Any] | None = None
    if resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
        envelope_packet = (
            validated_planner_packet
            if resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
            else _canonicalize_generic_packet_for_request_envelope(validated_planner_packet)
        )
        planner_request_envelope, planner_packet_accounting = build_planner_request_envelope_with_accounting(
            prompt=user_prompt,
            planner_packet=envelope_packet,
            task_intent="code_change_required",
            max_steps=limit,
            planner_attempt_number=max(int(planner_attempt_number or 0), 1),
            project_id=project_id,
            task_id=raw_task_id,
        )
        runtime_capsule = _inject_runtime_capsule_into_envelope(
            planner_request_envelope=planner_request_envelope,
            planner_packet=validated_planner_packet,
            raw_task_id=raw_task_id,
            raw_slice_id=raw_slice_id,
            project_id=project_id,
        )
        if isinstance(runtime_capsule, dict) and runtime_capsule:
            references = dict(planner_request_envelope.get("references") or {})
            capsule_id = str(runtime_capsule.get("capsule_id") or "").strip()
            if capsule_id:
                references["runtime_capsule_ref"] = f"run_ledger://memory_capsules/{capsule_id}"
                planner_request_envelope["references"] = references
                references_bytes = len(json.dumps(references, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
                planner_packet_accounting["component_bytes"]["references"] = references_bytes
                planner_packet_accounting["total_bytes"] = len(
                    json.dumps(planner_request_envelope, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                )
                planner_packet_accounting["top_contributors"] = sorted(
                    [
                        {
                            "component": "planner_packet",
                            "bytes": int(planner_packet_accounting["component_bytes"]["planner_packet"]),
                        },
                        {
                            "component": "execution_contract",
                            "bytes": int(planner_packet_accounting["component_bytes"]["execution_contract"]),
                        },
                        {
                            "component": "task_identity",
                            "bytes": int(planner_packet_accounting["component_bytes"]["task_identity"]),
                        },
                        {"component": "references", "bytes": references_bytes},
                        {
                            "component": "planner_packet.allowed_surface.anchor",
                            "bytes": int(
                                planner_packet_accounting["planner_packet_component_bytes"]["allowed_surface.anchor"]
                            ),
                        },
                        {
                            "component": "planner_packet.core_contract",
                            "bytes": int(planner_packet_accounting["planner_packet_component_bytes"]["core_contract"]),
                        },
                    ],
                    key=lambda item: cast(int, item["bytes"]),
                    reverse=True,
                )
        planner_user = serialize_planner_request_envelope(envelope=planner_request_envelope)
    else:
        planner_user = user_prompt
    planner_system_bytes = len(planner_system.encode("utf-8"))
    planner_user_packet_bytes = len(planner_user.encode("utf-8"))
    planner_packet_accounting["planner_packet_audit"] = dict(planner_packet_audit or {})
    planner_packet_accounting["planner_packet_serialized_bytes"] = int(planner_packet_serialized_bytes or 0)
    if (
        resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION
        and planner_user_packet_bytes > MUTATION_PLANNER_PACKET_CAP_BYTES
    ):
        raise PlannerBridgeError(
            "PLANNER_PAYLOAD_CAP_EXCEEDED"
            f" payload_bytes={planner_user_packet_bytes}"
            f" cap_bytes={MUTATION_PLANNER_PACKET_CAP_BYTES}"
            " packet_scope=mutation_lane_user_packet",
            diagnostics={
                "payload_bytes": planner_user_packet_bytes,
                "payload_cap_bytes": MUTATION_PLANNER_PACKET_CAP_BYTES,
                "packet_scope": "mutation_lane_user_packet",
                "planner_request_shape": "aix.utf_task.current",
                "planner_system_prompt_bytes": planner_system_bytes,
                "planner_user_packet_bytes": planner_user_packet_bytes,
                "planner_packet_accounting": planner_packet_accounting,
                "planner_packet_top_contributors": list(planner_packet_accounting.get("top_contributors") or []),
            },
        )
    if resolved_planner_mode == PLANNER_MODE_PACKET_BOUND_REPLACE_FUNCTION:
        initial_message_pair = tuple(
            _build_planner_messages(
                planner_system=planner_system,
                planner_user=planner_user,
                runtime_capsule=runtime_capsule,
                capsule_task_ref=str(capsule_task_id or capsule_task_number or "unknown"),
            )
        )
    else:
        initial_message_pair = (
            {"role": "system", "content": planner_system},
            {"role": "user", "content": planner_user},
        )
    request_metadata = _build_planner_request_metadata(
        planner_packet=validated_planner_packet,
        packet_fingerprint=packet_fingerprint or "",
        contract_fingerprint=contract_fingerprint,
        runtime_capsule=runtime_capsule,
        planner_system_bytes=planner_system_bytes,
        planner_user_packet_bytes=planner_user_packet_bytes,
        planner_packet_accounting=planner_packet_accounting,
    )
    request_metadata["planner_mode"] = resolved_planner_mode
    request_shape = "aix.utf_task.current"
    planner_user_packet_authority = "planner_request_envelope.current"
    if resolved_planner_mode == PLANNER_MODE_GENERIC:
        request_shape = "generic_prompt"
        planner_user_packet_authority = "raw_user_prompt"
        request_metadata["planner_request_shape"] = request_shape
        request_metadata["planner_user_packet_authority"] = planner_user_packet_authority
    try:
        request_body_preview = _build_planner_request_body_preview(
            provider=provider,
            model=model,
            messages=list(initial_message_pair),
            project_id=project_id,
            correlation_id=correlation_id,
            workspace_root=workspace_root,
            request_metadata=request_metadata,
            timeout_seconds=timeout_seconds,
        )
    except ModelIngressGateError as exc:
        raise PlannerBridgeError(str(exc), diagnostics=dict(exc.diagnostics)) from exc
    active_messages = list(map(dict, initial_message_pair))
    semantic_repair_attempts_used = 0
    for semantic_attempt in range(MAX_SEMANTIC_REPAIRS + 1):
        try:
            parsed, transport_diag, output_text, json_repair_attempts_used = _get_structured_plan(
                tenant_id=tenant_id,
                project_id=project_id,
                correlation_id=correlation_id,
                workspace_root=workspace_root,
                provider=provider,
                model=model,
                messages=active_messages,
                request_metadata=request_metadata,
                planner_packet=validated_planner_packet,
                planner_mode=resolved_planner_mode,
                authorization=authorization,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                retry_backoff_ms=retry_backoff_ms,
            )
        except PlannerBridgeError as exc:
            trace_attempts_used = max(int(planner_attempt_number or 0), 1)
            exc_diagnostics = dict(getattr(exc, "diagnostics", {}) or {})
            upstream_payload = dict(exc_diagnostics.get("upstream_payload") or {})
            if "request_body" not in upstream_payload:
                upstream_payload["request_body"] = request_body_preview
            if not upstream_payload:
                upstream_payload = {"transport_error": str(exc), "request_body": request_body_preview}
            local_trace_path, local_trace_error = _persist_planner_trace_local(
                tenant_id=tenant_id,
                project_id=project_id,
                correlation_id=correlation_id,
                workspace_root=Path(workspace_root).as_posix(),
                provider=provider,
                model=model,
                prompt=user_prompt,
                attempts_used=trace_attempts_used,
                raw_output_text=str(exc_diagnostics.get("raw_output_text") or ""),
                parsed_output=None,
                normalized_steps=None,
                planner_packet_snapshot=validated_planner_packet,
                planner_request_shape=request_shape,
                planner_packet_fingerprint=packet_fingerprint,
                request_payload_bytes=None,
                request_metadata=request_metadata,
                transport_diagnostics=exc_diagnostics,
                routing_trace=routing_trace,
                parse_error=str(exc),
                upstream_payload=upstream_payload,
            )
            detail = str(exc)
            if local_trace_path:
                detail = f"{detail} local_trace_ref={local_trace_path}"
            if local_trace_error:
                detail = f"{detail} local_trace_error={local_trace_error}"
            raise PlannerBridgeError(detail, diagnostics=exc_diagnostics) from exc
        trace_attempts_used = max(int(planner_attempt_number or 0), int(transport_diag.get("attempts") or 0))
        try:
            hydrated_parsed = _hydrate_anchor_window_read_output_content(parsed, validated_planner_packet)
            validated_anchor = dict((validated_planner_packet.get("allowed_surface") or {}).get("anchor") or {})
            steps = _normalize_planner_steps(
                hydrated_parsed,
                max_steps=limit,
                grounded_source=(
                    str(
                        validated_planner_packet.get("grounded_symbol_source")
                        or validated_anchor.get("compact_snippet")
                        or ""
                    ).strip()
                    or None
                ),
                planner_packet=validated_planner_packet,
                workspace_root=workspace_root,
                planner_mode=resolved_planner_mode,
            )
            break
        except PlannerBridgeError as exc:
            if semantic_attempt < MAX_SEMANTIC_REPAIRS and _is_semantic_repairable_planner_error(exc):
                semantic_repair_attempts_used = semantic_attempt + 1
                active_messages = _append_semantic_repair_messages(
                    messages=active_messages,
                    raw_output=output_text,
                    error_text=str(exc),
                    planner_packet=validated_planner_packet,
                )
                continue
            transport_diag_snapshot = dict(transport_diag)
            local_trace_path, local_trace_error = _persist_planner_trace_local(
                tenant_id=tenant_id,
                project_id=project_id,
                correlation_id=correlation_id,
                workspace_root=Path(workspace_root).as_posix(),
                provider=provider,
                model=model,
                prompt=user_prompt,
                attempts_used=trace_attempts_used,
                raw_output_text=output_text,
                parsed_output=None,
                normalized_steps=None,
                planner_packet_snapshot=validated_planner_packet,
                planner_request_shape="aix.utf_task.current",
                planner_packet_fingerprint=packet_fingerprint,
                request_payload_bytes=int(transport_diag.get("payload_bytes") or 0) or None,
                request_metadata=request_metadata,
                transport_diagnostics=transport_diag_snapshot,
                routing_trace=routing_trace,
                parse_error=str(exc),
                upstream_payload={"parsed_output": parsed, "request_body": request_body_preview},
            )
            detail = str(exc)
            if local_trace_path:
                detail = f"{detail} local_trace_ref={local_trace_path}"
            if local_trace_error:
                detail = f"{detail} local_trace_error={local_trace_error}"
            raise PlannerBridgeError(detail, diagnostics=transport_diag_snapshot) from exc
    trace_packet_id, trace_error = _maybe_shadow_write_planner_trace(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=Path(workspace_root).as_posix(),
        provider=provider,
        model=model,
        prompt=user_prompt,
        attempts_used=trace_attempts_used,
        raw_output_text=output_text,
        parsed_output=parsed,
        normalized_steps=steps,
        transport_diagnostics=dict(transport_diag),
    )
    local_trace_path, local_trace_error = _persist_planner_trace_local(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        workspace_root=Path(workspace_root).as_posix(),
        provider=provider,
        model=model,
        prompt=user_prompt,
        attempts_used=trace_attempts_used,
        raw_output_text=output_text,
        parsed_output=parsed,
        normalized_steps=steps,
        planner_packet_snapshot=validated_planner_packet,
        planner_request_shape=request_shape,
        planner_packet_fingerprint=packet_fingerprint,
        request_payload_bytes=int(transport_diag.get("payload_bytes") or 0) or None,
        request_metadata=request_metadata,
        transport_diagnostics=dict(transport_diag),
        routing_trace=routing_trace,
    )
    diagnostics = {
        "planner_used": True,
        "planner_mode": resolved_planner_mode,
        "provider": provider,
        "model": model,
        "planner_raw_output_snippet": str(output_text or "").strip()[:300] or None,
        "planner_packet_forwarded": bool(isinstance(planner_packet, dict) and planner_packet),
        "planner_request_shape": request_shape,
        "planner_json_mode": "content_json_object",
        "json_repair_attempts_used": int(json_repair_attempts_used),
        "semantic_repair_attempts_used": int(semantic_repair_attempts_used),
        "planner_packet_fingerprint": packet_fingerprint,
        "planner_prompt_authority": "tool_contract_registry",
        "contract_fingerprint": contract_fingerprint,
        "planner_user_packet_authority": planner_user_packet_authority,
        "planner_secondary_prompt_injected": False,
        "runtime_capsule_injected": bool(runtime_capsule),
        "runtime_capsule_id": str((runtime_capsule or {}).get("capsule_id") or "").strip() or None,
        "planner_system_prompt_bytes": planner_system_bytes,
        "planner_user_packet_bytes": planner_user_packet_bytes,
        "planner_packet_accounting": dict(planner_packet_accounting or {}),
        "planner_packet_top_contributors": list(planner_packet_accounting.get("top_contributors") or []),
        "max_steps": limit,
        "timeout_seconds": transport_diag["timeout_seconds"],
        "normalized_usage": dict(transport_diag.get("normalized_usage") or {}),
        "finish_reason": transport_diag.get("finish_reason"),
        "reasoning_tokens": transport_diag.get("reasoning_tokens"),
        "planner_completion_evidence": dict(transport_diag.get("planner_completion_evidence") or {}),
        "routing_trace": dict(routing_trace or {}),
        "max_retries": transport_diag["max_retries"],
        "retry_backoff_ms": transport_diag["retry_backoff_ms"],
        "target_url": transport_diag["target_url"],
        "attempts": transport_diag["attempts"],
        "configured_retries_used": transport_diag.get("configured_retries_used"),
        "fresh_connect_retry_used": transport_diag.get("fresh_connect_retry_used"),
        "socket_preflight_attempted": transport_diag.get("socket_preflight_attempted"),
        "socket_preflight_skipped": transport_diag.get("socket_preflight_skipped"),
        "socket_preflight_ok": transport_diag.get("socket_preflight_ok"),
        "socket_preflight_error": transport_diag.get("socket_preflight_error"),
        "transport_failure_code": transport_diag.get("transport_failure_code"),
        "planner_attempt_number": trace_attempts_used,
        "trace_packet_id": trace_packet_id,
        "trace_ref": f"ayrpack://packet/{trace_packet_id}" if trace_packet_id else None,
        "trace_shadow_write_error": trace_error,
        "trace_shadow_role": SHADOW_TRACE_ROLE,
        "trace_shadow_authority": SHADOW_TRACE_AUTHORITY,
        "trace_shadow_required_for_control_plane": False,
        "canonical_operator_sources": list(CANONICAL_OPERATOR_SOURCES),
        "local_trace_ref": local_trace_path,
        "local_trace_error": local_trace_error,
        "shadow_trace_contract": _shadow_trace_contract(),
        "request_build_ms": transport_diag.get("request_build_ms"),
        "model_ingress_artifact_refs": dict(transport_diag.get("model_ingress_artifact_refs") or {}),
        "prompt_token_breakdown": dict(transport_diag.get("prompt_token_breakdown") or {}),
        "transport_attempts": list(transport_diag.get("transport_attempts") or []),
        "transport_phase_timings": dict(transport_diag.get("transport_phase_timings") or {}),
        "failure_phase": transport_diag.get("failure_phase"),
    }
    return steps, diagnostics


__all__ = ["PlannerBridgeError", "plan_steps_via_llm", "request_planner_json_via_llm"]
