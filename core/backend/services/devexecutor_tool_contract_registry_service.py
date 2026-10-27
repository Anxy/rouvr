from __future__ import annotations

# AIX autonomous lane changes must run through scripts/run_aix_lane_gauntlet.sh before deploy.
import logging
import os
from dataclasses import dataclass

from backend.aix_core.persona_runtime.intent_engine import classify_intent
from backend.services.aix_prompt_builder_service import PlannerPromptBuilder

logger = logging.getLogger(__name__)

# Dark-launch comparison only. Default OFF. When on, computes the persona_runtime
# equivalent prompt for the executor_coder role and logs it alongside the real
# production output for comparison -- the production return value is never affected
# by this flag; see _dark_launch_persona_runtime_prompt.
_PERSONA_RUNTIME_DARK_LAUNCH_ENV = "AIX_PERSONA_RUNTIME_DARK_LAUNCH"


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: str
    canonical_args: tuple[str, ...]
    description: str
    mutating: bool = False
    legacy_compatibility_args: tuple[str, ...] = ()
    legacy_compatibility_note: str | None = None


_TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    ToolContract("list_dir", ("relative_path",), "List files/directories under a workspace-relative path."),
    ToolContract(
        "read_file",
        ("relative_path",),
        'Read an entire workspace-relative file. Use source="filesystem" after any mutation.',
    ),
    ToolContract(
        "read_range",
        ("relative_path", "start_line", "end_line"),
        "Read a line range from a workspace-relative file.",
    ),
    ToolContract("search", ("pattern", "relative_path"), "Search for text under a workspace-relative path."),
    ToolContract("get_status", (), "Inspect workspace status before or after edits."),
    ToolContract(
        "cmd.run",
        ("command_text", "timeout_seconds"),
        "Run the one declared validation command exactly as provided by the planner packet acceptance gate.",
        legacy_compatibility_args=("command", "timeout_seconds"),
        legacy_compatibility_note="Legacy compatibility accepted by the runner normalizer only: args.command, args.timeout_seconds.",
    ),
    ToolContract(
        "write_file",
        ("relative_path", "content"),
        "Write full file content to a workspace-relative file.",
        mutating=True,
    ),
    ToolContract(
        "replace_function",
        ("relative_path", "qualified_symbol", "replacement"),
        "Replace one grounded Python function or method via symbol-resolved LibCST rewrite using the complete updated function definition copied from live symbol grounding.",
        mutating=True,
        legacy_compatibility_args=("qualified_symbol", "target_symbol", "replacement_text"),
        legacy_compatibility_note="Planner-facing canonical shape uses file + qualified_symbol + replacement; runtime adapter normalizes qualified_symbol to function_name for the LibCST replace_in_function executor.",
    ),
    ToolContract(
        "edit_file",
        (
            "relative_path",
            "grounding_source",
            "search_range",
            "read_step_idx",
            "edit_range_within_read",
            "replacement_text",
        ),
        "Primary mutation tool. Form A uses grounding_source=anchor plus explicit search_range inside the anchor window. Form B uses grounding_source=read_range plus read_step_idx=0 and edit_range_within_read. Both forms require replacement_text.",
        mutating=True,
        legacy_compatibility_args=(),
        legacy_compatibility_note=None,
    ),
    ToolContract(
        "extend_python_signature",
        ("relative_path", "symbol", "parameter_name", "parameter_type", "default_value", "parameter_kind"),
        "Additive Python signature mutation (preserves all other shape).",
        mutating=True,
    ),
    ToolContract(
        "insert_at_anchor",
        ("relative_path", "anchor_text", "new_content", "position", "indent_style"),
        "Insert text at exact anchor (before/after).",
        mutating=True,
    ),
    ToolContract(
        "apply_patch",
        ("patch_text",),
        "Apply unified diff patch. No path arguments.",
        mutating=True,
    ),
    # ... (other non-mutating tools unchanged)
)

CODE_CHANGE_ALLOWED_MUTATION_TOOLS: tuple[str, ...] = (
    "replace_function",
    "edit_file",
    "extend_python_signature",
    "insert_at_anchor",
    "apply_patch",
    "write_file",
)


def get_tool_contracts() -> tuple[ToolContract, ...]:
    return _TOOL_CONTRACTS


def get_allowed_tool_names() -> set[str]:
    return {tool.name for tool in _TOOL_CONTRACTS}


def get_code_change_allowed_mutation_tools() -> tuple[str, ...]:
    return CODE_CHANGE_ALLOWED_MUTATION_TOOLS


def _is_codeintel_325_form_a_only_lane(packet: dict[str, object] | None = None) -> bool:
    packet_dict = packet if isinstance(packet, dict) else {}
    target_file = str(packet_dict.get("target_file") or "").strip().replace("\\", "/")
    task_number = packet_dict.get("task_number")
    validation_command = str(packet_dict.get("validation_command") or "").strip()
    return (
        target_file == "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py"
        and str(task_number or "").strip() == "325"
        and "skips_duplicate_test_prefix" in validation_command
    )


def _persona_runtime_dark_launch_enabled() -> bool:
    return os.getenv(_PERSONA_RUNTIME_DARK_LAUNCH_ENV, "").strip().lower() in {"1", "true", "on"}


