"""AIX-MPE-003 Packet MPE-08 / AIX-EC-004 Packet EC-02: truthful executor profiles.

Configuration maps models to profiles. This module does not hardcode GPT/Kimi/Claude.
Each strategy inside `run_real_execute` has its own profile; the aggregate default
describes only effects the live lane can actually select a strategy for.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import ExecutorProfileV1, RequiredEffectKind

_STRUCTURAL: tuple[RequiredEffectKind, ...] = (
    "GROUP_METHODS",
    "MOVE_METHODS",
    "EXTRACT_HELPER",
    "EXTRACT_TO_FILES",
)

_PHP_STRUCTURAL: tuple[RequiredEffectKind, ...] = (
    *_STRUCTURAL,
    "STRIP_UNUSED_IMPORTS",
    "PRESERVE_PUBLIC_INTERFACE",
    "RUN_DECLARED_VALIDATION",
)

_LITERAL: tuple[RequiredEffectKind, ...] = ("LITERAL_REPLACE",)

# Model-backed snippet generation is the implementation lane for bounded,
# single-file structural edits outside PHP.  These effects are subsequently
# proved by structural verification; EXTRACT_TO_FILES is deliberately omitted
# because this profile does not support multi-file mutation.
_SNIPPET_STRUCTURAL: tuple[RequiredEffectKind, ...] = (
    "GROUP_METHODS",
    "MOVE_METHODS",
    "EXTRACT_HELPER",
    "STRIP_UNUSED_IMPORTS",
    "PRESERVE_PUBLIC_INTERFACE",
    "RUN_DECLARED_VALIDATION",
    "LITERAL_REPLACE",
)


STRATEGY_PHP_STRUCTURAL = "aix.structural.php.v1"
STRATEGY_ROUTE = "aix.fallback.route.v1"
STRATEGY_TEXT_REPLACE = "aix.fallback.text_replace.v1"
STRATEGY_ENV_ASSIGN = "aix.fallback.env_assign.v1"
STRATEGY_PHP_COMMENTS = "aix.fallback.php_comments.v1"
STRATEGY_SNIPPET_CODEGEN = "aix.codegen.snippet.v1"
STRATEGY_REAL_EXECUTE = "aix.real_execute.v1"


def _profile(
    executor_id: str,
    effects: tuple[RequiredEffectKind, ...],
    *,
    structural: bool = False,
    tests: bool = False,
    symbol: bool = False,
    context_budget: int = 8_000,
    max_scope: int = 4,
) -> ExecutorProfileV1:
    return ExecutorProfileV1(
        executor_id=executor_id,
        supported_effects=effects,
        supports_single_file=True,
        supports_multi_file=False,
        supports_shell=True,
        supports_tests=tests,
        supports_symbol_search=symbol,
        supports_structural_edit=structural,
        context_budget=context_budget,
        preferred_complexity="medium",
        max_recommended_scope=max_scope,
    )


def strategy_profile(strategy_id: str) -> ExecutorProfileV1:
    if strategy_id == STRATEGY_PHP_STRUCTURAL:
        profile = _profile(
            STRATEGY_PHP_STRUCTURAL,
            _PHP_STRUCTURAL,
            structural=True,
            tests=True,
            symbol=True,
            context_budget=16_000,
            max_scope=12,
        )
        # God-class → service extraction creates sibling files under the same tree.
        return profile.model_copy(update={"supports_multi_file": True})
    if strategy_id == STRATEGY_ROUTE:
        return _profile(STRATEGY_ROUTE, _LITERAL)
    if strategy_id == STRATEGY_TEXT_REPLACE:
        return _profile(STRATEGY_TEXT_REPLACE, _LITERAL)
    if strategy_id == STRATEGY_ENV_ASSIGN:
        return _profile(STRATEGY_ENV_ASSIGN, _LITERAL)
    if strategy_id == STRATEGY_PHP_COMMENTS:
        # Comment insertion is not a required-effect capability. Empty support list
        # means it cannot win a step that declared GROUP_METHODS / EXTRACT / imports.
        return _profile(STRATEGY_PHP_COMMENTS, ())
    if strategy_id == STRATEGY_SNIPPET_CODEGEN:
        return _profile(
            STRATEGY_SNIPPET_CODEGEN,
            _SNIPPET_STRUCTURAL,
            structural=True,
            tests=True,
            symbol=True,
            context_budget=16_000,
        )
    return default_executor_profile(executor_id=strategy_id)


def default_executor_profile(*, executor_id: str = STRATEGY_REAL_EXECUTE) -> ExecutorProfileV1:
    """Aggregate lane profile shown to the planner: effects the dispatcher can bind."""
    return _profile(
        executor_id,
        (
            *_PHP_STRUCTURAL,
            "LITERAL_REPLACE",
        ),
        structural=True,
        tests=True,
        symbol=True,
        context_budget=16_000,
        max_scope=4,
    )


__all__ = [
    "STRATEGY_ENV_ASSIGN",
    "STRATEGY_PHP_COMMENTS",
    "STRATEGY_PHP_STRUCTURAL",
    "STRATEGY_REAL_EXECUTE",
    "STRATEGY_ROUTE",
    "STRATEGY_SNIPPET_CODEGEN",
    "STRATEGY_TEXT_REPLACE",
    "default_executor_profile",
    "strategy_profile",
]
