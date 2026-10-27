"""AIX Closed-Loop Execution Plan, Sprint W5: the real, HOW-capable planner.

The model call is stubbed (no live LLM in this test environment) -- same pattern this
codebase already uses for `run_real_execute`/`build_workhub_client` elsewhere
(`test_mark_workhub_done.py`, `test_work_order_gate.py`). What's under test is real:
prompt construction, JSON parsing, evidence-grounding enforcement, and StepContractV1
validation -- not the LLM's actual reasoning quality, which cannot be unit-tested.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.aix_core.runtime.planner.step_contract_planner import (
    _parse_actions,
    parse_evidence_requests,
    propose_step_contract,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1)


def _understanding() -> UnderstandingEvidenceV1:
    return UnderstandingEvidenceV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        files_read=("FeeDue.php",),
        symbols_found=("getDueFeeStudent",),
        observed_problem="refactor and optimise FeeDue.php (evidence: 1 file read)",
        understanding_complete=True,
    )


def _stub_result(payload: dict[str, Any]) -> ChatCompletionResultV1:
    return ChatCompletionResultV1(
        output_text=json.dumps(payload), provider="test", resolved_provider="test", model="test-model", usage={},
    )


async def _call(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    *,
    obligations: tuple[ObjectiveObligationV1, ...] = (),
) -> Any:
    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return _stub_result(payload)

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion", _fake_chat_completion
    )
    return await propose_step_contract(
        objective="refactor and optimise FeeDue.php",
        definition_of_done=["N+1 queries batched"],
        understanding=_understanding(),
        constraints=["do not change the return shape"],
        tenant_id="tenant-1",
        cycle_id="cyc-1",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="human-1",
        authorization_header="",
        timeout_seconds=5.0,
        obligations=obligations,
    )


@pytest.mark.asyncio
async def test_grounded_step_contract_is_constructed_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = await _call(
        monkeypatch,
        {
            "reply": "Batch the per-student queries before the loop.",
            "needs_more_evidence": False,
            "purpose": "Eliminate N+1 queries in getDueFeeStudent",
            "target_scope": ["FeeDue.php"],
            "acceptance_criteria": ["Loop query count decreases", "Return shape unchanged"],
            "validation_commands": ["php -l FeeDue.php"],
            "prohibited_actions": ["do not change the return shape"],
            "risk_tier": "MEDIUM",
        },
    )
    assert proposal.contract is not None
    assert isinstance(proposal.contract, StepContractV1)
    assert proposal.contract.purpose == "Eliminate N+1 queries in getDueFeeStudent"
    assert proposal.contract.target_scope == ("FeeDue.php",)
    assert proposal.contract.status == "PENDING"
    assert proposal.reason is None


@pytest.mark.asyncio
async def test_needs_more_evidence_refuses_without_a_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = await _call(
        monkeypatch,
        {"reply": "I need to see the fee-rule query before proposing a step.", "needs_more_evidence": True},
    )
    assert proposal.contract is None
    assert proposal.reason == "NEEDS_MORE_EVIDENCE"
    assert "fee-rule" in proposal.reply


@pytest.mark.asyncio
async def test_ungrounded_target_scope_is_rejected_not_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model is untrusted input -- a file it was never given as evidence must not
    become an authorized target_scope just because the model claimed it."""
    proposal = await _call(
        monkeypatch,
        {
            "reply": "x", "needs_more_evidence": False, "purpose": "x",
            "target_scope": ["SomeFileNeverRead.php"],
            "acceptance_criteria": ["x"], "validation_commands": ["x"], "risk_tier": "LOW",
        },
    )
    assert proposal.contract is None
    assert proposal.reason is not None
    assert "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE" in proposal.reason


