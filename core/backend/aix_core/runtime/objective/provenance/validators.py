"""Pure provenance validators. No I/O, no model, no Explore, no relevance/necessity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from backend.aix_core.runtime.objective.contracts import FORBIDDEN_EFFECTS, RequiredEffectKind
from backend.aix_core.runtime.objective.provenance.contracts import (
    ApprovalBinding,
    ApprovalPolicy,
    AttemptEvidence,
    ClaimKind,
    ExplorationEvidence,
    ExplorationNeed,
    GovernanceRevision,
    PlanDecision,
    PlanRevision,
    PlanTarget,
    UnderstandingClaim,
    UnderstandingRevision,
    UserIntent,
    UserTurn,
)

USER_DERIVED_KINDS: frozenset[str] = frozenset(
    {"OUTCOME", "SCOPE_IN", "SCOPE_OUT", "AMBIGUITY"}
)
_HOW_PHRASES: tuple[str, ...] = (
    "should modify",
    "should change",
    "we should modify",
    "implement steps",
    "target_scope",
    "implementation architecture",
)
_PATH_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".php",
    ".ts",
    ".tsx",
    ".js",
    ".sql",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".html",
    ".css",
)
FORBIDDEN_EXPLORE_SOURCES: frozenset[str] = frozenset(
    {
        "discovery.py",
        "run_repository_discovery",
        "run_focused_file_discovery",
        "glob_fallback",
        "seed_unbound_topic_reads",
        "_seed_unbound_topic_reads",
    }
)


@dataclass(frozen=True)
class RuleResult:
    ok: bool
    rule_id: str
    detail: str = ""
    decision: PlanDecision = "REJECT"

    @staticmethod
    def pass_(rule_id: str, *, decision: PlanDecision = "ACCEPT") -> RuleResult:
        return RuleResult(ok=True, rule_id=rule_id, decision=decision)

    @staticmethod
    def fail(
        rule_id: str,
        detail: str,
        *,
        decision: PlanDecision = "REJECT",
    ) -> RuleResult:
        return RuleResult(ok=False, rule_id=rule_id, detail=detail, decision=decision)


def _norm_text(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _looks_like_path(text: str) -> bool:
    raw = str(text or "").strip().replace("\\", "/")
    if not raw:
        return False
    lowered = raw.casefold()
    if "/" in raw:
        return True
    return any(lowered.endswith(sfx) for sfx in _PATH_SUFFIXES)


def _user_corpus(intent: UserIntent, turns: Iterable[UserTurn]) -> str:
    parts = [intent.text, *intent.definition_of_done]
    for turn in turns:
        parts.append(turn.text)
    return "\n".join(parts)


def normalize_rel_path(raw: str) -> str | None:
    """POSIX relative path; reject backslash, empty escape via '..'."""
    original = str(raw or "")
    if "\\" in original:
        return None
    text = original.replace("\\", "/").strip()
    if not text or text.startswith("/"):
        return None
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) if parts else None


def create_root_contains(*, target: str, root: str) -> bool:
    """Path-component containment. String prefix is insufficient (P4)."""
    t = normalize_rel_path(target)
    r = normalize_rel_path(root.rstrip("/"))
    if t is None or r is None:
        return False
    t_parts = PurePosixPath(t).parts
    r_parts = PurePosixPath(r).parts
    if len(t_parts) < len(r_parts):
        return False
    return t_parts[: len(r_parts)] == r_parts


def _claim_by_id(
    revision: UnderstandingRevision,
    parent: UnderstandingRevision | None,
    claim_id: str,
) -> UnderstandingClaim | None:
    for claim in revision.claims:
        if claim.id == claim_id:
            return claim
    if parent is not None:
        for claim in parent.claims:
            if claim.id == claim_id:
                return claim
    return None


def _active(claims: Iterable[UnderstandingClaim], kind: ClaimKind | None = None) -> tuple[UnderstandingClaim, ...]:
    out: list[UnderstandingClaim] = []
    for claim in claims:
        if claim.status != "ACTIVE":
            continue
        if kind is not None and claim.kind != kind:
            continue
        out.append(claim)
    return tuple(out)


def validate_understanding_revision(
    *,
    intent: UserIntent,
    revision: UnderstandingRevision,
    parent: UnderstandingRevision | None = None,
    turns: tuple[UserTurn, ...] = (),
    governance: GovernanceRevision | None = None,
) -> RuleResult:
    if revision.intent_id != intent.id:
        return RuleResult.fail("U1", "understanding.intent_id does not match UserIntent.id")
    corpus = _user_corpus(intent, turns)
    corpus_fold = _norm_text(corpus)
    create_roots = tuple(governance.create_root) if governance is not None else ()

    for claim in revision.claims:
        kinds = {c.kind for c in claim.cites}
        if claim.kind in USER_DERIVED_KINDS:
            if not kinds or kinds - {"INTENT_SPAN", "USER_TURN"}:
                return RuleResult.fail(
                    "U1",
                    f"{claim.kind} must cite INTENT_SPAN or USER_TURN only",
                )
            if claim.standing != "ASSERTED":
                return RuleResult.fail("U1", f"{claim.kind} standing must be ASSERTED")
        if claim.kind == "SCOPE_IN" and _looks_like_path(claim.text):
            if _norm_text(claim.text) not in corpus_fold and claim.text not in corpus:
                return RuleResult.fail(
                    "U2",
                    f"SCOPE_IN path {claim.text!r} is not in Intent/UserTurn text",
                )
        if claim.kind == "SCOPE_IN":
            for root in create_roots:
                nroot = normalize_rel_path(root.rstrip("/")) or root.rstrip("/")
                if nroot and nroot.casefold() in _norm_text(claim.text):
                    return RuleResult.fail(
                        "U3",
                        "Governance create_root must not appear as SCOPE_IN",
                    )
        if claim.kind in {"SITUATION", "CAUSE"}:
            if kinds != {"EVIDENCE"}:
                return RuleResult.fail("U4", f"{claim.kind} must cite EVIDENCE only")
            if claim.standing != "HYPOTHESIZED":
                return RuleResult.fail("U4", f"{claim.kind} standing must be HYPOTHESIZED")
        folded = _norm_text(claim.text)
        if any(phrase in folded for phrase in _HOW_PHRASES):
            return RuleResult.fail("U5", "Understanding claim contains HOW language")
        if claim.kind == "MISSING_INFO":
            if not claim.cites or any(c.kind != "CLAIM" for c in claim.cites):
                return RuleResult.fail("U8", "MISSING_INFO must cite CiteKind=CLAIM")
            blocked = _claim_by_id(revision, parent, claim.cites[0].ref)
            if blocked is None or blocked.status != "ACTIVE":
                return RuleResult.fail(
                    "U8",
                    "MISSING_INFO CLAIM ref must be an ACTIVE claim id",
                )

    if parent is not None:
        child_by_id = {c.id: c for c in revision.claims}
        for prior in parent.claims:
            if prior.status != "ACTIVE" or prior.kind not in USER_DERIVED_KINDS:
                continue
            child = child_by_id.get(prior.id)
            if child is None:
                return RuleResult.fail(
                    "U7",
                    f"ACTIVE {prior.kind} {prior.id} omitted without SUPERSEDED",
                )
            if child.status not in {"ACTIVE", "SUPERSEDED"}:
                return RuleResult.fail("U7", f"claim {prior.id} must be ACTIVE or SUPERSEDED")

    if _active(revision.claims, "AMBIGUITY"):
        return RuleResult.fail(
            "U6",
            "ACTIVE scope AMBIGUITY blocks plan accept",
            decision="NEED_CLARIFICATION",
        )
    return RuleResult.pass_("U1")


def validate_need(
    *,
    need: ExplorationNeed,
    understanding: UnderstandingRevision,
) -> RuleResult:
    missing = [
        c
        for c in understanding.claims
        if c.id in need.claim_ids and c.kind == "MISSING_INFO" and c.status == "ACTIVE"
    ]
    if not missing:
        return RuleResult.fail("N1", "Need.claim_ids must include an ACTIVE MISSING_INFO")
    for claim in missing:
        if not claim.cites or any(cite.kind != "CLAIM" for cite in claim.cites):
            return RuleResult.fail("N1", "MISSING_INFO on the need must use CiteKind=CLAIM")
    if need.source not in {"repository", "cycle_workspace"}:
        return RuleResult.fail("N2", "invalid need source")
    if need.result_type not in {
        "implementation_locations",
        "file_content",
        "symbol",
        "related_tests",
        "related_routes",
        "existence",
    }:
        return RuleResult.fail("N2", "invalid need result_type")
    return RuleResult.pass_("N1")


def validate_need_evidence(
    *,
    need: ExplorationNeed,
    evidence: tuple[ExplorationEvidence, ...],
) -> RuleResult:
    for item in evidence:
        if item.need_id != need.id:
            return RuleResult.fail("N4", f"evidence {item.id} need_id does not match")
    if len(evidence) > need.budget_files:
        return RuleResult.fail(
            "N3",
            f"evidence count {len(evidence)} exceeds budget_files {need.budget_files}",
        )
    return RuleResult.pass_("N3")


def validate_explore_gate_sources(sources: tuple[str, ...]) -> RuleResult:
    """N5: census/glob/discovery must not be the explore gate."""
    for source in sources:
        name = str(source or "")
        if name in FORBIDDEN_EXPLORE_SOURCES or name.endswith("discovery.py"):
            return RuleResult.fail("N5", f"forbidden explore source {name}")
    return RuleResult.pass_("N5")


def _existing_chain(
    *,
    target: PlanTarget,
    intent: UserIntent,
    understanding: UnderstandingRevision,
    needs: tuple[ExplorationNeed, ...],
    evidence: tuple[ExplorationEvidence, ...],
) -> RuleResult:
    if not target.evidence_id:
        return RuleResult.fail(
            "P1",
            "EXISTING target must cite evidence_id",
            decision="NEED_EVIDENCE",
        )
    item = next((e for e in evidence if e.id == target.evidence_id), None)
    if item is None:
        return RuleResult.fail(
            "P1",
            "cited evidence_id is not in the revision evidence set",
            decision="NEED_EVIDENCE",
        )
    need = next((n for n in needs if n.id == item.need_id), None)
    if need is None:
        return RuleResult.fail("P1", "evidence.need_id does not resolve")
    missing = [
        c
        for c in understanding.claims
        if c.id in need.claim_ids and c.kind == "MISSING_INFO"
    ]
    if not missing:
        return RuleResult.fail(
            "P1",
            "need does not originate from MISSING_INFO",
            decision="NEED_EVIDENCE",
        )
    if understanding.intent_id != intent.id:
        return RuleResult.fail("P1", "understanding does not cite this UserIntent")
    if item.location != target.path:
        return RuleResult.fail(
            "P2",
            "EXISTING target.path must equal evidence.location exactly",
            decision="NEED_EVIDENCE",
        )
    return RuleResult.pass_("P1")


def validate_plan_target(
    *,
    target: PlanTarget,
    intent: UserIntent,
    understanding: UnderstandingRevision,
    needs: tuple[ExplorationNeed, ...],
    evidence: tuple[ExplorationEvidence, ...],
    governance: GovernanceRevision,
) -> RuleResult:
    if target.kind not in {"EXISTING", "CREATE"}:
        return RuleResult.fail("P12", "TARGET_DERIVED is forbidden")
    if not target.outcome_claim_ids:
        return RuleResult.fail("P6", "target must cite ≥1 OUTCOME claim")
    outcomes = {c.id for c in _active(understanding.claims, "OUTCOME")}
    if not set(target.outcome_claim_ids) <= outcomes:
        return RuleResult.fail("P6", "outcome_claim_ids must be ACTIVE OUTCOME claims")
    if target.effect in FORBIDDEN_EFFECTS or target.effect in governance.forbidden_effects:
        return RuleResult.fail("P9", f"effect {target.effect} is forbidden")
    allowed_kinds: tuple[str, ...] = (
        "GROUP_METHODS",
        "MOVE_METHODS",
        "EXTRACT_HELPER",
        "EXTRACT_TO_FILES",
        "STRIP_UNUSED_IMPORTS",
        "LITERAL_REPLACE",
        "PRESERVE_PUBLIC_INTERFACE",
        "RUN_DECLARED_VALIDATION",
    )
    if target.effect not in allowed_kinds:
        return RuleResult.fail("P9", "effect must be RequiredEffectKind")

    if target.kind == "EXISTING":
        chain = _existing_chain(
            target=target,
            intent=intent,
            understanding=understanding,
            needs=needs,
            evidence=evidence,
        )
        if not chain.ok:
            return chain
    else:
        if target.create_capability is None or target.create_root is None:
            return RuleResult.fail("P4", "CREATE target requires capability and create_root")
        if target.create_capability not in governance.create_capability:
            return RuleResult.fail("P4", "create_capability not granted")
        if target.create_capability not in {"CREATE_FILE", "CREATE_DIRECTORY"}:
            return RuleResult.fail("P4", "CREATE_PROJECT is not a v1 capability")
        if not any(
            create_root_contains(target=target.path, root=root)
            and normalize_rel_path(target.create_root.rstrip("/"))
            == normalize_rel_path(root.rstrip("/"))
            for root in governance.create_root
        ):
            return RuleResult.fail(
                "P4",
                "CREATE path is not inside a normalized Governance create_root",
            )
        if not create_root_contains(target=target.path, root=target.create_root):
            return RuleResult.fail("P4", "CREATE path not contained in stated create_root")
        for claim in _active(understanding.claims, "SCOPE_IN"):
            cite_kinds = {c.kind for c in claim.cites}
            minted = bool(cite_kinds - {"INTENT_SPAN", "USER_TURN"})
            if minted and _looks_like_path(claim.text):
                return RuleResult.fail("P5", "CREATE must not mint SCOPE_IN")

    if understanding.scope_mode == "BOUNDED":
        named_in = [
            c.text.strip().replace("\\", "/").rstrip("/")
            for c in _active(understanding.claims, "SCOPE_IN")
            if _looks_like_path(c.text)
        ]
        target_path = target.path.strip().replace("\\", "/").rstrip("/")
        if named_in and target_path not in named_in:
            allowed = False
            if target.kind == "CREATE":
                # Bare CREATE mentions (index.html) become SCOPE_IN while PlanTargets
                # are create_root descendants (proposed/erp-system/index.html). U3:
                # create_root paths are not SCOPE_IN, so exact-match P7 is too strict.
                under_root = any(
                    create_root_contains(target=target_path, root=root)
                    for root in governance.create_root
                )
                target_base = target_path.rsplit("/", 1)[-1]
                named_bases = {p.rsplit("/", 1)[-1] for p in named_in}
                if under_root and target_base and target_base in named_bases:
                    allowed = True
                elif under_root and any(
                    target_path == p or target_path.startswith(p + "/") for p in named_in
                ):
                    allowed = True
            elif target.kind == "EXISTING":
                # Grounded ExplorationEvidence is authoritative for EXISTING targets
                # (admission allowed_files may exceed user-named SCOPE_IN spans).
                if target.evidence_id and any(
                    str(getattr(item, "id", "") or "") == str(target.evidence_id or "")
                    for item in evidence
                ):
                    allowed = True
                elif any(
                    target_path == p
                    or target_path.startswith(p + "/")
                    or p.startswith(target_path + "/")
                    or target_path.endswith("/" + p.rsplit("/", 1)[-1])
                    for p in named_in
                ):
                    allowed = True
            if not allowed:
                return RuleResult.fail(
                    "P7",
                    "BOUNDED target exceeds user-named SCOPE_IN paths",
                )
        for out in _active(understanding.claims, "SCOPE_OUT"):
            if _looks_like_path(out.text):
                if out.text.strip() == target.path or create_root_contains(
                    target=target.path, root=out.text
                ):
                    return RuleResult.fail("P7", "target violates SCOPE_OUT")
            elif target.path in out.text:
                return RuleResult.fail("P7", "target violates SCOPE_OUT")
    return RuleResult.pass_("P8")


def validate_plan_revision(
    *,
    plan: PlanRevision,
    intent: UserIntent,
    understanding: UnderstandingRevision,
    needs: tuple[ExplorationNeed, ...],
    evidence: tuple[ExplorationEvidence, ...],
    governance: GovernanceRevision,
    transformations: tuple[dict[str, object], ...] = (),
    turns: tuple[UserTurn, ...] = (),
    parent: UnderstandingRevision | None = None,
) -> RuleResult:
    amb = validate_understanding_revision(
        intent=intent,
        revision=understanding,
        parent=parent,
        turns=turns,
        governance=governance,
    )
    if not amb.ok:
        return amb
    if plan.intent_id != intent.id:
        return RuleResult.fail("P1", "plan.intent_id mismatch")
    if plan.understanding_revision_id != understanding.id:
        return RuleResult.fail("P1", "plan.understanding_revision_id mismatch")
    if plan.governance_revision_id != governance.id:
        return RuleResult.fail("P1", "plan.governance_revision_id mismatch")
    cited = {t.evidence_id for t in plan.targets if t.evidence_id}
    # P3: uncited evidence is unused — not a failure
    _ = cited
    for trans in transformations:
        if str(trans.get("kind") or "") == "SYSTEM_ENRICHED":
            return RuleResult.fail("P10", "SYSTEM_ENRICHED transformations are forbidden")
    allowed_paths = {t.path for t in plan.targets}
    for step in plan.steps:
        extra = [p for p in step.target_scope if p not in allowed_paths]
        if extra:
            return RuleResult.fail("P11", f"step target_scope not ⊆ PlanTargets: {extra}")
    for target in plan.targets:
        result = validate_plan_target(
            target=target,
            intent=intent,
            understanding=understanding,
            needs=needs,
            evidence=evidence,
            governance=governance,
        )
        if not result.ok:
            return result
    return RuleResult.pass_("P8")


def validate_approval_for_execute(
    *,
    plan: PlanRevision,
    governance: GovernanceRevision,
    approval: ApprovalBinding | None,
    auto_replan: bool = False,
) -> RuleResult:
    policy: ApprovalPolicy = governance.approval_policy
    if policy == "NEVER":
        return RuleResult.pass_("A5")
    if approval is None:
        if auto_replan:
            return RuleResult.fail(
                "A6",
                "auto-replan must still run approval policy for this revision",
                decision="NEED_APPROVAL",
            )
        if policy in {"EVERY_REPLAN", "ALWAYS"}:
            return RuleResult.fail(
                "A3",
                "approval_ref required for this revision",
                decision="NEED_APPROVAL",
            )
        return RuleResult.fail(
            "A4",
            "approval_ref required for this revision",
            decision="NEED_APPROVAL",
        )
    if approval.plan_id != plan.plan_id:
        if auto_replan:
            rule_id = "A6"
        elif plan.revision >= 2:
            rule_id = "A2"
        else:
            rule_id = "A1"
        return RuleResult.fail(
            rule_id,
            "approval binds exactly one plan_id; it cannot authorize another revision",
            decision="NEED_APPROVAL",
        )
    if plan.approval_ref is not None and plan.approval_ref != approval.ref:
        return RuleResult.fail(
            "A1",
            "approval_ref must name this plan's approval",
            decision="NEED_APPROVAL",
        )
    return RuleResult.pass_("A1")


def validate_execute_diff(*, changed_files: tuple[str, ...], plan: PlanRevision) -> RuleResult:
    allowed = {t.path for t in plan.targets}
    extra = [p for p in changed_files if p not in allowed]
    if extra:
        return RuleResult.fail("E1", f"diff paths not ⊆ PlanTargets: {extra}")
    return RuleResult.pass_("E1")


def validate_reunderstand(
    *,
    previous_understanding_id: str,
    new_understanding: UnderstandingRevision,
) -> RuleResult:
    if new_understanding.id == previous_understanding_id:
        return RuleResult.fail("E2", "REUNDERSTAND must emit a new UnderstandingRevision")
    for claim in _active(new_understanding.claims, "SCOPE_IN"):
        kinds = {c.kind for c in claim.cites}
        if kinds - {"INTENT_SPAN", "USER_TURN"}:
            return RuleResult.fail("E2", "reunderstand SCOPE_IN must not keep facet/heuristic cites")
    return RuleResult.pass_("E2")


def validate_replan_revision(*, plan: PlanRevision) -> RuleResult:
    if not plan.intent_id or not plan.understanding_revision_id or not plan.governance_revision_id:
        return RuleResult.fail("E3", "Plan vN missing required revision refs")
    if plan.revision < 2:
        return RuleResult.fail("E3", "replan must increment revision")
    return RuleResult.pass_("E3")


def validate_target_kind(kind: str) -> RuleResult:
    if kind not in {"EXISTING", "CREATE"}:
        return RuleResult.fail("P12", "TARGET_DERIVED is forbidden")
    return RuleResult.pass_("P12")


def assert_effect_is_required_effect_kind(value: object) -> RuleResult:
    allowed: tuple[str, ...] = (
        "GROUP_METHODS",
        "MOVE_METHODS",
        "EXTRACT_HELPER",
        "EXTRACT_TO_FILES",
        "STRIP_UNUSED_IMPORTS",
        "LITERAL_REPLACE",
        "PRESERVE_PUBLIC_INTERFACE",
        "RUN_DECLARED_VALIDATION",
    )
    if value not in allowed:
        return RuleResult.fail("P9", f"{value!r} is not RequiredEffectKind")
    return RuleResult.pass_("P9")


# Silence unused import if type checkers want RequiredEffectKind used at runtime.
_ = RequiredEffectKind
