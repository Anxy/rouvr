"""Deterministic, bounded explore-before-plan generation.

The planner produces importable plan data only.  Sprint 004 tool adapters execute the
steps and update :mod:`execution_context`; the production ``run_real_execute`` path is
deliberately not wired here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from backend.aix_core.runtime.planner.execution_context import ExecutionContext


EXPLORATION_REQUIRED = "EXPLORATION_REQUIRED"
EXPLORATION_FAMILIES = frozenset(
    {"EXPLORE", "SEARCH", "ANALYZE", "EXPLAIN", "EDIT", "DEBUG", "REVIEW", "REFACTOR"}
)
MUTATION_FAMILIES = frozenset({"EDIT", "CREATE", "DELETE", "REFACTOR"})


@dataclass(frozen=True)
class ExplorationStep:
    step_id: str
    name: str
    action: str
    capability: str
    arguments: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    category: str = "EXECUTABLE"
    phase: str = "exploration"
    mandatory: bool = True
    understanding_signal: str = ""


@dataclass(frozen=True)
class ExplorationPlan:
    task_family: str
    steps: tuple[ExplorationStep, ...]
    stop_condition: str
    understanding_criteria: tuple[str, ...]
    max_steps: int
    understanding_complete: bool = False
    clarification_required: bool = False
    clarification_question: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class PlanningGateResult:
    allowed: bool
    reason_code: str | None = None
    detail: str = ""


def _concrete_target_bound(target_path: str | None) -> bool:
    """Repository root (``.``) is a workspace, not a mutation file target."""
    text = str(target_path or "").strip().replace("\\", "/")
    return bool(text) and text not in {".", "./", "/"}


def requires_exploration(
    task_family: str,
    *,
    target_bound: bool = False,
) -> bool:
    family = str(task_family or "UNKNOWN").strip().upper()
    # Concrete file already named/bound: do not burn explore budget before mutation.
    # REFACTOR/DELETE are mutation families like EDIT — "refactor web.php" with
    # allowed_files=web.php must not loop on EXPLORATION_REQUIRED.
    if family in ("CREATE", "READ", "EDIT", "DELETE", "REFACTOR"):
        return not target_bound
    if family in EXPLORATION_FAMILIES:
        return True
    return False


def gate_execution_planning(
    *,
    task_family: str,
    context: ExecutionContext,
    target_bound: bool = False,
    clarification_required: bool = False,
) -> PlanningGateResult:
    """Block final-answer/mutation planning until explore understanding is terminal."""
    if clarification_required:
        return PlanningGateResult(True, detail="clarification is the next terminal planner action")
    if requires_exploration(task_family, target_bound=target_bound) and not context.understanding_complete:
        return PlanningGateResult(
            False,
            EXPLORATION_REQUIRED,
            "task family requires exploration before execution or mutation planning",
        )
    return PlanningGateResult(True)


def build_exploration_plan(
    *,
    objective: str,
    task_family: str,
    context: ExecutionContext,
    target_path: str | None = None,
    symbol_name: str | None = None,
    max_steps: int = 8,
) -> ExplorationPlan:
    """Create the next bounded, adaptive exploration slice.

    Existing successful search/read fingerprints are omitted.  If no new safe step is
    available, the plan stops for clarification instead of reissuing identical work.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")
    family = str(task_family or "UNKNOWN").strip().upper()
    objective = str(objective or "").strip()
    if context.understanding_complete:
        return ExplorationPlan(
            task_family=family,
            steps=(),
            stop_condition="understanding_complete",
            understanding_criteria=_criteria(objective, family),
            max_steps=max_steps,
            understanding_complete=True,
        )

    # Repository discovery can satisfy the evidence criteria before this adaptive
    # slice is built.  Report that as successful completion instead of treating the
    # exhausted (deduplicated) fingerprints as a request for clarification.
    evidence_complete = False
    if context.execution_root:
        if family in {"EXPLORE", "SEARCH", "ANALYZE", "EXPLAIN", "REVIEW"}:
            evidence_complete = bool(context.directories_listed) and (
                target_path in (None, ".") or bool(context.files_read)
            )
        elif family in (MUTATION_FAMILIES | {"DEBUG"}):
            evidence_complete = bool(context.directories_listed and context.files_read)
            if evidence_complete and symbol_name:
                evidence_complete = any(
                    record.name.casefold() == symbol_name.casefold()
                    for record in context.symbols_resolved
                )
    if evidence_complete:
        return ExplorationPlan(
            task_family=family,
            steps=(),
            stop_condition="understanding_complete",
            understanding_criteria=_criteria(objective, family),
            max_steps=max_steps,
            understanding_complete=True,
        )

    clarification = _clarification_question(objective, family, target_path, symbol_name)
    if clarification:
        return ExplorationPlan(
            task_family=family,
            steps=(),
            stop_condition="clarification_required",
            understanding_criteria=_criteria(objective, family),
            max_steps=max_steps,
            clarification_required=True,
            clarification_question=clarification,
            reason_code="INTENT_UNCLEAR",
        )

    steps: list[ExplorationStep] = []
    target_bound = _concrete_target_bound(target_path)
    exploration_needed = requires_exploration(family, target_bound=target_bound)
    if family == "READ" and target_path:
        _append_read_minimal(steps, target_path, context)
    elif exploration_needed:
        _append_adaptive_steps(
            steps,
            objective=objective,
            context=context,
            target_path=target_path,
            symbol_name=symbol_name,
        )

    steps = steps[:max_steps]
    if exploration_needed and not steps:
        return ExplorationPlan(
            task_family=family,
            steps=(),
            stop_condition="clarification_required",
            understanding_criteria=_criteria(objective, family),
            max_steps=max_steps,
            clarification_required=True,
            clarification_question="What additional target, symbol, or runtime evidence should be inspected?",
            reason_code="NO_NEW_EXPLORATION_AVAILABLE",
        )
    return ExplorationPlan(
        task_family=family,
        steps=tuple(steps),
        stop_condition="understanding_complete_or_clarification_required",
        understanding_criteria=_criteria(objective, family),
        max_steps=max_steps,
    )


