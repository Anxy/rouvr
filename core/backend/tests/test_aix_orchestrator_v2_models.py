from backend.aix_orchestrator_v2.models import AixOrchestratorV2RunState


def test_v2_run_state_adds_artifacts_and_failure():
    state = AixOrchestratorV2RunState()

    state.add_artifact(name="planner_trace", payload={"trace": True}, ref="local://trace")
    state.fail(
        classification="plan_invalid",
        phase="planner",
        code="PLANNER_BLOCKED",
        message="blocked",
        status="blocked",
    )

    assert state.artifacts[0].name == "planner_trace"
    assert state.failure is not None
    assert state.failure.classification == "plan_invalid"
    assert state.failure.phase == "planner"
    assert state.status == "blocked"
