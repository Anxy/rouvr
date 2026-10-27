"""AIX-E2E-CYCLE-001G-W08: git/proposal/PR gate.

A step attempt that produces a code change never pushes or merges anything itself. It
produces a `GitProposalV1`: an attributable, reviewable record of what changed and why,
which a human (via WorkHub review, per the architecture doc) promotes into an actual PR.
This module enforces the boundary conditions that make a proposal trustworthy — it does
not shell out to `git` itself; a live executor integration supplies the already-computed
diff/tree-state facts (this matches the bounded-adapter style already used in
`adapters/devexecutor.py`, which does not literally invoke tools either).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

PROTECTED_BRANCHES = frozenset({"main", "master", "production", "release"})

_ATTEMPT_REF_PATTERN = re.compile(r"\battempt[:\-]?[A-Za-z0-9_-]+\b", re.IGNORECASE)


class GitGateError(ValueError):
    """Raised when a proposed change fails a git-gate invariant. Fail closed."""


@dataclass(frozen=True)
class TreeStateV1:
    """Facts about the working tree at proposal time, as reported by the caller."""

    clean_before_change: bool
    diff: str
    target_branch: str
    commit_message: str


@dataclass(frozen=True)
class GitProposalV1:
    """The record a human promotes into a real PR. Never auto-merged, never
    auto-pushed — producing one of these is this module's entire authority."""

    attempt_id: str
    diff: str
    commit_message: str
    target_branch: str
    diff_digest: str
    requires_human_promotion: bool = True


class GitProposalGate:
    def build_proposal(self, *, attempt_id: str, tree_state: TreeStateV1) -> GitProposalV1:
        """Validate `tree_state` and produce a `GitProposalV1`. Raises `GitGateError` on
        any of: a dirty tree before the change started (the step's diff can't be
        attributed if other uncommitted changes were already present — see the
        Non-Happy-Path 'dirty-tree bypass' acceptance criterion), an empty diff, a
        commit message that doesn't reference the attempt, or a target branch that is
        protected without an explicit human-promotion flag (which this method always
        sets True — a caller cannot construct a proposal that skips human promotion)."""
        if not tree_state.clean_before_change:
            raise GitGateError(
                "working tree was not clean before this attempt started; a diff cannot "
                "be safely attributed to this attempt alone (dirty-tree bypass forbidden)"
            )
        if not tree_state.diff or not tree_state.diff.strip():
            raise GitGateError("empty diff cannot become a proposal")
        if not attempt_id or not attempt_id.strip():
            raise GitGateError("attempt_id is required for commit provenance")
        if not _ATTEMPT_REF_PATTERN.search(tree_state.commit_message):
            raise GitGateError(
                f"commit message must reference the attempt (expected a pattern like "
                f"'attempt:{attempt_id}'), got: {tree_state.commit_message!r}"
            )
        if attempt_id not in tree_state.commit_message:
            raise GitGateError(f"commit message references an attempt id other than {attempt_id!r}")

        digest = hashlib.sha256(tree_state.diff.encode("utf-8")).hexdigest()
        return GitProposalV1(
            attempt_id=attempt_id,
            diff=tree_state.diff,
            commit_message=tree_state.commit_message,
            target_branch=tree_state.target_branch,
            diff_digest=digest,
            requires_human_promotion=True,
        )

    def is_protected_branch(self, branch: str) -> bool:
        return branch.strip().lower() in PROTECTED_BRANCHES


__all__ = [
    "PROTECTED_BRANCHES",
    "GitGateError",
    "TreeStateV1",
    "GitProposalV1",
    "GitProposalGate",
]
