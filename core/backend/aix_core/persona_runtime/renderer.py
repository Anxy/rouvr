"""render(): pure function, RuntimeContextV1 (+ an explicit task brief) -> prompt text.

Design note / deliberate deviation from the target design as stated: the
original spec says the Renderer is `RuntimeContext -> prompt text`, full
stop. In practice the real production prompts being compared against
(PlannerPromptBuilder in aix_prompt_builder_service.py) always carry
task-instance specifics -- target_file, target_symbol, expected_change,
acceptance_check -- that are properties of *this one task*, not of the
persona/capability/policy/overlay composition. Folding them into
RuntimeContextV1 would conflate "who is allowed to do what" (governance,
resolved once per persona+policy+overlay combination) with "what this one
task is" (changes every call). Instead this module takes RuntimeContextV1
plus a separate, explicit TaskBriefV1 argument. render() is still a pure
function of its inputs, does no registry lookups, and has no knowledge of
policy resolution -- it only formats what it's given. This is flagged as an
open design question for T2/T6 to ratify or reject, not a silent workaround.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.persona_runtime.runtime_context import RuntimeContextV1


@dataclass(frozen=True)
class TaskBriefV1:
    target_file: str = ""
    target_symbol: str = ""
    expected_change: str = ""
    acceptance_check: str = ""

    def as_lines(self) -> tuple[str, ...]:
        return (
            f"target_file={self.target_file or 'unspecified'}",
            f"target_symbol={self.target_symbol or 'unspecified'}",
            f"expected_change={self.expected_change or 'unspecified'}",
            f"acceptance_check={self.acceptance_check or 'unspecified'}",
        )


@dataclass(frozen=True)
class RenderedPromptV1:
    """Deliberately structurally parallel to services.aix_prompt_builder_service.LayeredPrompt
    (system_layer / lane_layer / task_brief lines / structured_envelope) so the two can be
    diffed section-by-section. This module does not import that class -- the parallel shape
    is independently derived, not inherited, which is what "no side knowledge" means here.
    """

    system_layer: tuple[str, ...]
    lane_layer: tuple[str, ...]
    task_brief_lines: tuple[str, ...]
    structured_envelope: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        sections = [
            ("Layer A: Stable System", self.system_layer),
            ("Layer B: Lane Instructions", self.lane_layer),
            ("Layer C: Task Brief", self.task_brief_lines),
        ]
        rendered: list[str] = []
        for title, lines in sections:
            rendered.append(title)
            rendered.extend(_dedupe(lines))
            rendered.append("")
        if self.structured_envelope:
            rendered.append("Structured Envelope")
            rendered.append(
                json.dumps(self.structured_envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            )
        return "\n".join(rendered).strip()


def _dedupe(lines: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = line.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def render(context: RuntimeContextV1, task_brief: TaskBriefV1 | None = None) -> RenderedPromptV1:
    brief = task_brief or TaskBriefV1()

    system_layer = (
        f"persona_id={context.persona_id}",
        f"mission={context.mission}",
        f"communication_style={','.join(context.communication_style) or 'unspecified'}",
        f"reasoning_style={','.join(context.reasoning_style) or 'unspecified'}",
        f"autonomy_cap={context.autonomy_cap}",
        *(f"constraint={item}" for item in context.constraints),
        "Return only the requested structured output.",
        "Use workspace-relative paths.",
    )

    lane_layer_parts: list[str] = list(context.knowledge)
    lane_layer_parts.extend(context.tool_invocation_protocol)
    lane_layer_parts.extend(f"verify: {rule}" for rule in context.verification_rules)
    lane_layer_parts.append(f"granted_tools={','.join(sorted(context.granted_tools)) or 'none'}")
    if context.denied_requested_tools:
        lane_layer_parts.append(f"denied_tools={','.join(sorted(context.denied_requested_tools))}")
    lane_layer_parts.append(f"evidence_required={','.join(context.evidence_requirements) or 'none'}")

    task_brief_lines = (f"objective={context.objective or 'unspecified'}", *brief.as_lines())

    envelope: dict[str, Any] = {
        "policy_id": context.policy_id,
        "capability_ids": list(context.capability_ids),
        "schema_version": context.schema_version,
    }
    if context.overlay_id:
        envelope["overlay_id"] = context.overlay_id
    if context.missing_capability_ids:
        envelope["missing_capability_ids"] = list(context.missing_capability_ids)

    return RenderedPromptV1(
        system_layer=system_layer,
        lane_layer=tuple(lane_layer_parts),
        task_brief_lines=task_brief_lines,
        structured_envelope=envelope,
    )
