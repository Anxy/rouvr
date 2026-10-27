"""AIX-SEM-001-W2 — Scoped upstream discovery (depends_on → read-only paths).

W2-T1: UI depends_on API → discovery paths include API; files_read if on disk
W2-T2: No depends_on → discovery unchanged (targets only)
W2-T3: Missing upstream → NEED_UPSTREAM (fail closed; no invented content)
W2-T4: Cap → upstream body truncated deterministically; hash recorded
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox
from backend.aix_core.runtime.executor.multi_step_execute import (
    MultiStepMutationError,
    _apply_upstream_body_caps,
    _plan_and_validate_upstream_discovery,
    run_multi_step_mutation,
)
from backend.aix_core.runtime.executor.step_upstream_discovery import (
    merge_discovery_paths,
    resolve_upstream_target_paths,
    truncate_upstream_content,
)
from backend.aix_core.runtime.objective.contracts import BudgetV1, RequiredEffectV1, StepContractV1
from backend.aix_core.runtime.objective.discovery import DiscoveryReport, run_bound_files_discovery


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
        cycle_id="cyc-sem001-w2",
        tenant_id="tenant-1",
        step_index=0,
        purpose="step",
        target_scope=("proposed/erp-system/api.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("proposed/erp-system/api.php exists",),
        validation_commands=("test -f proposed/erp-system/api.php",),
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        assigned_role="executor",
        step_budget=_budget(),
        status="PENDING",
    )
    base.update(kwargs)
    return StepContractV1(**base)


def test_w2_t1_ui_depends_on_api_in_discovery_paths(tmp_path: Path) -> None:
    """W2-T1: UI step depends_on API → discovery lists API; content read if present."""
    api = _contract(
        id="step-api",
        step_index=1,
        purpose="Create API",
        target_scope=("proposed/erp-system/api.php",),
    )
    ui = _contract(
        id="step-ui",
        step_index=2,
        purpose="Create UI",
        target_scope=("proposed/erp-system/index.html",),
        depends_on=("step-api",),
        acceptance_criteria=("proposed/erp-system/index.html exists",),
        validation_commands=("test -f proposed/erp-system/index.html",),
    )
    plan = resolve_upstream_target_paths(step_contract=ui, plan_steps=(api, ui))
    assert plan.upstream_paths == ("proposed/erp-system/api.php",)
    merged = merge_discovery_paths(
        mutation_paths=ui.target_scope,
        upstream_paths=plan.upstream_paths,
    )
    assert "proposed/erp-system/api.php" in merged
    assert "proposed/erp-system/index.html" in merged

    api_path = tmp_path / "proposed" / "erp-system" / "api.php"
    api_path.parent.mkdir(parents=True, exist_ok=True)
    api_path.write_text("<?php\nfunction products_list() {}\n", encoding="utf-8")
    (tmp_path / "proposed" / "erp-system" / "index.html").write_text(
        "<html></html>", encoding="utf-8"
    )

    events: list[dict[str, Any]] = []

    def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append({"event": name, **payload})

    validated = _plan_and_validate_upstream_discovery(
        step_contract=ui,
        plan_steps=(api, ui),
        repository_root=tmp_path,
        looks_like_a_file=lambda p: "." in Path(p).name,
        events=events,
        on_event=on_event,
    )
    assert validated.upstream_paths == ("proposed/erp-system/api.php",)
    assert any(e.get("event") == "discovery.upstream_planned" for e in events)

    toolbox = RepositoryToolbox(repository_root=tmp_path, on_event=lambda *_a, **_k: None)
    report = run_bound_files_discovery(
        toolbox=toolbox,
        target_paths=list(merged),
        objective="wire UI to API",
    )
    assert "proposed/erp-system/api.php" in report.config_files
    assert "products_list" in report.config_files["proposed/erp-system/api.php"]


def test_w2_t2_no_depends_on_targets_only() -> None:
    """W2-T2: No depends_on → discovery unchanged (targets only)."""
    step = _contract(
        id="step-db",
        purpose="Create DB",
        target_scope=("proposed/erp-system/database.sql",),
        depends_on=(),
    )
    plan = resolve_upstream_target_paths(
        step_contract=step,
        plan_steps=(step,),
    )
    assert plan.upstream_paths == ()
    assert plan.depends_on_step_ids == ()
    merged = merge_discovery_paths(
        mutation_paths=step.target_scope,
        upstream_paths=plan.upstream_paths,
    )
    assert merged == ("proposed/erp-system/database.sql",)


@pytest.mark.asyncio
async def test_w2_t3_missing_upstream_need_upstream(tmp_path: Path) -> None:
    """W2-T3: Missing upstream file → NEED_UPSTREAM (fail closed, no invent)."""
    api = _contract(
        id="step-api",
        step_index=1,
        target_scope=("proposed/erp-system/api.php",),
    )
    ui = _contract(
        id="step-ui",
        step_index=2,
        purpose="Create UI that calls API",
        target_scope=("proposed/erp-system/index.html",),
        depends_on=("step-api",),
        acceptance_criteria=("proposed/erp-system/index.html exists",),
        validation_commands=("test -f proposed/erp-system/index.html",),
    )
    # UI target may exist; API upstream must be absent → fail closed.
    ui_file = tmp_path / "proposed" / "erp-system" / "index.html"
    ui_file.parent.mkdir(parents=True, exist_ok=True)
    ui_file.write_text("<html></html>", encoding="utf-8")

    events: list[dict[str, Any]] = []

    def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append({"event": name, **payload})

    toolbox = RepositoryToolbox(repository_root=tmp_path, on_event=on_event)

    with pytest.raises(MultiStepMutationError) as ei:
        await run_multi_step_mutation(
            objective="Create UI that calls API",
            toolbox=toolbox,
            repository_root=tmp_path,
            settings=None,
            model_override=None,
            budget_mgr=None,
            on_event=on_event,
            events=events,
            allowed_files=("proposed/erp-system/index.html",),
            step_contract=ui,
            plan_steps=(api, ui),
            evaluate_completion_gates=lambda **_k: {"ok": True},
            infer_test_command=lambda _s: None,
            normalize_repo_relative_path=lambda p: str(p).replace("\\", "/").lstrip("./"),
            looks_like_a_file=lambda p: "." in Path(str(p)).name,
            extract_target_path=lambda _o: None,
            resolve_relative_path=lambda p, _r: str(p),
            apply_edits=False,
            max_rounds=1,
        )
    assert ei.value.code == "NEED_UPSTREAM"
    assert "api.php" in ei.value.detail
    assert any(e.get("event") == "discovery.upstream_missing" for e in events)


def test_w2_t3_unresolved_depends_on_need_upstream(tmp_path: Path) -> None:
    """W2-T3 variant: depends_on id not in plan_steps → NEED_UPSTREAM."""
    ui = _contract(
        id="step-ui",
        depends_on=("step-api-missing",),
        target_scope=("proposed/erp-system/index.html",),
    )
    events: list[dict[str, Any]] = []

    def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append({"event": name, **payload})

    with pytest.raises(MultiStepMutationError) as ei:
        _plan_and_validate_upstream_discovery(
            step_contract=ui,
            plan_steps=(ui,),
            repository_root=tmp_path,
            looks_like_a_file=lambda p: "." in Path(p).name,
            events=events,
            on_event=on_event,
        )
    assert ei.value.code == "NEED_UPSTREAM"
    assert "unresolved" in ei.value.detail


def test_w2_t4_upstream_body_truncated_with_hash() -> None:
    """W2-T4: Upstream body truncated deterministically; hash recorded."""
    big = "A" * 50_000
    trunc = truncate_upstream_content(big, max_bytes=1024, path="proposed/erp-system/api.php")
    assert trunc.truncated
    assert trunc.original_bytes == 50_000
    assert trunc.kept_bytes <= 1024
    assert trunc.content_sha256_12
    assert "SEM-001-W2 upstream truncated" in trunc.text
    assert trunc.content_sha256_12 in trunc.text
    # Deterministic across calls
    trunc2 = truncate_upstream_content(big, max_bytes=1024, path="proposed/erp-system/api.php")
    assert trunc2.text == trunc.text
    assert trunc2.content_sha256_12 == trunc.content_sha256_12

    report = DiscoveryReport(config_files={"proposed/erp-system/api.php": big})
    events: list[dict[str, Any]] = []

    def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append({"event": name, **payload})

    capped = _apply_upstream_body_caps(
        report=report,
        upstream_paths=("proposed/erp-system/api.php",),
        max_bytes=1024,
        on_event=on_event,
    )
    assert len(capped.config_files["proposed/erp-system/api.php"].encode("utf-8")) < 50_000
    trunc_events = [e for e in events if e.get("event") == "discovery.upstream_truncated"]
    assert trunc_events
    assert trunc_events[0]["sha256_12"] == trunc.content_sha256_12
    assert trunc_events[0]["truncated"] is True
