"""Intake no longer routes execution by task_class or invented file scopes."""

from __future__ import annotations

import pytest

from backend.infrastructure.model_gateway.intelligence_layer.prompts import (
    PLANNER_INTAKE_SYSTEM_PROMPT,
    planner_intake_prompt,
)
from backend.infrastructure.model_gateway.intelligence_layer.service import (
    DiscussTurnResultV1,
    _sanitize_file_hints,
)


def test_planner_prompt_describes_what_not_how() -> None:
    prompt = planner_intake_prompt(["single_file_helper", "bounded_repair", "verification_only"])
    assert "normalized_objective" in prompt
    assert "file_hints" in prompt
    assert "symbol_hints" in prompt
    assert "constraints" in prompt
    assert "repository_hint" in prompt
    assert "Preserve every explicit deliverable" in prompt
    # Removed static routing instructions.
    assert "Use bounded_repair for HTML/CSS" not in prompt
    assert "Use single_file_helper" not in PLANNER_INTAKE_SYSTEM_PROMPT
    assert "KEY=value lines in `.env`" not in prompt
    assert "must NOT choose an implementation strategy" in prompt


@pytest.mark.parametrize(
    "objective",
    [
        "Add retry handling to payment processing",
        "Improve caching for search results",
        "Wire up webhook signature verification",
        "Reduce latency in the notification worker",
    ],
)
def test_unrelated_objectives_share_generic_intake_shape(objective: str) -> None:
    """No filenames → empty hints; task_class stays optional metadata only."""
    # Simulated planner output for goals without explicit paths.
    parsed_scope = _sanitize_file_hints([])
    result = DiscussTurnResultV1(
        reply=f"Understood: {objective}. Click **Create & run with AIX**.",
        task_class=None,
        proposed_scope=parsed_scope,
        ambiguous=False,
        intent="objective",
        confidence=0.9,
        objective_summary=objective,
        routing_phase="planning",
        model_role="planner",
        planner_invoked=True,
        requires_objective_confirm=False,
        provider="openrouter",
        resolved_provider="openrouter",
        model="test/model",
        usage={},
        route_evidence={},
        normalized_objective=objective,
        repository_hint=None,
        file_hints=parsed_scope,
        symbol_hints=[],
        constraints=[],
    )
    assert result.file_hints == []
    assert result.proposed_scope == []
    assert result.task_class is None
    assert result.normalized_objective == objective
    assert result.planner_invoked is True


def test_mutating_verb_heuristic_removed() -> None:
    import backend.infrastructure.model_gateway.intelligence_layer.service as service

    assert not hasattr(service, "_looks_like_mutating_file_objective")