def _append_read_minimal(steps: list[ExplorationStep], target_path: str, context: ExecutionContext) -> None:
    steps.append(_step(steps, "Check target exists", "filesystem.exists", {"path": target_path}))
    steps.append(_step(steps, "Inspect target metadata", "filesystem.stat", {"path": target_path}))
    if context.check_read(target_path).should_issue:
        steps.append(
            _step(
                steps,
                "Read bound target",
                "filesystem.read_file",
                {"path": target_path, "redact_secrets": True},
                signal="target content and hash captured",
            )
        )


def _append_adaptive_steps(
    steps: list[ExplorationStep],
    *,
    objective: str,
    context: ExecutionContext,
    target_path: str | None,
    symbol_name: str | None,
) -> None:
    scope = target_path or context.execution_root or "."
    if not context.repositories_explored:
        steps.append(
            _step(
                steps,
                "Explore repository structure",
                "filesystem.list_directory",
                {"path": scope, "max_depth": 1},
                signal="candidate services and source roots identified",
            )
        )

    search_terms = _search_terms(objective, symbol_name)
    search_ids: list[str] = []
    for term in search_terms:
        if not context.check_search(term, scope).should_issue:
            continue
        search_step = _step(
            steps,
            f"Search for {term}",
            "filesystem.search",
            {"query": term, "scope": scope, "max_results": 50},
            signal=f"locations related to {term} identified",
        )
        steps.append(search_step)
        search_ids.append(search_step.step_id)

    source_dep = search_ids[0] if search_ids else (steps[-1].step_id if steps else None)
    if search_ids:
        steps.append(
            _step(
                steps,
                "Read the most relevant search hit",
                "filesystem.read_file",
                {"path_from_step": search_ids[0], "selector": "first_source_hit", "redact_secrets": True},
                depends_on=(search_ids[0],),
                signal="control flow and failure handling observed",
            )
        )

    inferred_symbol = symbol_name or _infer_symbol(objective)
    if inferred_symbol and not any(record.name.casefold() == inferred_symbol.casefold() for record in context.symbols_resolved):
        dependencies = (source_dep,) if source_dep else ()
        resolved = _step(
            steps,
            f"Resolve symbol {inferred_symbol}",
            "repository.resolve_symbol",
            {"symbol_name": inferred_symbol, "scope": scope},
            depends_on=dependencies,
            signal="symbol definition and references resolved without guessing",
        )
        steps.append(resolved)
        steps.append(
            _step(
                steps,
                "Read surrounding symbol context",
                "filesystem.read_file",
                {"path_from_step": resolved.step_id, "selector": "resolved_file", "context_lines": 80},
                depends_on=(resolved.step_id,),
                signal="callers, middleware, and configuration dependencies understood",
            )
        )


def _step(
    existing: Iterable[ExplorationStep],
    name: str,
    action: str,
    arguments: dict[str, Any],
    *,
    depends_on: tuple[str, ...] | None = None,
    signal: str = "",
) -> ExplorationStep:
    prior = list(existing)
    step_id = f"explore-{len(prior) + 1:03d}"
    dependencies = depends_on if depends_on is not None else ((prior[-1].step_id,) if prior else ())
    return ExplorationStep(
        step_id=step_id,
        name=name,
        action=action,
        capability=action,
        arguments=arguments,
        depends_on=dependencies,
        understanding_signal=signal,
    )


