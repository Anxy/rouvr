from __future__ import annotations


class _AixWorkflowRegistry:
    """Workflow Registry single source of truth for strict planner packets."""

    _RECOVERY_TEST_FILE = "backend/tests/aix_core/bootstrap/test_contract_admission.py"
    _RECOVERY_TEST_SYMBOL = (
        "backend.tests."
        "aix_core.bootstrap.test_contract_admission."
        "test_executor_admits_complete_authoritative_implementation_contract"
    )
    _RECOVERY_VALIDATION = (
        "pytest --tb=no -q "
        "backend/tests/aix_core/bootstrap/test_contract_admission.py::"
        "test_executor_admits_complete_authoritative_implementation_contract"
    )
    _RECOVERY_OBJECTIVE = "Child tests: bootstrap contract admission"
    _RECOVERY_BEHAVIOR_CHANGE = (
        "A recovery mutation task that is dispatch-ready and simulation_ready=true "
        "does not fail slice_validation solely with PLANNER_PACKET_REQUIRED."
    )
    _RECOVERY_MUTATION_INSTRUCTIONS = (
        "Edit ONLY the designated test file for this recovery slice. "
        "Never touch production helpers or other surfaces."
    )

    def resolve_packet(self, packet_id: str, slice_context: dict):
        if packet_id == "planner_slice.current":
            return {
                "schema_name": "planner_slice.current",
                "schema_version": "current",
                "task_number": 2573,
                "slice_id": "recovery-test-2573",
                "objective": "Force planner to use bounded recovery test context",
                "target_file": self._RECOVERY_TEST_FILE,
                "target_symbol": self._RECOVERY_TEST_SYMBOL,
                "bounded_mutation_goal": "Make one executable assertion or boolean change inside the target test only.",
                "validation_command": self._RECOVERY_VALIDATION,
                "planner_mode": "packet_bound_replace_function",
                "allowed_surface": {
                    "anchor": {
                        "symbol": self._RECOVERY_TEST_SYMBOL,
                        "compact_snippet": """def test_should_require_strict_planner_packet_for_mutation_required_recovery_slice() -> None:
    result = admit_contract(
        contract_snapshot=_implementation_contract(),
        required_kind=TaskKind.IMPLEMENTATION,
    )
    assert result.admitted is True""",
                    }
                },
                "output_schema": {"type": "replace_function", "fields": ["file", "qualified_symbol", "edit_intent"]},
                "hard_failure_rules": [
                    "No files outside target_file.",
                    "No comments-only or formatting-only edit.",
                    "Run the declared validation command.",
                ],
            }
        raise ValueError(f"Unknown packet_id: {packet_id}")


AIX_WORKFLOW_REGISTRY = _AixWorkflowRegistry()
