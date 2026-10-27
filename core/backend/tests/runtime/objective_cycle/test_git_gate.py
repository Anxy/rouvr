"""AIX-E2E-CYCLE-001G-W08 git/proposal/PR gate tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.git_gate import (
    GitGateError,
    GitProposalGate,
    TreeStateV1,
)


def _tree(**overrides: object) -> TreeStateV1:
    base = dict(
        clean_before_change=True,
        diff="--- a/f.py\n+++ b/f.py\n+print('x')\n",
        target_branch="feature/step-1",
        commit_message="apply step change (attempt:attempt-1)",
    )
    base.update(overrides)
    return TreeStateV1(**base)


class TestBuildProposal:
    def test_valid_proposal(self) -> None:
        gate = GitProposalGate()
        proposal = gate.build_proposal(attempt_id="attempt-1", tree_state=_tree())
        assert proposal.requires_human_promotion is True
        assert proposal.diff_digest

    def test_dirty_tree_before_change_rejected(self) -> None:
        gate = GitProposalGate()
        with pytest.raises(GitGateError):
            gate.build_proposal(attempt_id="attempt-1", tree_state=_tree(clean_before_change=False))

    def test_empty_diff_rejected(self) -> None:
        gate = GitProposalGate()
        with pytest.raises(GitGateError):
            gate.build_proposal(attempt_id="attempt-1", tree_state=_tree(diff=""))

    def test_commit_message_missing_attempt_reference_rejected(self) -> None:
        gate = GitProposalGate()
        with pytest.raises(GitGateError):
            gate.build_proposal(attempt_id="attempt-1", tree_state=_tree(commit_message="fix bug"))

    def test_commit_message_referencing_a_different_attempt_rejected(self) -> None:
        gate = GitProposalGate()
        with pytest.raises(GitGateError):
            gate.build_proposal(
                attempt_id="attempt-1",
                tree_state=_tree(commit_message="apply change (attempt:attempt-999)"),
            )

    def test_proposal_never_skips_human_promotion(self) -> None:
        # No constructor argument exists for the caller to set this False.
        gate = GitProposalGate()
        proposal = gate.build_proposal(attempt_id="attempt-1", tree_state=_tree())
        assert proposal.requires_human_promotion is True

    def test_digest_is_deterministic_for_same_diff(self) -> None:
        gate = GitProposalGate()
        first = gate.build_proposal(attempt_id="attempt-1", tree_state=_tree())
        second = gate.build_proposal(attempt_id="attempt-1", tree_state=_tree())
        assert first.diff_digest == second.diff_digest


class TestProtectedBranch:
    @pytest.mark.parametrize("branch", ["main", "master", "MAIN", "production", "release"])
    def test_protected_branches_detected(self, branch: str) -> None:
        assert GitProposalGate().is_protected_branch(branch) is True

    def test_feature_branch_not_protected(self) -> None:
        assert GitProposalGate().is_protected_branch("feature/step-1") is False
