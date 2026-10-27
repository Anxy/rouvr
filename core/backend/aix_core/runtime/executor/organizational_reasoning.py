"""EX4-R4-P11-S001: Organizational Reasoning Engine — read/reason only.

Composes Knowledge (P3), graph edges (P2), semantic retrieval (P9), and
planning briefs (P10). Produces provenance-bearing answers under tenant scope.

Prohibited: silent policy/plan mutation; cross-tenant reads; treating inference
as fact; bypassing Proposal levels. Writes remain Proposal / Learning only.
"""

from __future__ import annotations

from typing import Any, Literal

from backend.aix_core.runtime.executor.capability_registry import InMemoryCapabilityRegistry
from backend.aix_core.runtime.executor.knowledge_graph import (
    InMemoryKnowledgeGraph,
    edge_to_dict,
)
from backend.aix_core.runtime.executor.organizational_knowledge import (
    detect_missing_knowledge,
    query_organizational_knowledge,
    reconcile_cross_project_knowledge,
)
from backend.aix_core.runtime.executor.planning_engine import (
    decompose_plan,
    evaluate_plan_quality,
)
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.executor.semantic_retrieval import search_semantic
from backend.aix_core.runtime.executor.context_builder import InMemoryKnowledgeStore

ReasoningMode = Literal[
    "precedent_search", "conflict_report", "pre_planning_brief", "capability_reuse_search"
]
AnswerKind = Literal["fact", "inference", "recommendation"]

REASONING_MODES = frozenset(
    {"precedent_search", "conflict_report", "pre_planning_brief", "capability_reuse_search"}
)

# Golden scenario ids — used in tests and docs.
GOLDEN_SCENARIOS = (
    {
        "id": "G1",
        "mode": "precedent_search",
        "question": "What precedents exist for tenant-scoped keys?",
    },
    {
        "id": "G2",
        "mode": "conflict_report",
        "question": "Which knowledge conflicts are active for this tenant?",
    },
    {
        "id": "G3",
        "mode": "pre_planning_brief",
        "question": "What do we already know before planning this capability?",
    },
    {
        "id": "G4",
        "mode": "capability_reuse_search",
        "question": "EX5-S10: is there an existing registered capability we could reuse instead of building this again?",
    },
)


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def _answer(
    *,
    kind: AnswerKind,
    statement: str,
    provenance: list[dict[str, Any]],
    confidence: str = "medium",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "statement": statement,
        "provenance": provenance,
        "confidence": confidence,
        # Explicit: inference must never be silently promoted to fact by callers.
        "is_fact": kind == "fact",
    }


