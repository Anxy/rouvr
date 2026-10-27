"""AIX-EC-004 Packet EC-03: capability-aware strategy selection.

An executor/fallback may run only when its profile supports every required effect.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import ExecutorProfileV1, RequiredEffectV1
from backend.aix_core.runtime.executor.executor_profile import (
    STRATEGY_ENV_ASSIGN,
    STRATEGY_PHP_COMMENTS,
    STRATEGY_PHP_STRUCTURAL,
    STRATEGY_ROUTE,
    STRATEGY_SNIPPET_CODEGEN,
    STRATEGY_TEXT_REPLACE,
    strategy_profile,
)
from backend.aix_core.runtime.executor.required_effects import effect_kinds

# Preference order when several strategies are capable. Comment insertion is last
# and has an empty effect set, so it never wins a typed structural step.
_STRATEGY_ORDER = (
    STRATEGY_PHP_STRUCTURAL,
    STRATEGY_ROUTE,
    STRATEGY_TEXT_REPLACE,
    STRATEGY_ENV_ASSIGN,
    STRATEGY_SNIPPET_CODEGEN,
    STRATEGY_PHP_COMMENTS,
)


class CapabilityMismatch(Exception):
    def __init__(self, code: str, detail: str, *, unmet: tuple[str, ...] = ()) -> None:
        self.code = code
        self.detail = detail
        self.unmet = unmet
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class StrategyChoice:
    strategy_id: str
    profile: ExecutorProfileV1
    reason: str


def profile_supports(profile: ExecutorProfileV1, effects: tuple[RequiredEffectV1, ...]) -> bool:
    if not effects:
        return True
    supported = set(profile.supported_effects)
    needed = effect_kinds(effects)
    # PRESERVE / RUN_DECLARED_VALIDATION can ride along with a capable mutation strategy.
    mutation = needed - {"PRESERVE_PUBLIC_INTERFACE", "RUN_DECLARED_VALIDATION"}
    if not mutation:
        return needed <= supported or bool(supported)
    return mutation <= supported


def select_strategy(
    *,
    effects: tuple[RequiredEffectV1, ...],
    language: str = "",
    forbidden_strategies: tuple[str, ...] = (),
    preferred_strategy: str | None = None,
) -> StrategyChoice:
    forbidden = set(forbidden_strategies)
    candidates = list(_STRATEGY_ORDER)
    if language == "php":
        # PHP structural effects have a deterministic, AST-aware implementation.
        # The generic snippet lane advertises structural effects only for other
        # languages and must not weaken PHP dispatch or its repair exclusions.
        candidates = [s for s in candidates if s != STRATEGY_SNIPPET_CODEGEN]
    else:
        candidates = [s for s in candidates if s not in {STRATEGY_PHP_STRUCTURAL, STRATEGY_PHP_COMMENTS}]
        # Route refactor is a Laravel/PHP specialization that also advertises
        # LITERAL_REPLACE. Preferring it for Python/TS source edits causes
        # UNSUPPORTED_CAPABILITY after an empty route patch (live S5 TypedDict).
        candidates = [s for s in candidates if s != STRATEGY_ROUTE]
    if preferred_strategy and preferred_strategy not in forbidden and preferred_strategy in candidates:
        candidates = [preferred_strategy] + [s for s in candidates if s != preferred_strategy]

    capable: list[StrategyChoice] = []
    for strategy_id in candidates:
        if strategy_id in forbidden:
            continue
        profile = strategy_profile(strategy_id)
        if profile_supports(profile, effects):
            capable.append(
                StrategyChoice(
                    strategy_id=strategy_id,
                    profile=profile,
                    reason=f"supports {sorted(effect_kinds(effects))}",
                )
            )
    if not capable:
        needed = tuple(sorted(effect_kinds(effects)))
        raise CapabilityMismatch(
            "UNSUPPORTED_CAPABILITY",
            f"no strategy supports required effects {list(needed)} "
            f"(forbidden={list(forbidden) or 'none'})",
            unmet=needed,
        )
    return capable[0]


__all__ = [
    "CapabilityMismatch",
    "StrategyChoice",
    "profile_supports",
    "select_strategy",
]
