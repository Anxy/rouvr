"""AIX Closed-Loop Execution Plan, Sprint W9: the work-order-aware independent
verifier.

Two layers, deliberately kept separate:

1. **Mechanical** (`verification_engine.py`, reused not recomputed) -- always runs, no
   model call, no cost. If a mandatory mechanical check failed, there is no point asking
   a model whether the acceptance criteria were satisfied; the answer is already no.
2. **Semantic** (this module's `_ask_verifier_model`, opt-in behind
   `INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED`) -- only runs once mechanical checks
   pass, asks the independent `verifier` model role (never the `executor` role --
   `config.py`'s own comment: "must differ from executor") whether the diff actually
   satisfies the declared `StepContractV1.acceptance_criteria`/`prohibited_actions`, not
   just whether a diff exists and tests ran.

Critical rule carried over from the plan doc, enforced here: the executor never
self-certifies. This module is only ever called with an already-produced
`RealExecuteResult`/`StepAttemptV1` it did not create, and it never mutates either.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.aix_core.runtime.objective.contracts import StepContractV1, VerificationVerdict
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatMessage,
    HyeveIntegrationChatError,
    chat_completion,
)
from backend.infrastructure.model_gateway.intelligence_layer.model_registry import load_model_registry
from backend.aix_core.runtime.model_registry.registry import get_model_registry
from backend.infrastructure.model_gateway.intelligence_layer.verifier_prompts import semantic_verifier_prompt
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.executor.source_patch_codegen import extract_text_replacements
from backend.aix_core.framework.verification import (
    aggregate_outcome,
    build_checks_from_completion_gates,
    build_checks_from_read_verification,
)


@dataclass(frozen=True)
class SemanticVerificationOutcome:
    verdict: VerificationVerdict
    reasons: tuple[str, ...]
    mechanical_passed: bool
    failed_mandatory_checks: tuple[str, ...] = ()
    semantic_checked: bool = False
    criteria_results: tuple[dict[str, object], ...] = field(default_factory=tuple)


_PROPOSAL_OBJECTIVE_STATUSES = frozenset({"AWAITING_REVIEW", "COMPLETED", "COMPLETED_WITH_WARNINGS"})


def _structural_effects_passed(result: RealExecuteResult) -> bool:
    gates = result.completion_gates or {}
    inner = gates.get("gates") if isinstance(gates, dict) else getattr(gates, "gates", None)
    if isinstance(inner, dict):
        return inner.get("structural_effects") is True
    return False


def _step_declares_mutation(step_contract: StepContractV1 | None) -> bool:
    if step_contract is None:
        return False
    from backend.aix_core.runtime.executor.required_effects import effect_kinds, effects_for_step

    mutation_effects = effect_kinds(effects_for_step(step_contract)) - {
        "PRESERVE_PUBLIC_INTERFACE",
        "RUN_DECLARED_VALIDATION",
    }
    if mutation_effects:
        return True
    if any(
        a.capability == "filesystem" and a.operation == "patch"
        for a in (step_contract.actions or ())
    ):
        return True
    schema = str(step_contract.expected_output_schema_ref or "").casefold()
    # Planner default schema is ``...generic_edit``. That is a mutation signal for
    # real edit/create steps, but must NOT force validate/evidence-only steps into
    # empty-diff REPAIR/HALT (substring ``"edit" in schema`` was always true).
    if not (
        schema.endswith("generic_edit")
        or schema.endswith(".patch")
        or "mutation" in schema
    ):
        return False
    purpose = f"{step_contract.purpose} {' '.join(step_contract.acceptance_criteria)}".casefold()
    if any(
        tok in purpose
        for tok in ("validate", "validation", "attach evidence", "static validation")
    ):
        return False
    return True


def _is_read_only_evidence_result(
    result: RealExecuteResult, step_contract: StepContractV1 | None
) -> bool:
    """True when a non-mutating step produced evidence and no repository diff.

    Read-only summary/inspect/read tasks must not be sent to the semantic model
    with an empty diff; that path hallucinates a mutation requirement.
    Mutation contracts still require a real diff.
    """
    if _step_declares_mutation(step_contract):
        return False
    if int(result.target_files_modified or 0) > 0:
        return False
    if str(result.diff_text or "").strip():
        return False
    if not result.ok:
        return False
    has_evidence = bool(
        result.evidence_refs
        or result.artifacts
        or int(result.target_files_read or 0) > 0
        or int(result.real_operations or 0) > 0
        or str(result.detail or "").strip()
    )
    return has_evidence


def _is_proposal_only_result(result: RealExecuteResult) -> bool:
    """Workspace proposal (docs/HTML demo) with no repository mutation to verify."""
    if str(result.code or "") == "PROPOSAL_READY":
        return True
    if not result.ok:
        return False
    if str(result.objective_status or "") != "AWAITING_REVIEW":
        return False
    if str(result.diff_text or "").strip():
        return False
    if int(result.target_files_modified or 0) > 0:
        return False
    return True


def _is_already_satisfied_result(result: RealExecuteResult) -> bool:
    """Prior attempt already wrote in-scope deliverables; empty diff is expected."""
    if not result.ok:
        return False
    code = str(result.code or "").strip().upper()
    if code in {"ALREADY_SATISFIED", "REAL_EXECUTE_ALREADY_SATISFIED"}:
        return True
    detail = str(result.detail or "").casefold()
    if "already satisfied" in detail:
        return True
    report = result.artifacts.get("execution_report") if isinstance(result.artifacts, dict) else None
    if report and Path(str(report)).is_file():
        try:
            payload = json.loads(Path(str(report)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("already_satisfied"):
            return True
    return False


def _mechanical_layer(result: RealExecuteResult) -> tuple[bool, tuple[str, ...]]:
    checks = build_checks_from_completion_gates(result.completion_gates) + build_checks_from_read_verification(
        result.read_verification
    )
    if not checks:
        # Neither branch produced typed checks (e.g. a demo/docs proposal-only result) --
        # fall back to the executor's own ok/objective_status signal. Proposal paths
        # intentionally use AWAITING_REVIEW (human reviews the artifact); that is
        # success for this layer, not a failed COMPLETED mutation.
        return bool(result.ok and result.objective_status in _PROPOSAL_OBJECTIVE_STATUSES), ()
    outcome = aggregate_outcome(checks)
    return outcome.all_mandatory_passed, outcome.failed_mandatory


def _literal_replacements_proved(
    *, step_contract: StepContractV1, diff_text: str
) -> bool:
    prose = "\n".join((step_contract.purpose, *step_contract.acceptance_criteria))
    pairs = extract_text_replacements(prose)
    if not pairs:
        pairs = [
            (match.group(1).strip(), match.group(2).strip())
            for match in re.finditer(
                r"\breplace\b[^\n]{0,100}?[`'\"](.+?)[`'\"]\s+with\s+[`'\"](.+?)[`'\"]",
                prose,
                re.IGNORECASE,
            )
            if match.group(1).strip() and match.group(2).strip()
        ]
    if not pairs:
        return False
    removed = "\n".join(
        line[1:] for line in str(diff_text or "").splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    added = "\n".join(
        line[1:] for line in str(diff_text or "").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return all(old in removed and new in added for old, new in pairs)


def _scoped_literal_contract_is_decidable(
    *, step_contract: StepContractV1, result: RealExecuteResult
) -> bool:
    """Return true only for literal edits whose remaining criteria are mechanical.

    Planners do not always emit ``LITERAL_REPLACE`` even when their purpose is an
    exact replacement.  Requiring a model to restate that a one-file diff changed
    only that file creates a false repair loop after the old text is already gone.
    Keep this shortcut narrow: explicit scope, changed files inside that scope,
    and criteria limited to the replacement, scope, or artifact prohibitions.
    """
    scope = {str(path).replace("\\", "/").casefold() for path in step_contract.target_scope}
    changed = {
        str(item.get("path") if isinstance(item, dict) else item)
        .replace("\\", "/")
        .casefold()
        for item in (result.changed_source_files or ())
    }
    if not scope or not changed or not changed.issubset(scope):
        return False
    allowed_markers = (
        "contain",
        "replace",
        "no longer",
        "only ",
        "limited to",
        "no html",
        "no css",
        "proposal artifact",
    )
    return all(
        any(marker in str(criterion).casefold() for marker in allowed_markers)
        for criterion in step_contract.acceptance_criteria
    )


def _new_file_creation_is_decidable(
    *, step_contract: StepContractV1, result: RealExecuteResult
) -> bool:
    """A step that creates one or more brand-new files in-scope needs no model.

    When the diff shows `--- /dev/null` for every changed path and all paths
    are inside the declared target scope, the semantic verifier's judgment on
    "did the code change enough" is meaningless — there was nothing before.

    Also handles files created in earlier steps of the same multi-step plan:
    if the target files now exist on disk (created by a prior step) and the
    step's purpose is creation, mechanical evidence suffices.
    """
    diff_text = result.diff_text or ""
    scope = {str(path).replace("\\", "/").casefold() for path in step_contract.target_scope}
    changed = {
        str(item.get("path") if isinstance(item, dict) else item)
        .replace("\\", "/")
        .casefold()
        for item in (result.changed_source_files or ())
    }

    if "--- /dev/null" in diff_text and scope and changed and changed.issubset(scope):
        created_paths = set()
        current_file: str | None = None
        is_new = False
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:].strip().replace("\\", "/").casefold()
            elif line.startswith("--- /dev/null"):
                is_new = True
            elif line.startswith("--- "):
                is_new = False
            if current_file and is_new:
                created_paths.add(current_file)
        if created_paths and changed.issubset(created_paths):
            return True

    # Files created in earlier steps: if all scoped files are new and tests passed,
    # and the step purpose explicitly describes file/module creation.
    if _focused_tests_passed(result) and scope and changed and changed.issubset(scope):
        purpose_lower = step_contract.purpose.casefold()
        creation_phrases = (
            "create file", "create module", "create the file", "create the module",
            "write file", "write module", "generate file", "generate module",
            "add file", "add module", "add new file", "add new module",
            "implement file", "implement module",
            "create util", "create test", "create helper",
        )
        if any(phrase in purpose_lower for phrase in creation_phrases):
            return True

    return False


def _deterministic_acceptance_proved(
    *, step_contract: StepContractV1, result: RealExecuteResult
) -> bool:
    """Return True when all acceptance criteria are mechanically provable.

    Checks: required files exist in changed set, tests passed, changed-file scope
    is valid, and no prohibited actions detected. This avoids sending objectively
    proven steps to the semantic verifier.
    """
    diff_text = result.diff_text or ""
    if not diff_text.strip():
        return False

    scope = {str(path).replace("\\", "/").casefold() for path in step_contract.target_scope}
    changed = {
        str(item.get("path") if isinstance(item, dict) else item)
        .replace("\\", "/")
        .casefold()
        for item in (result.changed_source_files or ())
    }
    if scope and changed and not changed.issubset(scope):
        return False

    tests_passed = _focused_tests_passed(result)
    has_validation = bool(step_contract.validation_commands or step_contract.expected_checks)
    if has_validation and not tests_passed:
        return False

    criteria = list(step_contract.acceptance_criteria)
    if not criteria:
        return False

    diff_lower = diff_text.casefold()
    for criterion in criteria:
        c = criterion.casefold().strip()
        # File-existence criteria
        if any(k in c for k in ("file exists", "file is created", "file must exist")):
            continue
        # Test-pass criteria
        if any(k in c for k in ("tests pass", "test passes", "pytest", "all tests")):
            if not tests_passed:
                return False
            continue
        # Scope-containment criteria
        if any(k in c for k in ("only ", "limited to", "no other file", "confined to")):
            if not scope or not changed.issubset(scope):
                return False
            continue
        # Content-presence criteria (check diff for required strings)
        if "contain" in c or "include" in c or "must have" in c:
            backtick_vals = re.findall(r"`([^`]+)`", criterion)
            quote_vals = re.findall(r"['\"]([^'\"]+)['\"]", criterion)
            check_vals = backtick_vals + quote_vals
            if check_vals and all(v.casefold() in diff_lower for v in check_vals):
                continue
            if not check_vals:
                return False
            return False
        # Replacement criteria
        if "replace" in c:
            continue
        # Genuinely ambiguous criterion — cannot decide mechanically
        return False

    return True


async def _ask_verifier_model(
    *,
    step_contract: StepContractV1,
    diff_text: str,
    tenant_id: str,
    actor_id: str,
    authorization_header: str,
    timeout_seconds: float,
    cumulative_diff: str = "",
) -> tuple[VerificationVerdict | None, tuple[str, ...], tuple[dict[str, object], ...]]:
    system = semantic_verifier_prompt(
        purpose=step_contract.purpose,
        acceptance_criteria=list(step_contract.acceptance_criteria),
        prohibited_actions=list(step_contract.prohibited_actions),
        diff_text=diff_text,
        cumulative_diff=cumulative_diff,
    )
    registry = load_model_registry()
    route = get_model_registry().get_route("verifier")
    model_ref = route.model_ref
    gpr = GovernedProviderRouter().resolve(role="verifier", model_ref=model_ref)
    if gpr.disposition != "ALLOW":
        return None, (f"GOVERNED_ROUTE_DENIED:{','.join(gpr.reason_codes)}",), ()

    try:
        from backend.aix_core.framework.integration.hyeve.llm_ingress import (
            governed_chat_completion,
            hyeve_capabilities_enabled,
        )

        if hyeve_capabilities_enabled():
            result = await governed_chat_completion(
                role="verifier",
                messages=[ChatMessage(role="system", content=system)],
                tenant_id=tenant_id,
                actor_id=actor_id,
                authorization_header=authorization_header,
                model_ref=model_ref,
                response_format={"type": "json_object"},
                timeout_seconds=timeout_seconds,
                use_hyeve_adapter=True,
            )
        else:
            result = await chat_completion(
                messages=[ChatMessage(role="system", content=system)],
                tenant_id=tenant_id,
                actor_id=actor_id,
                authorization_header=authorization_header,
                response_format={"type": "json_object"},
                model=model_ref,
                timeout_seconds=timeout_seconds,
            )
        parsed = json.loads(result.output_text)
        if not isinstance(parsed, dict):
            raise ValueError("verifier reply JSON is not an object")
    except (HyeveIntegrationChatError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, (f"VERIFIER_MODEL_ERROR:{exc}",), ()

    verdict = str(parsed.get("verdict") or "").strip().upper()
    if verdict not in ("PASS", "REPAIR", "REPLAN", "REUNDERSTAND", "HALT"):
        return None, (f"VERIFIER_MODEL_INVALID_VERDICT:{verdict!r}",), ()
    reasons = tuple(str(r) for r in (parsed.get("reasons") or []) if str(r).strip())
    criteria_results = tuple(
        c for c in (parsed.get("criteria_results") or []) if isinstance(c, dict)
    )
    return verdict, reasons, criteria_results  # type: ignore[return-value]


def _focused_tests_passed(result: RealExecuteResult) -> bool:
    """True when a real pytest/validation command exited 0 during this attempt."""
    for event in result.events or ():
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") == "tool.command_run":
            refs = event.get("payload_refs") or {}
            cmd = str(refs.get("command") or "").casefold()
            if "pytest" not in cmd and "php -l" not in cmd and "npm test" not in cmd:
                continue
            if str(refs.get("returncode")) == "0":
                return True
        if str(event.get("event_type") or "") == "mutation.round.completed":
            refs = event.get("payload_refs") or {}
            if refs.get("test_returncode") == 0 or str(refs.get("test_returncode")) == "0":
                return True
    gates = result.completion_gates or {}
    inner = gates.get("gates") if isinstance(gates, dict) else None
    return bool(isinstance(inner, dict) and inner.get("verification_passed") is True and inner.get("verification_ran") is True)


def _diff_touches_non_test_source(diff_text: str) -> bool:
    """True when unified diff mutates a non-test source path."""
    for line in str(diff_text or "").splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        path = line[4:].strip()
        if path.startswith("b/") or path.startswith("a/"):
            path = path[2:]
        path = path.replace("\\", "/")
        if path in {"/dev/null", "dev/null"}:
            continue
        base = path.rsplit("/", 1)[-1]
        if base.startswith("test_") or base.endswith("_test.py") or base.endswith(".test.ts"):
            continue
        if "/tests/" in f"/{path}/" and base.endswith((".py", ".ts", ".js")):
            # Keep production modules that live under tests/fixtures (not test modules).
            if not base.startswith("test_") and not base.endswith("_test.py"):
                return True
            continue
        if path.endswith((".py", ".php", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java")):
            return True
    return False


def _governed_action_step_is_decidable(
    *, step_contract: StepContractV1, result: RealExecuteResult
) -> bool:
    """Deterministic governed action steps should not depend on verifier prose.

    For governed actions executed via the action-request executor:
    - Read-only steps (no mutation effects): any successful completion is a PASS.
    - Mutating steps (filesystem.mkdir, filesystem.rename): mechanical checks plus
      concrete changed paths are sufficient evidence.
    Requiring semantic-model narration here creates repair loops despite success.
    """
    if str(result.executor or "") != "objective_cycle.real_execute.action_request.v1":
        return False
    actions = tuple(step_contract.actions or ())
    if not actions:
        return False

    from backend.aix_core.runtime.foundation.capability_registry import is_information_operation
    from backend.aix_core.runtime.executor.required_effects import effect_kinds, effects_for_step

    # Read-only action steps with no mutation effects always pass.
    all_info = all(is_information_operation(a.capability, a.operation) for a in actions)
    mutation_effects = effect_kinds(effects_for_step(step_contract)) - {
        "PRESERVE_PUBLIC_INTERFACE",
        "RUN_DECLARED_VALIDATION",
    }
    if all_info and not mutation_effects:
        return True

    allowed_ops = {"mkdir", "rename"}
    if not all(a.capability == "filesystem" and a.operation in allowed_ops for a in actions):
        return False
    changed = {
        str(item.get("path") if isinstance(item, dict) else item).replace("\\", "/")
        for item in (result.changed_source_files or ())
    }
    if not changed:
        return False
    # For rename, evidence must include both source and destination paths.
    for action in actions:
        if action.operation == "rename":
            src = str(action.arguments.get("source") or "").replace("\\", "/")
            dst = str(action.arguments.get("destination") or "").replace("\\", "/")
            if not src or not dst or src not in changed or dst not in changed:
                return False
    scope = {str(path).replace("\\", "/") for path in step_contract.target_scope}
    return bool(scope and changed.issubset(scope))


async def verify_step(
    *,
    result: RealExecuteResult,
    step_contract: StepContractV1 | None,
    repair_budget_remaining: int,
    semantic_verifier_enabled: bool,
    tenant_id: str = "",
    actor_id: str = "",
    authorization_header: str = "",
    timeout_seconds: float = 90.0,
    prior_attempt_diffs: tuple[str, ...] | list[str] | None = None,
) -> SemanticVerificationOutcome:
    # Docs/HTML proposals write cycle-workspace artifacts for human review. There is
    # no repository diff for the semantic model to judge, and a StepContract from the
    # planner must not force REPAIR just because source mutation never happened.
    if _is_proposal_only_result(result):
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("proposal-only result awaiting human review",),
            mechanical_passed=True,
            semantic_checked=False,
        )
    if _is_already_satisfied_result(result):
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("objective already satisfied by existing in-scope deliverables",),
            mechanical_passed=True,
            semantic_checked=False,
        )
    if _is_read_only_evidence_result(result, step_contract):
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("read-only step verified from evidence; no mutation diff required",),
            mechanical_passed=True,
            semantic_checked=False,
        )
    if (
        _step_declares_mutation(step_contract)
        and not str(result.diff_text or "").strip()
        and int(result.target_files_modified or 0) == 0
    ):
        verdict = "REPAIR" if repair_budget_remaining > 0 else "HALT"
        return SemanticVerificationOutcome(
            verdict=verdict,
            reasons=("mutation step produced an empty diff; acceptance criteria require a real change",),
            mechanical_passed=False,
            failed_mandatory_checks=("diff_exists",),
        )

    mechanical_passed, failed_mandatory = _mechanical_layer(result)

    if not mechanical_passed:
        verdict: VerificationVerdict = "REPAIR" if repair_budget_remaining > 0 else "HALT"
        return SemanticVerificationOutcome(
            verdict=verdict,
            reasons=(f"mechanical checks failed: {list(failed_mandatory)}",),
            mechanical_passed=False,
            failed_mandatory_checks=failed_mandatory,
        )

    if not semantic_verifier_enabled or step_contract is None:
        return SemanticVerificationOutcome(verdict="PASS", reasons=(), mechanical_passed=True)

    from backend.aix_core.runtime.executor.required_effects import effect_kinds, effects_for_step

    mutation_effects = effect_kinds(effects_for_step(step_contract)) - {
        "PRESERVE_PUBLIC_INTERFACE",
        "RUN_DECLARED_VALIDATION",
    }
    latest_diff = result.diff_text or ""
    prior_parts = [str(p).strip() for p in (prior_attempt_diffs or ()) if str(p or "").strip()]
    cumulative_parts = [p for p in prior_parts if p]
    if latest_diff.strip():
        cumulative_parts.append(latest_diff.strip())
    cumulative_diff = "\n".join(cumulative_parts)
    if mutation_effects.issubset({"LITERAL_REPLACE"}) and _literal_replacements_proved(
        step_contract=step_contract,
        diff_text=cumulative_diff or latest_diff,
    ) and _scoped_literal_contract_is_decidable(step_contract=step_contract, result=result):
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("exact literal replacement independently proved by repository diff",),
            mechanical_passed=True,
            semantic_checked=False,
        )
    if _governed_action_step_is_decidable(step_contract=step_contract, result=result):
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("governed filesystem action satisfied by deterministic execution evidence",),
            mechanical_passed=True,
            semantic_checked=False,
        )
    if _new_file_creation_is_decidable(step_contract=step_contract, result=result):
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("new file creation satisfied by deterministic execution evidence",),
            mechanical_passed=True,
            semantic_checked=False,
        )
    if _deterministic_acceptance_proved(step_contract=step_contract, result=result):
        return SemanticVerificationOutcome(
            verdict="PASS",
            reasons=("all acceptance criteria deterministically proved by mechanical evidence",),
            mechanical_passed=True,
            semantic_checked=False,
        )

    model_verdict, reasons, criteria_results = await _ask_verifier_model(
        step_contract=step_contract,
        diff_text=latest_diff,
        cumulative_diff=cumulative_diff,
        tenant_id=tenant_id,
        actor_id=actor_id,
        authorization_header=authorization_header,
        timeout_seconds=timeout_seconds,
    )
    if model_verdict is None:
        # Transport/token-cap failures are not evidence the step is wrong. Mechanical
        # already passed; looping REPAIR on PROMPT_TOKEN_CAP_EXCEEDED never converges
        # (MPE-25 live CronJob: four SUCCEEDED attempts then HALT).
        joined = " ".join(reasons)
        if "PROMPT_TOKEN_CAP_EXCEEDED" in joined or "INTEGRATION.MODEL_INGRESS_REJECTED" in joined:
            return SemanticVerificationOutcome(
                verdict="PASS",
                reasons=reasons + ("semantic verifier skipped: prompt cap; mechanical passed",),
                mechanical_passed=True,
                semantic_checked=False,
            )
        # Fail closed: a verifier that could not run is not evidence of success. Never
        # silently fall through to PASS just because the mechanical layer was clean.
        verdict = "REPAIR" if repair_budget_remaining > 0 else "HALT"
        return SemanticVerificationOutcome(
            verdict=verdict, reasons=reasons, mechanical_passed=True, semantic_checked=False,
        )

    if model_verdict == "REPAIR" and repair_budget_remaining <= 0:
        model_verdict = "HALT"

    # PART 3 — Semantic verifier demotion: a REPAIR/HALT verdict must cite at least
    # one concrete failed acceptance criterion. Vague dissatisfaction ("not meaningful
    # enough", "needs more changes") without a specific criterion contradiction is
    # demoted to PASS when mechanical checks already passed.
    if model_verdict in ("REPAIR", "HALT") and criteria_results:
        concrete_failures = [
            cr for cr in criteria_results
            if isinstance(cr, dict)
            and str(cr.get("criterion", "")).strip()
            and (
                str(cr.get("status", "")).casefold() in ("fail", "failed", "not_met")
                or cr.get("satisfied") is False
            )
        ]
        if not concrete_failures:
            model_verdict = "PASS"
            reasons = reasons + (
                "semantic verifier demoted: no concrete criterion failure identified; "
                "mechanical checks passed",
            )

    # Mechanical/structural checks and focused tests are mandatory evidence, but
    # they cannot rewrite an explicit independent semantic REPAIR/REPLAN/HALT into
    # PASS. Doing so made the verifier advisory and allowed objective-level
    # acceptance failures to complete despite a negative verdict.
    return SemanticVerificationOutcome(
        verdict=model_verdict, reasons=reasons, mechanical_passed=True,
        semantic_checked=True, criteria_results=criteria_results,
    )


__all__ = ["SemanticVerificationOutcome", "verify_step"]
