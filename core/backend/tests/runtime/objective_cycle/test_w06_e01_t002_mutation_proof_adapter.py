"""AIX-W06-E01-T002: Mutation Proof for Every Target Class.

Verifies:
1. Tracked, untracked, gitignored, and generated file mutations produce valid evidence payloads.
2. Gitignored files modified via patch engine are preserved in diff_stats and applied_paths when git diff misses them.
3. Unified evidence synthesis merges git_diff and applied_patches cleanly.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.mutation_evidence import (
    AppliedMutation,
    MutationEvidence,
    merge_mutation_evidence,
)


def test_gitignored_file_mutation_proof() -> None:
    """Gitignored files (which git diff misses) produce explicit mutation evidence via AppliedMutation."""
    git_diff_result = {"diff": "", "stats": {}}
    applied_patches = [
        AppliedMutation(
            path=".env.local",
            unified_diff="--- .env.local\n+++ .env.local\n@@ -1 +1 @@\n-DEBUG=0\n+DEBUG=1\n",
            lines_added=1,
            lines_removed=1,
            after_content="DEBUG=1\n",
        )
    ]

    evidence = merge_mutation_evidence(
        git_diff_result=git_diff_result,
        applied=applied_patches,
    )

    assert isinstance(evidence, MutationEvidence)
    assert ".env.local" in evidence.applied_paths
    assert ".env.local" in evidence.diff_stats
    assert evidence.diff_stats[".env.local"]["additions"] == 1
    assert "applied_patch" in evidence.sources


def test_unified_tracked_and_untracked_evidence_synthesis() -> None:
    """Combines tracked git diff evidence with untracked/gitignored applied patches into a unified payload."""
    git_diff_result = {
        "diff": "diff --git a/src/main.py b/src/main.py\n--- a/src/main.py\n+++ b/src/main.py\n",
        "stats": {"src/main.py": {"insertions": 3, "deletions": 1}},
    }
    applied_patches = [
        AppliedMutation(
            path="build/generated_config.json",
            unified_diff="--- build/generated_config.json\n+++ build/generated_config.json\n@@ -0,0 +1 @@\n+{}\n",
            lines_added=1,
            lines_removed=0,
        )
    ]

    evidence = merge_mutation_evidence(
        git_diff_result=git_diff_result,
        applied=applied_patches,
    )

    assert "src/main.py" in evidence.diff_stats
    assert "build/generated_config.json" in evidence.diff_stats
    assert "git_diff" in evidence.sources
    assert "applied_patch" in evidence.sources
