"""Real structural + semantic parity check: new Renderer output vs. the actual
production PlannerPromptBuilder (backend/services/aix_prompt_builder_service.py),
for the executor_coder role, using REAL manifest data (backend/config/persona_manifests/
PM-OBJECTIVE-CYCLE-EXECUTOR-CODER-V1.json) and REAL tool-invocation-protocol content
extracted verbatim, at runtime, from PlannerPromptBuilder itself -- not paraphrased,
not hand-copied into a second static string.
"""
from __future__ import annotations

from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId
from backend.aix_core.persona_runtime.composer import compose
from backend.aix_core.persona_runtime.manifest_adapter import (
    _real_executor_coder_tool_invocation_protocol,
    capabilities_from_manifest,
    load_real_manifests,
    persona_from_manifest,
    policy_from_manifest,
)
from backend.aix_core.persona_runtime.renderer import TaskBriefV1, render
from backend.services.aix_prompt_builder_service import PlannerPromptBuilder

_OBJECTIVE = "Add a null check before dereferencing user.profile in the settings handler."
_TARGET_FILE = "backend/services/settings_handler_service.py"
_TARGET_SYMBOL = "settings_handler_service.render_settings"
_EXPECTED_CHANGE = "Guard user.profile access with an explicit None check before attribute access."
_VALIDATION_COMMAND = "python -m pytest backend/tests/services/test_settings_handler_service.py"


def _new_path_output() -> str:
    manifests = load_real_manifests()
    manifest = manifests[AgentRoleId.EXECUTOR_CODER]
    persona = persona_from_manifest(manifest, AgentRoleId.EXECUTOR_CODER)
    capabilities = capabilities_from_manifest(manifest, AgentRoleId.EXECUTOR_CODER)
    policy = policy_from_manifest(manifest)
    context = compose(persona=persona, capabilities=capabilities, policy=policy, overlay=None, objective=_OBJECTIVE)
    prompt = render(
        context,
        task_brief=TaskBriefV1(
            target_file=_TARGET_FILE,
            target_symbol=_TARGET_SYMBOL,
            expected_change=_EXPECTED_CHANGE,
            acceptance_check=_VALIDATION_COMMAND,
        ),
    )
    return prompt.render()


def _old_path_output() -> str:
    packet = {
        "objective": _OBJECTIVE,
        "target_file": _TARGET_FILE,
        "target_symbol": _TARGET_SYMBOL,
        "mutation_instructions": _EXPECTED_CHANGE,
        "validation_commands": [_VALIDATION_COMMAND],
    }
    return PlannerPromptBuilder.system_prompt(packet=packet, lane="generic")


def test_both_paths_produce_non_empty_output() -> None:
    assert _new_path_output().strip()
    assert _old_path_output().strip()


def test_both_paths_share_the_same_three_layer_plus_envelope_structure() -> None:
    new_output = _new_path_output()
    old_output = _old_path_output()
    for marker in ("Layer A: Stable System", "Layer B: Lane Instructions", "Layer C: Task Brief"):
        assert marker in new_output
        assert marker in old_output
    assert "Structured Envelope" in new_output
    assert "Structured Envelope" in old_output


def test_new_path_task_brief_carries_the_same_task_facts_as_old_path() -> None:
    new_output = _new_path_output()
    old_output = _old_path_output()
    for fact in (_TARGET_FILE, _TARGET_SYMBOL, _VALIDATION_COMMAND):
        assert fact in new_output
        assert fact in old_output


def test_new_path_layer_b_contains_every_real_protocol_line_verbatim() -> None:
    """This is the semantic-match check: every single line of the OLD builder's real
    Layer B protocol content must appear byte-for-byte in the NEW renderer's Layer B --
    because the new content was extracted from the same live function, not paraphrased."""
    real_protocol_lines = _real_executor_coder_tool_invocation_protocol()
    assert real_protocol_lines, "extraction must actually find real lines, or this test is vacuous"
    new_output = _new_path_output()
    for line in real_protocol_lines:
        assert line in new_output, f"missing real protocol line in new renderer output: {line!r}"


def test_new_path_layer_b_is_a_superset_not_a_reinterpretation() -> None:
    """The new Layer B = old Layer B's exact lines + new governance lines (granted_tools,
    evidence_required) that the old builder never surfaced. Confirms this is additive
    transparency, not a lossy rewrite."""
    old_layer_b_lines = {
        line
        for line in _old_path_output().splitlines()
        if line and line not in ("Layer A: Stable System", "Layer B: Lane Instructions", "Layer C: Task Brief")
    }
    # crude section slice: everything between the two known markers in the old output
    old_text = _old_path_output()
    b_start = old_text.index("Layer B: Lane Instructions") + len("Layer B: Lane Instructions")
    b_end = old_text.index("Layer C: Task Brief")
    old_b_block = {line.strip() for line in old_text[b_start:b_end].splitlines() if line.strip()}

    new_text = _new_path_output()
    n_start = new_text.index("Layer B: Lane Instructions") + len("Layer B: Lane Instructions")
    n_end = new_text.index("Layer C: Task Brief")
    new_b_block = {line.strip() for line in new_text[n_start:n_end].splitlines() if line.strip()}

    missing_from_new = old_b_block - new_b_block
    assert not missing_from_new, f"old Layer B lines dropped by new renderer: {missing_from_new}"

    added_by_new = new_b_block - old_b_block
    # Every added line must be one of the new governance-transparency lines, not silent content loss.
    for line in added_by_new:
        assert line.startswith(("granted_tools=", "denied_tools=", "evidence_required=")), (
            f"unexpected new Layer B line with no known governance prefix: {line!r}"
        )
