"""F3-P4 Context compression — preserve SEM-001 Law A continuity tokens."""

from __future__ import annotations

from backend.aix_core.framework.context.memory import ContextBundle


class ContextCompressor:
    """Token-budget compression that never drops Law A continuity / target refs."""

    @staticmethod
    def compress(bundle: ContextBundle, token_budget: int = 4000) -> ContextBundle:
        budget = max(64, int(token_budget or 4000))
        # Protected: continuity tokens, target files, previous failure, intent head.
        protected_tokens = tuple(bundle.continuity_tokens)
        protected_files = tuple(bundle.target_files)
        intent = bundle.intent_text or ""
        failure = bundle.previous_failure

        # Compress auxiliary symbol tables first.
        symbols = list(bundle.symbol_definitions)
        guidelines = list(bundle.validation_guidelines)
        evidence = list(bundle.evidence_refs)

        def _estimate() -> int:
            parts = [
                intent,
                " ".join(protected_files),
                " ".join(protected_tokens),
                failure or "",
                " ".join(symbols),
                " ".join(guidelines),
                " ".join(evidence),
            ]
            return max(1, sum(len(p) for p in parts) // 4)

        while _estimate() > budget and symbols:
            symbols.pop()
        while _estimate() > budget and evidence:
            evidence.pop()
        while _estimate() > budget and guidelines and len(guidelines) > 1:
            guidelines.pop()
        # Never drop continuity tokens or target files. Truncate intent only as last resort
        # while keeping a non-empty prefix when present.
        if _estimate() > budget and len(intent) > 80:
            # Keep head; never wipe.
            keep_chars = max(80, budget * 2)
            intent = intent[:keep_chars]

        return ContextBundle(
            bundle_id=bundle.bundle_id,
            step_id=bundle.step_id,
            intent_text=intent,
            target_files=protected_files,
            symbol_definitions=tuple(symbols),
            previous_failure=failure,
            validation_guidelines=tuple(guidelines),
            continuity_tokens=protected_tokens,
            evidence_refs=tuple(evidence),
            memory_classes=bundle.memory_classes,
            token_count_estimate=_estimate(),
        )


__all__ = ["ContextCompressor"]
