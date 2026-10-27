from __future__ import annotations

import pytest

from backend.aix_core.persona_runtime.composer import ComposerError, compose
from backend.aix_core.persona_runtime.entities import CapabilityV1, OverlayV1, PersonaV1, PolicyV1


def _persona(**overrides: object) -> PersonaV1:
    base = dict(
        persona_id="persona.coder.v1",
        persona_name="Coder",
        mission="Implement bounded code changes safely.",
        communication_style=("terse", "cite file:line"),
        reasoning_style=("stepwise",),
        requested_capability_ids=("cap.python", "cap.postgres"),
    )
    base.update(overrides)
    return PersonaV1(**base)  # type: ignore[arg-type]


def _capabilities() -> tuple[CapabilityV1, ...]:
    python_cap = CapabilityV1(
        capability_id="cap.python",
        capability_name="Python",
        knowledge=("Prefer explicit types.", "Avoid mutable default arguments."),
        verification_rules=("run pytest before closeout",),
        requested_tools=("read_file", "apply_patch", "run_command"),
    )
    postgres_cap = CapabilityV1(
        capability_id="cap.postgres",
        capability_name="PostgreSQL",
        knowledge=("Never run migrations without a reviewed rollback.",),
        verification_rules=("EXPLAIN new queries over indexed tables",),
        requested_tools=("run_command", "read_file", "provider.direct_db_write"),
    )
    return (python_cap, postgres_cap)


def _policy(**overrides: object) -> PolicyV1:
    base = dict(
        policy_id="policy.executor_coder.v1",
        autonomy_cap="L2",
        granted_tools=("read_file", "apply_patch", "run_command", "list_dir"),
        evidence_requirements=("acceptance_mapping",),
    )
    base.update(overrides)
    return PolicyV1(**base)  # type: ignore[arg-type]


def test_granted_tools_is_requested_intersect_granted_not_granted_alone() -> None:
    context = compose(
        persona=_persona(),
        capabilities=_capabilities(),
        policy=_policy(),
        overlay=None,
        objective="Fix a bug",
    )
    # requested = {read_file, apply_patch, run_command, provider.direct_db_write}
    # granted   = {read_file, apply_patch, run_command, list_dir}
    # provider.direct_db_write was requested but never granted by policy -> must be denied.
    assert set(context.granted_tools) == {"read_file", "apply_patch", "run_command"}
    assert context.denied_requested_tools == ("provider.direct_db_write",)
    # list_dir was granted by policy but never requested by any capability -> must not leak in.
    assert "list_dir" not in context.granted_tools


def test_capability_not_requested_by_persona_is_excluded_even_if_passed_in() -> None:
    persona = _persona(requested_capability_ids=("cap.python",))  # cap.postgres deliberately omitted
    context = compose(
        persona=persona,
        capabilities=_capabilities(),
        policy=_policy(),
        overlay=None,
        objective="Fix a bug",
    )
    assert context.capability_ids == ("cap.python",)
    assert "provider.direct_db_write" not in context.requested_tools
    assert not any("postgres" in line.lower() for line in context.knowledge)


def test_missing_capability_is_reported_not_silently_dropped() -> None:
    persona = _persona(requested_capability_ids=("cap.python", "cap.does_not_exist"))
    context = compose(
        persona=persona,
        capabilities=_capabilities(),
        policy=_policy(),
        overlay=None,
        objective="Fix a bug",
    )
    assert context.missing_capability_ids == ("cap.does_not_exist",)


def test_objective_required() -> None:
    with pytest.raises(ComposerError):
        compose(persona=_persona(), capabilities=_capabilities(), policy=_policy(), overlay=None, objective="   ")


# --- non-escalation guarantee: the load-bearing property this whole prototype exists to prove ---


@pytest.mark.parametrize(
    "overlay_autonomy_cap,base_autonomy_cap,should_raise",
    [
        ("L1", "L2", False),  # narrower -- allowed
        ("L2", "L2", False),  # equal -- allowed
        ("L3", "L2", True),  # wider -- must be rejected
        ("L4", "L0", True),  # maximally wider -- must be rejected
    ],
)
def test_overlay_cannot_widen_autonomy_cap(
    overlay_autonomy_cap: str, base_autonomy_cap: str, should_raise: bool
) -> None:
    overlay = OverlayV1(
        overlay_id="overlay.tenant-1",
        tenant_id="tenant-1",
        autonomy_cap_override=overlay_autonomy_cap,  # type: ignore[arg-type]
    )
    policy = _policy(autonomy_cap=base_autonomy_cap)
    call = lambda: compose(
        persona=_persona(), capabilities=_capabilities(), policy=policy, overlay=overlay, objective="Fix a bug"
    )
    if should_raise:
        with pytest.raises(ComposerError, match="PERSONA_RUNTIME_OVERLAY_AUTONOMY_ESCALATION"):
            call()
    else:
        context = call()
        assert context.autonomy_cap == overlay_autonomy_cap


@pytest.mark.parametrize(
    "overlay_tools,should_raise",
    [
        (("read_file",), False),  # subset -- allowed
        (("read_file", "apply_patch", "run_command", "list_dir"), False),  # equal to base -- allowed
        ((), False),  # empty -- allowed (maximally narrow)
        (("read_file", "delete_repo"), True),  # introduces a tool the base policy never granted -- must reject
    ],
)
def test_overlay_cannot_widen_granted_tools(overlay_tools: tuple[str, ...], should_raise: bool) -> None:
    overlay = OverlayV1(overlay_id="overlay.tenant-1", tenant_id="tenant-1", granted_tools_override=overlay_tools)
    call = lambda: compose(
        persona=_persona(), capabilities=_capabilities(), policy=_policy(), overlay=overlay, objective="Fix a bug"
    )
    if should_raise:
        with pytest.raises(ComposerError, match="PERSONA_RUNTIME_OVERLAY_TOOL_ESCALATION"):
            call()
    else:
        call()  # must not raise


def test_overlay_can_only_add_evidence_requirements_never_remove() -> None:
    overlay = OverlayV1(
        overlay_id="overlay.tenant-1",
        tenant_id="tenant-1",
        evidence_requirements_add=("hostile_sweep",),
    )
    context = compose(
        persona=_persona(), capabilities=_capabilities(), policy=_policy(), overlay=overlay, objective="Fix a bug"
    )
    assert set(context.evidence_requirements) >= {"acceptance_mapping", "hostile_sweep"}


def test_overlay_requires_tenant_id() -> None:
    overlay = OverlayV1(overlay_id="overlay.x", tenant_id="  ")
    with pytest.raises(ComposerError, match="PERSONA_RUNTIME_OVERLAY_TENANT_REQUIRED"):
        compose(
            persona=_persona(), capabilities=_capabilities(), policy=_policy(), overlay=overlay, objective="Fix a bug"
        )


def test_compose_is_pure_same_inputs_same_output() -> None:
    persona, capabilities, policy = _persona(), _capabilities(), _policy()
    first = compose(persona=persona, capabilities=capabilities, policy=policy, overlay=None, objective="Fix a bug")
    second = compose(persona=persona, capabilities=capabilities, policy=policy, overlay=None, objective="Fix a bug")
    assert first == second
