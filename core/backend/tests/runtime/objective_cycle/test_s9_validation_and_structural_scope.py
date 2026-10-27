"""Regression helpers for structural gate scoping and validation fallback."""

from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.objective.contracts import RequiredEffectV1
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.multi_step_execute import _validation_targets_mutated_paths
from backend.aix_core.runtime.verification.structural_verify import verify_structural_effects


def test_structural_gates_only_consider_mutated_paths() -> None:
    discovery = DiscoveryReport()
    discovery.config_files = {
        "backend/tests/aix_core/objective_cycle/live_fixtures/timeout_loader.py": "return 5 * 2\n",
        "AIX-Frontend/src/components/aix/FixtureModeWarning.tsx": "export const x = 1\n",
    }
    result = evaluate_completion_gates(
        discovery=discovery,
        diff_stats={
            "backend/tests/aix_core/objective_cycle/live_fixtures/timeout_loader.py": {
                "additions": 1,
                "deletions": 1,
            }
        },
        diff_text=(
            "--- a/backend/tests/aix_core/objective_cycle/live_fixtures/timeout_loader.py\n"
            "+++ b/backend/tests/aix_core/objective_cycle/live_fixtures/timeout_loader.py\n"
            "@@ -1 +1 @@\n"
            "-return 5 * 2\n"
            "+return 45\n"
        ),
        test_result={"returncode": 0, "stdout": "2 passed"},
        required_effects=(RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"),),
        before_files=dict(discovery.config_files),
        after_files={
            "backend/tests/aix_core/objective_cycle/live_fixtures/timeout_loader.py": "return 45\n"
        },
        objective="change default timeout to 45",
    )
    assert result.gates.get("structural_effects") is True
    assert result.passed is True


def test_preserve_public_interface_soft_ok_for_python() -> None:
    sv = verify_structural_effects(
        path="timeout_loader.py",
        before="return 5 * 2\n",
        after="return 45\n",
        effects=(RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"),),
    )
    assert sv.passed is True


def test_validation_targets_mutated_paths() -> None:
    assert _validation_targets_mutated_paths(
        ["python", "-m", "pytest", "backend/tests/.../live_fixtures/test_timeout_service.py"],
        ["backend/tests/.../live_fixtures/timeout_loader.py"],
    )
    assert not _validation_targets_mutated_paths(
        ["python", "-m", "pytest", "backend/tests/test_s25_loop_replay_fixtures.py", "-k", "timeout"],
        ["backend/tests/.../live_fixtures/timeout_loader.py"],
    )
