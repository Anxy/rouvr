from __future__ import annotations

from typing import Any


def build_transitional_path_registry() -> dict[str, Any]:
    return {
        "schema_version": "aix.transitional_paths.current",
        "classification": "audit",
        "paths": [
            {
                "path_id": "cli_execution_digest_fallback",
                "location": "scripts.wh._execution_digest",
                "authority_classification": "projection",
                "status": "deleted",
                "reason": "obsolete ad hoc digest synthesis was removed because canonical execution_digest projection is now the only supported digest source",
                "metric_anchor": "hyeve_aix_runner_primary_mutation_path_total",
            },
            {
                "path_id": "deprecated_symbol_scoped_edit_primitives",
                "location": "backend.services.devexecutor_mutation_tools_service",
                "authority_classification": "audit",
                "status": "deprecate",
                "reason": "fallback-only mutators are hidden from the advertised tool surface by default, execution is default-off behind explicit feature flags, insert_before_return, insert_after_symbol, and edit_symbol have been removed from orchestration execution",
                "metric_anchor": "hyeve_aix_runner_mutation_fallback_total",
            },
            {
                "path_id": "metadata_based_readiness_projection",
                "location": "removed Sprint 8 v1 runtime shell",
                "authority_classification": "projection",
                "status": "deleted",
                "reason": "v1 runtime shell was removed; readiness and lifecycle authority live only in v2 packet/runtime paths",
                "metric_anchor": "hyeve_aix_review_ready_conversions_total",
            },
            {
                "path_id": "runner_admission_status_derivation",
                "location": "removed Sprint 8 v1 runtime shell",
                "authority_classification": "projection",
                "status": "deleted",
                "reason": "admission derivation was removed with the v1 runtime shell; admission status is owned by v2 contract admission and packet state",
                "metric_anchor": "hyeve_aix_runner_attestation_failures_total",
            },
            {
                "path_id": "dual_semantic_validation_gates",
                "location": "backend.services.devexecutor_patch_engine_service",
                "authority_classification": "projection",
                "status": "keep",
                "reason": "pre-apply LSP and post-apply pyright coexist intentionally during transition",
                "metric_anchor": "hyeve_aix_runner_post_apply_reject_total",
            },
            {
                "path_id": "human_review_confirmation_gate",
                "location": "backend.legacy.orphaned_services.review_contract_enrichment_service.confirm_review_enriched_contract",
                "authority_classification": "source_of_truth",
                "status": "keep",
                "reason": "explicit human confirmation is the canonical promotion gate from review_enriched to ready",
                "metric_anchor": "hyeve_aix_runner_manual_interventions_total",
            },
        ],
    }


__all__ = ["build_transitional_path_registry"]