def reason_organizationally(
    knowledge: InMemoryKnowledgeStore,
    graph: InMemoryKnowledgeGraph,
    *,
    tenant_id: str,
    mode: str,
    question: str = "",
    capability_id: str | None = None,
    project_id: str | None = None,
    objective: str | None = None,
    required_knowledge_kinds: list[str] | None = None,
    semantic_limit: int = 5,
    registry: InMemoryCapabilityRegistry | None = None,
) -> dict[str, Any]:
    """Answer a cross-cutting organisational question. Never mutates state."""
    tenant_id = _require_tenant(tenant_id)
    if mode not in REASONING_MODES:
        raise ExecutionKernelRepositoryError(f"invalid reasoning mode={mode!r}")
    question = str(question or "").strip()

    answers: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    sources_used: list[str] = []

    if mode == "precedent_search":
        sources_used.extend(["semantic_retrieval", "organizational_knowledge", "cross_project"])
        query = question or objective or "precedent"
        hits = search_semantic(
            knowledge,
            tenant_id=tenant_id,
            query=query,
            limit=semantic_limit,
            capability_id=capability_id,
        )
        for hit in hits:
            answers.append(
                _answer(
                    kind="fact",
                    statement=hit.content,
                    provenance=[
                        {
                            "source": "knowledge",
                            "knowledge_entry_id": hit.knowledge_entry_id,
                            "score": hit.score,
                            "backend": hit.backend,
                        }
                    ],
                    confidence="high" if hit.score >= 0.2 else "medium",
                )
            )
        groups = reconcile_cross_project_knowledge(knowledge, tenant_id=tenant_id)
        for group in groups:
            answers.append(
                _answer(
                    kind="inference",
                    statement=(
                        f"Identical knowledge fingerprint reused across projects "
                        f"{group['project_ids']}: {group['sample_content'][:160]}"
                    ),
                    provenance=[
                        {
                            "source": "cross_project_reconciliation",
                            "fingerprint": group["fingerprint"],
                            "knowledge_entry_ids": group["knowledge_entry_ids"],
                        }
                    ],
                    confidence="medium",
                )
            )
        if not answers:
            answers.append(
                _answer(
                    kind="inference",
                    statement="No precedents found under current tenant scope.",
                    provenance=[{"source": "semantic_retrieval", "hit_count": 0}],
                    confidence="low",
                )
            )
        options.append(
            {
                "action": "raise_proposal",
                "mutation_kind": "add_work_items_same_problem",
                "rationale": "Adopt a precedent only via Proposal rails — not silent write",
            }
        )

    elif mode == "conflict_report":
        sources_used.append("knowledge_graph")
        edges = graph.list_edges(
            tenant_id=tenant_id, edge_type="CONFLICTS_WITH", active_only=True
        )
        for edge in edges:
            conflicts.append(edge_to_dict(edge))
            answers.append(
                _answer(
                    kind="fact",
                    statement=(
                        f"Active CONFLICTS_WITH between {edge.from_id} and {edge.to_id}"
                    ),
                    provenance=[
                        {
                            "source": "knowledge_graph",
                            "edge_id": edge.edge_id,
                            "edge_type": edge.edge_type,
                            "authority_context": edge.authority_context,
                        }
                    ],
                    confidence="high",
                )
            )
        if not conflicts:
            answers.append(
                _answer(
                    kind="fact",
                    statement="No active knowledge conflicts for this tenant.",
                    provenance=[{"source": "knowledge_graph", "edge_type": "CONFLICTS_WITH", "count": 0}],
                    confidence="high",
                )
            )
        else:
            options.append(
                {
                    "action": "resolve_knowledge_conflict",
                    "rationale": "Resolve via governed conflict API — retains history",
                }
            )

    elif mode == "pre_planning_brief":
        sources_used.extend(
            ["organizational_knowledge", "missing_knowledge", "knowledge_graph", "planning_engine"]
        )
        if not capability_id:
            raise ExecutionKernelRepositoryError(
                "capability_id is required for pre_planning_brief"
            )
        known = query_organizational_knowledge(
            knowledge,
            graph,
            tenant_id=tenant_id,
            capability_id=capability_id,
            project_id=project_id,
            minimum_confidence="unknown",
        )
        for entry in known:
            answers.append(
                _answer(
                    kind="fact",
                    statement=entry["content"],
                    provenance=[
                        {
                            "source": "organizational_knowledge",
                            "knowledge_entry_id": entry["knowledge_entry_id"],
                            "confidence": entry["confidence"],
                            "provenance_edges": entry.get("provenance_edges") or [],
                        }
                    ],
                    confidence=entry.get("confidence") or "unknown",
                )
            )
            constraints.append(
                {
                    "type": "current_knowledge",
                    "knowledge_entry_id": entry["knowledge_entry_id"],
                    "knowledge_kind": entry["knowledge_kind"],
                }
            )
        missing = detect_missing_knowledge(
            knowledge,
            tenant_id=tenant_id,
            capability_id=capability_id,
            required_kinds=required_knowledge_kinds or ["architecture", "pattern"],
        )
        if missing.get("missing"):
            answers.append(
                _answer(
                    kind="inference",
                    statement=(
                        f"Knowledge gaps for capability {capability_id}: "
                        f"missing_kinds={missing.get('missing_kinds')}"
                    ),
                    provenance=[{"source": "missing_knowledge", **missing}],
                    confidence="medium",
                )
            )
            options.append(
                {
                    "action": "raise_proposal",
                    "mutation_kind": "add_work_items_same_problem",
                    "rationale": "Fill gaps via planned work — not invented facts",
                }
            )
        active_conflicts = graph.list_edges(
            tenant_id=tenant_id, edge_type="CONFLICTS_WITH", active_only=True
        )
        for edge in active_conflicts:
            conflicts.append(edge_to_dict(edge))
            constraints.append(
                {
                    "type": "unresolved_conflict",
                    "edge_id": edge.edge_id,
                    "from_id": edge.from_id,
                    "to_id": edge.to_id,
                }
            )
        if objective:
            plan = decompose_plan(objective=objective)
            quality = evaluate_plan_quality(plan)
            answers.append(
                _answer(
                    kind="recommendation",
                    statement=(
                        f"Draft plan disposition={plan.get('disposition')} "
                        f"quality_score={quality.get('score')} "
                        f"work_items={len(plan.get('work_items') or [])}"
                    ),
                    provenance=[
                        {
                            "source": "planning_engine",
                            "disposition": plan.get("disposition"),
                            "quality": quality,
                        }
                    ],
                    confidence="medium",
                )
            )
            options.append(
                {
                    "action": "propose_plan_mutation",
                    "mutation_kind": "replan_within_capability",
                    "rationale": "Commit plan only through Proposal rails",
                }
            )

    else:  # capability_reuse_search — EX5-S10: "org reuse" queries
        sources_used.append("capability_registry")
        if registry is None:
            answers.append(
                _answer(
                    kind="inference",
                    statement="No capability registry available to search — reuse cannot be assessed.",
                    provenance=[{"source": "capability_registry", "available": False}],
                    confidence="low",
                )
            )
        else:
            query_text = (question or objective or "").strip().lower()
            query_terms = {t for t in query_text.split() if t}
            candidates = registry.list(tenant_id=tenant_id, active_only=True)
            scored: list[tuple[int, Any]] = []
            for cap in candidates:
                haystack = f"{cap.capability_key} {cap.display_name} {cap.description}".lower()
                overlap = sum(1 for term in query_terms if term in haystack)
                if overlap > 0:
                    scored.append((overlap, cap))
            scored.sort(key=lambda pair: (-pair[0], pair[1].capability_key))
            for overlap, cap in scored[:semantic_limit]:
                answers.append(
                    _answer(
                        # A textual overlap match is a lead, not a certainty that
                        # this capability actually fits — always a recommendation,
                        # never asserted as fact.
                        kind="recommendation",
                        statement=(
                            f"Existing capability {cap.capability_key} v{cap.version} "
                            f"('{cap.display_name}') may satisfy this need instead of building anew."
                        ),
                        provenance=[
                            {
                                "source": "capability_registry",
                                "capability_key": cap.capability_key,
                                "version": cap.version,
                                "term_overlap": overlap,
                                "cost_class": cap.cost_class,
                                "risk_class": cap.risk_class,
                            }
                        ],
                        confidence="medium" if overlap >= 2 else "low",
                    )
                )
            if not scored:
                answers.append(
                    _answer(
                        kind="inference",
                        statement="No registered capability appears reusable for this need — building anew may be warranted.",
                        provenance=[{"source": "capability_registry", "candidates_checked": len(candidates)}],
                        confidence="low",
                    )
                )
            else:
                options.append(
                    {
                        "action": "register_capability_dependency",
                        "rationale": "Adopt a reusable capability via its own binding contract — never silently forked",
                    }
                )

    return {
        "tenant_id": tenant_id,
        "mode": mode,
        "question": question,
        "capability_id": capability_id,
        "project_id": project_id,
        "answers": answers,
        "constraints": constraints,
        "options": options,
        "conflicts": conflicts,
        "sources_used": sources_used,
        # Hard guarantee for this engine:
        "state_mutations": [],
        "silent_policy_change": False,
        "treats_inference_as_fact": False,
        "cross_tenant": False,
    }


def golden_scenarios() -> list[dict[str, str]]:
    return [dict(item) for item in GOLDEN_SCENARIOS]


__all__ = [
    "REASONING_MODES",
    "GOLDEN_SCENARIOS",
    "reason_organizationally",
    "golden_scenarios",
]
