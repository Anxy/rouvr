"""AIX-SEM-001-W1 — Prompt continuity: refs → derived_execution_prompt.

W1-T1: API step with operation_refs → prompt contains each op token
W1-T2: DB step with entity_refs → prompt contains each entity token
W1-T3: empty refs → existence-scoped prompt (no invented continuity block)
W1-T4: run_real_execute evidence objective.json includes continuity_refs
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.executor.real_execute import run_real_execute
from backend.aix_core.runtime.executor.step_prompt_continuity import (
    actionable_tokens_from_refs,
    build_continuity_prompt_section,
    derive_tokens_from_obligation_ref,
)
from backend.aix_core.runtime.objective.contracts import BudgetV1, RequiredEffectV1, StepContractV1


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=3600,
        tokens=100_000,
        cost_usd=5.0,
        retries=2,
        steps=10,
        concurrency=1,
    )


def _contract(**kwargs: Any) -> StepContractV1:
    base = dict(
        cycle_id="cyc-sem001-w1",
        tenant_id="tenant-1",
        step_index=0,
        purpose="Create API/backend artifacts from ProductStructure",
        target_scope=("proposed/erp-system/api.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("proposed/erp-system/api.php exists",),
        validation_commands=("test -f proposed/erp-system/api.php",),
        # Mutation effect required so validation_commands do not force RUN_TESTS-only.
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        assigned_role="executor",
        step_budget=_budget(),
        status="PENDING",
    )
    base.update(kwargs)
    return StepContractV1(**base)


def test_w1_t1_api_operation_refs_in_prompt_section() -> None:
    """W1-T1: API step fixture with operation_refs → each op token in section."""
    ops = (
        "auth.login",
        "customers.list",
        "products.create",
        "products.list",
    )
    section = actionable_tokens_from_refs(operation_refs=ops, mapping_refs=("map-14",))
    assert section.has_content
    for op in ops:
        assert op in section.section_text
        assert op in section.actionable_tokens
    assert "Must implement operations:" in section.section_text
    # mapping ids are context only when actionable ops exist
    assert "map-14" in section.section_text
    # must not be opaque-only
    assert "obl_" not in section.section_text


def test_w1_t2_db_entity_refs_in_prompt_section() -> None:
    """W1-T2: DB step with entity_refs → each entity token in section."""
    entities = ("customers", "products", "orders", "sessions")
    section = actionable_tokens_from_refs(entity_refs=entities)
    assert section.has_content
    for ent in entities:
        assert ent in section.section_text
        assert ent in section.actionable_tokens
    assert "Must persist / align entities:" in section.section_text


def test_w1_t3_empty_refs_no_continuity_block() -> None:
    """W1-T3: empty refs → no continuity section (existence-scoped stays as-is)."""
    section = build_continuity_prompt_section(
        _contract(
            purpose="Create governed product directory under create_root",
            target_scope=("proposed/erp-system/",),
            acceptance_criteria=("proposed/erp-system/ exists",),
            validation_commands=("test -d proposed/erp-system/",),
            operation_refs=(),
            entity_refs=(),
            mapping_refs=(),
            obligation_refs=(),
        )
    )
    assert not section.has_content
    assert section.section_text == ""
    assert section.actionable_tokens == ()


def test_w1_opaque_mapping_alone_is_not_actionable() -> None:
    """Opaque map-* / obl_map_* alone must not invent a continuity block."""
    section = actionable_tokens_from_refs(mapping_refs=("map-14", "map-15"))
    assert not section.has_content
    assert derive_tokens_from_obligation_ref("obl_map_map_14") == ()


def test_w1_obligation_refs_derive_actionable_tokens() -> None:
    """Obligation ids alone are expanded to tokens — never left as obl_* only."""
    section = actionable_tokens_from_refs(
        obligation_refs=(
            "obl_api_auth_login",
            "obl_api_products_list",
            "obl_db_customers",
            "obl_map_map_14",
        )
    )
    assert section.has_content
    assert "auth.login" in section.actionable_tokens
    assert "products.list" in section.actionable_tokens
    assert "customers" in section.actionable_tokens
    assert "obl_api_auth_login" not in section.section_text
    assert "obl_map_map_14" not in section.section_text


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.mark.asyncio
async def test_w1_t4_continuity_refs_recorded_on_objective_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W1-T4: attempt evidence includes continuity_refs list."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _init_git_repo(repo)
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        f'{{"visions": "{repo.as_posix()}"}}',
    )

    captured: dict[str, Any] = {}

    async def _fake_mutation(**kwargs: Any) -> Any:
        captured["objective"] = kwargs.get("objective")
        raise SystemExit("stop after capture")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.run_multi_step_mutation",
        _fake_mutation,
    )

    ops = ("auth.login", "products.list", "orders.create")
    contract = _contract(
        operation_refs=ops,
        mapping_refs=("map-14",),
        obligation_refs=("obl_api_auth_login",),
    )
    ws = tmp_path / "ws"
    with pytest.raises(SystemExit):
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc-sem001-w1",
            objective_id="obj-sem001-w1",
            objective="Create greenfield ERP api under proposed/erp-system/api.php",
            workhub_task_id="wh-1",
            project_id=None,
            task_number=None,
            repository_id="visions",
            require_workhub_load=False,
            workspace_base=ws,
            step_contract=contract,
        )

    prompt = str(captured.get("objective") or "")
    for op in ops:
        assert op in prompt, f"missing {op} in derived prompt"
    assert "Continuity contract (must honor):" in prompt
    assert "Must implement operations:" in prompt
    # Opaque obligation id must not be the sole/primary continuity content
    assert "obl_api_auth_login" not in prompt

    # Evidence file written before mutation dispatch
    evidence_files = list(ws.rglob("objective.json"))
    assert evidence_files, "expected objective.json under workspace evidence"
    payload = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    refs = payload.get("continuity_refs") or []
    assert isinstance(refs, list)
    for op in ops:
        assert op in refs
    assert payload.get("continuity_helper_version")
    derived = payload.get("derived_execution_prompt") or ""
    for op in ops:
        assert op in derived
