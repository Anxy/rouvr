"""Decomposer-lite: brief in, verifiable tasks out (S021).

validator: mini-language parse/run plus the five rejection rules.
runner: run gate (pinned touch fails) and parent check.
decompose: one model call turns a raw brief into a single v1.3 candidate.
"""
from rouvr.decompose.decompose import decompose_brief
from rouvr.decompose.runner import check_parent, run_gate
from rouvr.decompose.validator import (
    VerifierParseError,
    parse_verifier,
    run_verifier,
    validate_doc,
    validate_task,
)

__all__ = ["VerifierParseError", "check_parent", "decompose_brief",
           "parse_verifier", "run_gate", "run_verifier",
           "validate_doc", "validate_task"]
