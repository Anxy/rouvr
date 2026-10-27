"""Discuss turn orchestration for the intelligence layer (W03–W06)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatMessage,
    ChatCompletionResultV1,
    HyeveIntegrationChatError,
    chat_completion,
)
from backend.infrastructure.model_gateway.intelligence_layer.chat_intent import classify_chat_intent
from backend.infrastructure.model_gateway.intelligence_layer.metrics import intelligence_metrics
from backend.infrastructure.model_gateway.intelligence_layer.model_registry import load_model_registry
from backend.infrastructure.model_gateway.intelligence_layer.intake_objective_scope import (
    resolve_normalized_objective,
)
from backend.infrastructure.model_gateway.intelligence_layer.prompts import (
    CONVERSATION_SYSTEM_PROMPT,
    planner_intake_prompt,
)
from backend.infrastructure.model_gateway.intelligence_layer.routing import route_discuss_turn
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter


@dataclass(frozen=True)
class DiscussTurnResultV1:
    reply: str
    task_class: str | None
    proposed_scope: list[str]
    ambiguous: bool
    intent: str
    confidence: float
    objective_summary: str | None
    routing_phase: str
    model_role: str
    planner_invoked: bool
    requires_objective_confirm: bool
    provider: str | None
    resolved_provider: str | None
    model: str | None
    usage: dict[str, Any]
    route_evidence: dict[str, str]
    normalized_objective: str | None = None
    repository_hint: str | None = None
    file_hints: list[str] | None = None
    symbol_hints: list[str] | None = None
    constraints: list[str] | None = None
    raw_model_output: str | None = None
    intake_scope_code: str | None = None
    intake_scope_fallback: bool = False


def _latest_user_text(messages: list[dict[str, str]]) -> str:
    for item in reversed(messages):
        if str(item.get("role") or "") == "user":
            return str(item.get("content") or "").strip()
    return ""


def _strip_dot_slash_prefix(text: str) -> str:
    """Strip leading ``./`` segments without mangling dotfiles like ``.env``."""
    value = text.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _sanitize_file_hints(items: list[str]) -> list[str]:
    """Keep bare repo-relative paths from explicit hints; strip planner/user prose."""
    out: list[str] = []
    for raw in items:
        text = str(raw or "").strip().strip("\"'")
        if not text:
            continue
        # Drop trailing parenthetical notes.
        text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
        if not text:
            continue
        # Absolute Windows/Unix path → basename when it looks like a file.
        if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("/"):
            name = Path(text.replace("\\", "/")).name
            if name:
                out.append(name)
            continue
        text = _strip_dot_slash_prefix(text)
        if text and " " not in text:
            out.append(text)
    # Deduplicate preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


# Backward-compatible alias used by older tests/imports.
_sanitize_proposed_scope = _sanitize_file_hints


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_planner_json(output_text: str) -> dict[str, Any]:
    parsed = json.loads(output_text)
    if not isinstance(parsed, dict):
        raise ValueError("model reply JSON is not an object")
    return parsed


_SOURCE_DUMP_MARKERS = (
    "<!doctype html",
    "<html",
    "<style",
    "```html",
    "```css",
    "```javascript",
    "```js",
    "```tsx",
    "```jsx",
)


def sanitize_discuss_reply(reply: str) -> str:
    """Strip accidental full-source dumps from discuss chat (files come from Create & run)."""
    text = str(reply or "").strip()
    if not text:
        return text
    lowered = text.lower()
    looks_like_dump = any(marker in lowered for marker in _SOURCE_DUMP_MARKERS) or (
        text.count("\n") > 40 and ("function" in lowered or "<div" in lowered or "password" in lowered)
    )
    if not looks_like_dump:
        return text
    return (
        "I won’t paste the full page source in chat. Use **Create & run with AIX** so the "
        "governed cycle writes the needed artifacts, then open the preview link on the "
        "Execution screen after you approve/resume."
    )


async def run_discuss_turn(
    *,
    messages: list[dict[str, str]],
    tenant_id: str,
    actor_id: str,
    authorization_header: str,
    task_classes: frozenset[str],
    escalate_to_planner: bool = False,
    active_objective_id: str | None = None,
    planner_on_objective: bool | None = None,
) -> DiscussTurnResultV1:
    """Run intent → route → conversation or planner intake via Integration only."""
    registry = load_model_registry()
    started = time.perf_counter()
    latest = _latest_user_text(messages)
    intent = classify_chat_intent(
        latest_user_text=latest,
        history=messages,
        active_objective_id=active_objective_id,
    )
    intelligence_metrics.record_intent(intent.intent)

    # Auto-run planner when objective detected unless confirm-only mode.
    from backend.hyeve_aix_service.config import settings

    auto_planner = (
        bool(settings.INTELLIGENCE_LAYER_PLANNER_ON_OBJECTIVE)
        if planner_on_objective is None
        else bool(planner_on_objective)
    )
    force_planner = escalate_to_planner or (
        intent.intent == "objective" and auto_planner
    )
    route = route_discuss_turn(intent, escalate_to_planner=force_planner)
    if force_planner and intent.intent == "objective":
        route = route_discuss_turn(intent, escalate_to_planner=True)

    model_ref = registry.model_ref_for(route.model_role)
    gpr = GovernedProviderRouter().resolve(role=route.model_role, model_ref=model_ref)
    if gpr.disposition != "ALLOW":
        raise HyeveIntegrationChatError(
            "GOVERNED_ROUTE_DENIED",
            detail=",".join(gpr.reason_codes),
        )

    if route.invoke_planner:
        intelligence_metrics.record_planner()
        system = planner_intake_prompt(list(task_classes))
        chat_messages = [ChatMessage(role="system", content=system)] + [
            ChatMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
            for m in messages
            if m.get("role") in {"user", "assistant"} and m.get("content")
        ]
        try:
            result = await chat_completion(
                messages=chat_messages,
                tenant_id=tenant_id,
                actor_id=actor_id,
                authorization_header=authorization_header,
                response_format={"type": "json_object"},
                model=model_ref,
                timeout_seconds=float(settings.INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS),
            )
            raw_model_output = str(result.output_text or "")
            parsed = _parse_planner_json(raw_model_output)
            reply = sanitize_discuss_reply(str(parsed.get("reply") or "").strip())
            if not reply:
                raise ValueError("empty reply field")
            # Compatibility metadata only — never used to restrict execution capability.
            task_class = str(parsed.get("task_class") or "").strip().lower()
            if task_class and task_class not in task_classes:
                task_class = ""
            ambiguous = bool(parsed.get("ambiguous", False))
            file_hints = _sanitize_file_hints(
                _string_list(parsed.get("file_hints"))
                or _string_list(parsed.get("proposed_scope"))
            )
            symbol_hints = _string_list(parsed.get("symbol_hints"))
            constraints = _string_list(parsed.get("constraints"))
            model_normalized = str(parsed.get("normalized_objective") or "").strip() or None
            if not model_normalized:
                model_normalized = route.objective_summary
            # Invariant: normalized_objective must not silently drop explicit deliverables.
            original_ask = latest or (route.objective_summary or "")
            normalized_objective, scope = resolve_normalized_objective(
                original_objective=original_ask,
                normalized_objective=model_normalized,
            )
            if scope.used_fallback:
                intelligence_metrics.extras["intake_scope_fallback"] = (
                    intelligence_metrics.extras.get("intake_scope_fallback", 0) + 1
                )
            repository_hint = str(
                parsed.get("repository_hint") or parsed.get("repository_id") or ""
            ).strip() or None
            usage = dict(result.usage or {})
            intelligence_metrics.record_usage(usage, latency_ms=(time.perf_counter() - started) * 1000)
            return DiscussTurnResultV1(
                reply=reply,
                task_class=task_class or None,
                proposed_scope=list(file_hints),
                ambiguous=ambiguous,
                intent=route.intent,
                confidence=route.confidence,
                objective_summary=route.objective_summary,
                routing_phase=route.routing_phase,
                model_role=route.model_role,
                planner_invoked=True,
                requires_objective_confirm=False,
                provider=result.provider,
                resolved_provider=result.resolved_provider,
                model=result.model,
                usage=usage,
                route_evidence=dict(gpr.evidence),
                normalized_objective=normalized_objective,
                repository_hint=repository_hint,
                file_hints=list(file_hints),
                symbol_hints=list(symbol_hints),
                constraints=list(constraints),
                raw_model_output=raw_model_output,
                intake_scope_code=scope.code,
                intake_scope_fallback=bool(scope.used_fallback),
            )
        except (HyeveIntegrationChatError, json.JSONDecodeError, TypeError, ValueError):
            # Planner model/provider flake or non-JSON: degrade to conversation clarify
            # instead of hard 502 so the chat UX stays usable.
            intelligence_metrics.extras["planner_fallback_to_conversation"] = (
                intelligence_metrics.extras.get("planner_fallback_to_conversation", 0) + 1
            )
            route = route_discuss_turn(intent, escalate_to_planner=False)
            model_ref = registry.model_ref_for("conversation")
            gpr = GovernedProviderRouter().resolve(role="conversation", model_ref=model_ref)
            if gpr.disposition != "ALLOW":
                raise HyeveIntegrationChatError(
                    "GOVERNED_ROUTE_DENIED",
                    detail=",".join(gpr.reason_codes),
                )

    # Conversation / clarification / continue / awaiting_confirm (no planner)
    system = CONVERSATION_SYSTEM_PROMPT
    if route.routing_phase == "awaiting_confirm":
        system += (
            " The user appears to describe an actionable objective. Ask them to confirm "
            "creating a governed WorkHub objective (or click Create & run with AIX). "
            "Do not invent task_class, file scope, or implementation strategy. "
            "Do not emit any HTML/CSS/JS source."
        )
    if route.routing_phase == "continue_objective":
        system += (
            " The user wants to continue an existing objective. Acknowledge and point them "
            "to the existing WorkHub/objective cycle; do not start a new plan."
        )
    if route.routing_phase == "clarification":
        system += (
            " If a proposal already exists and the user is affirming (yes / start / go ahead), "
            "tell them to click **Create & run with AIX** — do not generate source files here."
        )

    chat_messages = [ChatMessage(role="system", content=system)] + [
        ChatMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
        for m in messages
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
    result: ChatCompletionResultV1 = await chat_completion(
        messages=chat_messages,
        tenant_id=tenant_id,
        actor_id=actor_id,
        authorization_header=authorization_header,
        model=model_ref,
        timeout_seconds=float(settings.INTELLIGENCE_LAYER_CHAT_TIMEOUT_SECONDS),
    )
    reply = str(result.output_text or "").strip()
    # Allow models that still wrap JSON
    if reply.startswith("{") and '"reply"' in reply:
        try:
            parsed = json.loads(reply)
            if isinstance(parsed, dict) and "reply" in parsed:
                reply = str(parsed.get("reply") or "").strip()
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    reply = sanitize_discuss_reply(reply)
    if not reply:
        raise HyeveIntegrationChatError("MODEL_REPLY_EMPTY", detail="empty conversation reply")

    usage = dict(result.usage or {})
    intelligence_metrics.record_usage(usage, latency_ms=(time.perf_counter() - started) * 1000)
    return DiscussTurnResultV1(
        reply=reply,
        task_class=None,
        proposed_scope=[],
        ambiguous=route.routing_phase in {"awaiting_confirm", "clarification"},
        intent=route.intent,
        confidence=route.confidence,
        objective_summary=route.objective_summary,
        routing_phase=route.routing_phase,
        model_role=route.model_role,
        planner_invoked=False,
        requires_objective_confirm=route.routing_phase == "awaiting_confirm",
        provider=result.provider,
        resolved_provider=result.resolved_provider,
        model=result.model,
        usage=usage,
        route_evidence=dict(gpr.evidence),
        normalized_objective=route.objective_summary,
        repository_hint=None,
        file_hints=[],
        symbol_hints=[],
        constraints=[],
    )
