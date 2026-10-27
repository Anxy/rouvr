"""
Custom router for deterministic model gateway resolution (AIX M5 SG2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, ValidationError, model_validator

from backend.clients.ayrpack_client import ayrpack_client
from backend.clients.codeintel_query_client import codeintel_query_client
from backend.clients.opsbus_run_logs_client import opsbus_run_logs_client
from backend.clients.workhub_action_map_client import workhub_action_map_client
from backend.clients.workhub_enhancement_stream_client import workhub_enhancement_stream_client
from backend.clients.workhub_feedback_intake_client import workhub_feedback_intake_client
from backend.clients.workhub_task_evidence_client import workhub_task_evidence_client
from backend.dependencies.aix_gateway_decisions_service_dependencies import require_permission, validate_tenant_access
from backend.hyeve_aix_service.config import settings
from backend.models.admin_audit_log_service_models import AdminAuditLogCreate
from backend.models.aix_gateway_decisions_service_models import AixGatewayDecisionsCreate
from backend.models.aix_model_gateway_policies_service_models import AixModelGatewayPoliciesUpdate
from backend.models.aix_model_health_status_service_models import AixModelHealthStatusCreate, AixModelHealthStatusUpdate
from backend.models.aix_model_provider_bindings_service_models import (
    AixModelProviderBindingsCreate,
    AixModelProviderBindingsUpdate,
)
from backend.models.planner_slice_packet_models import PlannerSlicePacketCurrent
from backend.services.admin_audit_log_service import admin_audit_log_service
from backend.services.aix_escalation_policies_service import aix_escalation_policies_service
from backend.services.aix_gateway_decisions_service import aix_gateway_decisions_service
from backend.services.aix_model_gateway_policies_service import aix_model_gateway_policies_service
from backend.services.aix_model_health_status_service import aix_model_health_status_service
from backend.services.aix_model_provider_bindings_service import aix_model_provider_bindings_service
from backend.services.ayrpack_trace_packet_service import build_model_gateway_trace_packet_payload
from backend.services.business_kpi_service import compute_business_kpis
from backend.services.drift_quality_service import assess_action_map_drift_quality
from backend.services.marketplace_contract_service import (
    build_marketplace_install_audit_event,
    validate_marketplace_template_record,
)
from backend.services.model_lane_policy_service import ModelLanePolicyService
from backend.services.model_routing_policy_engine import RoutingDecision, resolve_model_route
from backend.services.openrouter_model_catalog_service import fetch_openrouter_model_catalog
from backend.services.plugin_runtime_policy_service import evaluate_plugin_runtime_policy
from backend.services.redaction_worker_service import apply_redaction_worker
from backend.services.retrieval_policy_service import evaluate_retrieval_policy
from backend.services.router_v2_scoring_service import build_model_leaderboard
from backend.services.template_pack_governance_service import validate_template_pack_compatibility
from backend.services.voice_nlp_policy_service import evaluate_voice_nlp_enablement, simulate_voice_nlp_rollback_drill
from backend.services.worker_autoscale_policy_service import compute_autoscale_decision

def _synthesize_task_ticket_contract(*, title: str, description: str | None, metadata: dict[str, Any]) -> dict[str, Any]:
    """Resolve ticket-contract synthesis lazily.

    The ``hyeve_task_contract_engine`` package is not published and is absent
    from current hyeve monorepo history, so module import must not require
    it: the model-gateway auxiliary surface stays bootable and only the
    endpoint that actually synthesizes a ticket contract fails closed (503)
    when the package is unavailable.
    """
    try:
        from hyeve_task_contract_engine import (  # noqa: PLC0415
            synthesize_task_ticket_contract,
        )
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "TICKET_CONTRACT_ENGINE_UNAVAILABLE",
                "message": (
                    "hyeve_task_contract_engine is not installed; "
                    "ticket-contract synthesis is unavailable."
                ),
            },
        ) from exc
    return dict(
        synthesize_task_ticket_contract(
            title=title, description=description, metadata=metadata
        )
    )

CODEINTEL_EVIDENCE_CONTRACT_NAME = "wh_codeintel_evidence"
CODEINTEL_EVIDENCE_CONTRACT_VERSION = "2.0.0"

router = APIRouter(
    prefix="/api/aix/model-gateway",
    tags=["aix model gateway"],
)

_MODEL_LANE_POLICY_SERVICE = ModelLanePolicyService()


class ResolveModelGatewayRequest(BaseModel):
    project_id: str | None = None
    correlation_id: str = Field(..., min_length=1)
    task_type: str = Field(..., min_length=1)
    risk_level: str = Field(..., min_length=1)
    model_policy: Literal["slavx_only", "allow_escalate", "onlineai_only"]
    retry_count: int = Field(0, ge=0)
    phase: Literal["planner", "executor", "verifier"] = "executor"
    complexity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    workhub_task_id: str | None = None
    workhub_task_number: int | None = None
    opsbus_run_id: str | None = None
    tokens_used: int | None = Field(None, ge=0)
    estimated_cost: float | None = Field(None, ge=0)
    latency_ms: int | None = Field(None, ge=0)
    retrieval_mode: Literal["codeintel_first", "direct_filesystem"] = "codeintel_first"
    retrieval_override_reason: str | None = None
    retrieval_paths: list[str] = Field(default_factory=list)
    action_map_name: str = "default_dispatch"
    action_map_version: int | None = Field(default=None, ge=1)
    intent_capability: str = "model_routing"
    task_title: str | None = None
    task_description: str | None = None
    task_metadata_json: dict[str, Any] | None = None
    ticket_contract_current: dict[str, Any] | None = None
    canonical_task_contract: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _normalize_current_ticket_contract(self) -> ResolveModelGatewayRequest:
        if self.ticket_contract_current and not self.canonical_task_contract:
            self.canonical_task_contract = dict(self.ticket_contract_current)
        return self


class ResolveModelGatewayResponse(BaseModel):
    success: bool = True
    message: str = "Model route resolved"
    data: dict


class DriftProposalRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    map_name: str = "default_dispatch"
    task_type: str = "code_change"
    risk_level: str = "medium"
    model_policy: Literal["slavx_only", "allow_escalate", "onlineai_only"] = "allow_escalate"
    entity_query: str | None = None
    force_proposal: bool = False


class DriftProposalResponse(BaseModel):
    success: bool = True
    message: str = "Drift proposal evaluated"
    data: dict


class ModelRegistryResponse(BaseModel):
    success: bool = True
    message: str = "Model registry retrieved"
    data: dict


class ModelRegistrySyncResponse(BaseModel):
    success: bool = True
    message: str = "Model registry synchronized"
    data: dict


class ModelLedgerResponse(BaseModel):
    success: bool = True
    message: str = "Model cost ledger retrieved"
    data: dict


class ModelLeaderboardResponse(BaseModel):
    success: bool = True
    message: str = "Model leaderboard retrieved"
    data: dict


class TenantThresholdsUpdateRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    policy_key: str = Field(..., min_length=1)
    task_type: str = Field(..., min_length=1)
    risk_level: str = Field(..., min_length=1)
    model_policy: Literal["slavx_only", "allow_escalate", "onlineai_only"] = "allow_escalate"
    max_tokens: int | None = Field(default=None, ge=0)
    max_estimated_cost: float | None = Field(default=None, ge=0)
    max_latency_ms: int | None = Field(default=None, ge=0)


class TenantThresholdsUpdateResponse(BaseModel):
    success: bool = True
    message: str = "Tenant thresholds updated"
    data: dict


class CorrelationTraceResponse(BaseModel):
    success: bool = True
    message: str = "Correlation trace retrieved"
    data: dict


class SloAlertDigestResponse(BaseModel):
    success: bool = True
    message: str = "SLO alert digest generated"
    data: dict


class AuthzMatrixResponse(BaseModel):
    success: bool = True
    message: str = "Authz matrix generated"
    data: dict


class DataResidencyControlsResponse(BaseModel):
    success: bool = True
    message: str = "Data residency controls retrieved"
    data: dict


class FeedbackIntakeRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=3)
    details: str = Field(..., min_length=3)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    category: str = "pilot_feedback"
    source: str = "aix_app"
    reporter: str | None = None
    tags: list[str] = Field(default_factory=list)


class FeedbackIntakeResponse(BaseModel):
    success: bool = True
    message: str = "Feedback ingested"
    data: dict


class AutoscaleSimulationRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    current_workers: int = Field(..., ge=0)
    min_workers: int = Field(1, ge=1)
    max_workers: int = Field(20, ge=1)
    queue_per_worker: int = Field(10, ge=1)
    min_share_per_active_tenant: int = Field(1, ge=1)
    tenant_backlog: dict[str, int] = Field(default_factory=dict)


class AutoscaleSimulationResponse(BaseModel):
    success: bool = True
    message: str = "Autoscale simulation generated"
    data: dict


class TemplatePackValidationRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    map_name: str = "default_dispatch"
    map_version: int | None = Field(default=None, ge=1)
    pack_name: str = Field(..., min_length=1)
    pack_version: str = Field(..., min_length=1)
    min_map_version: int | None = Field(default=None, ge=1)
    max_map_version: int | None = Field(default=None, ge=1)
    pack_definition: dict[str, Any]


class TemplatePackValidationResponse(BaseModel):
    success: bool = True
    message: str = "Template pack validation complete"
    data: dict


class MarketplaceTemplateValidationRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    record: dict[str, Any]


class MarketplaceInstallAuditRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    template_id: str = Field(..., min_length=1)
    template_version: str = Field(..., min_length=1)


class MarketplaceResponse(BaseModel):
    success: bool = True
    message: str = "Marketplace contract processed"
    data: dict


class PluginSandboxEvaluateRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    plugin_manifest: dict[str, Any]
    allowed_egress_hosts: list[str] = Field(default_factory=list)
    allowed_token_scopes: list[str] = Field(default_factory=list)
    max_quota_per_hour: int = Field(1000, ge=1)


class PluginSandboxEvaluateResponse(BaseModel):
    success: bool = True
    message: str = "Plugin sandbox policy evaluated"
    data: dict


class BusinessKpiEvaluateRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    definition_version: str = "v1"
    source_metrics: dict[str, Any]


class BusinessKpiEvaluateResponse(BaseModel):
    success: bool = True
    message: str = "Business KPI evaluation complete"
    data: dict


class VoiceNlpEvaluateRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    feature_flag_enabled: bool = False
    policy_checks_passed: bool = False
    rollback_drill_recent: bool = False
    compliance_approved: bool = False


class VoiceNlpRollbackDrillRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    incident_id: str = Field(..., min_length=1)


class VoiceNlpResponse(BaseModel):
    success: bool = True
    message: str = "Voice NLP policy evaluated"
    data: dict


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _record_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _record_items(value: Any) -> list[dict[str, Any]]:
    payload = _record_dict(value)
    return [_record_dict(item) for item in _as_list(payload.get("items"))]


def _resolve_action_map_capability(
    *,
    action_map: dict[str, Any] | None,
    capability_name: str,
    base_decision: RoutingDecision,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "capability": capability_name,
        "capability_hit": False,
        "miss_reason": None,
        "dispatch_path": "aix.model_gateway.resolve",
        "allowed_providers": [],
        "allowed_route_classes": [],
        "map_name": action_map.get("map_name") if action_map else None,
        "map_version": action_map.get("map_version") if action_map else None,
    }
    if not action_map:
        result["miss_reason"] = "ACTION_MAP_NOT_FOUND"
        return result
    definition = _as_dict(action_map.get("definition_json"))
    capability_map = _as_dict(definition.get("capabilities"))
    capability = capability_map.get(capability_name)
    if not isinstance(capability, dict):
        result["miss_reason"] = "ACTION_MAP_CAPABILITY_NOT_FOUND"
        return result
    dispatch_path = str(capability.get("dispatch_path") or "aix.model_gateway.resolve")
    allowed_providers = [
        str(item).strip().lower() for item in capability.get("allowed_providers", []) if str(item).strip()
    ]
    allowed_route_classes = [
        str(item).strip().lower() for item in capability.get("allowed_route_classes", []) if str(item).strip()
    ]
    result["dispatch_path"] = dispatch_path
    result["allowed_providers"] = allowed_providers
    result["allowed_route_classes"] = allowed_route_classes
    provider_ok = True
    if allowed_providers and base_decision.selected_provider:
        provider_ok = _norm(base_decision.selected_provider) in set(allowed_providers)
    route_ok = True
    if allowed_route_classes and base_decision.route_class:
        route_ok = _norm(base_decision.route_class) in set(allowed_route_classes)
    if not provider_ok:
        result["miss_reason"] = "ACTION_MAP_PROVIDER_NOT_ALLOWED"
        return result
    if not route_ok:
        result["miss_reason"] = "ACTION_MAP_ROUTE_CLASS_NOT_ALLOWED"
        return result
    result["capability_hit"] = True
    return result


def _apply_action_map_result(*, base_decision: RoutingDecision, action_map_result: dict[str, Any]) -> RoutingDecision:
    if action_map_result.get("capability_hit"):
        return base_decision
    return RoutingDecision(
        decision_status="escalated",
        selected_provider=base_decision.selected_provider,
        selected_model_id=base_decision.selected_model_id,
        selected_model_name=base_decision.selected_model_name,
        decision_reason=str(action_map_result.get("miss_reason") or "ACTION_MAP_CAPABILITY_MISS"),
        route_class=base_decision.route_class,
        policy_key=base_decision.policy_key,
        retry_count=base_decision.retry_count,
        escalation_threshold=base_decision.escalation_threshold,
    )


def _routing_decision_evidence_payload(
    *,
    request: ResolveModelGatewayRequest,
    decision: RoutingDecision,
    action_map_result: dict[str, Any],
) -> dict[str, Any]:
    miss_reason = action_map_result.get("miss_reason")
    return {
        "correlation_id": request.correlation_id,
        "selected_model": decision.selected_model_id or decision.selected_model_name or "unselected",
        "estimated_cost": float(request.estimated_cost or 0.0),
        "latency_ms": int(request.latency_ms or 0),
        "thresholds": {
            "retry_count": decision.retry_count,
            "escalation_threshold": decision.escalation_threshold,
            "action_map_version": action_map_result.get("map_version"),
            "intent_capability": action_map_result.get("capability"),
        },
        "escalation": {
            "required": bool(miss_reason),
            "reason": miss_reason or "",
            "dispatch_path": action_map_result.get("dispatch_path"),
            "map_name": action_map_result.get("map_name"),
            "map_version": action_map_result.get("map_version"),
        },
        "selected_provider": decision.selected_provider,
        "route_class": decision.route_class,
        "decision_status": decision.decision_status,
        "decision_reason": decision.decision_reason,
    }


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _query_fingerprint(*, project_id: str | None, entity_query: str | None, retrieval_mode: str) -> str:
    raw = {
        "project_id": project_id or "",
        "entity_query": entity_query or "",
        "retrieval_mode": retrieval_mode,
    }
    return hashlib.sha256(_stable_json(raw).encode("utf-8")).hexdigest()


def _normalize_codeintel_citations(raw_citations: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_citations or []:
        if not isinstance(item, dict):
            continue
        start_line = item.get("start_line")
        if start_line is None:
            start_line = item.get("line")
        end_line = item.get("end_line")
        if end_line is None:
            end_line = start_line
        snapshot_id = item.get("snapshot_id")
        if snapshot_id is None:
            snapshot_id = item.get("snapshot")
        file_path = item.get("file_path")
        if file_path is None:
            file_path = item.get("file")
        normalized.append(
            {
                "repo_id": item.get("repo_id"),
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "snapshot_id": snapshot_id,
                "commit_sha": item.get("commit_sha"),
                "confidence": item.get("confidence"),
                # Backward-compatible aliases for v1 consumers.
                "file": file_path,
                "line": start_line,
                "snapshot": snapshot_id,
            }
        )
    return normalized


def _build_codeintel_reliability(*, codeintel_context: dict[str, Any] | None) -> dict[str, Any]:
    context = codeintel_context or {}
    confidence = _to_float(context.get("confidence_score"))
    fallback_policy = context.get("fallback_policy") if isinstance(context.get("fallback_policy"), dict) else None
    status = "fresh" if fallback_policy is None else "degraded"
    rationale_parts: list[str] = [f"confidence={confidence:.2f}"]
    if fallback_policy:
        rationale_parts.append(f"fallback_reason={fallback_policy.get('reason') or 'unknown'}")
    return {
        "retrieval_confidence": confidence,
        "staleness": {
            "status": status,
            "seconds": None,
            "reason": "fallback_triggered" if fallback_policy else "confidence_within_threshold",
        },
        "ranking_rationale": "; ".join(rationale_parts),
    }


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_ayrpack_rollout_telemetry(
    *,
    request: ResolveModelGatewayRequest,
    ayrpack_shadow_write_enqueued: bool,
    ayrpack_shadow_write_reason: str | None,
) -> dict[str, Any]:
    shadow_attempted = bool(request.project_id) and bool(settings.AYRPACK_SHADOW_WRITE_ENABLED)
    coverage = 1.0 if shadow_attempted and ayrpack_shadow_write_enqueued else 0.0
    return {
        "packet_write": {
            "role": "debug_forensic_mirror",
            "authority": "non_canonical",
            "required_for_control_plane": False,
            "shadow_attempted": shadow_attempted,
            "shadow_enqueued": ayrpack_shadow_write_enqueued,
            "coverage": coverage,
            "reason": ayrpack_shadow_write_reason,
        },
        "latency": {
            "model_latency_ms": _to_int(request.latency_ms),
        },
    }


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def _symbol_targets(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        relative_path = str(item.get("relative_path") or item.get("path") or "").strip()
        if not symbol and not relative_path:
            continue
        normalized.append({"symbol": symbol, "relative_path": relative_path})
    return normalized


def _resolve_codeintel_entity_query(*, request: ResolveModelGatewayRequest) -> str:
    explicit = str(getattr(request, "entity_query", None) or "").strip()
    if explicit:
        return explicit
    metadata = dict(request.task_metadata_json or {})
    ticket_contract = dict(
        request.ticket_contract_current
        or request.canonical_task_contract
        or metadata.get("ticket_contract_current")
        or {}
    )
    target_surface = dict(ticket_contract.get("target_surface") or {})
    execution_slice = dict(ticket_contract.get("execution_slice") or {})

    symbol_candidates = [
        symbol
        for symbol in [
            *[
                str(item.get("symbol") or "").strip()
                for item in _symbol_targets(target_surface.get("prompt_symbol_targets"))
            ],
            *_text_list(target_surface.get("owning_symbols")),
        ]
        if symbol
    ]
    if symbol_candidates:
        return symbol_candidates[0]

    target_file_candidates = _text_list(execution_slice.get("target_files")) or _text_list(
        target_surface.get("target_files")
    )
    for candidate in target_file_candidates:
        normalized = str(candidate).strip().replace("\\", "/").strip("/")
        if normalized:
            return Path(normalized).stem or normalized

    for text_candidate in (
        request.task_title,
        request.task_description,
        str(metadata.get("behavior_change") or "").strip() or None,
        str(ticket_contract.get("objective") or "").strip() or None,
    ):
        text = str(text_candidate or "").strip()
        if text:
            return text
    return request.task_type


def _planner_mutation_kind(*, target_file: str, target_symbol: str | None, metadata: dict[str, Any]) -> str:
    explicit = str(metadata.get("mutation_kind") or "").strip()
    if explicit:
        return explicit
    interface_change_policy = str(metadata.get("interface_change_policy") or "").strip().lower().replace("-", "_")
    lowered = target_file.lower()
    if "\\tests\\" in lowered or "/tests/" in lowered or lowered.startswith("tests/") or lowered.endswith("_test.py"):
        return "test_fix"
    if target_symbol:
        if interface_change_policy in {"additive_optional_params", "signature_change_allowed"}:
            return "bounded_method_protocol_extension"
        return "symbol_body_edit"
    return "file_local_refactor"


def _planner_slice_tool_contract() -> dict[str, Any]:
    return {
        "tool_sequence": ["propose_mutation", "run_validation", "request_clarification"],
        "primary_mutation_tool": "propose_mutation",
        "validation_tool": "run_validation",
        "clarification_tool": "request_clarification",
    }


def _append_unique(items: list[str], value: str | None) -> None:
    text = str(value or "").strip()
    if text and text not in items:
        items.append(text)


def _planner_clarification_question(gap: dict[str, str]) -> str:
    field = str(gap.get("field") or "").strip()
    code = str(gap.get("code") or "").strip()
    if field == "target_file":
        return "Which single file should this slice mutate?"
    if field == "target_symbol":
        return "Which single symbol should this slice target?"
    if field == "validation_command":
        return "What single validation command proves this slice?"
    if field == "behavior_change":
        return "What is the one concrete behavior change for this slice?"
    if field == "codeintel_anchor":
        return "Which grounded CodeIntel anchor should this slice use?"
    if field == "acceptance_criteria":
        return "What acceptance criteria define success for this slice?"
    if field == "task_number":
        return "What is the originating WorkHub task number for this slice?"
    return code or "What clarification is needed for this planner slice?"


def _planner_assist_loop_packet(*, request: ResolveModelGatewayRequest, gaps: list[dict[str, str]]) -> dict[str, Any]:
    open_questions = [_planner_clarification_question(gap) for gap in gaps]
    return {
        "schema_version": "aix.assist_loop.current",
        "conversation_state": "awaiting_guidance",
        "awaiting_actor": "onlineai",
        "task_intent": request.task_type,
        "task_number": int(request.workhub_task_number or 0),
        "final_status": "needs_clarification",
        "open_questions": open_questions,
        "guidance_requests": [
            {
                "requester": "planner",
                "reason_code": str(gap.get("code") or "").strip(),
                "question": _planner_clarification_question(gap),
                "channel": "onlineai",
                "field": str(gap.get("field") or "").strip(),
            }
            for gap in gaps
        ],
        "onlineai_guidance": [],
        "resume_context": {
            "conversation_ref": str(request.workhub_task_id or request.workhub_task_number or request.correlation_id),
            "resume_supported": True,
        },
        "diagnostic_reason_code": "PLANNER_PACKET_NEEDS_CLARIFICATION",
        "verifier_decision": None,
        "planner_error": None,
    }


def _planner_slice_prompt(packet: PlannerSlicePacketCurrent) -> dict[str, str]:
    target = packet.target_symbol or packet.target_file
    return {
        "system": (
            "Produce exactly one bounded code-change proposal for the provided slice packet. "
            "Stay within the single target file and preserve listed constraints."
        ),
        "user": (
            f"Objective: {packet.objective}\n"
            f"Target: {target}\n"
            f"Mutation goal: {packet.bounded_mutation_goal}\n"
            f"Validation: {packet.validation_command}"
        ),
    }


def _find_matching_codeintel_entity(
    *,
    codeintel_context: dict[str, Any] | None,
    target_file: str,
    target_symbol: str | None,
) -> dict[str, Any] | None:
    entities = (codeintel_context or {}).get("entities")
    if not isinstance(entities, list):
        return None
    matches: list[dict[str, Any]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_file = str(entity.get("file_path") or entity.get("relative_path") or "").strip()
        entity_name = str(entity.get("name") or "").strip()
        entity_qualified = str(entity.get("qualified_name") or "").strip()
        symbol_match = not target_symbol or target_symbol in {entity_name, entity_qualified}
        file_match = not target_file or target_file == entity_file
        if symbol_match and file_match:
            matches.append(entity)
    if len(matches) == 1:
        return matches[0]
    return None


def _infer_lite_constraints(
    *,
    existing_constraints: list[str],
    codeintel_context: dict[str, Any] | None,
    target_file: str,
    target_symbol: str | None,
) -> list[str]:
    constraints: list[str] = []
    for item in existing_constraints:
        _append_unique(constraints, item)
    _append_unique(constraints, f"Do not modify files outside {target_file}")
    if target_symbol:
        _append_unique(constraints, f"Keep changes scoped to {target_symbol}")
    entity = _find_matching_codeintel_entity(
        codeintel_context=codeintel_context,
        target_file=target_file,
        target_symbol=target_symbol,
    )
    if isinstance(entity, dict):
        signature = str(entity.get("signature") or "").strip()
        qualified_name = str(entity.get("qualified_name") or entity.get("name") or "").strip()
        if signature:
            _append_unique(constraints, f"Preserve signature: {signature}")
        if qualified_name:
            _append_unique(constraints, f"Preserve symbol identity: {qualified_name}")
    return constraints[:6]


def _build_codeintel_anchor(
    *,
    metadata: dict[str, Any],
    codeintel_context: dict[str, Any] | None,
    normalized_citations: list[dict[str, Any]],
    target_file: str,
    target_symbol: str | None,
) -> dict[str, Any] | None:
    explicit = metadata.get("codeintel_anchor")
    if isinstance(explicit, dict):
        anchor = {
            "file_path": str(explicit.get("file_path") or target_file).strip(),
            "symbol": str(explicit.get("symbol") or target_symbol or "").strip() or None,
            "snapshot_id": str(explicit.get("snapshot_id") or "").strip(),
            "commit_sha": str(explicit.get("commit_sha") or "").strip(),
            "symbol_source_hash": str(explicit.get("symbol_source_hash") or "").strip(),
            "signature": str(explicit.get("signature") or "").strip() or None,
            "start_line": explicit.get("start_line"),
            "end_line": explicit.get("end_line"),
        }
        if all(anchor.get(key) for key in ("file_path", "snapshot_id", "commit_sha", "symbol_source_hash")):
            return anchor
    entity = _find_matching_codeintel_entity(
        codeintel_context=codeintel_context,
        target_file=target_file,
        target_symbol=target_symbol,
    )
    if isinstance(entity, dict):
        entity_file = str(entity.get("file_path") or entity.get("relative_path") or target_file).strip()
        snapshot_id = str(entity.get("snapshot_id") or "").strip()
        commit_sha = str(entity.get("commit_sha") or "").strip()
        if entity_file and snapshot_id and commit_sha:
            # Wave 0: entity-derived symbol quarantined — an entity may never
            # supply a missing contract symbol (posture "on" only, rejected in v2).
            from backend.services.retrieval_policy_service import _codeintel_posture

            entity_symbol = (
                str(entity.get("qualified_name") or entity.get("name") or "").strip()
                if _codeintel_posture() == "on"
                else ""
            )
            source_hash = hashlib.sha256(_stable_json(entity).encode("utf-8")).hexdigest()
            return {
                "file_path": entity_file,
                "symbol": target_symbol or entity_symbol or None,
                "snapshot_id": snapshot_id,
                "commit_sha": commit_sha,
                "symbol_source_hash": f"sha256:{source_hash}",
                "signature": str(entity.get("signature") or "").strip() or None,
                "start_line": entity.get("start_line"),
                "end_line": entity.get("end_line"),
            }
    for citation in normalized_citations:
        if str(citation.get("file_path") or "").strip() != target_file:
            continue
        source_hash = hashlib.sha256(_stable_json(citation).encode("utf-8")).hexdigest()
        snapshot_id = str(citation.get("snapshot_id") or "").strip()
        commit_sha = str(citation.get("commit_sha") or "").strip()
        if not snapshot_id or not commit_sha:
            continue
        return {
            "file_path": target_file,
            "symbol": target_symbol,
            "snapshot_id": snapshot_id,
            "commit_sha": commit_sha,
            "symbol_source_hash": f"sha256:{source_hash}",
            "start_line": citation.get("start_line"),
            "end_line": citation.get("end_line"),
        }
    return None


def _infer_target_files(
    *,
    explicit_target_files: list[str],
    symbol_candidates: list[str],
    codeintel_context: dict[str, Any] | None,
    normalized_citations: list[dict[str, Any]],
) -> list[str]:
    if explicit_target_files:
        return explicit_target_files
    # Wave 0 authority freeze: retrieval results (CodeIntel/Haystack) may never
    # become effective targets. Inference code below is preserved but gated on
    # posture == "on", which v2 startup rejects — quarantine, not deletion.
    from backend.services.retrieval_policy_service import _codeintel_posture

    if _codeintel_posture() != "on":
        return []
    entities = (codeintel_context or {}).get("entities")
    inferred_files: list[str] = []
    if isinstance(entities, list) and len(symbol_candidates) == 1:
        target_symbol = symbol_candidates[0]
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            entity_name = str(entity.get("name") or "").strip()
            entity_qualified = str(entity.get("qualified_name") or "").strip()
            if target_symbol not in {entity_name, entity_qualified}:
                continue
            entity_file = str(entity.get("file_path") or entity.get("relative_path") or "").strip()
            if entity_file and entity_file not in inferred_files:
                inferred_files.append(entity_file)
        if len(inferred_files) == 1:
            return inferred_files
    citation_files = [
        str(citation.get("file_path") or "").strip()
        for citation in normalized_citations
        if str(citation.get("file_path") or "").strip()
    ]
    unique_citation_files = list(dict.fromkeys(citation_files))
    if len(unique_citation_files) == 1:
        return unique_citation_files
    return []


def _planner_anchor_compact_snippet(
    *, target_file: str, target_symbol: str | None, anchor: dict[str, Any] | None
) -> str:
    signature = str((anchor or {}).get("signature") or "").strip()
    if signature:
        return signature[:800]
    symbol = str(target_symbol or (anchor or {}).get("symbol") or "").strip()
    if symbol:
        return f"anchor symbol {symbol} in {target_file}"[:800]
    return f"anchor file {target_file}"[:800]


def _invalid_mutation_kind_gap(metadata: dict[str, Any]) -> dict[str, str] | None:
    explicit = str(metadata.get("mutation_kind") or "").strip()
    if not explicit:
        return None
    allowed = {
        "add_import",
        "symbol_body_edit",
        "bounded_method_protocol_extension",
        "signature_preserving_edit",
        "file_local_refactor",
        "test_addition",
        "test_fix",
    }
    if explicit in allowed:
        return None
    return {
        "field": "mutation_kind",
        "code": "INVALID_MUTATION_KIND",
        "message": "Planner slice packet mutation_kind must be one of the supported bounded mutation kinds.",
    }


def _build_planner_slice_packet(
    *,
    request: ResolveModelGatewayRequest,
    codeintel_context: dict[str, Any] | None,
    normalized_citations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not request.ticket_contract_current and not request.canonical_task_contract and not request.task_metadata_json:
        return None

    metadata = dict(request.task_metadata_json or {})
    ticket_contract = dict(request.ticket_contract_current or request.canonical_task_contract or {})
    if ticket_contract and "ticket_contract_current" not in metadata:
        metadata["ticket_contract_current"] = ticket_contract
    contract = _synthesize_task_ticket_contract(
        title=request.task_title or request.task_type,
        description=request.task_description,
        metadata=metadata,
    )
    if ticket_contract:
        merged_contract = dict(contract)
        for key in (
            "target_surface",
            "acceptance_criteria",
            "validation_plan",
            "constraints",
            "execution_slice",
        ):
            current = merged_contract.get(key)
            if not current and ticket_contract.get(key):
                merged_contract[key] = ticket_contract[key]
        merged_surface = _as_dict(merged_contract.get("target_surface"))
        ticket_surface = _as_dict(ticket_contract.get("target_surface"))
        if (
            not _text_list(merged_surface.get("target_files"))
            and not _symbol_targets(merged_surface.get("prompt_symbol_targets"))
            and (
                _text_list(ticket_surface.get("target_files"))
                or _symbol_targets(ticket_surface.get("prompt_symbol_targets"))
            )
        ):
            merged_contract["target_surface"] = dict(ticket_contract["target_surface"])
        contract = merged_contract
    target_surface = dict(contract.get("target_surface") or {})
    execution_slice = dict(contract.get("execution_slice") or {})
    validation_candidates = _text_list(execution_slice.get("validation_commands"))
    if "validation_commands" not in execution_slice:
        validation_candidates = validation_candidates or _text_list(contract.get("validation_plan"))
    symbol_candidates = [
        symbol
        for symbol in [
            *[
                str(item.get("symbol") or "").strip()
                for item in _symbol_targets(target_surface.get("prompt_symbol_targets"))
            ],
            *_text_list(target_surface.get("owning_symbols")),
        ]
        if symbol
    ]
    target_files = _infer_target_files(
        explicit_target_files=(
            _text_list(execution_slice.get("target_files")) or _text_list(target_surface.get("target_files"))
        ),
        symbol_candidates=symbol_candidates,
        codeintel_context=codeintel_context,
        normalized_citations=normalized_citations,
    )
    behavior_change = str(metadata.get("behavior_change") or execution_slice.get("summary") or "").strip()
    anchor = _build_codeintel_anchor(
        metadata=metadata,
        codeintel_context=codeintel_context,
        normalized_citations=normalized_citations,
        target_file=target_files[0] if target_files else "",
        target_symbol=symbol_candidates[0] if symbol_candidates else None,
    )
    gaps: list[dict[str, str]] = []
    if len(target_files) != 1:
        gaps.append(
            {
                "field": "target_file",
                "code": "EXACTLY_ONE_TARGET_FILE_REQUIRED",
                "message": "Planner slice packet requires exactly one target file.",
            }
        )
    if len(symbol_candidates) > 1:
        gaps.append(
            {
                "field": "target_symbol",
                "code": "AT_MOST_ONE_TARGET_SYMBOL_REQUIRED",
                "message": "Planner slice packet allows at most one target symbol.",
            }
        )
    if len(validation_candidates) != 1:
        gaps.append(
            {
                "field": "validation_command",
                "code": "EXACTLY_ONE_VALIDATION_COMMAND_REQUIRED",
                "message": "Planner slice packet requires exactly one validation command.",
            }
        )
    if not behavior_change:
        gaps.append(
            {
                "field": "behavior_change",
                "code": "BEHAVIOR_CHANGE_REQUIRED",
                "message": "Planner slice packet requires one concrete behavior-change sentence.",
            }
        )
    if anchor is None:
        gaps.append(
            {
                "field": "codeintel_anchor",
                "code": "CODEINTEL_ANCHOR_REQUIRED",
                "message": "Planner slice packet requires a grounded CodeIntel anchor.",
            }
        )
    acceptance_criteria = _text_list(contract.get("acceptance_criteria"))
    if request.workhub_task_number is None:
        gaps.append(
            {
                "field": "task_number",
                "code": "TASK_NUMBER_REQUIRED",
                "message": "Planner slice packet requires the originating WorkHub task number.",
            }
        )
    if not acceptance_criteria:
        gaps.append(
            {
                "field": "acceptance_criteria",
                "code": "ACCEPTANCE_CRITERIA_REQUIRED",
                "message": "Planner slice packet requires at least one acceptance criterion.",
            }
        )
    invalid_mutation_kind_gap = _invalid_mutation_kind_gap(metadata)
    if invalid_mutation_kind_gap:
        gaps.append(invalid_mutation_kind_gap)
    if gaps:
        return {
            "status": "needs_clarification",
            "packet_schema": "planner_slice.current",
            "ticket_contract_current": contract,
            "clarification_gaps": gaps,
            "tool_contract": _planner_slice_tool_contract(),
            "assist_loop": _planner_assist_loop_packet(request=request, gaps=gaps),
        }
    packet_payload = {
        "task_number": request.workhub_task_number,
        "slice_id": str(metadata.get("slice_id") or f"slice-{request.workhub_task_number}-001"),
        "objective": str(contract.get("objective") or request.task_type).strip(),
        "target_file": target_files[0],
        "target_symbol": symbol_candidates[0] if symbol_candidates else None,
        "bounded_mutation_goal": behavior_change,
        "allowed_surface": {
            "anchor": {
                "symbol": symbol_candidates[0] if symbol_candidates else None,
                "compact_snippet": _planner_anchor_compact_snippet(
                    target_file=target_files[0],
                    target_symbol=symbol_candidates[0] if symbol_candidates else None,
                    anchor=anchor,
                ),
            }
        },
        "validation_command": validation_candidates[0],
        "planner_mode": "packet_bound_replace_function" if symbol_candidates else "generic",
        "output_schema": {"type": "edit", "fields": ["target_file", "validation_command"]},
        "hard_failure_rules": [
            "mutate only the declared target_file",
            "run exactly the declared validation_command",
        ],
    }
    try:
        packet = PlannerSlicePacketCurrent.model_validate(packet_payload)
    except ValidationError as exc:
        validation_gaps: list[dict[str, str]] = []
        for error in exc.errors():
            field = str((error.get("loc") or ["planner_slice_packet"])[0]).strip() or "planner_slice_packet"
            code = "INVALID_PACKET_FIELD"
            message = str(error.get("msg") or "Planner slice packet validation failed.").strip()
            if field == "mutation_kind":
                code = "INVALID_MUTATION_KIND"
                message = "Planner slice packet mutation_kind must be one of the supported bounded mutation kinds."
            validation_gaps.append({"field": field, "code": code, "message": message})
        return {
            "status": "needs_clarification",
            "packet_schema": "planner_slice.current",
            "ticket_contract_current": contract,
            "clarification_gaps": validation_gaps,
            "tool_contract": _planner_slice_tool_contract(),
            "assist_loop": _planner_assist_loop_packet(request=request, gaps=validation_gaps),
        }
    return {
        "status": "ready",
        "packet_schema": "planner_slice.current",
        "ticket_contract_current": contract,
        "slice_packet_current": packet.model_dump(),
        "tool_contract": _planner_slice_tool_contract(),
        "prompt_contract": _planner_slice_prompt(packet),
    }


def _build_ayrpack_shadow_packet_id(
    *,
    tenant_id: str,
    project_id: str,
    request: ResolveModelGatewayRequest,
    decision: RoutingDecision,
) -> str:
    stable_key = "|".join(
        [
            tenant_id,
            project_id,
            request.correlation_id,
            request.phase,
            request.task_type,
            request.risk_level,
            str(decision.selected_provider or ""),
            str(decision.selected_model_id or ""),
            str(decision.route_class or ""),
            str(decision.decision_status or ""),
        ]
    )
    return str(uuid5(NAMESPACE_URL, stable_key))


async def _write_ayrpack_shadow_packet(
    *,
    tenant_id: str,
    actor_id: str,
    request: ResolveModelGatewayRequest,
    decision: RoutingDecision,
    redaction_report: dict[str, Any],
    usage_block_redacted: dict[str, Any],
    routing_evidence_payload: dict[str, Any],
    decision_record_id: str | None,
) -> None:
    project_id = str(request.project_id or "").strip()
    if not project_id:
        return
    packet_id = _build_ayrpack_shadow_packet_id(
        tenant_id=tenant_id,
        project_id=project_id,
        request=request,
        decision=decision,
    )
    conversation_ref = str(request.workhub_task_id or request.workhub_task_number or request.correlation_id)
    packet_payload = build_model_gateway_trace_packet_payload(
        packet_id=packet_id,
        project_id=project_id,
        conversation_id=conversation_ref,
        correlation_id=request.correlation_id,
        actor_id=actor_id,
        request_payload=request.model_dump(mode="json"),
        decision_payload={
            "decision_status": decision.decision_status,
            "decision_reason": decision.decision_reason,
            "selected_provider": decision.selected_provider,
            "selected_model_id": decision.selected_model_id,
            "selected_model_name": decision.selected_model_name,
            "route_class": decision.route_class,
        },
        usage_payload=usage_block_redacted,
        redaction_payload=redaction_report,
        routing_payload=routing_evidence_payload,
        decision_record_id=decision_record_id,
    )
    _, packet_error = await ayrpack_client.submit_packet(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=request.correlation_id,
        packet_payload=packet_payload,
        idempotency_key=packet_id,
    )
    if packet_error:
        return
    await ayrpack_client.append_audit(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=request.correlation_id,
        packet_id=packet_id,
        audit_payload={
            "event_type": "model_gateway_shadow_write",
            "event_data_json": {
                "packet_id": packet_id,
                "decision_record_id": decision_record_id,
                "decision_status": decision.decision_status,
                "decision_reason": decision.decision_reason,
                "route_class": decision.route_class,
            },
            "actor_id": actor_id,
            "status": "recorded",
        },
    )


def _enqueue_ayrpack_shadow_write(
    *,
    tenant_id: str,
    actor_id: str,
    request: ResolveModelGatewayRequest,
    decision: RoutingDecision,
    redaction_report: dict[str, Any],
    usage_block_redacted: dict[str, Any],
    routing_evidence_payload: dict[str, Any],
    decision_record_id: str | None,
    redaction_error: str | None,
) -> tuple[bool, str | None]:
    if not bool(settings.AYRPACK_SHADOW_WRITE_ENABLED):
        return False, "AYRPACK_SHADOW_WRITE_DISABLED"
    if not request.project_id:
        return False, "PROJECT_ID_MISSING"
    if redaction_error and (request.model_policy != "slavx_only" or _norm(decision.selected_provider) == "onlineai"):
        return False, "REDACTION_FAILED_EXTERNAL_CALLS_BLOCKED"

    async def _runner() -> None:
        try:
            await _write_ayrpack_shadow_packet(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request=request,
                decision=decision,
                redaction_report=redaction_report,
                usage_block_redacted=usage_block_redacted,
                routing_evidence_payload=routing_evidence_payload,
                decision_record_id=decision_record_id,
            )
        except Exception:
            return

    asyncio.create_task(_runner())
    return True, None


def _extract_tenant_thresholds(
    *,
    metadata_json: Any,
    tenant_id: str,
    phase: Literal["planner", "executor", "verifier"] | None = None,
) -> dict[str, float | int]:
    metadata = _as_dict(metadata_json)
    tenant_thresholds = _as_dict(metadata.get("tenant_thresholds"))
    scoped_thresholds = _as_dict(tenant_thresholds.get(tenant_id))
    default_thresholds = _as_dict(metadata.get("thresholds"))
    source = scoped_thresholds or default_thresholds
    if phase:
        scoped_phase = _as_dict(_as_dict(source.get("phase_thresholds")).get(phase))
        default_phase = _as_dict(_as_dict(metadata.get("phase_thresholds")).get(phase))
        source = {**default_thresholds, **source, **default_phase, **scoped_phase}
    return {
        "max_tokens": _to_int(source.get("max_tokens")) if source.get("max_tokens") is not None else 0,
        "max_estimated_cost": (
            _to_float(source.get("max_estimated_cost")) if source.get("max_estimated_cost") is not None else 0.0
        ),
        "max_latency_ms": _to_int(source.get("max_latency_ms")) if source.get("max_latency_ms") is not None else 0,
    }


def _extract_phase_router_config(
    *,
    metadata_json: Any,
    tenant_id: str,
    phase: Literal["planner", "executor", "verifier"],
) -> dict[str, Any]:
    def _default_pool_group(current_phase: Literal["planner", "executor", "verifier"]) -> str:
        if current_phase == "planner":
            return "A"
        if current_phase == "executor":
            return "B"
        return "C"

    def _default_routing_mode(pool_group: str) -> str:
        upper = pool_group.upper()
        if upper == "B":
            return "openrouter_auto_exacto"
        if upper == "C":
            return "openrouter_auto_nitro"
        if upper == "D":
            return "local_only_fixed"
        return "openrouter_auto"

    def _recommended_model_patterns(pool_group: str) -> list[str]:
        upper = pool_group.upper()
        if upper == "A":
            return [
                "openrouter/openai/gpt-5*",
                "openrouter/anthropic/claude-*",
            ]
        if upper == "B":
            return [
                "openrouter/openai/gpt-5.6-luna*",
                "openrouter/qwen/qwen3-coder*",
                "openrouter/z-ai/*",
            ]
        if upper == "C":
            return [
                "openrouter/openai/gpt-5.6-luna*",
                "openrouter/deepseek/*",
                "openrouter/meta-llama/*",
            ]
        if upper == "D":
            return [
                "openrouter/*",
                "local/*",
            ]
        return []

    metadata = _as_dict(metadata_json)
    tenant_cfg_root = _as_dict(metadata.get("tenant_thresholds"))
    tenant_cfg = _as_dict(tenant_cfg_root.get(tenant_id))
    phase_router_default = _as_dict(metadata.get("phase_router"))
    phase_router_tenant = _as_dict(tenant_cfg.get("phase_router"))
    default_phase = _as_dict(phase_router_default.get(phase))
    tenant_phase = _as_dict(phase_router_tenant.get(phase))
    merged = {**default_phase, **tenant_phase}
    allowed_models = merged.get("allowed_models")
    allowed_patterns = [str(item).strip() for item in _as_list(allowed_models) if str(item).strip()]
    pool_group = str(merged.get("pool_group") or _default_pool_group(phase)).strip().upper()
    routing_mode = str(merged.get("routing_mode") or _default_routing_mode(pool_group)).strip().lower()
    return {
        "router_version": str(merged.get("router_version") or "v1"),
        "pool_group": pool_group,
        "privacy_mode": str(merged.get("privacy_mode") or "hybrid").strip().lower(),
        "routing_mode": routing_mode,
        "provider_sort": str(merged.get("provider_sort") or "balanced").strip().lower(),
        "latency_target_ms": _to_int(merged.get("latency_target_ms")),
        "max_price": _to_float(merged.get("max_price")),
        "allowed_models": allowed_patterns,
        "recommended_allowed_models": _recommended_model_patterns(pool_group),
        "preferred_provider": str(merged.get("preferred_provider") or "openrouter").strip().lower(),
        "complexity_escalation_threshold": float(merged.get("complexity_escalation_threshold") or 0.8),
        "circuit_breaker_retry_threshold": max(0, int(merged.get("circuit_breaker_retry_threshold") or 3)),
    }


def _phase_to_lane(phase: Literal["planner", "executor", "verifier"]) -> str:
    if phase == "planner":
        return "planning"
    if phase == "verifier":
        return "verification"
    return "mutation"


def _yaml_phase_router_config(
    *,
    request: ResolveModelGatewayRequest,
) -> dict[str, Any] | None:
    metadata = dict(request.task_metadata_json or {})
    instruction_contract = dict(
        request.ticket_contract_current
        or request.canonical_task_contract
        or metadata.get("ticket_contract_current")
        or {}
    )
    if instruction_contract and "ticket_contract_current" not in metadata:
        metadata["ticket_contract_current"] = dict(instruction_contract)
    prompt_characteristics = {
        "estimated_tokens": int(request.tokens_used or 0),
        "task_family": str(request.task_type or "").strip(),
        "oversized": bool((request.tokens_used or 0) >= 2000),
        "complexity": "high" if float(request.complexity_score or 0.0) >= 0.8 else "normal",
    }
    resolved = _MODEL_LANE_POLICY_SERVICE.resolve_pool(
        metadata=metadata,
        instruction_contract=instruction_contract,
        lane=_phase_to_lane(request.phase),
        phase=(
            "planning" if request.phase == "planner" else "verification" if request.phase == "verifier" else "mutation"
        ),
        prompt_characteristics=prompt_characteristics,
    )
    candidate_pool = list(resolved.get("candidate_pool") or [])
    if not candidate_pool:
        return None
    first = dict(candidate_pool[0])
    allowed_models = [
        str(item.get("model") or "").strip() for item in candidate_pool if str(item.get("model") or "").strip()
    ]
    return {
        "router_version": "yaml-first.current",
        "pool_group": resolved.get("persona"),
        "privacy_mode": "hybrid",
        "routing_mode": "yaml_pool_gateway_filter",
        "provider_sort": "yaml_priority",
        "latency_target_ms": int(first.get("timeout_seconds") or 0) * 1000 if first.get("timeout_seconds") else 0,
        "max_price": 0.0,
        "allowed_models": allowed_models,
        "recommended_allowed_models": allowed_models,
        "preferred_provider": "openrouter",
        "complexity_escalation_threshold": 0.8,
        "circuit_breaker_retry_threshold": 3,
        "first_stage_source": "yaml_persona_pool",
        "role": resolved.get("role"),
        "persona": resolved.get("persona"),
        "candidate_pool": candidate_pool,
        "selection_reason": resolved.get("selection_reason"),
    }


def _allowlisted_bindings(bindings: list[dict[str, Any]], allowed_patterns: list[str]) -> list[dict[str, Any]]:
    if not allowed_patterns:
        return list(bindings)
    kept: list[dict[str, Any]] = []
    for row in bindings:
        model_id = str(row.get("model_id") or "").strip()
        provider = _norm(row.get("provider"))
        if not model_id:
            continue
        candidate_variants = _gateway_model_id_variants(provider=provider, model_id=model_id)
        pattern_variants: set[str] = set()
        for pattern in allowed_patterns:
            pattern_variants.update(_gateway_model_id_variants(provider=provider, model_id=pattern))
        if any(
            fnmatch(candidate_variant, pattern_variant)
            for candidate_variant in candidate_variants
            for pattern_variant in pattern_variants
        ):
            kept.append(row)
    return kept


def _canonical_gateway_model_id(*, provider: str, model_id: str) -> str:
    normalized_provider = _norm(provider)
    normalized_model_id = str(model_id or "").strip()
    if not normalized_model_id:
        return normalized_model_id
    if normalized_provider == "openrouter" and not normalized_model_id.startswith("openrouter/"):
        normalized_model_id = f"openrouter/{normalized_model_id}"
    return normalized_model_id


def _gateway_model_id_variants(*, provider: str, model_id: str) -> set[str]:
    normalized = _canonical_gateway_model_id(provider=provider, model_id=model_id)
    if not normalized:
        return set()
    variants = {normalized}
    if normalized.startswith("openrouter/"):
        variants.add(normalized[len("openrouter/") :])
    else:
        variants.add(f"openrouter/{normalized}")
    return {item for item in variants if item}


def _resolve_yaml_pool_decision(
    *,
    bindings_for_route: list[dict[str, Any]],
    health_rows: list[dict[str, Any]],
    retry_count: int,
    policy_key: str | None,
    escalation_policies: list[dict[str, Any]],
    force_escalation: bool,
) -> RoutingDecision:
    health_index: dict[tuple[str, str], str] = {}
    for row in list(health_rows or []):
        provider = _norm(row.get("provider"))
        model_id = str(row.get("model_id") or "").strip()
        if not model_id:
            continue
        health_status = _norm(row.get("health_status"))
        for model_id_variant in _gateway_model_id_variants(provider=provider, model_id=model_id):
            health_index[(provider, model_id_variant)] = health_status
    escalation_threshold = 0
    if escalation_policies:
        try:
            escalation_threshold = max(
                int(str(item.get("retry_threshold") or 0)) for item in escalation_policies if isinstance(item, dict)
            )
        except ValueError:
            escalation_threshold = 0

    primary_candidates: list[dict[str, Any]] = []
    escalation_candidates: list[dict[str, Any]] = []
    for row in bindings_for_route:
        route_class = _norm(row.get("route_class") or "primary")
        if route_class == "escalation":
            escalation_candidates.append(row)
        elif route_class == "primary":
            primary_candidates.append(row)

    for row in primary_candidates:
        provider = _norm(row.get("provider")) or "openrouter"
        model_id = str(row.get("model_id") or "").strip()
        canonical_model_id = _canonical_gateway_model_id(provider=provider, model_id=model_id)
        if not model_id:
            continue
        health_status = health_index.get((provider, canonical_model_id), "unknown")
        if health_status not in {"healthy", "degraded"}:
            continue
        return RoutingDecision(
            decision_status="selected",
            selected_provider=provider,
            selected_model_id=model_id,
            selected_model_name=str(row.get("model_name") or model_id),
            decision_reason="YAML_POOL_SELECTED",
            route_class="primary",
            policy_key=policy_key,
            retry_count=retry_count,
            escalation_threshold=0,
        )
    if force_escalation or (escalation_threshold > 0 and retry_count >= escalation_threshold):
        for row in escalation_candidates:
            provider = _norm(row.get("provider")) or "openrouter"
            model_id = str(row.get("model_id") or "").strip()
            canonical_model_id = _canonical_gateway_model_id(provider=provider, model_id=model_id)
            if not model_id:
                continue
            health_status = health_index.get((provider, canonical_model_id), "unknown")
            if health_status not in {"healthy", "degraded"}:
                continue
            return RoutingDecision(
                decision_status="escalated",
                selected_provider=provider,
                selected_model_id=model_id,
                selected_model_name=str(row.get("model_name") or model_id),
                decision_reason="YAML_POOL_ESCALATED",
                route_class=str(row.get("route_class") or "escalation").strip() or "escalation",
                policy_key=policy_key,
                retry_count=retry_count,
                escalation_threshold=escalation_threshold,
            )
    if escalation_candidates and escalation_threshold > 0 and retry_count < escalation_threshold:
        return RoutingDecision(
            decision_status="blocked",
            selected_provider=None,
            selected_model_id=None,
            selected_model_name=None,
            decision_reason="RETRY_THRESHOLD_NOT_REACHED",
            route_class=None,
            policy_key=policy_key,
            retry_count=retry_count,
            escalation_threshold=escalation_threshold,
        )
    return RoutingDecision(
        decision_status="blocked",
        selected_provider=None,
        selected_model_id=None,
        selected_model_name=None,
        decision_reason="NO_HEALTHY_YAML_POOL_BINDINGS",
        route_class=None,
        policy_key=policy_key,
        retry_count=retry_count,
        escalation_threshold=0,
    )


def _apply_router_v2_override(
    *,
    decision: RoutingDecision,
    bindings_for_route: list[dict[str, Any]],
    leaderboard: list[dict[str, Any]],
    minimum_samples: int = 3,
    minimum_gain: float = 0.03,
) -> tuple[RoutingDecision, dict[str, Any]]:
    """Optionally override selected model using leaderboard signals.

    Guardrails:
    - never change provider or route_class
    - ignore demoted models
    - require enough samples and a meaningful score gain
    """

    evidence: dict[str, Any] = {
        "enabled": True,
        "override_applied": False,
        "reason": "NO_OVERRIDE",
        "candidate_model_id": None,
        "candidate_score": None,
        "selected_score": None,
        "minimum_samples": minimum_samples,
        "minimum_gain": minimum_gain,
    }
    if decision.decision_status == "blocked":
        evidence["reason"] = "BLOCKED_DECISION"
        return decision, evidence
    if not decision.selected_provider:
        evidence["reason"] = "MISSING_PROVIDER"
        return decision, evidence
    route_class = _norm(decision.route_class or "primary")
    provider = _norm(decision.selected_provider)
    allowed_model_ids = {
        str(row.get("model_id") or "").strip()
        for row in bindings_for_route
        if _norm(row.get("provider")) == provider and _norm(row.get("route_class") or "primary") == route_class
    }
    if not allowed_model_ids:
        evidence["reason"] = "NO_ELIGIBLE_BINDINGS"
        return decision, evidence

    scoped = [
        row
        for row in leaderboard
        if _norm(row.get("provider")) == provider and str(row.get("model_id") or "") in allowed_model_ids
    ]
    if not scoped:
        evidence["reason"] = "NO_SCORING_DATA"
        return decision, evidence
    ranked = [
        row
        for row in sorted(scoped, key=lambda item: -float(item.get("score", 0.0)))
        if int(row.get("samples", 0)) >= minimum_samples and str(row.get("trend") or "") != "demote"
    ]
    if not ranked:
        evidence["reason"] = "INSUFFICIENT_SAMPLES"
        return decision, evidence

    selected_model_id = str(decision.selected_model_id or "")
    selected_entry = next((row for row in scoped if str(row.get("model_id") or "") == selected_model_id), None)
    selected_score = float((selected_entry or {}).get("score") or 0.0)
    candidate = ranked[0]
    candidate_model_id = str(candidate.get("model_id") or "")
    candidate_score = float(candidate.get("score") or 0.0)
    evidence["candidate_model_id"] = candidate_model_id or None
    evidence["candidate_score"] = candidate_score
    evidence["selected_score"] = selected_score
    if not candidate_model_id or candidate_model_id == selected_model_id:
        evidence["reason"] = "CURRENT_MODEL_TOP_RANKED"
        return decision, evidence
    if candidate_score - selected_score < minimum_gain:
        evidence["reason"] = "GAIN_BELOW_THRESHOLD"
        return decision, evidence

    evidence["override_applied"] = True
    evidence["reason"] = "LEADERBOARD_BETTER_MODEL"
    return (
        RoutingDecision(
            decision_status=decision.decision_status,
            selected_provider=decision.selected_provider,
            selected_model_id=candidate_model_id,
            selected_model_name=str(candidate.get("model_name") or decision.selected_model_name or candidate_model_id),
            decision_reason=(
                f"{decision.decision_reason}|ROUTER_V2_LEADERBOARD_OVERRIDE"
                if decision.decision_reason
                else "ROUTER_V2_LEADERBOARD_OVERRIDE"
            ),
            route_class=decision.route_class,
            policy_key=decision.policy_key,
            retry_count=decision.retry_count,
            escalation_threshold=decision.escalation_threshold,
        ),
        evidence,
    )


def _evaluate_threshold_violations(
    *,
    thresholds: dict[str, float | int],
    tokens_used: int | None,
    estimated_cost: float | None,
    latency_ms: int | None,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    observed_tokens = _to_int(tokens_used)
    observed_cost = _to_float(estimated_cost)
    observed_latency = _to_int(latency_ms)
    max_tokens = _to_int(thresholds.get("max_tokens"))
    max_estimated_cost = _to_float(thresholds.get("max_estimated_cost"))
    max_latency_ms = _to_int(thresholds.get("max_latency_ms"))
    if max_tokens > 0 and observed_tokens > max_tokens:
        violations.append({"metric": "tokens_used", "observed": observed_tokens, "threshold": max_tokens})
    if max_estimated_cost > 0 and observed_cost > max_estimated_cost:
        violations.append({"metric": "estimated_cost", "observed": observed_cost, "threshold": max_estimated_cost})
    if max_latency_ms > 0 and observed_latency > max_latency_ms:
        violations.append({"metric": "latency_ms", "observed": observed_latency, "threshold": max_latency_ms})
    return violations


def _truncate_text(value: str, max_len: int) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= max_len:
        return text, False
    return text[:max_len], True


def _get_data_residency_policy() -> dict[str, Any]:
    policy = settings.data_residency_policy
    return policy if isinstance(policy, dict) else {}


def _evaluate_data_residency(*, tenant_id: str, selected_provider: str | None) -> dict[str, Any]:
    policy = _get_data_residency_policy()
    tenant_rule = _as_dict(policy.get(tenant_id))
    default_rule = _as_dict(policy.get("default"))
    rule = tenant_rule or default_rule
    zone = str(rule.get("zone") or "unknown")
    allowed_providers = [
        str(provider).strip().lower()
        for provider in (rule.get("allowed_providers") or ["openrouter"])
        if str(provider).strip()
    ]
    allow_external = bool(rule.get("allow_external", False))
    provider = _norm(selected_provider)
    if not provider:
        return {
            "zone": zone,
            "allowed": True,
            "reason": None,
            "allowed_providers": allowed_providers,
            "allow_external": allow_external,
            "provider": selected_provider,
        }
    if provider in set(allowed_providers):
        return {
            "zone": zone,
            "allowed": True,
            "reason": None,
            "allowed_providers": allowed_providers,
            "allow_external": allow_external,
            "provider": provider,
        }
    if allow_external:
        return {
            "zone": zone,
            "allowed": True,
            "reason": None,
            "allowed_providers": allowed_providers,
            "allow_external": allow_external,
            "provider": provider,
        }
    return {
        "zone": zone,
        "allowed": False,
        "reason": "RESIDENCY_PROVIDER_BLOCKED",
        "allowed_providers": allowed_providers,
        "allow_external": allow_external,
        "provider": provider,
    }


def _extract_required_permission(callable_obj: Any) -> str | None:
    closure = getattr(callable_obj, "__closure__", None)
    if not closure:
        return None
    for cell in closure:
        value = cell.cell_contents
        if isinstance(value, str) and ":" in value:
            return value
    return None


def _normalize_feedback_tags(*, request: FeedbackIntakeRequest) -> list[str]:
    base_tags = [
        "pilot-feedback",
        f"severity:{request.severity}",
        f"category:{_norm(request.category) or 'general'}",
        f"source:{_norm(request.source) or 'unknown'}",
    ]
    extra = [str(tag).strip().lower() for tag in request.tags if str(tag).strip()]
    return sorted(dict.fromkeys(base_tags + extra))


def _resolve_integration_base_url() -> str:
    base_url = str(settings.RUNTIME_PLANNER_API_URL or "http://integration:8020").strip().rstrip("/")
    return base_url or "http://integration:8020"


def _provider_model_from_masked_config(provider: dict[str, Any]) -> str:
    config = _as_dict(provider.get("config_masked"))
    return str(config.get("model") or "").strip()


def _provider_sync_candidate(provider: dict[str, Any]) -> tuple[bool, str | None]:
    provider_id = _norm(provider.get("provider"))
    model_id = _provider_model_from_masked_config(provider)
    if not provider_id:
        return False, "missing_provider"
    if str(provider.get("status") or "").strip().lower() != "active":
        return False, "provider_not_active"
    if provider.get("configured") is not True:
        return False, "provider_not_configured"
    if provider.get("adapter_registered") is not True:
        return False, "adapter_not_registered"
    if provider.get("migration_required") is True:
        return False, "migration_required"
    if _as_list(provider.get("missing_keys")):
        return False, "missing_required_keys"
    if not model_id:
        return False, "missing_default_model"
    return True, None


def _provider_binding_key(provider_id: str, model_id: str) -> str:
    digest = hashlib.sha256(f"{provider_id}:{model_id}".encode()).hexdigest()[:12]
    return f"integration-provider:{provider_id}:{digest}"


def _provider_model_name(provider_id: str, model_id: str) -> str:
    if provider_id == "openrouter" and "/" in model_id:
        return model_id.split("/")[-1]
    return model_id


@router.get(
    "/registry",
    response_model=ModelRegistryResponse,
    summary="List model registry with health state",
)
async def get_model_registry(
    project_id: str | None = None,
    enabled_only: bool = True,
    refresh_openrouter: bool = False,
    current_user: dict = Depends(require_permission("aix_model_provider_bindings:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    binding_filters: dict[str, Any] = {}
    if project_id:
        binding_filters["project_id"] = project_id
    if enabled_only:
        binding_filters["is_enabled"] = True
    binding_rows = await aix_model_provider_bindings_service.list(
        tenant_id=tenant_id,
        limit=1000,
        offset=0,
        filters=binding_filters or None,
    )
    health_rows = await aix_model_health_status_service.list(
        tenant_id=tenant_id,
        limit=1000,
        offset=0,
    )
    health_index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _record_items(health_rows):
        provider_key = _norm(row.get("provider"))
        model_key = _norm(row.get("model_id"))
        health_index[(provider_key, model_key)] = row

    registry_entries: list[dict[str, Any]] = []
    provider_summary: dict[str, dict[str, int]] = {}
    for row in _record_items(binding_rows):
        provider = str(row.get("provider") or "")
        model_id = str(row.get("model_id") or "")
        index_key = (_norm(provider), _norm(model_id))
        health = health_index.get(index_key, {})
        health_status = str(health.get("health_status") or "unknown")
        provider_key = provider or "unknown"
        if provider_key not in provider_summary:
            provider_summary[provider_key] = {"total": 0, "healthy": 0, "unhealthy": 0, "degraded": 0, "unknown": 0}
        provider_summary[provider_key]["total"] += 1
        if health_status in provider_summary[provider_key]:
            provider_summary[provider_key][health_status] += 1
        else:
            provider_summary[provider_key]["unknown"] += 1
        registry_entries.append(
            {
                "binding_key": row.get("binding_key"),
                "project_id": row.get("project_id"),
                "provider": provider,
                "model_id": model_id,
                "model_name": row.get("model_name"),
                "route_class": row.get("route_class"),
                "is_enabled": bool(row.get("is_enabled")),
                "health_status": health_status,
                "latency_ms": health.get("latency_ms"),
                "error_rate": health.get("error_rate"),
                "updated_at": health.get("updated_at") or row.get("updated_at"),
            }
        )

    openrouter_refresh: dict[str, Any] = {"status": "disabled", "reason": None, "total": 0, "items": []}
    if refresh_openrouter:
        openrouter_refresh = await fetch_openrouter_model_catalog()

    return ModelRegistryResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "enabled_only": enabled_only,
            "refresh_openrouter": refresh_openrouter,
            "openrouter": openrouter_refresh,
            "total": len(registry_entries),
            "by_provider": provider_summary,
            "items": registry_entries,
        }
    )


@router.post(
    "/registry/sync-from-integration",
    response_model=ModelRegistrySyncResponse,
    summary="Synchronize canonical model bindings from governed Integration provider config",
)
async def sync_model_registry_from_integration(
    request: Request,
    project_id: str | None = None,
    current_user: dict = Depends(require_permission("aix_model_provider_bindings:update")),
):
    tenant_id = await validate_tenant_access(current_user)
    actor_id = str(current_user.get("id") or current_user.get("sub") or "system")
    authorization = request.headers.get("authorization")
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header required")

    integration_url = f"{_resolve_integration_base_url()}/api/integrations/llm/admin/providers"
    try:
        async with httpx.AsyncClient(timeout=float(settings.API_TIMEOUT_SECONDS)) as client:
            upstream = await client.get(
                integration_url,
                headers={
                    "Authorization": authorization,
                    "X-Tenant-ID": tenant_id,
                    "X-Hyeve-Tenant-Id": tenant_id,
                    "X-Actor-ID": actor_id,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "INTEGRATION_PROVIDER_ADMIN_UNAVAILABLE", "reason": exc.__class__.__name__},
        ) from exc
    if upstream.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "INTEGRATION_PROVIDER_ADMIN_REJECTED", "status_code": upstream.status_code},
        )

    payload = upstream.json()
    providers = _as_list(_as_dict(payload.get("data")).get("providers"))
    created_bindings = 0
    updated_bindings = 0
    created_health = 0
    updated_health = 0
    skipped: list[dict[str, str]] = []
    synced: list[dict[str, Any]] = []

    for raw_provider in providers:
        if not isinstance(raw_provider, dict):
            continue
        provider_id = _norm(raw_provider.get("provider"))
        eligible, reason = _provider_sync_candidate(raw_provider)
        if not eligible:
            skipped.append({"provider": provider_id or "unknown", "reason": reason or "not_eligible"})
            continue
        model_id = _provider_model_from_masked_config(raw_provider)
        model_name = _provider_model_name(provider_id, model_id)
        binding_key = _provider_binding_key(provider_id, model_id)
        metadata_json = {
            "source": "hyeve_integration_provider_admin",
            "endpoint_id": raw_provider.get("endpoint_id"),
            "endpoint_name": raw_provider.get("endpoint_name"),
            "credential_source": raw_provider.get("credential_source"),
            "source_of_truth": raw_provider.get("source_of_truth"),
            "provider_status": raw_provider.get("status"),
            "adapter_registered": bool(raw_provider.get("adapter_registered")),
            "synced_at": datetime.now(UTC).isoformat(),
        }
        existing = await aix_model_provider_bindings_service.list(
            tenant_id=tenant_id,
            limit=10,
            offset=0,
            filters={
                "project_id": project_id,
                "binding_key": binding_key,
                "provider": provider_id,
                "model_id": model_id,
                "route_class": "primary",
            },
        )
        existing_items = _record_items(existing)
        if existing_items:
            target = existing_items[0]
            await aix_model_provider_bindings_service.update(
                record_id=str(target.get("id")),
                data=AixModelProviderBindingsUpdate.model_validate(
                    {
                        "model_name": model_name,
                        "is_enabled": True,
                        "metadata_json": metadata_json,
                        "updated_by": actor_id,
                    }
                ),
                tenant_id=tenant_id,
                user_id=actor_id,
            )
            updated_bindings += 1
        else:
            await aix_model_provider_bindings_service.create(
                data=AixModelProviderBindingsCreate.model_validate(
                    {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "binding_key": binding_key,
                        "provider": provider_id,
                        "model_id": model_id,
                        "model_name": model_name,
                        "route_class": "primary",
                        "is_enabled": True,
                        "metadata_json": metadata_json,
                        "created_by": actor_id,
                        "updated_by": actor_id,
                    }
                ),
                tenant_id=tenant_id,
                user_id=actor_id,
            )
            created_bindings += 1

        existing_health = await aix_model_health_status_service.list(
            tenant_id=tenant_id,
            limit=10,
            offset=0,
            filters={"project_id": project_id, "provider": provider_id, "model_id": model_id},
        )
        health_items = _record_items(existing_health)
        health_details = {
            "source": "hyeve_integration_provider_admin",
            "configured": True,
            "adapter_registered": True,
            "missing_keys": [],
            "health_claim": "not_tested_by_sync",
            "synced_at": metadata_json["synced_at"],
        }
        resolved_health_status = "unknown"
        if health_items:
            health = health_items[0]
            resolved_health_status = str(health.get("health_status") or "unknown")
            await aix_model_health_status_service.update(
                record_id=str(health.get("id")),
                data=AixModelHealthStatusUpdate.model_validate(
                    {
                        "model_name": model_name,
                        "health_status": resolved_health_status,
                        "health_source": str(health.get("health_source") or "integration_provider_config"),
                        "details_json": {**_as_dict(health.get("details_json")), **health_details},
                        "updated_by": actor_id,
                    }
                ),
                tenant_id=tenant_id,
                user_id=actor_id,
            )
            updated_health += 1
        else:
            await aix_model_health_status_service.create(
                data=AixModelHealthStatusCreate.model_validate(
                    {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "provider": provider_id,
                        "model_id": model_id,
                        "model_name": model_name,
                        "health_status": "unknown",
                        "health_source": "integration_provider_config",
                        "details_json": health_details,
                        "created_by": actor_id,
                        "updated_by": actor_id,
                    }
                ),
                tenant_id=tenant_id,
                user_id=actor_id,
            )
            created_health += 1

        synced.append(
            {
                "provider": provider_id,
                "model_id": model_id,
                "binding_key": binding_key,
                "route_class": "primary",
                "health_status": resolved_health_status,
            }
        )

    return ModelRegistrySyncResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "source": "hyeve_integration_provider_admin",
            "provider_rows_seen": len(providers),
            "bindings_created": created_bindings,
            "bindings_updated": updated_bindings,
            "health_created": created_health,
            "health_updated": updated_health,
            "synced": synced,
            "skipped": skipped,
        }
    )


@router.get(
    "/ledger",
    response_model=ModelLedgerResponse,
    summary="Query tenant model usage cost/latency ledger",
)
async def get_model_cost_ledger(
    project_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    safe_limit = max(1, min(limit, 1000))
    safe_offset = max(0, offset)
    decision_filters: dict[str, Any] = {}
    if project_id:
        decision_filters["project_id"] = project_id
    decision_rows = await aix_gateway_decisions_service.list(
        tenant_id=tenant_id,
        limit=safe_limit,
        offset=safe_offset,
        filters=decision_filters or None,
    )
    items = _record_items(decision_rows)
    totals = {
        "records": len(items),
        "tokens": 0,
        "estimated_cost": 0.0,
        "latency_ms": 0,
    }
    by_provider: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    ledger_entries: list[dict[str, Any]] = []
    for row in items:
        tokens = _to_int(row.get("tokens_used"))
        estimated_cost = _to_float(row.get("estimated_cost"))
        latency_ms = _to_int(row.get("latency_ms"))
        decision_payload = _as_dict(row.get("decision_payload_json"))
        router_profile = _as_dict(decision_payload.get("router_profile"))
        chosen_pool = str(router_profile.get("pool_group") or decision_payload.get("chosen_pool") or "")
        provider = str(row.get("selected_provider") or "unknown")
        model_id = str(row.get("selected_model_id") or "unknown")
        model_key = f"{provider}:{model_id}"
        totals["tokens"] += tokens
        totals["estimated_cost"] += estimated_cost
        totals["latency_ms"] += latency_ms
        if provider not in by_provider:
            by_provider[provider] = {"records": 0, "tokens": 0, "estimated_cost": 0.0, "latency_ms": 0}
        if model_key not in by_model:
            by_model[model_key] = {
                "provider": provider,
                "model_id": model_id,
                "records": 0,
                "tokens": 0,
                "estimated_cost": 0.0,
                "latency_ms": 0,
            }
        by_provider[provider]["records"] += 1
        by_provider[provider]["tokens"] += tokens
        by_provider[provider]["estimated_cost"] += estimated_cost
        by_provider[provider]["latency_ms"] += latency_ms
        by_model[model_key]["records"] += 1
        by_model[model_key]["tokens"] += tokens
        by_model[model_key]["estimated_cost"] += estimated_cost
        by_model[model_key]["latency_ms"] += latency_ms
        ledger_entries.append(
            {
                "decision_record_id": row.get("id"),
                "project_id": row.get("project_id"),
                "task_type": row.get("task_type"),
                "risk_level": row.get("risk_level"),
                "selected_provider": provider,
                "selected_model_id": model_id,
                "selected_model_name": row.get("selected_model_name"),
                "correlation_id": row.get("correlation_id"),
                "workhub_task_id": row.get("workhub_task_id"),
                "workhub_task_number": row.get("workhub_task_number"),
                "opsbus_run_id": row.get("opsbus_run_id"),
                "decision_status": row.get("decision_status"),
                "chosen_pool": chosen_pool or None,
                "tokens_used": tokens,
                "estimated_cost": estimated_cost,
                "latency_ms": latency_ms,
                "wall_latency_ms": latency_ms,
                "created_at": row.get("created_at"),
            }
        )

    average_latency_ms = float(totals["latency_ms"] / totals["records"]) if totals["records"] else 0.0
    return ModelLedgerResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "limit": safe_limit,
            "offset": safe_offset,
            "total": decision_rows.get("total", len(items)),
            "has_more": bool(decision_rows.get("has_more", False)),
            "totals": {
                "records": totals["records"],
                "tokens": totals["tokens"],
                "estimated_cost": round(float(totals["estimated_cost"]), 10),
                "average_latency_ms": average_latency_ms,
            },
            "by_provider": by_provider,
            "by_model": list(by_model.values()),
            "items": ledger_entries,
        }
    )


@router.get(
    "/leaderboard",
    response_model=ModelLeaderboardResponse,
    summary="Query router v2 model leaderboard for phase/task/risk scope",
)
async def get_model_leaderboard(
    project_id: str | None = None,
    task_type: str | None = None,
    risk_level: str | None = None,
    phase: Literal["planner", "executor", "verifier"] | None = None,
    limit: int = 200,
    offset: int = 0,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    safe_limit = max(1, min(limit, 1000))
    safe_offset = max(0, offset)
    decision_filters: dict[str, Any] = {}
    if project_id:
        decision_filters["project_id"] = project_id
    if task_type:
        decision_filters["task_type"] = task_type
    if risk_level:
        decision_filters["risk_level"] = risk_level
    decision_rows = await aix_gateway_decisions_service.list(
        tenant_id=tenant_id,
        limit=safe_limit,
        offset=safe_offset,
        filters=decision_filters or None,
    )
    items = decision_rows.get("items", [])
    leaderboard = build_model_leaderboard(
        decision_rows=items,
        task_type=task_type,
        risk_level=risk_level,
        phase=phase,
    )
    return ModelLeaderboardResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "task_type": task_type,
            "risk_level": risk_level,
            "phase": phase,
            "limit": safe_limit,
            "offset": safe_offset,
            "total": len(leaderboard),
            "items": leaderboard,
            "scoring_formula": "w1*success + w2*json_ok + w3*tool_ok - w4*retries - w5*latency - w6*cost",
            "weights": {
                "success": 0.35,
                "json_ok": 0.2,
                "tool_ok": 0.2,
                "retries_penalty": 0.1,
                "latency_penalty": 0.1,
                "cost_penalty": 0.05,
            },
        }
    )


@router.post(
    "/marketplace/templates/validate",
    response_model=MarketplaceResponse,
    summary="Validate marketplace template contract/provenance metadata",
)
async def validate_marketplace_template(
    request: MarketplaceTemplateValidationRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    validation = validate_marketplace_template_record(record=request.record)
    return MarketplaceResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "correlation_id": request.correlation_id,
            "validation": validation,
        }
    )


@router.post(
    "/marketplace/install/audit",
    response_model=MarketplaceResponse,
    summary="Emit marketplace install audit payload for controlled install flow",
)
async def create_marketplace_install_audit(
    request: MarketplaceInstallAuditRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    actor_id = str(current_user.get("id") or current_user.get("sub") or "system")
    event = build_marketplace_install_audit_event(
        tenant_id=tenant_id,
        project_id=request.project_id,
        actor_id=actor_id,
        correlation_id=request.correlation_id,
        template_id=request.template_id,
        template_version=request.template_version,
    )
    return MarketplaceResponse(data={"audit_event": event})


@router.post(
    "/plugins/sandbox/evaluate",
    response_model=PluginSandboxEvaluateResponse,
    summary="Evaluate plugin manifest against sandbox and permission controls",
)
async def evaluate_plugin_sandbox_policy(
    request: PluginSandboxEvaluateRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    result = evaluate_plugin_runtime_policy(
        plugin_manifest=request.plugin_manifest,
        allowed_egress_hosts=request.allowed_egress_hosts,
        allowed_token_scopes=request.allowed_token_scopes,
        max_quota_per_hour=request.max_quota_per_hour,
    )
    return PluginSandboxEvaluateResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "correlation_id": request.correlation_id,
            "evaluation": result,
        }
    )


@router.post(
    "/business/kpi/evaluate",
    response_model=BusinessKpiEvaluateResponse,
    summary="Evaluate versioned business KPIs with source trace mappings",
)
async def evaluate_business_kpis(
    request: BusinessKpiEvaluateRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    result = compute_business_kpis(
        definition_version=request.definition_version,
        source_metrics=request.source_metrics,
    )
    return BusinessKpiEvaluateResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "correlation_id": request.correlation_id,
            "result": result,
        }
    )


@router.post(
    "/voice-nlp/evaluate",
    response_model=VoiceNlpResponse,
    summary="Evaluate feature-flagged voice NLP enablement criteria",
)
async def evaluate_voice_nlp_policy(
    request: VoiceNlpEvaluateRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    result = evaluate_voice_nlp_enablement(
        feature_flag_enabled=request.feature_flag_enabled,
        policy_checks_passed=request.policy_checks_passed,
        rollback_drill_recent=request.rollback_drill_recent,
        compliance_approved=request.compliance_approved,
    )
    return VoiceNlpResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "correlation_id": request.correlation_id,
            "result": result,
        }
    )


@router.post(
    "/voice-nlp/rollback-drill",
    response_model=VoiceNlpResponse,
    summary="Run rollback drill simulation for voice NLP feature",
)
async def run_voice_nlp_rollback_drill(
    request: VoiceNlpRollbackDrillRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    actor_id = str(current_user.get("id") or current_user.get("sub") or "system")
    drill = simulate_voice_nlp_rollback_drill(
        incident_id=request.incident_id,
        actor_id=actor_id,
    )
    return VoiceNlpResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "correlation_id": request.correlation_id,
            "drill": drill,
        }
    )


@router.post(
    "/template-packs/validate",
    response_model=TemplatePackValidationResponse,
    summary="Validate template pack compatibility against active action map",
)
async def validate_template_pack(
    request: TemplatePackValidationRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    fetched_map = await workhub_action_map_client.get_action_map_for_worker(
        tenant_id=tenant_id,
        project_id=request.project_id,
        map_name=request.map_name,
        pinned_version=request.map_version,
        correlation_id=request.correlation_id,
    )
    if isinstance(fetched_map, tuple) and len(fetched_map) == 3:
        active_map, map_error, fetch_trace = fetched_map
    elif isinstance(fetched_map, tuple) and len(fetched_map) == 2:
        active_map, map_error = fetched_map
        fetch_trace = {"cache_hit": False}
    else:
        active_map, map_error, fetch_trace = None, "ACTION_MAP_FETCH_INVALID", {"cache_hit": False}
    if not active_map:
        return TemplatePackValidationResponse(
            success=False,
            message="Action map not available",
            data={
                "project_id": request.project_id,
                "map_name": request.map_name,
                "map_version": request.map_version,
                "error": map_error,
                "action_map_fetch_trace": fetch_trace,
            },
        )

    validation = validate_template_pack_compatibility(
        pack_definition=request.pack_definition,
        active_map_definition=dict(active_map.get("definition_json") or {}),
        active_map_version=int(active_map.get("map_version") or 0),
        min_map_version=request.min_map_version,
        max_map_version=request.max_map_version,
    )
    return TemplatePackValidationResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "pack_name": request.pack_name,
            "pack_version": request.pack_version,
            "map_name": request.map_name,
            "map_version": active_map.get("map_version"),
            "validation": validation,
            "action_map_fetch_trace": fetch_trace,
        }
    )


@router.post(
    "/autoscale/simulate",
    response_model=AutoscaleSimulationResponse,
    summary="Simulate worker autoscaling with tenant fairness controls",
)
async def simulate_worker_autoscale(
    request: AutoscaleSimulationRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    sanitized_backlog = {str(key): max(0, int(value)) for key, value in request.tenant_backlog.items()}
    decision = compute_autoscale_decision(
        tenant_backlog=sanitized_backlog,
        current_workers=request.current_workers,
        min_workers=request.min_workers,
        max_workers=request.max_workers,
        queue_per_worker=request.queue_per_worker,
        min_share_per_active_tenant=request.min_share_per_active_tenant,
    )
    return AutoscaleSimulationResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "correlation_id": request.correlation_id,
            "target_workers": decision.target_workers,
            "action": decision.action,
            "reasons": decision.reasons,
            "per_tenant_allocation": decision.per_tenant_allocation,
            "telemetry": decision.telemetry,
        }
    )


@router.post(
    "/feedback/intake",
    response_model=FeedbackIntakeResponse,
    summary="Convert in-app feedback to structured WorkHub intake task",
)
async def intake_feedback(
    request: FeedbackIntakeRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    actor_id = str(current_user.get("id") or current_user.get("sub") or "system")
    tags = _normalize_feedback_tags(request=request)
    metadata_json = {
        "kind": "pilot_feedback_intake",
        "source": request.source,
        "severity": request.severity,
        "category": request.category,
        "correlation_id": request.correlation_id,
        "reported_by": request.reporter or actor_id,
        "triage": {"status": "new", "owner": "aix-ops", "labels": tags},
    }
    task_data, task_error = await workhub_feedback_intake_client.create_feedback_intake_task(
        tenant_id=tenant_id,
        project_id=request.project_id,
        correlation_id=request.correlation_id,
        title=f"[Feedback] {request.title}",
        description=request.details,
        priority=request.severity,
        metadata_json=metadata_json,
        tags_json=tags,
    )
    if not task_data:
        return FeedbackIntakeResponse(
            success=False,
            message="Feedback intake failed",
            data={
                "project_id": request.project_id,
                "correlation_id": request.correlation_id,
                "error": task_error,
            },
        )
    return FeedbackIntakeResponse(
        data={
            "project_id": request.project_id,
            "correlation_id": request.correlation_id,
            "feedback_title": request.title,
            "intake_task_id": task_data.get("id"),
            "intake_task_number": task_data.get("task_number"),
            "triage_tags": tags,
            "triage_metadata": metadata_json,
            "trace": {
                "source": request.source,
                "reported_by": request.reporter or actor_id,
                "feedback_to_task_linked": True,
            },
        }
    )


@router.get(
    "/authz/matrix",
    response_model=AuthzMatrixResponse,
    summary="Generate route/action authz matrix",
)
async def get_authz_matrix(
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    rows: list[dict[str, Any]] = []
    critical_paths = {
        "/api/aix/model-gateway/resolve",
        "/api/aix/model-gateway/policy/tenant-thresholds/update",
        "/api/aix/model-gateway/action-map/drift/propose-v2",
    }
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        permission = None
        dependencies = getattr(route, "dependant", None)
        if dependencies and dependencies.dependencies:
            permission = _extract_required_permission(dependencies.dependencies[0].call)
        methods = sorted([method for method in (route.methods or set()) if method != "HEAD" and method != "OPTIONS"])
        rows.append(
            {
                "path": route.path,
                "methods": methods,
                "required_permission": permission,
                "critical_action": route.path in critical_paths,
            }
        )
    rows.sort(key=lambda row: row["path"])
    return AuthzMatrixResponse(
        data={
            "subject": str(current_user.get("id") or current_user.get("sub") or "unknown"),
            "admin_bypass_roles": ["admin", "super_admin"],
            "routes": rows,
            "critical_actions": [row for row in rows if row["critical_action"]],
        }
    )


@router.get(
    "/compliance/residency/controls",
    response_model=DataResidencyControlsResponse,
    summary="Get data residency controls and audit status",
)
async def get_data_residency_controls(
    project_id: str | None = None,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    residency_eval = _evaluate_data_residency(tenant_id=tenant_id, selected_provider=None)
    filters: dict[str, Any] = {"decision_reason": "RESIDENCY_PROVIDER_BLOCKED"}
    if project_id:
        filters["project_id"] = project_id
    violations = await aix_gateway_decisions_service.list(
        tenant_id=tenant_id,
        limit=200,
        offset=0,
        filters=filters,
    )
    return DataResidencyControlsResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "residency_zone": residency_eval.get("zone"),
            "allowed_providers": residency_eval.get("allowed_providers"),
            "allow_external": residency_eval.get("allow_external"),
            "enforcement_rule": "selected_provider must be in allowed_providers unless allow_external=true",
            "blocked_decision_count": len(violations.get("items", [])),
            "blocked_decision_samples": [
                {
                    "decision_record_id": row.get("id"),
                    "correlation_id": row.get("correlation_id"),
                    "selected_provider": row.get("selected_provider"),
                    "decision_status": row.get("decision_status"),
                    "decision_reason": row.get("decision_reason"),
                }
                for row in violations.get("items", [])[:20]
            ],
        }
    )


@router.get(
    "/trace/correlation/{correlation_id}",
    response_model=CorrelationTraceResponse,
    summary="Resolve cross-service correlation trace and gaps",
)
async def get_correlation_trace(
    correlation_id: str,
    project_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    safe_limit = max(1, min(limit, 1000))
    safe_offset = max(0, offset)
    filters: dict[str, Any] = {"correlation_id": correlation_id}
    if project_id:
        filters["project_id"] = project_id
    decision_rows = await aix_gateway_decisions_service.list(
        tenant_id=tenant_id,
        limit=safe_limit,
        offset=safe_offset,
        filters=filters,
    )
    items = decision_rows.get("items", [])
    workhub_refs: dict[str, dict[str, Any]] = {}
    opsbus_refs: dict[str, dict[str, Any]] = {}
    codeintel_refs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for row in items:
        decision_id = str(row.get("id") or "")
        payload = row.get("decision_payload_json") if isinstance(row.get("decision_payload_json"), dict) else {}
        codeintel_contract = payload.get("codeintel_evidence_contract") if isinstance(payload, dict) else None
        workhub_task_id = str(row.get("workhub_task_id") or "").strip()
        workhub_task_number = row.get("workhub_task_number")
        opsbus_run_id = str(row.get("opsbus_run_id") or "").strip()
        if workhub_task_id:
            workhub_refs[workhub_task_id] = {
                "task_id": workhub_task_id,
                "task_number": workhub_task_number,
                "project_id": row.get("project_id"),
            }
        else:
            gaps.append({"decision_record_id": decision_id, "gap": "WORKHUB_TASK_LINK_MISSING"})
        if opsbus_run_id:
            opsbus_refs[opsbus_run_id] = {
                "run_id": opsbus_run_id,
                "project_id": row.get("project_id"),
                "decision_status": row.get("decision_status"),
            }
        else:
            gaps.append({"decision_record_id": decision_id, "gap": "OPSBUS_RUN_LINK_MISSING"})
        if isinstance(codeintel_contract, dict) and codeintel_contract:
            codeintel_refs.append(
                {
                    "decision_record_id": decision_id,
                    "contract_name": codeintel_contract.get("name"),
                    "contract_version": codeintel_contract.get("version"),
                    "status": codeintel_contract.get("status"),
                }
            )
        else:
            gaps.append({"decision_record_id": decision_id, "gap": "CODEINTEL_TRACE_MISSING"})
        traces.append(
            {
                "decision_record_id": decision_id,
                "project_id": row.get("project_id"),
                "task_type": row.get("task_type"),
                "risk_level": row.get("risk_level"),
                "decision_status": row.get("decision_status"),
                "selected_provider": row.get("selected_provider"),
                "selected_model_id": row.get("selected_model_id"),
                "workhub_task_id": workhub_task_id or None,
                "workhub_task_number": workhub_task_number,
                "opsbus_run_id": opsbus_run_id or None,
                "created_at": row.get("created_at"),
            }
        )
    if not items:
        gaps.append({"decision_record_id": None, "gap": "AIX_DECISION_NOT_FOUND"})
    return CorrelationTraceResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "correlation_id": correlation_id,
            "total_decisions": len(items),
            "workhub_refs": list(workhub_refs.values()),
            "opsbus_refs": list(opsbus_refs.values()),
            "codeintel_refs": codeintel_refs,
            "trace": traces,
            "trace_gaps": gaps,
            "trace_complete": len(gaps) == 0,
        }
    )


@router.get(
    "/slo/alerts/digest",
    response_model=SloAlertDigestResponse,
    summary="Evaluate failure-mode alerts and generate daily digest",
)
async def get_slo_alert_digest(
    project_id: str | None = None,
    limit: int = 500,
    offset: int = 0,
    failure_threshold: int = 3,
    dlq_threshold: int = 1,
    lease_churn_threshold: int = 2,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    safe_limit = max(1, min(limit, 1000))
    safe_offset = max(0, offset)
    filters: dict[str, Any] = {}
    if project_id:
        filters["project_id"] = project_id
    decision_rows = await aix_gateway_decisions_service.list(
        tenant_id=tenant_id,
        limit=safe_limit,
        offset=safe_offset,
        filters=filters or None,
    )
    items = decision_rows.get("items", [])
    failure_records: list[dict[str, Any]] = []
    dlq_records: list[dict[str, Any]] = []
    lease_churn_records: list[dict[str, Any]] = []
    for row in items:
        reason = str(row.get("decision_reason") or "")
        status = _norm(row.get("decision_status"))
        retry_count = _to_int(row.get("retry_count"))
        payload = row.get("decision_payload_json") if isinstance(row.get("decision_payload_json"), dict) else {}
        if status in {"blocked", "failed", "error"} or "fail" in _norm(reason):
            failure_records.append(row)
        if "dlq" in _norm(reason) or _to_int(payload.get("dlq_count")) > 0:
            dlq_records.append(row)
        if "lease" in _norm(reason) or retry_count >= 3:
            lease_churn_records.append(row)

    def _alert_state(count: int, threshold: int) -> str:
        return "fired" if count >= max(1, threshold) else "resolved"

    on_call_route = str(settings.RETRIEVAL_POLICY_ENHANCEMENT_TASK_NUMBER or "aix-oncall-primary")
    alerts = [
        {
            "rule_id": "aix.failure_mode.high",
            "count": len(failure_records),
            "threshold": max(1, failure_threshold),
            "state": _alert_state(len(failure_records), failure_threshold),
            "on_call_route": on_call_route,
        },
        {
            "rule_id": "aix.dlq.backlog",
            "count": len(dlq_records),
            "threshold": max(1, dlq_threshold),
            "state": _alert_state(len(dlq_records), dlq_threshold),
            "on_call_route": on_call_route,
        },
        {
            "rule_id": "aix.lease.churn",
            "count": len(lease_churn_records),
            "threshold": max(1, lease_churn_threshold),
            "state": _alert_state(len(lease_churn_records), lease_churn_threshold),
            "on_call_route": on_call_route,
        },
    ]
    return SloAlertDigestResponse(
        data={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "alerts": alerts,
            "digest": {
                "decision_records_scanned": len(items),
                "failure_mode_count": len(failure_records),
                "dlq_signal_count": len(dlq_records),
                "lease_churn_count": len(lease_churn_records),
                "fired_rules": [a["rule_id"] for a in alerts if a["state"] == "fired"],
                "sample_records": [
                    {
                        "decision_record_id": row.get("id"),
                        "correlation_id": row.get("correlation_id"),
                        "decision_status": row.get("decision_status"),
                        "decision_reason": row.get("decision_reason"),
                        "retry_count": row.get("retry_count"),
                    }
                    for row in items[:10]
                ],
            },
        }
    )


@router.post(
    "/policy/tenant-thresholds/update",
    response_model=TenantThresholdsUpdateResponse,
    summary="Update tenant model/cost/latency thresholds with audit",
)
async def update_tenant_thresholds(
    request: TenantThresholdsUpdateRequest,
    current_user: dict = Depends(require_permission("aix_model_gateway_policies:update")),
):
    tenant_id = await validate_tenant_access(current_user)
    actor_id = str(current_user.get("id") or current_user.get("sub") or "system")
    roles = current_user.get("roles") if isinstance(current_user.get("roles"), list) else []
    policy_rows = await aix_model_gateway_policies_service.list(
        tenant_id=tenant_id,
        limit=200,
        offset=0,
        filters={
            "project_id": request.project_id,
            "policy_key": request.policy_key,
            "task_type": request.task_type,
            "risk_level": request.risk_level,
            "model_policy": request.model_policy,
        },
    )
    target_policy = next(
        (row for row in policy_rows.get("items", []) if str(row.get("policy_key")) == request.policy_key), None
    )
    if not target_policy:
        return TenantThresholdsUpdateResponse(
            success=False,
            message="Tenant threshold policy not found",
            data={
                "project_id": request.project_id,
                "policy_key": request.policy_key,
                "task_type": request.task_type,
                "risk_level": request.risk_level,
            },
        )
    metadata_before = dict(_as_dict(target_policy.get("metadata_json")))
    threshold_payload = {
        "max_tokens": request.max_tokens,
        "max_estimated_cost": request.max_estimated_cost,
        "max_latency_ms": request.max_latency_ms,
    }
    tenant_thresholds = dict(_as_dict(metadata_before.get("tenant_thresholds")))
    tenant_thresholds[str(tenant_id)] = {k: v for k, v in threshold_payload.items() if v is not None}
    metadata_after = {**metadata_before, "tenant_thresholds": tenant_thresholds}
    updated_policy = await aix_model_gateway_policies_service.update(
        record_id=str(target_policy.get("id")),
        data=AixModelGatewayPoliciesUpdate.model_validate({"metadata_json": metadata_after, "updated_by": actor_id}),
        tenant_id=tenant_id,
        user_id=actor_id,
        correlation_id=request.correlation_id,
    )
    audit_entry = await admin_audit_log_service.create(
        data=AdminAuditLogCreate.model_validate(
            {
                "tenant_id": tenant_id,
                "project_id": request.project_id,
                "actor_sub": actor_id,
                "actor_roles": {"roles": roles},
                "action": "aix.tenant_policy.thresholds.update",
                "resource_type": "aix_model_gateway_policies",
                "resource_id": str(target_policy.get("id")),
                "request_id": request.correlation_id,
                "metadata_json": {
                    "policy_key": request.policy_key,
                    "task_type": request.task_type,
                    "risk_level": request.risk_level,
                    "model_policy": request.model_policy,
                    "thresholds_before": _extract_tenant_thresholds(metadata_json=metadata_before, tenant_id=tenant_id),
                    "thresholds_after": _extract_tenant_thresholds(metadata_json=metadata_after, tenant_id=tenant_id),
                },
                "created_by": actor_id,
                "updated_by": actor_id,
            }
        ),
        tenant_id=tenant_id,
        user_id=actor_id,
        correlation_id=request.correlation_id,
    )
    enhancement_logged, enhancement_error = await workhub_enhancement_stream_client.log_tenant_threshold_policy_update(
        tenant_id=tenant_id,
        project_id=request.project_id,
        correlation_id=request.correlation_id,
        actor_id=actor_id,
        policy_key=request.policy_key,
        updated_thresholds=_extract_tenant_thresholds(metadata_json=metadata_after, tenant_id=tenant_id),
    )
    return TenantThresholdsUpdateResponse(
        data={
            "project_id": request.project_id,
            "policy_key": request.policy_key,
            "policy_record_id": str(target_policy.get("id")),
            "thresholds": _extract_tenant_thresholds(metadata_json=metadata_after, tenant_id=tenant_id),
            "updated_policy_id": getattr(updated_policy, "id", None),
            "audit_logged": audit_entry is not None,
            "audit_record_id": getattr(audit_entry, "id", None),
            "enhancement_event_logged": enhancement_logged,
            "enhancement_event_error": enhancement_error,
        }
    )


def _derive_proposed_action_map_definition(
    *,
    current_definition: dict[str, Any],
    task_type: str,
    risk_level: str,
    model_policy: str,
    allowed_providers: list[str],
    codeintel_context: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = dict(current_definition or {})
    capabilities = dict(current.get("capabilities") or {})
    current_capability = dict(capabilities.get("model_routing") or {})
    current_allowed = [
        str(item).strip().lower() for item in current_capability.get("allowed_providers", []) if str(item).strip()
    ]
    next_allowed = list(dict.fromkeys([item for item in allowed_providers if item]))
    if not next_allowed:
        next_allowed = current_allowed
    next_capability = {
        **current_capability,
        "dispatch_path": str(current_capability.get("dispatch_path") or "opsbus.dispatch"),
        "allowed_providers": next_allowed,
        "allowed_route_classes": current_capability.get("allowed_route_classes") or ["primary", "escalation"],
        "selection_policy": {
            "task_type": task_type,
            "risk_level": risk_level,
            "model_policy": model_policy,
        },
        "drift_inputs": {
            "entities_total": int((codeintel_context or {}).get("entities_total") or 0),
            "citations_total": len((codeintel_context or {}).get("citations") or []),
        },
    }
    capabilities["model_routing"] = next_capability
    proposed = {**current, "capabilities": capabilities}
    drift_summary = {
        "capability": "model_routing",
        "provider_before": current_allowed,
        "provider_after": next_allowed,
        "providers_added": sorted([item for item in next_allowed if item not in current_allowed]),
        "providers_removed": sorted([item for item in current_allowed if item not in next_allowed]),
        "entities_total": int((codeintel_context or {}).get("entities_total") or 0),
        "citations_total": len((codeintel_context or {}).get("citations") or []),
    }
    return proposed, drift_summary


@router.post(
    "/action-map/drift/propose-v2",
    response_model=DriftProposalResponse,
    summary="Detect action-map drift and create proposed v2 map",
)
async def propose_action_map_v2_from_drift(
    request: DriftProposalRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    actor_id = str(current_user.get("id") or current_user.get("sub") or "system")
    fetched_map = await workhub_action_map_client.get_action_map_for_worker(
        tenant_id=tenant_id,
        project_id=request.project_id,
        map_name=request.map_name,
        pinned_version=None,
        correlation_id=request.correlation_id,
    )
    if isinstance(fetched_map, tuple) and len(fetched_map) == 3:
        active_map, map_error, fetch_trace = fetched_map
    elif isinstance(fetched_map, tuple) and len(fetched_map) == 2:
        active_map, map_error = fetched_map
        fetch_trace = {"cache_hit": False}
    else:
        active_map, map_error, fetch_trace = None, "ACTION_MAP_FETCH_INVALID", {"cache_hit": False}
    if not active_map:
        return DriftProposalResponse(
            success=False,
            message="Active action map not found",
            data={
                "project_id": request.project_id,
                "map_name": request.map_name,
                "error": map_error,
                "action_map_fetch_trace": fetch_trace,
            },
        )

    # Wave 4b: same posture gate as the resolve path — no CodeIntel calls
    # when effective posture is off. Downstream consumers take `or {}`.
    from backend.services.retrieval_policy_service import _codeintel_posture as _w4b_posture

    if _w4b_posture() == "off":
        codeintel_context, codeintel_error = None, "WAVE0_CODEINTEL_DISABLED"
    else:
        codeintel_context, codeintel_error = await codeintel_query_client.get_context(
            tenant_id=tenant_id,
            project_id=request.project_id,
            entity_query=request.entity_query or request.task_type,
            retrieval_policy={"requested_mode": "codeintel_first", "effective_mode": "codeintel_first"},
        )
    policy_rows = await aix_model_gateway_policies_service.list(
        tenant_id=tenant_id,
        limit=200,
        offset=0,
        filters={
            "task_type": request.task_type,
            "risk_level": request.risk_level,
            "model_policy": request.model_policy,
        },
    )
    provider_candidates: list[str] = []
    for row in policy_rows.get("items", []):
        provider_candidates.extend(
            [
                _norm(row.get("default_provider")),
                _norm(row.get("escalation_provider")),
            ]
        )
    allowed_providers = [item for item in provider_candidates if item]
    proposed_definition, drift_summary = _derive_proposed_action_map_definition(
        current_definition=dict(active_map.get("definition_json") or {}),
        task_type=request.task_type,
        risk_level=request.risk_level,
        model_policy=request.model_policy,
        allowed_providers=allowed_providers,
        codeintel_context=codeintel_context or {},
    )
    current_serialized = _stable_json(dict(active_map.get("definition_json") or {}))
    proposed_serialized = _stable_json(proposed_definition)
    changed = current_serialized != proposed_serialized
    drift_quality = assess_action_map_drift_quality(
        provider_before=drift_summary.get("provider_before", []),
        provider_after=drift_summary.get("provider_after", []),
        codeintel_context=codeintel_context or {},
    )
    if not changed and not request.force_proposal:
        return DriftProposalResponse(
            data={
                "project_id": request.project_id,
                "map_name": request.map_name,
                "active_map_id": active_map.get("id"),
                "active_map_version": active_map.get("map_version"),
                "drift_detected": False,
                "proposal_created": False,
                "reason": "NO_ACTION_MAP_DRIFT",
                "drift_summary": drift_summary,
                "drift_quality": drift_quality,
                "codeintel_error": codeintel_error,
                "action_map_fetch_trace": fetch_trace,
            }
        )
    if changed and not request.force_proposal and not bool(drift_quality.get("should_propose")):
        return DriftProposalResponse(
            data={
                "project_id": request.project_id,
                "map_name": request.map_name,
                "active_map_id": active_map.get("id"),
                "active_map_version": active_map.get("map_version"),
                "drift_detected": True,
                "proposal_created": False,
                "reason": "DRIFT_SIGNAL_BELOW_THRESHOLD",
                "drift_summary": drift_summary,
                "drift_quality": drift_quality,
                "codeintel_error": codeintel_error,
                "action_map_fetch_trace": fetch_trace,
            }
        )

    proposal_metadata = {
        "kind": "action_map_drift_proposal_v2",
        "proposed_from_action_map_id": active_map.get("id"),
        "proposed_from_map_version": int(active_map.get("map_version") or 0),
        "correlation_id": request.correlation_id,
        "drift_summary": drift_summary,
        "drift_quality": drift_quality,
        "codeintel_error": codeintel_error,
    }
    created_map, create_error = await workhub_action_map_client.create_proposed_action_map(
        tenant_id=tenant_id,
        project_id=request.project_id,
        correlation_id=request.correlation_id,
        map_name=request.map_name,
        definition_json=proposed_definition,
        metadata_json=proposal_metadata,
    )
    if not created_map:
        return DriftProposalResponse(
            success=False,
            message="Failed to create proposed action map",
            data={
                "project_id": request.project_id,
                "map_name": request.map_name,
                "active_map_id": active_map.get("id"),
                "active_map_version": active_map.get("map_version"),
                "error": create_error,
                "drift_summary": drift_summary,
                "action_map_fetch_trace": fetch_trace,
            },
        )

    notified, notification_error = await workhub_enhancement_stream_client.log_action_map_drift_proposal(
        tenant_id=tenant_id,
        project_id=request.project_id,
        correlation_id=request.correlation_id,
        actor_id=actor_id,
        map_name=request.map_name,
        from_version=int(active_map.get("map_version") or 0),
        to_version=int(created_map.get("map_version") or 0),
        drift_summary=drift_summary,
    )
    return DriftProposalResponse(
        data={
            "project_id": request.project_id,
            "map_name": request.map_name,
            "active_map_id": active_map.get("id"),
            "active_map_version": active_map.get("map_version"),
            "drift_detected": changed,
            "proposal_created": True,
            "proposal_map_id": created_map.get("id"),
            "proposal_map_version": created_map.get("map_version"),
            "proposal_status": created_map.get("status"),
            "activation_required": True,
            "auto_activated": False,
            "drift_summary": drift_summary,
            "drift_quality": drift_quality,
            "codeintel_error": codeintel_error,
            "action_map_fetch_trace": fetch_trace,
            "admin_notification": {
                "logged": notified,
                "error": notification_error,
                "channel": "workhub_enhancement_stream_comment",
            },
        }
    )


@router.post(
    "/resolve",
    response_model=ResolveModelGatewayResponse,
    summary="Resolve deterministic model route",
)
async def resolve_model_gateway(
    request: ResolveModelGatewayRequest,
    current_user: dict = Depends(require_permission("aix_gateway_decisions:read")),
):
    tenant_id = await validate_tenant_access(current_user)
    codeintel_entity_query = _resolve_codeintel_entity_query(request=request)
    retrieval_policy = evaluate_retrieval_policy(
        requested_mode=request.retrieval_mode,
        override_reason=request.retrieval_override_reason,
        requested_paths=request.retrieval_paths,
        exception_paths=settings.retrieval_policy_exception_paths,
    )

    actor_id = str(current_user.get("id") or current_user.get("sub") or "system")
    policy_outage_error: str | None = None
    try:
        policy_rows = await aix_model_gateway_policies_service.list(
            tenant_id=tenant_id,
            limit=200,
            offset=0,
            filters={"task_type": request.task_type, "risk_level": request.risk_level},
        )
    except Exception as exc:
        policy_rows = {"items": []}
        policy_outage_error = str(exc)
    if policy_outage_error is not None:
        # Sensitive routes fail closed when policy engine is unavailable.
        if request.model_policy != "slavx_only":
            return ResolveModelGatewayResponse(
                success=False,
                message="Policy engine unavailable; sensitive route blocked",
                data={
                    "decision_status": "blocked",
                    "decision_reason": "POLICY_OUTAGE_FAIL_CLOSED",
                    "selected_provider": None,
                    "selected_model_id": None,
                    "selected_model_name": None,
                    "correlation_id": request.correlation_id,
                    "policy_outage": {
                        "mode": "fail_closed",
                        "sensitive_route": True,
                        "error": policy_outage_error,
                    },
                    "external_calls_blocked": True,
                    "local_only_mode": False,
                },
            )
        # Non-sensitive routes degrade to local-only mode with strict truncation.
        truncated_task_type, task_type_truncated = _truncate_text(request.task_type, 64)
        degraded_decision_payload = {
            "route_class": "primary",
            "policy_key": "policy_outage_local_mode",
            "retry_count": request.retry_count,
            "escalation_threshold": 0,
            "policy_outage": {
                "mode": "local_only",
                "sensitive_route": False,
                "error": policy_outage_error,
            },
            "truncation": {"task_type_truncated": task_type_truncated, "max_len": 64},
        }
        persisted = await aix_gateway_decisions_service.create(
            data=AixGatewayDecisionsCreate.model_validate(
                {
                    "tenant_id": tenant_id,
                    "project_id": request.project_id,
                    "correlation_id": request.correlation_id,
                    "workhub_task_id": request.workhub_task_id,
                    "workhub_task_number": request.workhub_task_number,
                    "opsbus_run_id": request.opsbus_run_id,
                    "task_type": truncated_task_type,
                    "risk_level": request.risk_level,
                    "model_policy": request.model_policy,
                    "selected_provider": "slavx",
                    "selected_model_id": "local-safemode",
                    "selected_model_name": "Slavx Local SafeMode",
                    "decision_status": "selected",
                    "decision_reason": "POLICY_OUTAGE_LOCAL_DEGRADED",
                    "retry_count": request.retry_count,
                    "escalation_attempted": False,
                    "tokens_used": request.tokens_used,
                    "estimated_cost": request.estimated_cost,
                    "latency_ms": request.latency_ms,
                    "decision_payload_json": degraded_decision_payload,
                    "created_by": actor_id,
                    "updated_by": actor_id,
                }
            ),
            tenant_id=tenant_id,
            user_id=actor_id,
        )
        return ResolveModelGatewayResponse(
            message="Policy engine unavailable; non-sensitive route degraded to local-only mode",
            data={
                "decision_status": "selected",
                "selected_provider": "slavx",
                "selected_model_id": "local-safemode",
                "selected_model_name": "Slavx Local SafeMode",
                "decision_reason": "POLICY_OUTAGE_LOCAL_DEGRADED",
                "route_class": "primary",
                "policy_key": "policy_outage_local_mode",
                "retry_count": request.retry_count,
                "escalation_threshold": 0,
                "decision_record_id": getattr(persisted, "id", None),
                "local_only_mode": True,
                "external_calls_blocked": True,
                "truncation": {"task_type_truncated": task_type_truncated, "max_len": 64},
                "policy_outage": {
                    "mode": "local_only",
                    "sensitive_route": False,
                    "error": policy_outage_error,
                },
                "model_usage": {
                    "model_provider": "slavx",
                    "model_id": "local-safemode",
                    "model_name": "Slavx Local SafeMode",
                    "tokens": request.tokens_used,
                    "cost": request.estimated_cost,
                    "latency": request.latency_ms,
                    "correlation_id": request.correlation_id,
                    "decision_status": "selected",
                    "decision_reason": "POLICY_OUTAGE_LOCAL_DEGRADED",
                    "route_class": "primary",
                    "local_only_mode": True,
                    "external_calls_blocked": True,
                },
            },
        )
    enhancement_logged = False
    enhancement_log_error: str | None = None
    if retrieval_policy.direct_filesystem_requested:
        enhancement_logged, enhancement_log_error = await workhub_enhancement_stream_client.log_retrieval_override(
            tenant_id=tenant_id,
            project_id=request.project_id or "unknown",
            correlation_id=request.correlation_id,
            actor_id=actor_id,
            override_reason=request.retrieval_override_reason or "",
            requested_paths=retrieval_policy.requested_paths,
            allowed=retrieval_policy.direct_filesystem_allowed,
            policy_result=retrieval_policy.denied_reason or "ALLOWED_CODEINTEL_FIRST_ENFORCED",
        )

    if retrieval_policy.effective_mode == "direct_filesystem":
        # Wave 0 authority freeze: no CodeIntel/Haystack calls; downstream
        # already handles codeintel_context=None. Advisory posture still
        # refuses to let retrieval results become targets (see _infer_target_files).
        codeintel_context, codeintel_error = None, "WAVE0_CODEINTEL_DISABLED"
    else:
        codeintel_context, codeintel_error = await codeintel_query_client.get_context(
            tenant_id=tenant_id,
            project_id=request.project_id or "unknown",
            entity_query=codeintel_entity_query,
            retrieval_policy={
                "requested_mode": retrieval_policy.requested_mode,
                "effective_mode": retrieval_policy.effective_mode,
                "direct_filesystem_requested": retrieval_policy.direct_filesystem_requested,
                "direct_filesystem_allowed": retrieval_policy.direct_filesystem_allowed,
                "denied_reason": retrieval_policy.denied_reason,
                "requested_paths": retrieval_policy.requested_paths,
            },
        )
    normalized_citations = _normalize_codeintel_citations((codeintel_context or {}).get("citations") or [])
    planner_packet = _build_planner_slice_packet(
        request=request,
        codeintel_context=codeintel_context,
        normalized_citations=normalized_citations,
    )
    matched_policy = next(
        (
            row
            for row in policy_rows.get("items", [])
            if _norm(row.get("model_policy")) == _norm(request.model_policy)
            and _norm(row.get("status")) in {"active", ""}
        ),
        None,
    )
    threshold_policy_key = str((matched_policy or {}).get("policy_key") or "")
    threshold_config = _extract_tenant_thresholds(
        metadata_json=(matched_policy or {}).get("metadata_json"),
        tenant_id=tenant_id,
        phase=request.phase,
    )
    phase_router_config = _extract_phase_router_config(
        metadata_json=(matched_policy or {}).get("metadata_json"),
        tenant_id=tenant_id,
        phase=request.phase,
    )
    yaml_phase_router = _yaml_phase_router_config(request=request)
    if yaml_phase_router:
        phase_router_config = {
            **phase_router_config,
            **yaml_phase_router,
            "router_version": phase_router_config.get("router_version") or yaml_phase_router.get("router_version"),
            "complexity_escalation_threshold": phase_router_config.get("complexity_escalation_threshold")
            or yaml_phase_router.get("complexity_escalation_threshold"),
            "circuit_breaker_retry_threshold": phase_router_config.get("circuit_breaker_retry_threshold")
            or yaml_phase_router.get("circuit_breaker_retry_threshold"),
        }
    threshold_violations = _evaluate_threshold_violations(
        thresholds=threshold_config,
        tokens_used=request.tokens_used,
        estimated_cost=request.estimated_cost,
        latency_ms=request.latency_ms,
    )
    binding_rows = await aix_model_provider_bindings_service.list(
        tenant_id=tenant_id,
        limit=500,
        offset=0,
        filters={"is_enabled": True},
    )
    health_rows = await aix_model_health_status_service.list(
        tenant_id=tenant_id,
        limit=500,
        offset=0,
    )
    escalation_policy_rows = await aix_escalation_policies_service.list(
        tenant_id=tenant_id,
        limit=200,
        offset=0,
        filters={"task_type": request.task_type, "risk_level": request.risk_level},
    )

    complexity_threshold = float(phase_router_config.get("complexity_escalation_threshold") or 0.8)
    circuit_breaker_retry_threshold = int(phase_router_config.get("circuit_breaker_retry_threshold") or 3)
    complexity_score = float(request.complexity_score or 0.0)
    force_escalation = bool(
        (complexity_score >= complexity_threshold)
        or (request.retry_count >= circuit_breaker_retry_threshold > 0)
        or bool(threshold_violations)
    )
    preferred_provider = str(phase_router_config.get("preferred_provider") or "openrouter")
    allowed_model_patterns = list(phase_router_config.get("allowed_models") or [])
    all_enabled_bindings = list(binding_rows.get("items", []))
    bindings_for_route = _allowlisted_bindings(all_enabled_bindings, allowed_model_patterns)
    if allowed_model_patterns and not bindings_for_route:
        bindings_for_route = list(all_enabled_bindings)
    policy_rows_for_route = list(policy_rows.get("items", []))
    escalation_rows_for_route = list(escalation_policy_rows.get("items", []))
    if phase_router_config.get("first_stage_source") == "yaml_persona_pool":
        decision = _resolve_yaml_pool_decision(
            bindings_for_route=bindings_for_route,
            health_rows=list(health_rows.get("items", [])),
            retry_count=request.retry_count,
            policy_key=str((policy_rows_for_route[0] or {}).get("policy_key") or "") if policy_rows_for_route else None,
            escalation_policies=escalation_rows_for_route,
            force_escalation=force_escalation if request.model_policy == "allow_escalate" else False,
        )
    else:
        decision = resolve_model_route(
            task_type=request.task_type,
            risk_level=request.risk_level,
            model_policy=request.model_policy,
            project_id=request.project_id,
            policies=policy_rows_for_route,
            bindings=bindings_for_route,
            health_rows=health_rows.get("items", []),
            escalation_policies=escalation_rows_for_route,
            retry_count=request.retry_count,
            preferred_provider=preferred_provider if request.model_policy == "allow_escalate" else None,
            force_escalation=force_escalation if request.model_policy == "allow_escalate" else False,
        )
    router_v2_scoring: dict[str, Any] = {"enabled": False, "router_version": phase_router_config.get("router_version")}
    if phase_router_config.get("first_stage_source") == "yaml_persona_pool":
        router_v2_scoring = {
            "enabled": False,
            "router_version": phase_router_config.get("router_version"),
            "reason": "yaml_persona_pool_preserves_order",
        }
    elif str(phase_router_config.get("router_version") or "").strip().lower().startswith("v2"):
        history_filters: dict[str, Any] = {
            "task_type": request.task_type,
            "risk_level": request.risk_level,
            "model_policy": request.model_policy,
        }
        if request.project_id:
            history_filters["project_id"] = request.project_id
        history_rows = await aix_gateway_decisions_service.list(
            tenant_id=tenant_id,
            limit=300,
            offset=0,
            filters=history_filters,
        )
        leaderboard = build_model_leaderboard(
            decision_rows=history_rows.get("items", []),
            task_type=request.task_type,
            risk_level=request.risk_level,
            phase=request.phase,
        )
        decision, router_v2_override = _apply_router_v2_override(
            decision=decision,
            bindings_for_route=bindings_for_route,
            leaderboard=leaderboard,
        )
        router_v2_scoring = {
            "enabled": True,
            "router_version": phase_router_config.get("router_version"),
            "phase": request.phase,
            "history_rows": len(history_rows.get("items", [])),
            "leaderboard_top": leaderboard[:5],
            "override": router_v2_override,
        }
    action_map: dict[str, Any] | None = None
    action_map_error: str | None = None
    action_map_fetch_trace: dict[str, Any] = {"cache_hit": False}
    if request.project_id:
        fetched_map = await workhub_action_map_client.get_action_map_for_worker(
            tenant_id=tenant_id,
            project_id=request.project_id,
            map_name=request.action_map_name,
            pinned_version=request.action_map_version,
            correlation_id=request.correlation_id,
        )
        if isinstance(fetched_map, tuple) and len(fetched_map) == 3:
            action_map, action_map_error, action_map_fetch_trace = fetched_map
        elif isinstance(fetched_map, tuple) and len(fetched_map) == 2:
            action_map, action_map_error = fetched_map
            action_map_fetch_trace = {"cache_hit": False}
        else:
            action_map, action_map_error, action_map_fetch_trace = (
                None,
                "ACTION_MAP_FETCH_INVALID",
                {"cache_hit": False},
            )
        action_map_result = _resolve_action_map_capability(
            action_map=action_map,
            capability_name=request.intent_capability,
            base_decision=decision,
        )
        if action_map_error and action_map_result.get("miss_reason") is None:
            action_map_result["miss_reason"] = str(action_map_error)
    else:
        action_map_result = {
            "capability": request.intent_capability,
            "capability_hit": True,
            "miss_reason": None,
            "dispatch_path": "aix.model_gateway.resolve",
            "allowed_providers": [],
            "allowed_route_classes": [],
            "map_name": None,
            "map_version": None,
        }
    decision = _apply_action_map_result(base_decision=decision, action_map_result=action_map_result)
    residency_eval = (
        _evaluate_data_residency(tenant_id=tenant_id, selected_provider=decision.selected_provider)
        if decision.selected_provider
        else {
            "zone": _evaluate_data_residency(tenant_id=tenant_id, selected_provider=None).get("zone"),
            "allowed": True,
            "reason": None,
            "allowed_providers": _evaluate_data_residency(tenant_id=tenant_id, selected_provider=None).get(
                "allowed_providers", []
            ),
            "allow_external": _evaluate_data_residency(tenant_id=tenant_id, selected_provider=None).get(
                "allow_external", False
            ),
            "provider": None,
        }
    )
    if decision.selected_provider and not bool(residency_eval.get("allowed", True)):
        decision = RoutingDecision(
            decision_status="blocked",
            selected_provider=decision.selected_provider,
            selected_model_id=decision.selected_model_id,
            selected_model_name=decision.selected_model_name,
            decision_reason=str(residency_eval.get("reason") or "RESIDENCY_PROVIDER_BLOCKED"),
            route_class=decision.route_class,
            policy_key=decision.policy_key,
            retry_count=decision.retry_count,
            escalation_threshold=decision.escalation_threshold,
        )
    persisted = await aix_gateway_decisions_service.create(
        data=AixGatewayDecisionsCreate.model_validate(
            {
                "tenant_id": tenant_id,
                "project_id": request.project_id,
                "correlation_id": request.correlation_id,
                "workhub_task_id": request.workhub_task_id,
                "workhub_task_number": request.workhub_task_number,
                "opsbus_run_id": request.opsbus_run_id,
                "task_type": request.task_type,
                "risk_level": request.risk_level,
                "model_policy": request.model_policy,
                "selected_provider": decision.selected_provider,
                "selected_model_id": decision.selected_model_id,
                "selected_model_name": decision.selected_model_name,
                "decision_status": decision.decision_status,
                "decision_reason": decision.decision_reason,
                "retry_count": decision.retry_count,
                "escalation_attempted": decision.decision_status == "escalated",
                "tokens_used": request.tokens_used,
                "estimated_cost": request.estimated_cost,
                "latency_ms": request.latency_ms,
                "decision_payload_json": {
                    "route_class": decision.route_class,
                    "policy_key": decision.policy_key,
                    "chosen_pool": phase_router_config.get("pool_group"),
                    "retry_count": decision.retry_count,
                    "escalation_threshold": decision.escalation_threshold,
                    "tenant_threshold_policy_key": threshold_policy_key or None,
                    "tenant_thresholds": threshold_config,
                    "tenant_threshold_violations": threshold_violations,
                    "phase": request.phase,
                    "complexity_score": complexity_score,
                    "phase_router_config": phase_router_config,
                    "phase_router_force_escalation": force_escalation,
                    "router_v2_scoring": router_v2_scoring,
                    "router_profile": {
                        "router_version": phase_router_config.get("router_version"),
                        "pool_group": phase_router_config.get("pool_group"),
                        "first_stage_source": phase_router_config.get("first_stage_source"),
                        "role": phase_router_config.get("role"),
                        "persona": phase_router_config.get("persona"),
                        "privacy_mode": phase_router_config.get("privacy_mode"),
                        "routing_mode": phase_router_config.get("routing_mode"),
                        "provider_sort": phase_router_config.get("provider_sort"),
                        "latency_target_ms": phase_router_config.get("latency_target_ms"),
                        "max_price": phase_router_config.get("max_price"),
                        "allowed_models": allowed_model_patterns,
                        "recommended_allowed_models": phase_router_config.get("recommended_allowed_models"),
                    },
                    "allowlist": {
                        "allowed_models": allowed_model_patterns,
                        "available_bindings": len(binding_rows.get("items", [])),
                        "eligible_bindings": len(bindings_for_route),
                        "enforced": bool(allowed_model_patterns),
                    },
                    "data_residency": residency_eval,
                    "action_map": action_map_result,
                    "action_map_error": action_map_error,
                    "action_map_fetch_trace": action_map_fetch_trace,
                    "planner_packet": planner_packet,
                },
                "created_by": actor_id,
                "updated_by": actor_id,
            }
        ),
        tenant_id=tenant_id,
        user_id=actor_id,
    )
    codeintel_contract = {
        "name": CODEINTEL_EVIDENCE_CONTRACT_NAME,
        "version": CODEINTEL_EVIDENCE_CONTRACT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "hyeve-codeintel-service",
        "project_id": request.project_id,
        "task_type": request.task_type,
        "query": {
            "entity_query": codeintel_entity_query,
            "query_fingerprint": _query_fingerprint(
                project_id=request.project_id,
                entity_query=codeintel_entity_query,
                retrieval_mode="codeintel_first",
            ),
        },
        "status": "ok" if codeintel_error is None else "degraded",
        "index_jobs_total": codeintel_context.get("index_jobs_total", 0) if codeintel_context else 0,
        "citations": normalized_citations,
        "confidence_score": codeintel_context.get("confidence_score", 0.0) if codeintel_context else 0.0,
        "fallback_policy": codeintel_context.get("fallback_policy") if codeintel_context else None,
        "retrieval_policy": codeintel_context.get("retrieval_policy") if codeintel_context else None,
        "reliability": _build_codeintel_reliability(codeintel_context=codeintel_context),
        "error": codeintel_error,
    }
    usage_block = {
        "model_provider": decision.selected_provider,
        "model_id": decision.selected_model_id,
        "model_name": decision.selected_model_name,
        "tokens": request.tokens_used,
        "cost": request.estimated_cost,
        "latency": request.latency_ms,
        "wall_latency_ms": request.latency_ms,
        "correlation_id": request.correlation_id,
        "decision_status": decision.decision_status,
        "decision_reason": decision.decision_reason,
        "route_class": decision.route_class,
        "chosen_pool": phase_router_config.get("pool_group"),
        "router_v2_scoring": router_v2_scoring,
        "codeintel_context_available": bool(codeintel_context),
        "codeintel_context": codeintel_context,
        "codeintel_error": codeintel_error,
        "retrieval_policy": codeintel_context.get("retrieval_policy") if codeintel_context else None,
        "codeintel_evidence_contract": codeintel_contract,
        "action_map_routing": action_map_result,
        "action_map_error": action_map_error,
        "action_map_fetch_trace": action_map_fetch_trace,
        "data_residency": residency_eval,
        "router_profile": {
            "router_version": phase_router_config.get("router_version"),
            "pool_group": phase_router_config.get("pool_group"),
            "first_stage_source": phase_router_config.get("first_stage_source"),
            "role": phase_router_config.get("role"),
            "persona": phase_router_config.get("persona"),
            "privacy_mode": phase_router_config.get("privacy_mode"),
            "routing_mode": phase_router_config.get("routing_mode"),
            "provider_sort": phase_router_config.get("provider_sort"),
            "latency_target_ms": phase_router_config.get("latency_target_ms"),
            "max_price": phase_router_config.get("max_price"),
            "allowed_models": allowed_model_patterns,
            "recommended_allowed_models": phase_router_config.get("recommended_allowed_models"),
        },
        "planner_packet": planner_packet,
    }
    sensitive_route = request.model_policy != "slavx_only" or _norm(decision.selected_provider) == "onlineai"
    redaction_report: dict[str, Any] = {
        "redaction_applied": False,
        "sensitive_route": sensitive_route,
        "masked_count": 0,
        "patterns_masked": [],
    }
    redaction_error: str | None = None
    usage_block_redacted = usage_block
    try:
        redaction_result = apply_redaction_worker(payload=usage_block, sensitive_route=sensitive_route)
        usage_block_redacted = redaction_result.redacted_payload
        redaction_report = redaction_result.to_report()
    except Exception as exc:  # pragma: no cover
        redaction_error = str(exc)
        if sensitive_route:
            redaction_report = {
                "redaction_applied": False,
                "sensitive_route": True,
                "masked_count": 0,
                "patterns_masked": [],
                "error": redaction_error,
                "external_calls_blocked": True,
            }
    routing_evidence_payload = _routing_decision_evidence_payload(
        request=request,
        decision=decision,
        action_map_result=action_map_result,
    )
    run_telemetry_written = False
    run_telemetry_error: str | None = None
    if request.opsbus_run_id and request.project_id and not (sensitive_route and redaction_error):
        run_telemetry_written, run_telemetry_error = await opsbus_run_logs_client.log_model_usage(
            tenant_id=tenant_id,
            project_id=request.project_id,
            run_id=request.opsbus_run_id,
            correlation_id=request.correlation_id,
            actor_id=actor_id,
            usage_block=usage_block_redacted,
        )
    elif sensitive_route and redaction_error:
        run_telemetry_error = "REDACTION_FAILED_EXTERNAL_CALLS_BLOCKED"

    evidence_written = False
    evidence_error: str | None = None
    routing_evidence_written = False
    routing_evidence_error: str | None = None
    redaction_evidence_written = False
    redaction_evidence_error: str | None = None
    if request.workhub_task_id and request.project_id and not (sensitive_route and redaction_error):
        evidence_written, evidence_error = await workhub_task_evidence_client.add_model_usage_evidence(
            tenant_id=tenant_id,
            project_id=request.project_id,
            task_id=request.workhub_task_id,
            correlation_id=request.correlation_id,
            actor_id=actor_id,
            usage_block=usage_block_redacted,
        )
        (
            routing_evidence_written,
            routing_evidence_error,
        ) = await workhub_task_evidence_client.add_model_routing_decision_evidence(
            tenant_id=tenant_id,
            project_id=request.project_id,
            task_id=request.workhub_task_id,
            correlation_id=request.correlation_id,
            actor_id=actor_id,
            decision_payload=routing_evidence_payload,
        )
    if request.workhub_task_id and request.project_id:
        (
            redaction_evidence_written,
            redaction_evidence_error,
        ) = await workhub_task_evidence_client.add_redaction_report_evidence(
            tenant_id=tenant_id,
            project_id=request.project_id,
            task_id=request.workhub_task_id,
            correlation_id=request.correlation_id,
            actor_id=actor_id,
            report_payload=redaction_report,
        )
    threshold_alert_logged = False
    threshold_alert_error: str | None = None
    if threshold_violations and request.project_id:
        (
            threshold_alert_logged,
            threshold_alert_error,
        ) = await workhub_enhancement_stream_client.log_tenant_threshold_alert(
            tenant_id=tenant_id,
            project_id=request.project_id,
            correlation_id=request.correlation_id,
            actor_id=actor_id,
            policy_key=threshold_policy_key or "unknown",
            violations=threshold_violations,
            observed_usage={
                "tokens_used": _to_int(request.tokens_used),
                "estimated_cost": _to_float(request.estimated_cost),
                "latency_ms": _to_int(request.latency_ms),
            },
        )
    ayrpack_shadow_write_enqueued, ayrpack_shadow_write_reason = _enqueue_ayrpack_shadow_write(
        tenant_id=tenant_id,
        actor_id=actor_id,
        request=request,
        decision=decision,
        redaction_report=redaction_report,
        usage_block_redacted=usage_block_redacted,
        routing_evidence_payload=routing_evidence_payload,
        decision_record_id=getattr(persisted, "id", None),
        redaction_error=redaction_error,
    )
    payload = decision.to_payload()
    payload["decision_record_id"] = getattr(persisted, "id", None)
    payload["opsbus_run_telemetry_written"] = run_telemetry_written
    payload["opsbus_run_telemetry_error"] = run_telemetry_error
    payload["workhub_evidence_written"] = evidence_written
    payload["workhub_evidence_error"] = evidence_error
    payload["workhub_routing_evidence_written"] = routing_evidence_written
    payload["workhub_routing_evidence_error"] = routing_evidence_error
    payload["workhub_redaction_evidence_written"] = redaction_evidence_written
    payload["workhub_redaction_evidence_error"] = redaction_evidence_error
    payload["codeintel_context_available"] = bool(codeintel_context)
    payload["codeintel_context"] = codeintel_context
    payload["codeintel_error"] = codeintel_error
    payload["codeintel_evidence_contract"] = codeintel_contract
    payload["retrieval_policy"] = codeintel_context.get("retrieval_policy") if codeintel_context else None
    payload["retrieval_override_event_logged"] = enhancement_logged
    payload["retrieval_override_event_error"] = enhancement_log_error
    payload["model_usage"] = usage_block_redacted
    payload["redaction"] = redaction_report
    payload["redaction_error"] = redaction_error
    payload["tenant_thresholds"] = {
        "policy_key": threshold_policy_key or None,
        "phase": request.phase,
        "thresholds": threshold_config,
        "violations": threshold_violations,
        "alert_logged": threshold_alert_logged,
        "alert_error": threshold_alert_error,
        "phase_router": phase_router_config,
        "phase_router_force_escalation": force_escalation,
        "complexity_score": complexity_score,
        "router_v2_scoring": router_v2_scoring,
    }
    payload["data_residency"] = residency_eval
    payload["action_map_routing"] = action_map_result
    payload["action_map_error"] = action_map_error
    payload["action_map_fetch_trace"] = action_map_fetch_trace
    payload["model_routing_decision"] = routing_evidence_payload
    payload["planner_packet"] = planner_packet
    payload["router_profile"] = usage_block_redacted.get("router_profile")
    payload["allowlist"] = {
        "allowed_models": allowed_model_patterns,
        "available_bindings": len(binding_rows.get("items", [])),
        "eligible_bindings": len(bindings_for_route),
        "enforced": bool(allowed_model_patterns),
    }
    payload["ayrpack_rollout_flags"] = {
        "shadow_write_enabled": bool(settings.AYRPACK_SHADOW_WRITE_ENABLED),
        "shadow_write_role": "debug_forensic_mirror",
        "shadow_write_required_for_control_plane": False,
    }
    payload["ayrpack_rollout_telemetry"] = _build_ayrpack_rollout_telemetry(
        request=request,
        ayrpack_shadow_write_enqueued=ayrpack_shadow_write_enqueued,
        ayrpack_shadow_write_reason=ayrpack_shadow_write_reason,
    )
    payload["ayrpack_shadow_write_enqueued"] = ayrpack_shadow_write_enqueued
    payload["ayrpack_shadow_write_reason"] = ayrpack_shadow_write_reason
    return ResolveModelGatewayResponse(data=payload)
