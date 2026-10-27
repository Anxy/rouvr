"""F2/F3 runtime wiring — FLAG OFF legacy parity + FLAG ON framework path parity."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.aix_core.framework.capability import capability_facade_enabled
from backend.aix_core.framework.context import durable_provenance_enabled
from backend.aix_core.intelligence.context.step_context_capsule import build_step_context_capsule
from backend.aix_core.runtime.foundation.capability_executor import (
    _execute_action_legacy,
    _execute_action_via_framework,
    execute_action,
)
from backend.aix_core.runtime.objective.contracts import ActionRequestV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot, settings


def test_flags_default_off() -> None:
    assert bool(getattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", True)) is False
    assert bool(getattr(settings, "AIX_FRAMEWORK_DURABLE_PROVENANCE", True)) is False
    assert capability_facade_enabled() is False
    assert durable_provenance_enabled() is False


def test_flag_off_execute_action_uses_legacy_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", False, raising=False)
    calls = {"legacy": 0, "framework": 0}

    def _legacy(*args: object, **kwargs: object):
        calls["legacy"] += 1
        from backend.aix_core.runtime.objective.contracts import ActionObservationV1

        req = args[0]
        assert isinstance(req, ActionRequestV1)
        return ActionObservationV1(
            action_id=req.action_id,
            status="SUCCESS",
            changed=False,
            observation={"via": "legacy"},
        )

    def _framework(*args: object, **kwargs: object):
        calls["framework"] += 1
        raise AssertionError("framework path must not run when flag OFF")

    req = ActionRequestV1(capability="filesystem", operation="read", arguments={"path": "a.py"})
    with (
        patch(
            "backend.aix_core.runtime.foundation.capability_executor._execute_action_legacy",
            _legacy,
        ),
        patch(
            "backend.aix_core.runtime.foundation.capability_executor._execute_action_via_framework",
            _framework,
        ),
    ):
        obs = execute_action(req, repository_id="repo", settings=settings, apply_edits=False)
    assert calls == {"legacy": 1, "framework": 0}
    assert obs.observation["via"] == "legacy"


def test_flag_on_execute_action_uses_framework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", True, raising=False)
    calls = {"legacy": 0, "framework": 0}

    def _legacy(*args: object, **kwargs: object):
        calls["legacy"] += 1
        from backend.aix_core.runtime.objective.contracts import ActionObservationV1

        req = args[0]
        return ActionObservationV1(
            action_id=req.action_id,
            status="SUCCESS",
            changed=False,
            observation={"via": "legacy-core"},
        )

    def _framework(*args: object, **kwargs: object):
        calls["framework"] += 1
        from backend.aix_core.runtime.objective.contracts import ActionObservationV1

        req = args[0]
        return ActionObservationV1(
            action_id=req.action_id,
            status="SUCCESS",
            changed=False,
            observation={"via": "framework"},
        )

    req = ActionRequestV1(capability="filesystem", operation="read", arguments={"path": "a.py"})
    with (
        patch(
            "backend.aix_core.runtime.foundation.capability_executor._execute_action_legacy",
            _legacy,
        ),
        patch(
            "backend.aix_core.runtime.foundation.capability_executor._execute_action_via_framework",
            _framework,
        ),
    ):
        obs = execute_action(req, repository_id="repo", settings=settings, apply_edits=False)
    assert calls["framework"] == 1
    assert calls["legacy"] == 0
    assert obs.observation["via"] == "framework"


def test_f2_on_parity_matches_legacy_denial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Framework resolve + legacy core deny identically for unknown capability."""
    monkeypatch.setattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", False, raising=False)
    req = ActionRequestV1(capability="nope.cap", operation="explode", arguments={})

    with patch(
        "backend.aix_core.runtime.foundation.capability_executor.resolve_repository_root",
        return_value=tmp_path,
    ):
        legacy = _execute_action_legacy(
            req, repository_id="repo", settings=settings, apply_edits=False
        )
        framework = _execute_action_via_framework(
            req, repository_id="repo", settings=settings, apply_edits=False
        )
    assert legacy.status == framework.status
    assert legacy.error_code == framework.error_code == "CAPABILITY_NOT_AVAILABLE"


def test_f3_flag_off_no_durable_store() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(durable_provenance=False),
    )
    assert backend._durable_provenance is None  # noqa: SLF001


def test_f3_flag_on_mirrors_intent_and_capsule_parity() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(durable_provenance=True),
    )
    assert backend._durable_provenance is not None  # noqa: SLF001

    from backend.aix_core.runtime.objective.provenance.admit import freeze_user_intent

    intent = freeze_user_intent(objective_id="obj-wire", text="Wire the context path")
    backend._provenance.put_intent(intent)  # noqa: SLF001
    backend._mirror_durable_intent(intent)

    events = backend._durable_provenance.events("obj-wire")  # noqa: SLF001
    assert len(events) == 1
    assert events[0].payload["text"] == "Wire the context path"

    understanding = UnderstandingEvidenceV1(
        objective_id="obj-wire",
        cycle_id="c1",
        files_read=("a.py",),
        observed_problem="p",
        understanding_complete=True,
    )
    backend._understanding_evidence["obj-wire"] = understanding  # noqa: SLF001

    class _Obj:
        objective_id = "obj-wire"

    class _Cycle:
        cycle_id = "c1"

    legacy = build_step_context_capsule(
        step_id="step-1",
        understanding=understanding,
        target_scope=("a.py",),
        invariants=("inv",),
        intent_id=intent.id,
        intent_text=intent.text,
        plan_target_paths=("a.py",),
    )
    wired = backend._build_execute_context_capsule(  # noqa: SLF001
        obj=_Obj(),  # type: ignore[arg-type]
        cycle=_Cycle(),  # type: ignore[arg-type]
        active_step_contract=None,
        execution_scope=("a.py",),
        previous_failure=None,
        intent=intent,
        cited_claims=(),
        cited_items=(),
        prov_plan=None,
    )
    assert wired.relevant_files == legacy.relevant_files
    assert wired.intent_text == legacy.intent_text
    assert backend._last_context_bundle is not None  # noqa: SLF001
    assert "a.py" in backend._last_context_bundle.target_files  # noqa: SLF001


def test_snapshot_captures_new_flags() -> None:
    class S:
        AIX_FRAMEWORK_CAPABILITY_FACADE = True
        AIX_FRAMEWORK_DURABLE_PROVENANCE = True

    snap = FrameworkFlagsSnapshot.from_settings(S())
    assert snap.capability_facade is True
    assert snap.durable_provenance is True
