"""P3: execute typed ExplorationNeeds; emit Evidence; build Understanding v2.

Explore executes a Need. It does not invent a Need from the repository.
Search hits are Evidence, not PlanTargets and not SCOPE_IN.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.aix_core.runtime.objective.provenance.contracts import (
    ClaimCite,
    EvidenceProvenance,
    ExplorationEvidence,
    ExplorationNeed,
    GovernanceRevision,
    NeedResultType,
    NeedSource,
    PlanDecision,
    UnderstandingClaim,
    UnderstandingRevision,
    UserIntent,
    UserTurn,
)
from backend.aix_core.runtime.objective.provenance.understanding import (
    UnderstandingV1Result,
    decision_from_understanding,
)
from backend.aix_core.runtime.objective.provenance.validators import (
    USER_DERIVED_KINDS,
    RuleResult,
    validate_explore_gate_sources,
    validate_need,
    validate_need_evidence,
    validate_understanding_revision,
)

_HOW_PHRASES: tuple[str, ...] = (
    "should modify",
    "should change",
    "we should modify",
    "implement steps",
    "target_scope",
    "implementation architecture",
    "therefore we should",
)
_CLASS_HINT_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:"
    r"Controller|Service|Repository|Manager|Handler|Provider|Factory|Helper|"
    r"Job|Command|Middleware|Policy|Seeder|Listener|Subscriber"
    r"))\b"
)
_TOPIC_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "when",
        "refactor",
        "review",
        "fix",
        "update",
        "change",
        "please",
        "only",
        "service",
    }
)
_RESULT_TYPES: frozenset[str] = frozenset(
    {
        "implementation_locations",
        "file_content",
        "symbol",
        "related_tests",
        "related_routes",
        "existence",
    }
)
_SOURCES: frozenset[str] = frozenset({"repository", "cycle_workspace"})


@dataclass(frozen=True)
class ExploreHit:
    """One collector result. Not a PlanTarget."""

    location: str | None
    observation: str
    exists: bool | None = True
    tool: str = "search_files"
    query: str = ""


NeedCollector = Callable[[ExplorationNeed], Sequence[ExploreHit]]


@dataclass(frozen=True)
class ExploreRun:
    needs: tuple[ExplorationNeed, ...]
    evidence: tuple[ExplorationEvidence, ...]
    understanding: UnderstandingRevision
    validation: RuleResult
    decision: PlanDecision
    sources: tuple[str, ...]


def _norm(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _has_how(text: str) -> bool:
    folded = _norm(text)
    return any(phrase in folded for phrase in _HOW_PHRASES)


def _fingerprint(tool: str, query: str, location: str) -> str:
    payload = f"{tool}|{query}|{location}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _cite(kind: str, ref: str) -> ClaimCite:
    return ClaimCite(kind=kind, ref=ref)  # type: ignore[arg-type]


def _claim(
    *,
    kind: str,
    text: str,
    cites: tuple[ClaimCite, ...],
    standing: str = "HYPOTHESIZED",
    claim_id: str | None = None,
    status: str = "ACTIVE",
) -> UnderstandingClaim:
    kwargs: dict[str, object] = {
        "kind": kind,
        "text": text,
        "cites": cites,
        "standing": standing,
        "status": status,
    }
    if claim_id is not None:
        kwargs["id"] = claim_id
    return UnderstandingClaim(**kwargs)  # type: ignore[arg-type]


def class_name_hints(text: str) -> tuple[str, ...]:
    """User-text class hints. Never inspects a repository."""
    return tuple(dict.fromkeys(_CLASS_HINT_RE.findall(str(text or ""))))


def topic_hints(text: str) -> tuple[str, ...]:
    """User-text topic stems. Never inspects a repository and never writes scope."""
    words = [w for w in re.findall(r"[a-z]{3,}", str(text or "").casefold()) if w not in _TOPIC_STOP]
    out: list[str] = []
    seen: set[str] = set()
    for left, right in zip(words, words[1:]):
        stem = f"{left}_{right}"
        if stem in seen or len(stem) < 6:
            continue
        seen.add(stem)
        out.append(stem)
    return tuple(out[:6])


def _result_type_for(intent_text: str, missing_text: str) -> NeedResultType:
    corpus = f"{intent_text}\n{missing_text}".casefold()
    if class_name_hints(intent_text):
        return "symbol"
    if "test" in corpus:
        return "related_tests"
    if "route" in corpus or "endpoint" in corpus:
        return "related_routes"
    if "exist" in corpus:
        return "existence"
    if "content" in corpus or "read " in corpus:
        return "file_content"
    return "implementation_locations"


def _source_for(intent_text: str, missing_text: str) -> NeedSource:
    corpus = f"{intent_text}\n{missing_text}".casefold()
    if "cycle workspace" in corpus or "cycle_workspace" in corpus:
        return "cycle_workspace"
    return "repository"


def _question_for(*, intent_text: str, missing_text: str) -> str:
    """Heuristics may only suggest a query on an existing Need. They never mint scope."""
    classes = class_name_hints(intent_text)
    if classes:
        return " ".join(classes)
    topics = topic_hints(intent_text)
    if topics:
        return " ".join(topics)
    body = str(intent_text or "").strip() or str(missing_text or "").strip()
    return body[:240]


def needs_from_understanding(
    understanding: UnderstandingRevision,
    *,
    intent: UserIntent | None = None,
    budget_files: int = 8,
) -> tuple[ExplorationNeed, ...]:
    """Mint one Need per ACTIVE MISSING_INFO. Do not invent Needs from the repo."""
    if any(c.kind == "AMBIGUITY" and c.status == "ACTIVE" for c in understanding.claims):
        return ()
    intent_text = intent.text if intent is not None else ""
    needs: list[ExplorationNeed] = []
    for claim in understanding.claims:
        if claim.kind != "MISSING_INFO" or claim.status != "ACTIVE":
            continue
        question = _question_for(intent_text=intent_text, missing_text=claim.text)
        needs.append(
            ExplorationNeed(
                objective_id=understanding.objective_id,
                understanding_revision_id=understanding.id,
                claim_ids=(claim.id,),
                purpose=f"Answer MISSING_INFO: {claim.text}",
                question=question,
                source=_source_for(intent_text, claim.text),
                result_type=_result_type_for(intent_text, claim.text),
                budget_files=budget_files,
            )
        )
    return tuple(needs)


def evidence_from_hits(
    need: ExplorationNeed,
    hits: Sequence[ExploreHit],
) -> tuple[ExplorationEvidence, ...]:
    rows: list[ExplorationEvidence] = []
    for hit in hits:
        if len(rows) >= need.budget_files:
            break
        observation = str(hit.observation or "").strip()
        if not observation or _has_how(observation):
            location = str(hit.location or "").strip() or "unknown"
            observation = f"Search for {hit.query or need.question!r} matched {location}"
        if _has_how(observation):
            continue
        location = str(hit.location).replace("\\", "/") if hit.location else None
        query = hit.query or need.question
        tool = hit.tool or "search_files"
        rows.append(
            ExplorationEvidence(
                need_id=need.id,
                objective_id=need.objective_id,
                source=need.source,
                location=location,
                observation=observation,
                exists=hit.exists,
                provenance=EvidenceProvenance(
                    tool=tool,
                    query=query,
                    fingerprint=_fingerprint(tool, query, location or ""),
                ),
            )
        )
    return tuple(rows)


def execute_need(
    need: ExplorationNeed,
    *,
    understanding: UnderstandingRevision,
    collect: NeedCollector,
) -> tuple[ExplorationEvidence, ...]:
    """Execute one typed Need. Collector may not invent a different Need."""
    check = validate_need(need=need, understanding=understanding)
    if not check.ok:
        return ()
    if need.source not in _SOURCES or need.result_type not in _RESULT_TYPES:
        return ()
    hits = tuple(collect(need) or ())
    evidence = evidence_from_hits(need, hits)
    validation = validate_need_evidence(need=need, evidence=evidence)
    if not validation.ok:
        return evidence[: need.budget_files]
    return evidence


def execute_needs(
    needs: tuple[ExplorationNeed, ...],
    *,
    understanding: UnderstandingRevision,
    collect: NeedCollector,
) -> tuple[ExplorationEvidence, ...]:
    out: list[ExplorationEvidence] = []
    for need in needs:
        out.extend(execute_need(need, understanding=understanding, collect=collect))
    return tuple(out)


def _need_answered(need: ExplorationNeed, evidence: tuple[ExplorationEvidence, ...]) -> bool:
    return any(item.need_id == need.id for item in evidence)


def build_understanding_v2(
    *,
    intent: UserIntent,
    parent: UnderstandingRevision,
    evidence: tuple[ExplorationEvidence, ...],
    needs: tuple[ExplorationNeed, ...] = (),
    turns: tuple[UserTurn, ...] = (),
    governance: GovernanceRevision | None = None,
) -> UnderstandingV1Result:
    """Understanding v2 from Evidence: SITUATION and CAUSE only. No SCOPE_IN from hits."""
    if not evidence:
        validation = validate_understanding_revision(
            intent=intent,
            revision=parent,
            parent=None,
            turns=turns,
            governance=governance,
        )
        return UnderstandingV1Result(
            revision=parent,
            validation=validation,
            decision=decision_from_understanding(parent, validation),
        )

    answered_missing: set[str] = set()
    for need in needs:
        if not _need_answered(need, evidence):
            continue
        answered_missing.update(need.claim_ids)

    child: list[UnderstandingClaim] = []
    for prior in parent.claims:
        if prior.kind == "MISSING_INFO" and prior.status == "ACTIVE" and prior.id in answered_missing:
            child.append(prior.model_copy(update={"status": "SUPERSEDED"}))
        else:
            child.append(prior)

    for item in evidence:
        location = item.location or "unlocated observation"
        situation_text = f"Observed {location}: {item.observation}"
        if _has_how(situation_text):
            situation_text = f"Observed {location}"
        child.append(
            _claim(
                kind="SITUATION",
                text=situation_text[:500],
                cites=(_cite("EVIDENCE", item.id),),
                standing="HYPOTHESIZED",
            )
        )

    first = evidence[0]
    cause_text = (
        f"A hypothesized cause or implementation locus is {first.location or 'recorded evidence'} "
        f"for need {first.need_id}"
    )
    if not _has_how(cause_text):
        child.append(
            _claim(
                kind="CAUSE",
                text=cause_text[:500],
                cites=(_cite("EVIDENCE", first.id),),
                standing="HYPOTHESIZED",
            )
        )

    revision = UnderstandingRevision(
        objective_id=intent.objective_id,
        intent_id=intent.id,
        parent_revision_id=parent.id,
        scope_mode=parent.scope_mode,
        claims=tuple(child),
    )
    kinds = {c.kind for c in revision.claims if c.status == "ACTIVE"}
    if "SCOPE_IN" in kinds:
        parent_scope = {
            _norm(c.text)
            for c in parent.claims
            if c.kind == "SCOPE_IN" and c.status in {"ACTIVE", "SUPERSEDED"}
        }
        for claim in revision.claims:
            if claim.kind != "SCOPE_IN" or claim.status != "ACTIVE":
                continue
            if _norm(claim.text) not in parent_scope:
                validation = RuleResult.fail("U2", "Understanding v2 must not mint SCOPE_IN from Evidence")
                return UnderstandingV1Result(revision=revision, validation=validation, decision="REJECT")

    validation = validate_understanding_revision(
        intent=intent,
        revision=revision,
        parent=parent,
        turns=turns,
        governance=governance,
    )
    decision = decision_from_understanding(revision, validation)
    return UnderstandingV1Result(revision=revision, validation=validation, decision=decision)


def run_explore(
    *,
    intent: UserIntent,
    understanding: UnderstandingRevision,
    collect: NeedCollector,
    turns: tuple[UserTurn, ...] = (),
    governance: GovernanceRevision | None = None,
    sources: tuple[str, ...] = ("search_files",),
    budget_files: int = 8,
) -> ExploreRun:
    """Need → Explore → Evidence → Understanding v2. No repository census."""
    gate = validate_explore_gate_sources(sources)
    if not gate.ok:
        return ExploreRun(
            needs=(),
            evidence=(),
            understanding=understanding,
            validation=gate,
            decision="REJECT",
            sources=sources,
        )
    needs = needs_from_understanding(understanding, intent=intent, budget_files=budget_files)
    checked: list[ExplorationNeed] = []
    for need in needs:
        if validate_need(need=need, understanding=understanding).ok:
            checked.append(need)
    needs_ok = tuple(checked)
    evidence = execute_needs(needs_ok, understanding=understanding, collect=collect)
    result = build_understanding_v2(
        intent=intent,
        parent=understanding,
        evidence=evidence,
        needs=needs_ok,
        turns=turns,
        governance=governance,
    )
    return ExploreRun(
        needs=needs_ok,
        evidence=evidence,
        understanding=result.revision,
        validation=result.validation,
        decision=result.decision,
        sources=sources,
    )
