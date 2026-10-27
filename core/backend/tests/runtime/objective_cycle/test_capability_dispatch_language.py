"""Non-PHP languages must not prefer the Laravel route specialization."""

from __future__ import annotations

from backend.aix_core.runtime.executor.capability_dispatch import select_strategy
from backend.aix_core.runtime.objective.contracts import RequiredEffectV1
from backend.aix_core.runtime.executor.executor_profile import (
    STRATEGY_ROUTE,
    STRATEGY_SNIPPET_CODEGEN,
    STRATEGY_TEXT_REPLACE,
)


def test_python_literal_replace_skips_route_strategy() -> None:
    choice = select_strategy(
        effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        language="python",
    )
    assert choice.strategy_id != STRATEGY_ROUTE
    assert choice.strategy_id in {STRATEGY_TEXT_REPLACE, STRATEGY_SNIPPET_CODEGEN}


def test_php_literal_replace_may_still_select_route() -> None:
    choice = select_strategy(
        effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        language="php",
    )
    assert choice.strategy_id == STRATEGY_ROUTE


def test_python_helper_extraction_uses_verified_snippet_codegen() -> None:
    choice = select_strategy(
        effects=(
            RequiredEffectV1(kind="EXTRACT_HELPER"),
            RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"),
            RequiredEffectV1(kind="RUN_DECLARED_VALIDATION"),
        ),
        language="python",
        preferred_strategy=STRATEGY_SNIPPET_CODEGEN,
    )
    assert choice.strategy_id == STRATEGY_SNIPPET_CODEGEN
    assert choice.profile.supports_structural_edit is True
