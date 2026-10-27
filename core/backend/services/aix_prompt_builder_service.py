from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskBriefLayer:
    objective: str = ""
    target_file: str = ""
    target_symbol: str = ""
    expected_change: str = ""
    acceptance_check: str = ""

    def as_lines(self) -> list[str]:
        return [
            f"objective={self.objective or 'unspecified'}",
            f"target_file={self.target_file or 'unspecified'}",
            f"target_symbol={self.target_symbol or 'unspecified'}",
            f"expected_change={self.expected_change or 'unspecified'}",
            f"acceptance_check={self.acceptance_check or 'unspecified'}",
        ]


@dataclass(frozen=True)
class RepairBriefLayer:
    original_objective: str = ""
    original_target: str = ""
    failure_code: str = ""
    observed_reason: str = ""
    exact_required_correction: str = ""
    unchanged_acceptance_check: str = ""

    def as_lines(self) -> list[str]:
        return [
            f"original_objective={self.original_objective or 'unspecified'}",
            f"original_target={self.original_target or 'unspecified'}",
            f"failure_code={self.failure_code or 'unspecified'}",
            f"observed_reason={self.observed_reason or 'unspecified'}",
            f"exact_required_correction={self.exact_required_correction or 'unspecified'}",
            f"unchanged_acceptance_check={self.unchanged_acceptance_check or 'unspecified'}",
        ]


