"""F3-P3 Context assembly — wraps live step_context_capsule (no experimental capsule)."""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.context.memory import ContextBundle, MemoryClass
from backend.aix_core.intelligence.context.step_context_capsule import build_step_context_capsule
from backend.aix_core.runtime.executor.step_prompt_continuity import build_continuity_prompt_section
from backend.aix_core.runtime.objective.contracts import StepContextCapsuleV1, UnderstandingEvidenceV1


class ContextAssemblyPipeline:
    """Assemble a ContextBundle from live capsule + continuity helpers."""

    def assemble(
        self,
        *,
        step_id: str,
        understanding: UnderstandingEvidenceV1 | None = None,
        target_scope: tuple[str, ...] = (),
        invariants: tuple[str, ...] = (),
        relevant_previous_failure: str | None = None,
        intent_id: str | None = None,
        intent_text: str | None = None,
        cited_claim_ids: tuple[str, ...] = (),
        cited_evidence_ids: tuple[str, ...] = (),
        plan_target_paths: tuple[str, ...] = (),
        plan_id: str | None = None,
        step_contract: Any = None,
    ) -> tuple[ContextBundle, StepContextCapsuleV1]:
        capsule = build_step_context_capsule(
            step_id=step_id,
            understanding=understanding,
            target_scope=target_scope,
            invariants=invariants,
            relevant_previous_failure=relevant_previous_failure,
            intent_id=intent_id,
            intent_text=intent_text,
            cited_claim_ids=cited_claim_ids,
            cited_evidence_ids=cited_evidence_ids,
            plan_target_paths=plan_target_paths,
            plan_id=plan_id,
        )
        continuity = build_continuity_prompt_section(step_contract)
        tokens = tuple(continuity.actionable_tokens) if continuity is not None else ()
        intent = str(intent_text or getattr(capsule, "intent_text", None) or "")
        files = tuple(capsule.relevant_files or capsule.plan_target_paths or target_scope or ())
        symbols = tuple(capsule.relevant_symbols or ())
        evidence = tuple(capsule.relevant_evidence_refs or cited_evidence_ids or ())
        guidelines = tuple(capsule.invariants or invariants or ())
        estimate = max(
            1,
            (len(intent) + sum(len(f) for f in files) + sum(len(t) for t in tokens)) // 4,
        )
        classes: list[MemoryClass] = [MemoryClass.WORKING]
        if evidence or symbols:
            classes.append(MemoryClass.SEMANTIC)
        if relevant_previous_failure:
            classes.append(MemoryClass.EPISODIC)
        if guidelines:
            classes.append(MemoryClass.PROCEDURAL)
        if intent_id or intent:
            classes.append(MemoryClass.DURABLE_PROVENANCE)

        bundle = ContextBundle(
            step_id=step_id,
            intent_text=intent,
            target_files=files,
            symbol_definitions=symbols,
            previous_failure=relevant_previous_failure or capsule.relevant_previous_failure,
            validation_guidelines=guidelines,
            continuity_tokens=tokens,
            evidence_refs=evidence,
            memory_classes=tuple(dict.fromkeys(classes)),
            token_count_estimate=estimate,
        )
        return bundle, capsule


__all__ = ["ContextAssemblyPipeline"]