@pytest.mark.asyncio
async def test_allowed_files_constraint_does_not_ground_new_create_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allowed_files is not planner authority. CREATE requires a PlanTarget + Governance root."""
    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return _stub_result(
            {
                "reply": "create the directory marker",
                "needs_more_evidence": False,
                "purpose": "Create empty directory my-web-app",
                "target_scope": ["my-web-app/.gitkeep"],
                "acceptance_criteria": ["my-web-app exists as a directory"],
                "validation_commands": ["test -d my-web-app"],
                "risk_tier": "LOW",
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake_chat_completion,
    )
    understanding = UnderstandingEvidenceV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        files_read=(),
        symbols_found=(),
        observed_problem="create empty directory",
        understanding_complete=True,
    )
    proposal = await propose_step_contract(
        objective="Create an empty directory named my-web-app",
        definition_of_done=["directory exists"],
        understanding=understanding,
        constraints=["allowed_files:my-web-app/.gitkeep"],
        tenant_id="tenant-1",
        cycle_id="cyc-1",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="human-1",
        authorization_header="",
        timeout_seconds=5.0,
    )
    assert proposal.contract is None
    assert proposal.reason is not None
    assert "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE" in proposal.reason


@pytest.mark.asyncio
async def test_empty_validation_commands_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """StepContractV1.validation_commands requires min_length=1 -- a model that names no
    concrete check must not silently produce an unverifiable contract."""
    proposal = await _call(
        monkeypatch,
        {
            "reply": "x", "needs_more_evidence": False, "purpose": "x",
            "target_scope": ["FeeDue.php"], "acceptance_criteria": ["x"],
            "validation_commands": [], "risk_tier": "LOW",
        },
    )
    assert proposal.contract is None
    assert proposal.reason is not None
    assert "INVALID_STEP_CONTRACT" in proposal.reason


@pytest.mark.asyncio
async def test_malformed_model_json_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bad_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text="not json", provider="test", resolved_provider="test", model="test-model", usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion", _bad_chat_completion
    )
    proposal = await propose_step_contract(
        objective="x", definition_of_done=[], understanding=_understanding(), constraints=[],
        tenant_id="tenant-1", cycle_id="cyc-1", step_index=0, assigned_role="executor",
        step_budget=_budget(), actor_id="human-1", authorization_header="", timeout_seconds=5.0,
    )
    assert proposal.contract is None
    assert proposal.reason is not None
    assert "PLANNER_MODEL_ERROR" in proposal.reason


@pytest.mark.asyncio
async def test_obligation_refs_named_by_the_model_are_grounded_onto_the_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: a model reply naming real obligation
    ids becomes the contract's `obligation_refs`, threaded through for Packet P3's
    coverage gate to check against."""
    ob1 = ObjectiveObligationV1(objective_id="obj-1", description="Batch the N+1 queries")
    ob2 = ObjectiveObligationV1(objective_id="obj-1", description="Preserve the return shape")
    proposal = await _call(
        monkeypatch,
        {
            "reply": "Batch the per-student queries before the loop.",
            "needs_more_evidence": False,
            "purpose": "Eliminate N+1 queries in getDueFeeStudent",
            "target_scope": ["FeeDue.php"],
            "acceptance_criteria": ["Loop query count decreases"],
            "validation_commands": ["php -l FeeDue.php"],
            "risk_tier": "MEDIUM",
            "obligation_refs": [ob1.id, ob2.id],
        },
        obligations=(ob1, ob2),
    )
    assert proposal.contract is not None
    assert proposal.contract.obligation_refs == (ob1.id, ob2.id)


