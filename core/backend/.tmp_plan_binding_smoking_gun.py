"""Smoking-gun packet: OutcomeContract → Plan/Binding (architecture frozen).

1) Code-path facts (no LLM)
2) Bind typical file-level ERP-like plan to the *live LLM* attendance contract
3) Live provenance planner call for attendance greenfield (if Integration up)
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.outcome_contract_proposer import (
    bind_steps_to_contract,
    seal_from_raw_proposal,
)
from backend.aix_core.runtime.objective.provenance.admit import freeze_user_intent
from backend.aix_core.runtime.objective.provenance.plan_request import (
    build_plan_request,
    default_governance,
)
from backend.aix_core.runtime.objective.provenance.understanding import build_understanding_v1
from backend.aix_core.runtime.planner.step_contract_planner import propose_step_contract
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage
from backend.hyeve_aix_service.config import settings
import inspect
from backend.aix_core.runtime.planner import step_contract_planner as scp

OUT = Path(__file__).resolve().parent / ".tmp_plan_binding_smoking_gun.json"
LLM_ARTIFACT = Path(__file__).resolve().parent / ".tmp_llm_vs_heuristic_attendance.json"

OBJ = (
    "Build an attendance registration system under proposed/attendance-system/. "
    "Governance create_root = proposed/. CREATE index.html, styles.css, app.js, api.php, "
    "config.php, database.sql, and README.md. Frontend, PHP backend and MySQL. "
    "Staff must register students, view attendance, and submit attendance records."
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=200_000, cost_usd=10.0, retries=3, steps=20, concurrency=1)


def _step(i: int, purpose: str, scope: tuple[str, ...]) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc_probe",
        tenant_id="t",
        step_index=i,
        purpose=purpose,
        target_scope=scope,
        acceptance_criteria=("artifact exists",),
        validation_commands=("echo ok",),
        expected_output_schema_ref="text",
        assigned_role="executor",
        step_budget=_budget(),
        status="PENDING",
    )


def summarize(steps) -> list[dict]:
    return [
        {
            "step_index": s.step_index,
            "purpose": s.purpose,
            "target_scope": list(s.target_scope),
            "facet_refs": list(s.facet_refs),
            "operation_refs": list(s.operation_refs),
            "entity_refs": list(s.entity_refs),
            "mapping_refs": list(s.mapping_refs),
            "binding_role": s.binding_role,
        }
        for s in steps
    ]


async def login() -> tuple[str, str, str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "http://127.0.0.1:8222/v1/auth/login",
            headers={"Content-Type": "application/json", "X-Hyeve-Tenant-Id": "ydaiya"},
            json={"username": "ydaiya", "password": "Simple@123"},
        )
        resp.raise_for_status()
        body = resp.json()
    token = str(body.get("access_token") or body.get("token") or "")
    parts = token.split(".")
    pad = "=" * ((4 - (len(parts[1]) % 4)) % 4)
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    return token, str(payload.get("tenant_id") or ""), str(payload.get("sub") or "")


async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    raw = json.loads(LLM_ARTIFACT.read_text(encoding="utf-8"))["llm_no_fallback"]["raw"]
    sealed = seal_from_raw_proposal(
        objective_id="obj_att_bind", raw=raw, source="llm", need_auth=True
    )
    assert sealed.ok and sealed.contract
    contract = sealed.contract
    ui_ops = list(contract.facets.UI.required_operations)
    ui_views = list(contract.facets.UI.required_views)

    # --- A) Code path facts ---
    sig = inspect.signature(scp.propose_step_contract)
    code_path = {
        "propose_step_contract_params": list(sig.parameters),
        "has_outcome_contract_param": "outcome_contract" in sig.parameters,
        "bind_steps_creates_new_steps": False,
        "bind_steps_behavior": (
            "Annotates existing steps by file-extension/token heuristics; "
            "dumps ALL UI ops onto any UI-looking step; does not emit one step per operation/view."
        ),
        "durable_api_order": [
            "1. propose_step_contract (no OutcomeContract input)",
            "2. _ensure_outcome_contract (seal)",
            "3. bind_steps_to_contract (retrofit refs)",
            "4. CoverageGate / evaluate_plan_quality",
        ],
        "planner_prompt_greenfield_rule_13": (
            "When greenfield_page_generation: propose exactly ONE step with "
            "content.generate_html_css into proposed/ — not per-view UI ops."
        ),
    }

    # --- B) Typical ERP-like FILE plan bound to LIVE LLM contract ---
    file_plan = (
        _step(0, "Create frontend shell", ("proposed/attendance-system/index.html", "proposed/attendance-system/styles.css", "proposed/attendance-system/app.js")),
        _step(1, "Create PHP API backend", ("proposed/attendance-system/api.php", "proposed/attendance-system/config.php")),
        _step(2, "Create MySQL schema", ("proposed/attendance-system/database.sql",)),
        _step(3, "Write README", ("proposed/attendance-system/README.md",)),
    )
    bound_file = bind_steps_to_contract(steps=file_plan, contract=contract)
    cov_file = evaluate_contract_coverage(
        outcome_contract=contract, steps=bound_file, require_outcome_contract=True
    )
    per_op = {
        op: {
            "dedicated_step": any(
                set(s.operation_refs) == {op} and "UI" in s.facet_refs for s in bound_file
            ),
            "bundled_on_frontend_step": any(
                op in s.operation_refs and "UI" in s.facet_refs and set(ui_ops).issubset(set(s.operation_refs))
                for s in bound_file
            ),
        }
        for op in ui_ops
    }
    per_view = {
        v: any(v in s.purpose.casefold() or v in " ".join(s.target_scope).casefold() for s in bound_file)
        for v in ui_views
    }

    # --- C) Live provenance planner ---
    live: dict = {"attempted": False}
    try:
        token, tenant_id, actor_id = await login()
        intent = freeze_user_intent(
            objective_id="obj_att_bind",
            text=OBJ,
            definition_of_done=list(contract.definition_of_done),
            dod_user_authored=True,
        )
        gov = default_governance(
            objective_id="obj_att_bind",
            task_class="code_change",
            budgets=_budget(),
        )
        # Ensure create capability for proposed/
        gov = gov.model_copy(
            update={
                "create_root": ("proposed/",),
                "create_capability": ("CREATE_FILE",),
            }
        )
        und = build_understanding_v1(intent=intent, governance=gov)
        request = build_plan_request(
            cycle_id="cyc_att_bind",
            intent=intent,
            turns=(),
            understanding=und.revision,
            evidence=(),
            governance=gov,
        )
        understanding = UnderstandingEvidenceV1(
            objective_id="obj_att_bind",
            cycle_id="cyc_att_bind",
            files_read=(),
            symbols_found=(),
            observed_problem=OBJ,
            understanding_complete=True,
        )
        proposal = await propose_step_contract(
            objective=OBJ,
            definition_of_done=list(contract.definition_of_done),
            understanding=understanding,
            constraints=[],
            tenant_id=tenant_id,
            cycle_id="cyc_att_bind",
            step_index=0,
            assigned_role="executor",
            step_budget=_budget(),
            actor_id=actor_id,
            authorization_header=f"Bearer {token}",
            timeout_seconds=120.0,
            objective_id="obj_att_bind",
            plan_request=request,
            intent=intent,
            understanding_revision=und.revision,
            needs=(),
            exploration_evidence=(),
            governance=gov,
        )
        pre = list(proposal.plan.steps) if proposal.plan is not None else (
            [proposal.contract] if proposal.contract is not None else []
        )
        bound_live = bind_steps_to_contract(steps=pre, contract=contract) if pre else ()
        cov_live = evaluate_contract_coverage(
            outcome_contract=contract, steps=bound_live, require_outcome_contract=True
        )
        live = {
            "attempted": True,
            "planner_api": settings.RUNTIME_PLANNER_API_URL,
            "reason": proposal.reason,
            "reply": (proposal.reply or "")[:1500],
            "provenance_decision": proposal.provenance_decision,
            "step_count": len(pre),
            "steps_pre_bind": summarize(pre),
            "steps_post_bind": summarize(bound_live),
            "coverage_ok": cov_live.ok,
            "coverage_code": cov_live.code,
            "coverage_detail": cov_live.detail,
            "mentions_register_student": any(
                "register_student" in json.dumps(summarize(pre)).casefold() for _ in [0]
            ),
            "mentions_ui_views": {
                v: any(v in json.dumps(summarize(pre)).casefold() for _ in [0]) for v in ui_views
            },
        }
    except Exception as exc:  # noqa: BLE001
        live = {"attempted": True, "error": f"{type(exc).__name__}:{exc}"}

    payload = {
        "outcome_contract_ui": {"operations": ui_ops, "views": ui_views},
        "code_path": code_path,
        "file_level_plan_bound_to_llm_contract": {
            "steps": summarize(bound_file),
            "coverage_ok": cov_file.ok,
            "coverage_code": cov_file.code,
            "per_ui_operation": per_op,
            "per_ui_view_has_step": per_view,
            "verdict": (
                "Coverage can PASS while every UI op is bundled onto one frontend file step; "
                "no dedicated IMPLEMENT UI student_registration / attendance_view / "
                "attendance_submission steps exist."
            ),
        },
        "live_provenance_planner": live,
        "diagnosis": {
            "contract_intelligence": "GOOD (prior experiment)",
            "plan_driven_by_outcome_ops": False,
            "where_frontend_disappears": (
                "Between OutcomeContract and StepContracts: planner decomposes by "
                "files/PlanTargets (index.html/api.php/database.sql), not by "
                "register_student/view_attendance/submit_attendance. "
                "bind_steps_to_contract only labels file steps after the fact."
            ),
            "next_rung": "Plan/binding decomposition from sealed contract (not executor yet)",
        },
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    print("wrote", OUT)
    print("=== CODE PATH ===")
    print("outcome_contract param?", code_path["has_outcome_contract_param"])
    print("=== FILE PLAN BINDING ===")
    for s in summarize(bound_file):
        print(f"  {s['step_index']}: {s['purpose']!r} facets={s['facet_refs']} ops={s['operation_refs']}")
    print("coverage", cov_file.ok, cov_file.code)
    print("per_op", json.dumps(per_op, indent=2))
    print("per_view", per_view)
    print("=== LIVE PLANNER ===")
    print(json.dumps({k: live.get(k) for k in ("reason", "step_count", "coverage_code", "error", "reply") if k in live or live.get("error")}, indent=2)[:2000])
    if live.get("steps_pre_bind"):
        for s in live["steps_pre_bind"]:
            print(f"  live {s['step_index']}: {s['purpose']!r} scope={s['target_scope']}")


if __name__ == "__main__":
    asyncio.run(main())
