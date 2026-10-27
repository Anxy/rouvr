from backend.aix_core.runtime.executor.execution_reasons import REASON_CATALOG, reason_details


def test_minimum_containment_reasons_have_complete_ownership_metadata() -> None:
    required = {
        "INTENT_UNCLEAR",
        "NO_EXECUTABLE_STEPS",
        "NO_REAL_OPERATION_PERFORMED",
        "NO_TARGET_RESOURCE_ACCESSED",
        "VERIFICATION_STEP_MISSING",
        "VERIFICATION_FAILED",
        "CAPABILITY_NOT_REGISTERED",
        "EXECUTION_LOOP_DETECTED",
    }
    assert required <= REASON_CATALOG.keys()
    for code in required:
        details = reason_details(code)
        assert details is not None
        assert details["reason_stage"]
        assert details["reason_owner"]
        assert isinstance(details["retryable"], bool)
        assert isinstance(details["user_action_required"], bool)
        assert isinstance(details["system_action_required"], bool)