@pytest.mark.asyncio
async def test_hallucinated_obligation_ref_is_dropped_not_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same "model output is untrusted input" discipline as target_scope grounding: an
    obligation id the model invents (not among the real obligations it was given) must
    not silently count as coverage."""
    ob1 = ObjectiveObligationV1(objective_id="obj-1", description="Batch the N+1 queries")
    proposal = await _call(
        monkeypatch,
        {
            "reply": "x", "needs_more_evidence": False, "purpose": "x",
            "target_scope": ["FeeDue.php"], "acceptance_criteria": ["x"],
            "validation_commands": ["x"], "risk_tier": "LOW",
            "obligation_refs": [ob1.id, "ob-does-not-exist"],
        },
        obligations=(ob1,),
    )
    assert proposal.contract is not None
    assert proposal.contract.obligation_refs == (ob1.id,)
    assert "ob-does-not-exist" not in proposal.contract.obligation_refs


@pytest.mark.asyncio
async def test_prior_failure_evidence_reaches_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P4: 'Prior failure evidence when
    applicable' is one of the planner's documented inputs."""
    captured: dict[str, Any] = {}

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        captured["system"] = kwargs["messages"][0].content
        return _stub_result(
            {
                "reply": "x", "needs_more_evidence": False, "purpose": "x",
                "target_scope": ["FeeDue.php"], "acceptance_criteria": ["x"],
                "validation_commands": ["x"], "risk_tier": "LOW",
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion", _fake_chat_completion
    )
    await propose_step_contract(
        objective="refactor and optimise FeeDue.php", definition_of_done=["N+1 queries batched"],
        understanding=_understanding(), constraints=[], tenant_id="tenant-1", cycle_id="cyc-1",
        step_index=0, assigned_role="executor", step_budget=_budget(), actor_id="human-1",
        authorization_header="", timeout_seconds=5.0,
        prior_failure_evidence="verifier REPAIR: N+1 queries not actually batched",
    )
    assert "verifier REPAIR: N+1 queries not actually batched" in captured["system"]


def test_parse_evidence_requests_keeps_bounded_types_only() -> None:
    parsed = parse_evidence_requests(
        [
            {"type": "find_symbol", "query": "existing retry configuration"},
            {"type": "related_tests", "query": "integration job retry"},
            {"type": "invent_research", "query": "nope"},
            {"type": "read_file", "query": ""},
        ]
    )
    assert parsed == (
        {"type": "find_symbol", "query": "existing retry configuration"},
        {"type": "related_tests", "query": "integration job retry"},
    )


@pytest.mark.asyncio
async def test_needs_more_evidence_preserves_structured_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = await _call(
        monkeypatch,
        {
            "reply": "Need retry config location",
            "needs_more_evidence": True,
            "requests": [
                {"type": "find_symbol", "query": "existing retry configuration"},
                {"type": "related_tests", "query": "integration job retry"},
            ],
        },
    )
    assert proposal.contract is None
    assert proposal.reason == "NEEDS_MORE_EVIDENCE"
    assert proposal.evidence_requests == (
        {"type": "find_symbol", "query": "existing retry configuration"},
        {"type": "related_tests", "query": "integration job retry"},
    )


@pytest.mark.asyncio
async def test_legacy_one_step_shape_is_tagged(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = await _call(
        monkeypatch,
        {
            "reply": "Atomic timeout change",
            "needs_more_evidence": False,
            "purpose": "Change retry timeout to 60",
            "target_scope": ["FeeDue.php"],
            "acceptance_criteria": ["timeout is 60"],
            "validation_commands": ["php -l FeeDue.php"],
            "risk_tier": "LOW",
        },
    )
    assert proposal.contract is not None
    assert proposal.used_legacy_one_step is True
    assert proposal.plan is not None
    assert len(proposal.plan.steps) == 1
    kinds = {item["kind"] for item in proposal.transformations}
    ops = {item["op"] for item in proposal.transformations}
    assert "MODEL_GENERATED" in kinds
    assert "SYSTEM_ENRICHED" not in kinds
    assert "legacy_one_step_synthesis" not in ops
    assert proposal.planner_call is not None
    assert proposal.planner_call["raw_response"]["purpose"] == "Change retry timeout to 60"
    assert proposal.planner_call["parser_version"]
    assert "steps" not in proposal.planner_call["raw_response"]


@pytest.mark.asyncio
async def test_planner_call_captures_raw_json_before_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        seen["metadata"] = kwargs.get("metadata")
        return _stub_result(
            {
                "reply": "ok",
                "needs_more_evidence": False,
                "steps": [
                    {
                        "purpose": "batch the N+1",
                        "target_scope": ["FeeDue.php"],
                        "acceptance_criteria": ["loop query count decreases"],
                        "validation_commands": ["php -l FeeDue.php"],
                        "obligation_refs": ["unknown-ob"],
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion", _fake_chat_completion
    )
    proposal = await propose_step_contract(
        objective="refactor and optimise FeeDue.php",
        definition_of_done=["N+1 queries batched"],
        understanding=_understanding(),
        constraints=["do not change the return shape"],
        tenant_id="tenant-1",
        cycle_id="cyc-1",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="human-1",
        authorization_header="Bearer test",
        timeout_seconds=5.0,
        obligations=(
            ObjectiveObligationV1(id="ob-1", objective_id="obj-1", description="N+1 queries batched"),
        ),
    )
    assert seen["metadata"]["source"] == "aix.step_contract_planner"
    assert proposal.planner_call is not None
    assert proposal.planner_call["raw_response"]["steps"][0]["obligation_refs"] == ["unknown-ob"]
    removed = [item for item in proposal.transformations if item.get("kind") == "SYSTEM_REMOVED"]
    assert removed
    assert proposal.budget_report is not None
    assert "estimated_input_tokens" in proposal.budget_report
    assert "configured_input_cap" in proposal.budget_report


def test_parse_actions_accepts_registered_filesystem_read() -> None:
    actions = _parse_actions(
        [
            {
                "capability": "filesystem",
                "operation": "read",
                "arguments": {"path": "README_A.txt"},
            }
        ]
    )
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "read"
    assert actions[0].arguments == {"path": "README_A.txt"}


def test_parse_actions_rejects_unknown_capability() -> None:
    with pytest.raises(ValueError, match="CAPABILITY_NOT_AVAILABLE"):
        _parse_actions([{"capability": "filesystem", "operation": "teleport", "arguments": {}}])


def test_parse_actions_normalizes_dotted_capability() -> None:
    actions = _parse_actions(
        [
            {
                "capability": "filesystem.read",
                "operation": "read_file",
                "arguments": {"path": "README_A.txt"},
            }
        ]
    )
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "read"
    assert actions[0].arguments == {"path": "README_A.txt"}


def test_parse_actions_maps_provenance_create_file_write_to_filesystem_patch() -> None:
    actions = _parse_actions(
        [
            {
                "capability": "CREATE_FILE",
                "operation": "write",
                "arguments": {"path": "proposed/attendance/index.html"},
            }
        ]
    )
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "patch"
    assert actions[0].arguments == {"path": "proposed/attendance/index.html"}


def test_parse_actions_maps_create_directory_to_filesystem_mkdir() -> None:
    actions = _parse_actions(
        [
            {
                "capability": "CREATE_DIRECTORY",
                "operation": "mkdir",
                "arguments": {"path": "proposed/attendance"},
            }
        ]
    )
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "mkdir"


def test_parse_actions_maps_content_generate_to_filesystem_patch() -> None:
    actions = _parse_actions(
        [
            {
                "capability": "content.generate",
                "operation": (
                    "Create the greenfield attendance registration system entirely under "
                    "proposed/attendance/"
                ),
                "arguments": {},
            }
        ]
    )
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "patch"
    assert "purpose" in actions[0].arguments


def test_parse_actions_maps_content_generate_short_operation() -> None:
    actions = _parse_actions(
        [{"capability": "content", "operation": "generate", "arguments": {"paths": ["proposed/a.php"]}}]
    )
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "patch"


def test_parse_actions_coerces_invented_create_complete_files() -> None:
    """Whack-a-mole aliases are not enough — inventable create verbs must coerce once."""
    actions = _parse_actions(
        [
            {
                "capability": "filesystem.create_complete_files",
                "operation": (
                    "Plan to create the greenfield attendance registration system entirely "
                    "under proposed/attendance/"
                ),
                "arguments": {},
            }
        ]
    )
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "patch"


def test_parse_actions_coerces_arbitrary_create_write_invent() -> None:
    actions = _parse_actions(
        [{"capability": "repo", "operation": "write_all_sources", "arguments": {"paths": ["a.php"]}}]
    )
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "patch"


def test_parse_actions_still_rejects_non_mutation_invent() -> None:
    with pytest.raises(ValueError, match="CAPABILITY_NOT_AVAILABLE"):
        _parse_actions([{"capability": "quantum", "operation": "flarp", "arguments": {}}])


def test_bind_action_paths_fills_create_from_target_scope() -> None:
    from backend.aix_core.runtime.planner.step_contract_planner import _bind_action_paths_to_scope

    actions = _parse_actions(
        [{"capability": "filesystem", "operation": "create", "arguments": {}}]
    )
    bound = _bind_action_paths_to_scope(
        actions,
        [
            "proposed/attendance/index.html",
            "proposed/attendance/style.css",
            "proposed/attendance/register.php",
        ],
    )
    assert len(bound) == 1
    assert bound[0].capability == "filesystem"
    assert bound[0].operation == "patch"
    assert bound[0].arguments["paths"] == [
        "proposed/attendance/index.html",
        "proposed/attendance/style.css",
        "proposed/attendance/register.php",
    ]


def test_bind_action_paths_promotes_paths_to_path_for_create() -> None:
    # Live lane: planner emits filesystem.create with `paths` only; the
    # capability spec requires singular `path`. The binder must promote
    # paths[0] instead of letting execute die as INVALID_ARGUMENTS.
    from backend.aix_core.runtime.planner.step_contract_planner import _bind_action_paths_to_scope

    actions = _parse_actions(
        [
            {
                "capability": "filesystem",
                "operation": "create",
                "arguments": {"paths": ["proposed/notes.md"], "content": "# Live Run 3"},
            }
        ]
    )
    bound = _bind_action_paths_to_scope(actions, ["proposed/notes.md"])
    assert len(bound) == 1
    assert bound[0].operation == "create"
    assert bound[0].arguments["path"] == "proposed/notes.md"
    assert bound[0].arguments["content"] == "# Live Run 3"


def test_bind_action_paths_fills_patch_paths() -> None:
    from backend.aix_core.runtime.planner.step_contract_planner import _bind_action_paths_to_scope

    actions = _parse_actions(
        [{"capability": "filesystem", "operation": "patch", "arguments": {}}]
    )
    bound = _bind_action_paths_to_scope(actions, ["proposed/attendance/schema.sql"])
    assert bound[0].arguments["paths"] == ["proposed/attendance/schema.sql"]
