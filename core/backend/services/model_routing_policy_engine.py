"""
Deterministic model routing policy engine for AIX M5 SG2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PolicyLiteral = Literal["slavx_only", "allow_escalate", "onlineai_only"]
DecisionStatus = Literal["selected", "escalated", "blocked"]

HEALTHY_STATES = {"healthy", "degraded"}
ACTIVE_STATES = {"active"}


@dataclass(frozen=True)
class RoutingDecision:
    decision_status: DecisionStatus
    selected_provider: str | None
    selected_model_id: str | None
    selected_model_name: str | None
    decision_reason: str | None
    route_class: str | None
    policy_key: str | None
    retry_count: int
    escalation_threshold: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "decision_status": self.decision_status,
            "selected_provider": self.selected_provider,
            "selected_model_id": self.selected_model_id,
            "selected_model_name": self.selected_model_name,
            "decision_reason": self.decision_reason,
            "route_class": self.route_class,
            "policy_key": self.policy_key,
            "retry_count": self.retry_count,
            "escalation_threshold": self.escalation_threshold,
        }


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_active(status: Any) -> bool:
    normalized = _norm(status)
    if not normalized:
        return True
    return normalized in ACTIVE_STATES


def _is_healthy(status: Any) -> bool:
    return _norm(status) in HEALTHY_STATES


def _as_model_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    return dict(item.__dict__)


def _choose_policy(
    *,
    policies: list[dict[str, Any]],
    project_id: str | None,
    task_type: str,
    risk_level: str,
    model_policy: PolicyLiteral,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for raw in policies:
        row = _as_model_dict(raw)
        if not _is_active(row.get("status")):
            continue
        if _norm(row.get("task_type")) != _norm(task_type):
            continue
        if _norm(row.get("risk_level")) != _norm(risk_level):
            continue
        if _norm(row.get("model_policy")) != _norm(model_policy):
            continue
        candidates.append(row)

    if not candidates:
        return None

    def score(row: dict[str, Any]) -> tuple[int, str]:
        row_project = row.get("project_id")
        if project_id and row_project == project_id:
            return (0, str(row.get("policy_key", "")))
        if row_project in (None, ""):
            return (1, str(row.get("policy_key", "")))
        return (2, str(row.get("policy_key", "")))

    ranked = sorted(candidates, key=score)
    if ranked and score(ranked[0])[0] < 2:
        return ranked[0]
    return None


def _choose_escalation_policy(
    *,
    escalation_policies: list[dict[str, Any]],
    project_id: str | None,
    task_type: str,
    risk_level: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for raw in escalation_policies:
        row = _as_model_dict(raw)
        if not _is_active(row.get("status")):
            continue
        if _norm(row.get("task_type")) != _norm(task_type):
            continue
        if _norm(row.get("risk_level")) != _norm(risk_level):
            continue
        candidates.append(row)

    if not candidates:
        return None

    def score(row: dict[str, Any]) -> tuple[int, str]:
        row_project = row.get("project_id")
        if project_id and row_project == project_id:
            return (0, str(row.get("policy_key", "")))
        if row_project in (None, ""):
            return (1, str(row.get("policy_key", "")))
        return (2, str(row.get("policy_key", "")))

    ranked = sorted(candidates, key=score)
    if ranked and score(ranked[0])[0] < 2:
        return ranked[0]
    return None


def _candidate_bindings(
    *,
    bindings: list[dict[str, Any]],
    provider: str,
    route_classes: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows = []
    for raw in bindings:
        row = _as_model_dict(raw)
        if not row.get("is_enabled", True):
            continue
        if _norm(row.get("provider")) != _norm(provider):
            continue
        if _norm(row.get("route_class")) not in {rc.lower() for rc in route_classes}:
            continue
        rows.append(row)
    return sorted(rows, key=lambda r: (str(r.get("binding_key", "")), str(r.get("model_id", ""))))


def _health_index(health_rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for raw in health_rows:
        row = _as_model_dict(raw)
        key = (_norm(row.get("provider")), str(row.get("model_id", "")))
        index[key] = _norm(row.get("health_status"))
    return index


def _pick_healthy_binding(
    candidates: list[dict[str, Any]], health: dict[tuple[str, str], str]
) -> dict[str, Any] | None:
    for row in candidates:
        status = health.get((_norm(row.get("provider")), str(row.get("model_id", ""))), "unknown")
        if _is_healthy(status):
            return row
    return None


def resolve_model_route(
    *,
    task_type: str,
    risk_level: str,
    model_policy: PolicyLiteral,
    project_id: str | None,
    policies: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    health_rows: list[dict[str, Any]],
    escalation_policies: list[dict[str, Any]] | None = None,
    retry_count: int = 0,
    preferred_provider: str | None = None,
    force_escalation: bool = False,
) -> RoutingDecision:
    policy = _choose_policy(
        policies=policies,
        project_id=project_id,
        task_type=task_type,
        risk_level=risk_level,
        model_policy=model_policy,
    )
    if not policy:
        return RoutingDecision("blocked", None, None, None, "POLICY_NOT_FOUND", None, None, retry_count, 0)

    default_provider = _norm(policy.get("default_provider"))
    preferred = _norm(preferred_provider)
    escalation_provider = _norm(policy.get("escalation_provider"))
    policy_key = str(policy.get("policy_key", ""))
    health = _health_index(health_rows)

    strict_provider = (
        "slavx" if model_policy == "slavx_only" else "onlineai" if model_policy == "onlineai_only" else None
    )

    if strict_provider:
        primary = _candidate_bindings(bindings=bindings, provider=strict_provider, route_classes=("primary",))
        if not primary:
            return RoutingDecision(
                "blocked", None, None, None, "NO_PROVIDER_BINDINGS", None, policy_key, retry_count, 0
            )
        selected = _pick_healthy_binding(primary, health)
        if not selected:
            return RoutingDecision(
                "blocked",
                None,
                None,
                None,
                "NO_HEALTHY_MODEL_FOR_POLICY",
                None,
                policy_key,
                retry_count,
                0,
            )
        return RoutingDecision(
            "selected",
            str(selected.get("provider")),
            str(selected.get("model_id")),
            str(selected.get("model_name")),
            None,
            "primary",
            policy_key,
            retry_count,
            0,
        )

    route_provider = preferred or default_provider
    if force_escalation and model_policy == "allow_escalate":
        route_provider = escalation_provider or ("onlineai" if default_provider == "slavx" else "slavx")

    primary = _candidate_bindings(bindings=bindings, provider=route_provider, route_classes=("primary",))
    selected = _pick_healthy_binding(primary, health)
    if selected:
        return RoutingDecision(
            "selected",
            str(selected.get("provider")),
            str(selected.get("model_id")),
            str(selected.get("model_name")),
            None,
            "primary",
            policy_key,
            retry_count,
            0,
        )

    escalation_policy = _choose_escalation_policy(
        escalation_policies=escalation_policies or [],
        project_id=project_id,
        task_type=task_type,
        risk_level=risk_level,
    )
    escalation_threshold = int(
        (escalation_policy or {}).get("retry_threshold", policy.get("max_retry_before_escalate", 0)) or 0
    )
    if (not force_escalation) and retry_count < escalation_threshold:
        return RoutingDecision(
            "blocked",
            None,
            None,
            None,
            "RETRY_THRESHOLD_NOT_REACHED",
            None,
            policy_key,
            retry_count,
            escalation_threshold,
        )

    escalate_provider = _norm((escalation_policy or {}).get("escalate_to_provider")) or escalation_provider
    escalate_provider = escalate_provider or ("onlineai" if default_provider == "slavx" else "slavx")
    escalation = _candidate_bindings(
        bindings=bindings,
        provider=escalate_provider,
        route_classes=("escalation", "primary"),
    )
    selected = _pick_healthy_binding(escalation, health)
    if selected:
        return RoutingDecision(
            "escalated",
            str(selected.get("provider")),
            str(selected.get("model_id")),
            str(selected.get("model_name")),
            None,
            _norm(selected.get("route_class")) or "escalation",
            policy_key,
            retry_count,
            escalation_threshold,
        )

    block_on_unhealthy = bool((escalation_policy or {}).get("block_on_provider_unhealthy", True))
    reason = "ESCALATION_PROVIDER_UNHEALTHY" if escalation and block_on_unhealthy else "NO_ELIGIBLE_HEALTHY_MODEL"
    return RoutingDecision("blocked", None, None, None, reason, None, policy_key, retry_count, escalation_threshold)
