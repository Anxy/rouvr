from backend.aix_core.runtime.planner.exploration_planner import requires_exploration
from backend.aix_core.runtime.objective.task_family import classify_task_family


def test_refactor_objective_classifies_as_refactor() -> None:
    result = classify_task_family('refactor this file "D:/v2.1/Visions/web.php"')
    assert result.task_family == "REFACTOR"


def test_bound_refactor_skips_exploration_gate() -> None:
    assert requires_exploration("REFACTOR", target_bound=True) is False
    assert requires_exploration("REFACTOR", target_bound=False) is True
    assert requires_exploration("DELETE", target_bound=True) is False
    assert requires_exploration("EDIT", target_bound=True) is False
    # Broad analysis families still explore even with a path hint.
    assert requires_exploration("ANALYZE", target_bound=True) is True