def _new_executor_coder_layer_a_lines() -> tuple[str, ...] | None:
    """Always-on (no flag): builds the persona_runtime governance-derived Layer A for
    executor_coder from real manifest data. `grep -rn "role=code mutation planner"
    backend/ --include="*.py"` found matches only in aix_prompt_builder_service.py
    itself (where the string is constructed) -- no other production code or test
    depends on that literal line, so it is safe to replace by default. Returns None on
    any failure so the caller falls back to the untouched old text -- this must never
    break production output."""
    try:
        from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId
        from backend.aix_core.persona_runtime.composer import compose
        from backend.aix_core.persona_runtime.manifest_adapter import (
            capabilities_from_manifest,
            load_real_manifests,
            persona_from_manifest,
            policy_from_manifest,
        )
        from backend.aix_core.persona_runtime.renderer import render

        role = AgentRoleId.EXECUTOR_CODER
        manifest = load_real_manifests()[role]
        persona = persona_from_manifest(manifest, role)
        capabilities = capabilities_from_manifest(manifest, role)
        policy = policy_from_manifest(manifest)
        context = compose(
            persona=persona,
            capabilities=capabilities,
            policy=policy,
            overlay=None,
            # Layer A does not depend on the objective (see renderer.render: system_layer
            # is built only from persona/policy fields) -- a placeholder is safe here.
            objective="layer_a_default_switch",
        )
        return render(context).system_layer
    except Exception:  # noqa: BLE001 -- must never break the real prompt path
        logger.exception("persona_runtime_layer_a_default_failed")
        return None


def _splice_layer_a(old_text: str, new_layer_a_lines: tuple[str, ...]) -> str:
    """Text-level splice: replaces only the 'Layer A: Stable System' block of an
    already-rendered LayeredPrompt string, leaving Layer B / Task Brief / Structured
    Envelope byte-identical to what PlannerPromptBuilder produced."""
    lines = old_text.splitlines()
    try:
        title_idx = lines.index("Layer A: Stable System")
    except ValueError:
        return old_text  # unexpected shape -- fail safe, return untouched
    end_idx = title_idx + 1
    while end_idx < len(lines) and lines[end_idx].strip():
        end_idx += 1
    spliced = lines[: title_idx + 1] + list(new_layer_a_lines) + lines[end_idx:]
    return "\n".join(spliced)


def _dark_launch_persona_runtime_prompt(packet_dict: dict[str, object], *, lane: str) -> str | None:
    """Best-effort only. Computes the persona_runtime-composed equivalent of the real
    prompt, for logged comparison. Any failure here is caught and logged -- it must
    never affect build_planner_contract_text's return value or raise past this call.
    Imports are local so importing this module (and therefore every caller of
    build_planner_contract_text) never pays the persona_runtime import cost when the
    flag is off."""
    try:
        from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId
        from backend.aix_core.persona_runtime.composer import compose
        from backend.aix_core.persona_runtime.manifest_adapter import (
            capabilities_from_manifest,
            load_real_manifests,
            persona_from_manifest,
            policy_from_manifest,
        )
        from backend.aix_core.persona_runtime.renderer import TaskBriefV1, render
        from backend.services.aix_prompt_builder_service import (
            _acceptance_check_from_packet,
            _expected_change_from_packet,
            _target_file_from_packet,
            _target_symbol_from_packet,
        )

        role = AgentRoleId.EXECUTOR_CODER
        manifest = load_real_manifests()[role]
        persona = persona_from_manifest(manifest, role)
        capabilities = capabilities_from_manifest(manifest, role)
        policy = policy_from_manifest(manifest)
        objective = str(packet_dict.get("objective") or packet_dict.get("title") or "unspecified")
        context = compose(
            persona=persona, capabilities=capabilities, policy=policy, overlay=None, objective=objective
        )
        prompt = render(
            context,
            task_brief=TaskBriefV1(
                target_file=_target_file_from_packet(packet_dict),
                target_symbol=_target_symbol_from_packet(packet_dict),
                expected_change=_expected_change_from_packet(packet_dict),
                acceptance_check=_acceptance_check_from_packet(packet_dict),
            ),
        )
        return prompt.render()
    except Exception:  # noqa: BLE001 -- dark launch must never break the real prompt path
        logger.exception("persona_runtime_dark_launch_failed lane=%s", lane)
        return None


def build_planner_contract_text(packet: dict[str, object] | None = None) -> str:
    """Builds the canonical system prompt for lower-tier planner models.

    Higher-level, minimal, principle-driven contract. No legacy baggage.
    """
    packet_dict: dict[str, object] = packet if isinstance(packet, dict) else {}
    # Lane decision now goes through the IntentEngine -- same seven real conditions,
    # same order, extracted verbatim rather than reinvented. See
    # aix_core/persona_runtime/intent_engine.py and its parity tests.
    intent = classify_intent(packet_dict)
    lane = intent.lane

    text = PlannerPromptBuilder.system_prompt(packet=packet_dict, lane=lane)

    new_layer_a_lines = _new_executor_coder_layer_a_lines()
    if new_layer_a_lines is not None:
        text = _splice_layer_a(text, new_layer_a_lines)

    if _persona_runtime_dark_launch_enabled():
        new_text = _dark_launch_persona_runtime_prompt(packet_dict, lane=lane)
        if new_text is not None:
            logger.info(
                "persona_runtime_dark_launch_comparison lane=%s match=%s old_len=%d new_len=%d",
                lane,
                text == new_text,
                len(text),
                len(new_text),
            )
            logger.debug("persona_runtime_dark_launch_old_prompt lane=%s prompt=%s", lane, text)
            logger.debug("persona_runtime_dark_launch_new_prompt lane=%s prompt=%s", lane, new_text)

    return text


__all__ = [
    "CODE_CHANGE_ALLOWED_MUTATION_TOOLS",
    "ToolContract",
    "build_planner_contract_text",
    "get_allowed_tool_names",
    "get_code_change_allowed_mutation_tools",
    "get_tool_contracts",
]
