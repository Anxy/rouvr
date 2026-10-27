"""Tests for F8-P1 WorkHub Authority Boundary Specification."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.integration.workhub.boundary import (
    WorkHubAuthority,
    WorkHubBoundary,
    WorkHubOperation,
)


def test_read_operations_allowed_by_default():
    d1 = WorkHubBoundary.evaluate_operation(WorkHubOperation.TASK_READ)
    assert d1.allowed is True
    assert "System of Record" in d1.reason

    d2 = WorkHubBoundary.evaluate_operation(WorkHubOperation.CONTEXT_LOAD)
    assert d2.allowed is True


def test_task_create_authority_gated_by_w12_flag():
    # Denied by default
    d_denied = WorkHubBoundary.evaluate_operation(WorkHubOperation.TASK_CREATE, create_task_flag=False)
    assert d_denied.allowed is False
    assert d_denied.requires_flag == "OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK"

    # Authorized when explicit flag enabled
    d_allowed = WorkHubBoundary.evaluate_operation(WorkHubOperation.TASK_CREATE, create_task_flag=True)
    assert d_allowed.allowed is True


def test_state_transition_authority_gated_by_w13_flag():
    # Denied by default
    d_denied = WorkHubBoundary.evaluate_operation(WorkHubOperation.STATE_TRANSITION, mark_done_flag=False)
    assert d_denied.allowed is False
    assert d_denied.requires_flag == "OBJECTIVE_CYCLE_MARK_WORKHUB_DONE"

    # Authorized when explicit flag enabled
    d_allowed = WorkHubBoundary.evaluate_operation(WorkHubOperation.STATE_TRANSITION, mark_done_flag=True)
    assert d_allowed.allowed is True


def test_task_close_strictly_forbidden_inv06():
    d = WorkHubBoundary.evaluate_operation(WorkHubOperation.TASK_CLOSE, mark_done_flag=True, create_task_flag=True)
    assert d.allowed is False
    assert "INV-06" in d.reason


def test_evidence_attach_authorized():
    d = WorkHubBoundary.evaluate_operation(WorkHubOperation.EVIDENCE_ATTACH)
    assert d.allowed is True

    d_verif = WorkHubBoundary.evaluate_operation(WorkHubOperation.VERIFICATION_RECORD)
    assert d_verif.allowed is True