def _search_terms(objective: str, symbol_name: str | None) -> tuple[str, ...]:
    text = str(objective or "").strip()
    lowered = text.casefold()
    if re.search(r"\b(login|signin|sign-in|authentication|auth)\b", lowered) or "sign in" in lowered:
        terms = ["auth", "login", "middleware", "config"]
    elif any(token in lowered for token in ("retry", "retries", "backoff")):
        terms = ["retry", "backoff", "retry policy", "config"]
    else:
        # Imperative edit verbs are objective syntax, not repository search needles.
        ignored = {
            "where",
            "what",
            "when",
            "why",
            "how",
            "this",
            "that",
            "with",
            "from",
            "into",
            "find",
            "review",
            "explain",
            "failing",
            "failed",
            "failure",
            "replace",
            "rename",
            "change",
            "update",
            "modify",
            "append",
            "patch",
            "edit",
            "rewrite",
            "implement",
            "integrate",
            "configure",
            "enable",
            "without",
            "other",
            "keys",
            "all",
            "occurrences",
            "and",
            "the",
            "for",
            "to",
            "explore",
            "classify",
            "persist",
            "prove",
            "include",
            "live",
            "aix",
            "backend",
            "service",
            "every",
            "mounted",
            "public",
            "actual",
            "production",
            "behavior",
            "hyeve",
        }
        # Preserve identifier case from the objective — repos are usually case-sensitive
        # (VALUE_HOST != value_host under default rg).
        terms = [
            token
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text)
            if len(token) > 2 and token.casefold() not in ignored
        ][:3]
    if symbol_name:
        terms.insert(0, symbol_name)
    return tuple(dict.fromkeys(terms))


def _infer_symbol(objective: str) -> str | None:
    lowered = objective.casefold()
    if any(token in lowered for token in ("login", "sign in", "signin")):
        return "login"
    match = re.search(r"(?:function|class|symbol|method)\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)", objective)
    return match.group(1) if match else None


# Shared with durable_api_backend.py's `clarify` control action: when a `clarify`
# answer resolves an `INTENT_UNCLEAR` block raised because the objective still
# contains one of these phrases, that phrase must be REPLACED (not left in place
# while the answer is appended after it) -- otherwise this exact substring check
# re-fires on every subsequent evaluation of the same objective text, no matter what
# the operator answers, since the trigger phrase never actually leaves the objective.
VAGUE_CREATE_PHRASES: tuple[str, ...] = ("any path", "any name", "just any", "any folder")


def _clarification_question(
    objective: str,
    family: str,
    target_path: str | None,
    symbol_name: str | None,
) -> str | None:
    if not objective:
        return "What repository behavior or target should I investigate?"
    vague = len(re.findall(r"[A-Za-z0-9_]+", objective)) < 4
    generic_target = bool(re.search(r"\b(the|this|that)\s+(client|service|file|thing|code)\b", objective.casefold()))
    if family in {"EDIT", "CREATE", "DELETE", "REFACTOR"} and not target_path and not symbol_name and (vague or generic_target):
        return "Which repository target or symbol should be changed?"
    underspecified_create = (
        family == "CREATE"
        and not target_path
        and any(tok in objective.casefold() for tok in VAGUE_CREATE_PHRASES)
    )
    if underspecified_create:
        return "Which directory name and repository path should be created?"
    if family == "READ" and not target_path and (vague or generic_target):
        return "Which file, directory, or repository should I read?"
    return None


def _criteria(objective: str, family: str) -> tuple[str, ...]:
    criteria = [
        "relevant repository area and target files are identified",
        "the primary symbol or control flow is resolved from real search/read evidence",
        "configuration, middleware, and caller dependencies affecting the behavior are known",
    ]
    if family in MUTATION_FAMILIES:
        criteria.append("the intended change boundary and verification surface are specific enough to patch safely")
    if "fail" in objective.casefold() or family == "DEBUG":
        criteria.append("a failure hypothesis is supported by code, configuration, or available runtime evidence")
    return tuple(criteria)


def build_discovery_config_for_exploration(
    *,
    objective: str,
    repository_root: Path,
) -> dict[str, str]:
    """Load repository file contents for class-hint symbol resolution during exploration."""
    from backend.aix_core.intelligence.repository.class_path_resolve import (
        extract_class_name_hints,
        resolve_class_hints_in_repository,
    )

    hints = extract_class_name_hints(objective)
    if not hints:
        return {}
    config: dict[str, str] = {}
    for rel in resolve_class_hints_in_repository(repository_root, hints):
        try:
            config[rel] = (repository_root / rel).read_text(encoding="utf-8")[:65536]
        except OSError:
            continue
    return config


__all__ = [
    "EXPLORATION_FAMILIES",
    "EXPLORATION_REQUIRED",
    "ExplorationPlan",
    "ExplorationStep",
    "PlanningGateResult",
    "build_discovery_config_for_exploration",
    "build_exploration_plan",
    "gate_execution_planning",
    "requires_exploration",
]
