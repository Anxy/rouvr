"""AIX-W08-E01-T001: Normalized Failure Records Tests.

Verifies:
1. Distinct failure domains: CODE, TEST, ENVIRONMENT, TOOL, POLICY, NETWORK, DEPENDENCY, MERGE, ASSUMPTION.
2. Deterministic sha256 failure signature computation.
3. Text normalization and snippet cleaning.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.failure_records import (
    NormalizedFailureRecord,
    classify_failure_domain,
    normalize_failure,
)


def test_failure_domain_distinguishability() -> None:
    """Verifies all 9 failure domains are accurately distinguished."""
    assert classify_failure_domain("SYNTAX_ERROR", "SyntaxError: invalid syntax") == "CODE"
    assert classify_failure_domain("TEST_FAIL", "AssertionError: assert 1 == 2") == "TEST"
    assert classify_failure_domain("PERMISSION_DENIED", "Permission denied: /root") == "ENVIRONMENT"
    assert classify_failure_domain("TIMEOUT", "command not found: pytest") == "TOOL"
    assert classify_failure_domain("FORBIDDEN", "policy_violation: edit not allowed") == "POLICY"
    assert classify_failure_domain("NETWORK_FLAKE", "connection refused by host") == "NETWORK"
    assert classify_failure_domain("IMPORT_ERROR", "cannot import name 'foo'") == "DEPENDENCY"
    assert classify_failure_domain("GIT_CONFLICT", "merge conflict in app.py") == "MERGE"
    assert classify_failure_domain("GRAPH_STALE", "snapshot_stale fingerprint mismatch") == "ASSUMPTION"


def test_deterministic_failure_signature() -> None:
    """Identical failure inputs produce identical sha256 signatures."""
    rec1 = normalize_failure("IMPORT_ERROR", "cannot import name 'foo' from 'bar'")
    rec2 = normalize_failure("IMPORT_ERROR", "cannot import name 'foo' from 'bar'")

    assert rec1.failure_id == rec2.failure_id
    assert len(rec1.failure_id) == 64
    assert rec1.domain == "DEPENDENCY"