@dataclass(frozen=True)
class LayeredPrompt:
    system_layer: tuple[str, ...]
    lane_layer: tuple[str, ...]
    task_brief_layer: TaskBriefLayer | RepairBriefLayer
    structured_envelope: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        sections = [
            ("Layer A: Stable System", self.system_layer),
            ("Layer B: Lane Instructions", self.lane_layer),
            ("Layer C: Task Brief", tuple(self.task_brief_layer.as_lines())),
        ]
        rendered: list[str] = []
        for title, lines in sections:
            rendered.append(title)
            rendered.extend(_dedupe_lines(lines))
            rendered.append("")
        if self.structured_envelope:
            rendered.append("Structured Envelope")
            rendered.append(
                json.dumps(self.structured_envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            )
        return "\n".join(rendered).strip()

    def section_map(self) -> dict[str, Any]:
        return {
            "system_layer": list(self.system_layer),
            "lane_layer": list(self.lane_layer),
            "task_brief_layer": _brief_to_map(self.task_brief_layer),
            "structured_envelope": dict(self.structured_envelope),
        }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _string_list(values: Iterable[Any] | None) -> list[str]:
    return list(dict.fromkeys(_clean(item) for item in list(values or []) if _clean(item)))


def _dedupe_lines(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = str(line or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _brief_to_map(brief: TaskBriefLayer | RepairBriefLayer) -> dict[str, str]:
    if isinstance(brief, RepairBriefLayer):
        return {
            "original_objective": brief.original_objective,
            "original_target": brief.original_target,
            "failure_code": brief.failure_code,
            "observed_reason": brief.observed_reason,
            "exact_required_correction": brief.exact_required_correction,
            "unchanged_acceptance_check": brief.unchanged_acceptance_check,
        }
    return {
        "objective": brief.objective,
        "target_file": brief.target_file,
        "target_symbol": brief.target_symbol,
        "expected_change": brief.expected_change,
        "acceptance_check": brief.acceptance_check,
    }


def _layer_c_value(prompt: str, key: str) -> str:
    prefix = f"{key}="
    for line in str(prompt or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            return "" if value == "unspecified" else value
    return ""


def _packet_anchor(packet: dict[str, Any]) -> dict[str, Any]:
    raw_anchor = packet.get("codeintel_anchor")
    return dict(raw_anchor) if isinstance(raw_anchor, dict) else {}


def _target_symbol_from_packet(packet: dict[str, Any]) -> str:
    anchor = _packet_anchor(packet)
    for value in (
        packet.get("qualified_symbol"),
        packet.get("target_symbol"),
        anchor.get("symbol"),
        anchor.get("qualified_symbol"),
    ):
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _target_file_from_packet(packet: dict[str, Any]) -> str:
    anchor = _packet_anchor(packet)
    for value in (packet.get("target_file"), anchor.get("file_path"), anchor.get("relative_path")):
        cleaned = _clean(value).replace("\\", "/")
        if cleaned:
            return cleaned
    return ""


def _expected_change_from_packet(packet: dict[str, Any]) -> str:
    anchor = _packet_anchor(packet)
    for value in (anchor.get("expected_change"), packet.get("mutation_instructions"), packet.get("edit_intent")):
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _acceptance_check_from_packet(packet: dict[str, Any]) -> str:
    commands = _string_list(packet.get("validation_commands"))
    if commands:
        return commands[0]
    return _clean(packet.get("validation_command"))


class CurrentPromptLayerComposer:
    @staticmethod
    def build(
        *,
        role: str,
        lane: str,
        task_brief: TaskBriefLayer | RepairBriefLayer,
        lane_lines: Iterable[str],
        envelope: dict[str, Any] | None = None,
    ) -> LayeredPrompt:
        role_name = _clean(role) or "planner"
        system_layer = (
            f"role={role_name}",
            "Return only the requested structured output.",
            "Use workspace-relative paths.",
        )
        return LayeredPrompt(
            system_layer=system_layer,
            lane_layer=tuple(_dedupe_lines(lane_lines)),
            task_brief_layer=task_brief,
            structured_envelope={"lane": _clean(lane) or "generic", **dict(envelope or {})},
        )


class PlannerPromptBuilder:
    @staticmethod
    def system_prompt(*, packet: dict[str, Any] | None = None, lane: str = "generic") -> str:
        packet_dict = dict(packet or {}) if isinstance(packet, dict) else {}
        normalized_lane = _clean(lane) or "generic"
        lane_lines: tuple[str, ...]
        if normalized_lane == "strict_replace_function":
            lane_lines = (
                'Output schema: {"steps":[...]}',
                "the only allowed mutation primitive is replace_function",
                "Use a grounded structural patch: first executable step MUST be a same-file read_range for the declared target symbol.",
                "Plan shape: read_range using file+symbol, then replace_function using the same qualified symbol, then run_validation_command.",
                "Example replace_function plan: read_range(file,symbol) -> replace_function(file,qualified_symbol,edit_intent,preserve_constraints,line_targets) -> run_validation_command(command).",
                "Do not emit edit_file, apply_patch, write_file, replacement bodies, prose, fences, or patch text.",
                "Forbidden: any search string that is not an exact substring of the live target file.",
                "If the symbol cannot be grounded, return failure_analysis with reason_code=INSUFFICIENT_CONTEXT_FOR_MUTATION.",
            )
        else:
            lane_lines = (
                'Output schema: {"steps":[...]}',
                "Allowed mutation primitives: edit_file, apply_patch, write_file.",
                "Use a grounded structural patch for every edit.",
                "When the anchor window must be read, the first executable step MUST be read_range.",
                "Ground every mutation from compact anchor text or a same-file read in this attempt.",
                "If the compact anchor snippet is sufficient, emit a directly grounded same-file edit_file using exact anchor text only.",
                "Path A: if the compact anchor already contains the exact mutation hotspot, emit a direct same-file edit_file whose SEARCH is copied verbatim from that compact anchor.",
                "Path B: if the compact anchor is insufficient, emit read_range on the declared anchor window first, then emit Form B using read_step_idx=0 plus edit_range_within_read on that same-file read output.",
                "Do not emit output_content for any anchor-window read_range.",
                "Forbidden: any search string that is not an exact substring of the live target file.",
                "Do not emit legacy search_replace_blocks.",
                "Any edit_file containing read_source, step_idx, buffer_ref, or output_content is invalid.",
                "Any read_range containing step_idx or output_content is invalid.",
                "Anchor read_range must contain only type, file, start_line, and end_line.",
                '{"type":"read_range","file":str,"start_line":int,"end_line":int}',
                "step 2 edit_file must remain self-contained.",
                "Do not rewrite the entire anchored window; return failure_analysis instead of attempting a broad rewrite.",
                "Use at most 3 steps total; do not scaffold with additional discovery steps before edit_file.",
                "Anchor-direct example: edit_file grounded from compact anchor text, then run_validation_command.",
                "The final step of every real mutation plan must be run_validation_command.",
                "final step of every real mutation plan must be run_validation_command",
                "TERMINAL OUTCOME RULE (MANDATORY): the final non-negotiable acceptance gate is planner_packet.validation_command.",
                "The final non-negotiable acceptance gate must show exit code 0 with PASSED output.",
                "End a real mutation plan with the declared run_validation_command.",
                "If the change cannot be grounded, return failure_analysis with reason_code=INSUFFICIENT_CONTEXT_FOR_MUTATION.",
            )
        prompt = CurrentPromptLayerComposer.build(
            role="code mutation planner",
            lane=normalized_lane,
            lane_lines=lane_lines,
            task_brief=TaskBriefLayer(
                objective=_clean(packet_dict.get("objective") or packet_dict.get("title")),
                target_file=_target_file_from_packet(packet_dict),
                target_symbol=_target_symbol_from_packet(packet_dict),
                expected_change=_expected_change_from_packet(packet_dict),
                acceptance_check=_acceptance_check_from_packet(packet_dict),
            ),
            envelope={"planner_contract_version": "layered_current"},
        )
        return prompt.render()

    @staticmethod
    def task_prompt(
        *,
        task_number: int,
        title: str,
        description: str,
        task_intent: str,
        target_files: Iterable[str] | None = None,
        target_symbols: Iterable[str] | None = None,
        expected_change: str = "",
        validation_commands: Iterable[str] | None = None,
        planner_slice_packet: dict[str, Any] | None = None,
        lane: str = "generic",
    ) -> str:
        packet = dict(planner_slice_packet or {}) if isinstance(planner_slice_packet, dict) else {}
        target_file = _target_file_from_packet(packet) or (_string_list(target_files) or [""])[0]
        target_symbol = _target_symbol_from_packet(packet) or (_string_list(target_symbols) or [""])[0]
        acceptance_check = _acceptance_check_from_packet(packet) or (_string_list(validation_commands) or [""])[0]
        expected = _expected_change_from_packet(packet) or _clean(expected_change or description)
        lane_lines: tuple[str, ...]
        lane_lines = (
            "Use the structured envelope as the authoritative execution packet.",
            "Keep planning output separate from executor/runtime governance.",
            "Do not restate schema, validation, or tool-policy rules enforced outside the prompt.",
        )
        if _clean(lane) == "strict_replace_function":
            lane_lines = (
                *lane_lines,
                "Strict lane: read the grounded symbol and emit one bounded replace_function spec for that same symbol.",
                "Strict lane: no edit_file fallback and no silent downgrade.",
            )
        else:
            lane_lines = (
                *lane_lines,
                "Generic lane: choose the smallest grounded file mutation primitive that satisfies the task brief.",
            )
        envelope: dict[str, Any] = {
            "schema_name": "aix.planner_prompt.current",
            "task_number": int(task_number),
            "task_intent": _clean(task_intent) or "unspecified",
            "target_files": _string_list(target_files),
            "target_symbols": _string_list(target_symbols),
            "validation_commands": _string_list(validation_commands),
        }
        if packet:
            envelope["planner_slice_packet"] = packet
        prompt = CurrentPromptLayerComposer.build(
            role="code mutation planner",
            lane=_clean(lane) or "generic",
            lane_lines=lane_lines,
            task_brief=TaskBriefLayer(
                objective=_clean(title or description),
                target_file=target_file,
                target_symbol=target_symbol,
                expected_change=expected,
                acceptance_check=acceptance_check,
            ),
            envelope=envelope,
        )
        return prompt.render()


class RewritePromptBuilder:
    @staticmethod
    def system_prompt() -> str:
        prompt = CurrentPromptLayerComposer.build(
            role="single function rewrite executor",
            lane="rewrite",
            lane_lines=(
                "Output JSON fields: qualified_symbol, replacement, notes.",
                "replacement contains exactly one complete observed Python function definition.",
                "Do not emit tool steps, execution plans, validation commands, prose, or fences.",
            ),
            task_brief=TaskBriefLayer(
                objective="rewrite one grounded Python function",
                target_file="provided in rewrite_spec",
                target_symbol="provided in rewrite_spec",
                expected_change="provided in rewrite_spec.edit_intent",
                acceptance_check="response schema validation",
            ),
            envelope={"schema_name": "aix.layered_rewrite_system_prompt.current"},
        )
        return prompt.render()

    @staticmethod
    def user_prompt(
        *,
        target_file: str,
        qualified_symbol: str,
        edit_intent: str,
        preserve_constraints: Iterable[str],
        line_targets: Iterable[str],
        observed_shape: dict[str, Any],
        observed_source: str,
        validation_error: str | None = None,
    ) -> str:
        envelope = {
            "schema_name": "aix.layered_rewrite_user_prompt.current",
            "rewrite_spec": {
                "target_file": _clean(target_file),
                "qualified_symbol": _clean(qualified_symbol),
                "edit_intent": _clean(edit_intent),
                "preserve_constraints": _string_list(preserve_constraints),
                "line_targets": _string_list(line_targets),
                "observed_shape": dict(observed_shape or {}),
                "validation_error": _clean(validation_error) or None,
            },
            "observed_symbol_source": str(observed_source or ""),
        }
        prompt = CurrentPromptLayerComposer.build(
            role="single function rewrite executor",
            lane="rewrite",
            lane_lines=(
                "Derive the replacement directly from observed_symbol_source.",
                "Preserve async/sync kind, exact function name, receiver, decorators, and unchanged lines unless intentionally edited.",
                "Reject placeholders, elisions, helper functions, and partial bodies.",
            ),
            task_brief=TaskBriefLayer(
                objective="materialize final replacement function",
                target_file=target_file,
                target_symbol=qualified_symbol,
                expected_change=edit_intent,
                acceptance_check="strict rewrite response schema",
            ),
            envelope=envelope,
        )
        return prompt.render()


def build_cli_executor_prompt_current(
    *,
    task_number: int,
    title: str,
    description: str,
    task_intent: str,
    target_files: Iterable[str] | None = None,
    target_symbols: Iterable[str] | None = None,
    validation_commands: Iterable[str] | None = None,
    planner_constraints: dict[str, Any] | None = None,
) -> str:
    constraints_payload = dict(planner_constraints or {})
    normalized_target_files = _string_list(target_files)
    normalized_target_symbols = _string_list(target_symbols)
    normalized_validation_commands = _string_list(validation_commands)
    success_definition = _clean(constraints_payload.get("success_definition") or description or title)
    prompt = CurrentPromptLayerComposer.build(
        role="code mutation executor",
        lane="cli_executor",
        lane_lines=(
            "Mutate files directly in the provided workspace.",
            "Stay inside target_files when target_files are provided.",
            "Do not mark WorkHub tasks done; report execution outcome only.",
            "Finish with exactly one status line: COMPLETE, NEEDS_INPUT: <question>, or FAILED: <reason>.",
        ),
        task_brief=TaskBriefLayer(
            objective=_clean(title or description),
            target_file=normalized_target_files[0] if normalized_target_files else "",
            target_symbol=normalized_target_symbols[0] if normalized_target_symbols else "",
            expected_change=success_definition,
            acceptance_check=normalized_validation_commands[0] if normalized_validation_commands else "",
        ),
        envelope={
            "schema_name": "aix.cli_execution_prompt.current",
            "task_number": int(task_number),
            "task_intent": _clean(task_intent) or "unspecified",
            "planner_constraints": constraints_payload,
        },
    )
    return prompt.render()


class RepairPromptBuilder:
    @staticmethod
    def repair_prompt(
        *,
        base_prompt: str,
        repair_category: str = "planner_validation_failure",
        rejection_codes: Iterable[str],
        violated_invariants: Iterable[str],
        observations: Iterable[str],
        retry_rules: Iterable[str],
        positive_partial: str | None = None,
    ) -> str:
        codes = _string_list(rejection_codes)
        envelope = {
            "schema_name": "aix.layered_repair_prompt.current",
            "repair_category": _clean(repair_category) or "planner_validation_failure",
            "base_prompt_ref": "parsed_layer_c_brief",
            "rejection_codes": codes,
            "violated_invariants": _string_list(violated_invariants)[:4],
            "observations": _string_list(observations)[:6],
            "retry_rules": _dedupe_lines(_string_list(retry_rules))[:8],
            "positive_partial": _clean(positive_partial) or None,
        }
        objective = _layer_c_value(base_prompt, "objective") or _layer_c_value(base_prompt, "original_objective")
        target_file = _layer_c_value(base_prompt, "target_file")
        target_symbol = _layer_c_value(base_prompt, "target_symbol")
        original_target = " ".join(item for item in (target_file, target_symbol) if item).strip()
        observed_reason = (_string_list(observations) or codes or ["retry rejected"])[0]
        exact_correction = (_dedupe_lines(_string_list(retry_rules)) or ["return a corrected planner response"])[0]
        acceptance_check = _layer_c_value(base_prompt, "acceptance_check") or _layer_c_value(
            base_prompt, "unchanged_acceptance_check"
        )
        prompt = CurrentPromptLayerComposer.build(
            role="planner repair",
            lane="repair",
            lane_lines=(
                "Repair only the rejected planner output using the structured retry brief.",
                "Preserve the original objective, target, and acceptance check.",
                "Do not broaden target surface or redefine the task.",
                "Return strict JSON only for the corrected planner response.",
            ),
            task_brief=RepairBriefLayer(
                original_objective=objective,
                original_target=original_target,
                failure_code=", ".join(codes),
                observed_reason=observed_reason,
                exact_required_correction=exact_correction,
                unchanged_acceptance_check=acceptance_check,
            ),
            envelope=envelope,
        )
        return prompt.render()

    @staticmethod
    def readiness_failure_outcome(readiness: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(readiness or {})
        reasons = [
            dict(item)
            for item in payload.get("blocking_reasons") or []
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        ]
        return {
            "status": "lane_not_ready",
            "repair_category": "readiness_failure",
            "model_repair_prompt": None,
            "human_or_system_correction_required": True,
            "blocking_reasons": reasons,
            "degraded_reasons": [
                dict(item) for item in payload.get("degraded_reasons") or [] if isinstance(item, dict)
            ],
        }


__all__ = [
    "LayeredPrompt",
    "PlannerPromptBuilder",
    "CurrentPromptLayerComposer",
    "RepairBriefLayer",
    "RepairPromptBuilder",
    "RewritePromptBuilder",
    "TaskBriefLayer",
    "build_cli_executor_prompt_current",
]
