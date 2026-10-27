"""P1: UserIntent freeze + UserTurn append. Intent text cannot be rewritten."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.provenance.admit import (
    IntentFrozenError,
    append_user_turn,
    apply_scope_turn,
    dod_texts_eligible_for_scope,
    freeze_user_intent,
    infer_dod_user_authored,
    original_objective_alias,
)
from backend.aix_core.runtime.objective.provenance.contracts import (
    ClaimCite,
    UnderstandingClaim,
    UnderstandingRevision,
)
from backend.aix_core.runtime.objective.provenance.history import ProvenanceHistory
from backend.aix_core.runtime.objective.provenance.validators import validate_understanding_revision


def _cite(kind: str, ref: str) -> ClaimCite:
    return ClaimCite(kind=kind, ref=ref)  # type: ignore[arg-type]


def _claim(*, kind: str, text: str, cites: tuple[ClaimCite, ...], claim_id: str, status: str = "ACTIVE") -> UnderstandingClaim:
    return UnderstandingClaim(id=claim_id, kind=kind, text=text, cites=cites, status=status)  # type: ignore[arg-type]


async def _admit(backend: DurableObjectiveCycleApiBackend, *, objective: str, key: str, **extra: object) -> dict:
    body: dict[str, object] = {
        "workhub_task_id": "wh-task-1",
        "objective": objective,
        "task_class": "bounded_repair",
        "definition_of_done": extra.pop("definition_of_done", ("done",)),
        "repository_id": "aix",
        "allowed_files": (),
    }
    body.update(extra)
    return await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body=body,
        idempotency_key=key,
        expected_version=None,
    )


def test_freeze_user_intent_is_immutable() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="Fix login timeout")
    assert intent.text == "Fix login timeout"
    assert original_objective_alias(intent) == intent.text
    with pytest.raises(ValidationError):
        intent.text = "silently rewritten"


def test_history_rejects_intent_rewrite() -> None:
    history = ProvenanceHistory()
    first = freeze_user_intent(objective_id="obj_1", text="Only the MySQL schema")
    history.put_intent(first)
    clone = freeze_user_intent(objective_id="obj_1", text="Also build a PHP API")
    with pytest.raises(IntentFrozenError):
        history.put_intent(clone)
    history.assert_intent_unchanged("obj_1", "Only the MySQL schema")


def test_append_user_turn_does_not_change_intent() -> None:
    history = ProvenanceHistory()
    intent = history.put_intent(freeze_user_intent(objective_id="obj_1", text="Only the MySQL schema"))
    turn = append_user_turn(intent=intent, text="Also build PHP API.")
    history.append_turn(turn)
    assert history.require_intent("obj_1").text == "Only the MySQL schema"
    assert history.turns("obj_1")[0].text == "Also build PHP API."
    assert history.turns("obj_1")[0].intent_id == intent.id


def test_facet_dod_is_not_scope_unless_user_authored() -> None:
    facet = freeze_user_intent(
        objective_id="obj_1",
        text="Only the MySQL schema",
        definition_of_done=("Frontend / UI surface",),
    )
    assert facet.dod_user_authored is False
    assert dod_texts_eligible_for_scope(facet) == ()

    authored = freeze_user_intent(
        objective_id="obj_2",
        text="Ship the schema and the schema must compile",
        definition_of_done=("schema must compile",),
    )
    assert authored.dod_user_authored is True
    assert dod_texts_eligible_for_scope(authored) == ("schema must compile",)

    assert infer_dod_user_authored(
        definition_of_done=("Frontend / UI surface",),
        intent_text="Only the MySQL schema",
    ) is False


def test_u7_extend_keeps_prior_claim_active() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="Only the MySQL schema")
    turn = append_user_turn(intent=intent, text="Also build PHP API.")
    parent_claim = _claim(
        kind="SCOPE_IN",
        text="MySQL schema",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_schema",
    )
    parent = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(parent_claim,),
    )
    added = _claim(
        kind="SCOPE_IN",
        text="PHP API",
        cites=(_cite("USER_TURN", turn.id),),
        claim_id="cl_php",
    )
    child = apply_scope_turn(
        intent=intent,
        parent=parent,
        turn=turn,
        mode="extend",
        new_claims=(added,),
    )
    by_id = {c.id: c for c in child.claims}
    assert by_id["cl_schema"].status == "ACTIVE"
    assert by_id["cl_php"].status == "ACTIVE"
    result = validate_understanding_revision(
        intent=intent, revision=child, parent=parent, turns=(turn,)
    )
    assert result.ok is True


def test_u7_replace_marks_prior_claim_superseded() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="Only the MySQL schema")
    turn = append_user_turn(intent=intent, text="PHP API only.")
    parent_claim = _claim(
        kind="SCOPE_IN",
        text="MySQL schema",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_schema",
    )
    parent = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(parent_claim,),
    )
    replacement = _claim(
        kind="SCOPE_IN",
        text="PHP API",
        cites=(_cite("USER_TURN", turn.id),),
        claim_id="cl_php",
    )
    child = apply_scope_turn(
        intent=intent,
        parent=parent,
        turn=turn,
        mode="replace",
        new_claims=(replacement,),
        replace_claim_ids=("cl_schema",),
    )
    by_id = {c.id: c for c in child.claims}
    assert by_id["cl_schema"].status == "SUPERSEDED"
    assert by_id["cl_php"].status == "ACTIVE"
    result = validate_understanding_revision(
        intent=intent, revision=child, parent=parent, turns=(turn,)
    )
    assert result.ok is True


def test_u7_silent_drop_of_active_user_claim_fails() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="Only the MySQL schema")
    turn = append_user_turn(intent=intent, text="PHP API only.")
    parent_claim = _claim(
        kind="SCOPE_IN",
        text="MySQL schema",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_schema",
    )
    parent = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(parent_claim,),
    )
    dropped = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        parent_revision_id=parent.id,
        scope_mode="BOUNDED",
        claims=(
            _claim(
                kind="SCOPE_IN",
                text="PHP API",
                cites=(_cite("USER_TURN", turn.id),),
                claim_id="cl_php",
            ),
        ),
    )
    result = validate_understanding_revision(
        intent=intent, revision=dropped, parent=parent, turns=(turn,)
    )
    assert result.ok is False
    assert result.rule_id == "U7"


@pytest.mark.asyncio
async def test_admit_freezes_intent_and_aliases_original_objective() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    snapshot = "Fix login timeout in src/auth/jwt.py"
    result = await _admit(backend, objective=snapshot, key="p1-admit-1")
    assert result["ok"] is True
    intent = backend._provenance.require_intent(result["objective_id"])  # noqa: SLF001
    record = backend._objectives[result["objective_id"]]  # noqa: SLF001
    assert intent.text == snapshot
    assert result["original_objective"] == snapshot
    assert result["intent_id"] == intent.id
    assert record.objective == snapshot
    assert record.original_objective == snapshot
    assert original_objective_alias(intent) == record.original_objective


@pytest.mark.asyncio
async def test_clarify_appends_user_turn_without_rewriting_intent() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    snapshot = "fix the client"
    admitted = await _admit(backend, objective=snapshot, key="p1-clarify-admit")
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admitted["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="p1-clarify-start",
        expected_version=None,
    )
    await backend.command(
        operation="clarify_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"clarification": {"answer": "backend/client.py"}},
        idempotency_key="p1-clarify-1",
        expected_version=None,
    )
    intent = backend._provenance.require_intent(admitted["objective_id"])  # noqa: SLF001
    turns = backend._provenance.turns(admitted["objective_id"])  # noqa: SLF001
    record = backend._objectives[admitted["objective_id"]]  # noqa: SLF001
    assert intent.text == snapshot
    assert record.objective == snapshot
    assert record.original_objective == snapshot
    assert len(turns) == 1
    assert turns[0].text == "backend/client.py"
    assert turns[0].intent_id == intent.id


@pytest.mark.asyncio
async def test_replan_appends_user_turn_and_intent_stays_byte_for_byte() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    snapshot = "Run affected tests"
    admitted = await _admit(backend, objective=snapshot, key="p1-replan-admit")
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admitted["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="p1-replan-start",
        expected_version=None,
    )
    await backend.command(
        operation="replan_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"replan_input": {"objective": "Also build PHP API"}},
        idempotency_key="p1-replan-1",
        expected_version=started["version"],
    )
    intent = backend._provenance.require_intent(admitted["objective_id"])  # noqa: SLF001
    turns = backend._provenance.turns(admitted["objective_id"])  # noqa: SLF001
    record = backend._objectives[admitted["objective_id"]]  # noqa: SLF001
    assert intent.text == snapshot
    assert record.objective == snapshot
    assert record.original_objective == snapshot
    assert [turn.text for turn in turns] == ["Also build PHP API"]
    backend._provenance.assert_intent_unchanged(admitted["objective_id"], snapshot)  # noqa: SLF001


def test_p1_live_path_does_not_assign_objective_text() -> None:
    source = (
        Path(__file__).resolve().parents[4]
        / "aix_core"
        / "runtime"
        / "objective"
        / "durable_api_backend.py"
    ).read_text(encoding="utf-8")
    assert "obj.objective =" not in source
    assert "obj.objective=" not in source
