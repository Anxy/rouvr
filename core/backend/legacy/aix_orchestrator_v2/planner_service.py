from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.legacy.aix_orchestrator_v2.discipline import (
    build_active_packet,
    compact_recon_for_active_packet,
    run_planner_prechecks,
    validate_active_packet,
)
from backend.aix_orchestrator_v2.models import PlannerResult
from backend.models.planner_slice_packet_models import (
    PlannerAllowedSurfaceAnchorCurrent,
    PlannerAllowedSurfaceCurrent,
    PlannerOutputSchemaCurrent,
    PlannerSlicePacketCurrent,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _acceptance_criteria(task_payload: dict[str, Any], recon: dict[str, Any]) -> list[str]:
    contract = _as_dict(recon.get("execution_contract"))
    for candidate in (
        contract.get("acceptance_criteria"),
        _as_dict(task_payload.get("metadata_json")).get("acceptance_criteria"),
    ):
        if isinstance(candidate, list):
            items = [str(item).strip() for item in candidate if str(item).strip()]
            if items:
                return items[:4]
    summary = str(task_payload.get("description") or task_payload.get("title") or "").strip()
    fallback = [summary] if summary else ["complete the bounded code change safely"]
    validation_target = str(recon.get("validation_target") or "").strip()
    if validation_target:
        fallback.append(f"validation must pass via `{validation_target}`")
    return fallback[:4]


def _read_trace_payload(trace_ref: str | None) -> dict[str, Any]:
    path_text = str(trace_ref or "").strip()
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _step_args(step: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(step, dict):
        return {}
    args = step.get("args")
    return dict(args) if isinstance(args, dict) else {}


def _step_tool(step: dict[str, Any] | None) -> str:
    if not isinstance(step, dict):
        return ""
    return str(step.get("tool") or step.get("type") or "").strip()


def _step_relative_path(step: dict[str, Any] | None) -> str:
    args = _step_args(step)
    return (
        str(args.get("relative_path") or args.get("file") or (step or {}).get("file") or "")
        .strip()
        .replace("\\", "/")
        .strip("/")
    )


def _step_symbol(step: dict[str, Any] | None) -> str:
    args = _step_args(step)
    return str(
        args.get("symbol")
        or args.get("qualified_symbol")
        or args.get("function_name")
        or args.get("target_symbol")
        or (step or {}).get("symbol")
        or (step or {}).get("qualified_symbol")
        or ""
    ).strip()


def _observed_symbol_source(
    *,
    workspace_root: str,
    relative_path: str,
    symbol_name: str,
    preread_step: dict[str, Any],
    planner_packet: dict[str, Any],
) -> str:
    read_args = _step_args(preread_step)
    result = preread_step.get("result") if isinstance(preread_step.get("result"), dict) else {}
    for candidate in (
        preread_step.get("output_content"),
        read_args.get("output_content"),
        result.get("content") if isinstance(result, dict) else None,
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    from backend.services.devexecutor_python_symbol_service import resolve_python_symbol_range

    try:
        symbol_range = resolve_python_symbol_range(
            file_path=Path(workspace_root) / relative_path,
            symbol_name=str(read_args.get("symbol") or symbol_name).strip(),
        )
    except Exception:
        anchor = _as_dict(_as_dict(planner_packet.get("allowed_surface")).get("anchor"))
        return str(anchor.get("compact_snippet") or "").strip()
    return str(symbol_range.symbol_source or "").strip()


def _materialize_replace_function_steps(
    *,
    steps: list[dict[str, Any]],
    planner_packet: dict[str, Any],
    workspace_root: str,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    provider: str | None,
    model: str | None,
) -> list[dict[str, Any]]:
    if str(planner_packet.get("planner_mode") or "").strip() != "packet_bound_replace_function":
        return steps
    target_file = str(planner_packet.get("target_file") or "").strip().replace("\\", "/").strip("/")
    target_symbol = str(planner_packet.get("target_symbol") or "").strip()
    if not target_file or not target_symbol:
        return steps

    materialized = [dict(step) for step in steps]
    for index, step in enumerate(materialized):
        if _step_tool(step) != "replace_function":
            continue
        args = _step_args(step)
        if str(args.get("replacement") or "").strip():
            raise ValueError("PLANNER_REPLACE_FUNCTION_DIRECT_BODY_FORBIDDEN")
        relative_path = _step_relative_path(step) or target_file
        qualified_symbol = _step_symbol(step) or target_symbol
        if relative_path != target_file:
            raise ValueError("EDIT_FILE_WRONG_TARGET")
        if qualified_symbol != target_symbol:
            raise ValueError("REPLACE_FUNCTION_WRONG_SYMBOL")
        preread = materialized[index - 1] if index > 0 else None
        if _step_tool(preread) != "read_range" or _step_relative_path(preread) != target_file:
            raise ValueError("REPLACE_FUNCTION_MISSING_PREREAD")
        if not isinstance(preread, dict):
            raise ValueError("REPLACE_FUNCTION_MISSING_PREREAD")
        preread_symbol = _step_symbol(preread)
        if preread_symbol and preread_symbol != target_symbol:
            raise ValueError("REPLACE_FUNCTION_MISSING_PREREAD")
        observed_source = _observed_symbol_source(
            workspace_root=workspace_root,
            relative_path=target_file,
            symbol_name=target_symbol,
            preread_step=preread,
            planner_packet=planner_packet,
        )
        from backend.services.task_runner_planner_validation import _materialize_replace_function_rewrite

        materialized[index] = _materialize_replace_function_rewrite(
            step=step,
            packet=planner_packet,
            workspace_root=workspace_root,
            observed_symbol_source=observed_source,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            provider=str(provider or "").strip() or "openrouter",
            model=model,
            authorization=None,
        )
        break
    return materialized


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _compact_anchor_snippet(value: Any) -> str:
    lines = [line.rstrip() for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()]
    compact_lines: list[str] = []
    current_bytes = 0
    for line in lines:
        if not line.strip():
            continue
        line_bytes = len((line + "\n").encode("utf-8"))
        if compact_lines and current_bytes + line_bytes > 800:
            break
        if not compact_lines and line_bytes > 800:
            return line.encode("utf-8")[:800].decode("utf-8", errors="ignore").strip()
        compact_lines.append(line)
        current_bytes += line_bytes
        if len(compact_lines) >= 10:
            break
    return "\n".join(compact_lines).strip()


def _bounded_planner_text(*values: Any, fallback: str, max_chars: int = 240) -> str:
    text = next((str(value or "").strip() for value in values if str(value or "").strip()), fallback)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _task_metadata_mutation_values(task_payload: dict[str, Any]) -> list[str]:
    metadata = _as_dict(task_payload.get("metadata_json"))
    task_metadata = _as_dict(metadata.get("task_metadata_json"))
    values: list[str] = []
    for source in (task_metadata, metadata, task_payload):
        for key in ("mutation_instructions", "behavior_change"):
            text = str(source.get(key) or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def _synthetic_edge_intent_reason(*, planner_packet: PlannerSlicePacketCurrent, task_shape_text: str) -> str | None:
    validation_command = str(planner_packet.validation_command or "").strip().lower()
    if not validation_command.startswith("python -c"):
        return None
    if "block before execution" not in validation_command and "reject before execution" not in validation_command:
        return None
    no_op_markers = (
        "no-op",
        "no op",
        "meaningless mutation intent",
        "non-behavior-bearing intent",
        "must block early",
    )
    broad_markers = (
        "overly broad",
        "broad unsafe task",
        "arbitrary narrowing",
        "reject early",
        "bounded clarification",
    )
    if any(marker in task_shape_text for marker in no_op_markers):
        return "NO_OP_INTENT_REJECTED: task intent is non-behavior-bearing and must be rejected before planning"
    if any(marker in task_shape_text for marker in broad_markers):
        return "BROAD_INTENT_REJECTED: task intent is overly broad and requires explicit narrowing before planning"
    return None


def _mutation_shaping_reason(*, task_payload: dict[str, Any], planner_packet: PlannerSlicePacketCurrent) -> str | None:
    target_file = str(planner_packet.target_file or "").strip().lower()
    if not target_file.endswith(".py"):
        return None
    bounded_goal = str(planner_packet.bounded_mutation_goal or "").strip()
    metadata = _as_dict(task_payload.get("metadata_json"))
    metadata_task = _as_dict(metadata.get("task_metadata_json"))
    contract = _as_dict(task_payload.get("active_execution_contract"))
    execution_slice = _as_dict(contract.get("execution_slice"))
    metadata_behavior = str(metadata_task.get("behavior_change") or metadata.get("behavior_change") or "").strip()
    metadata_instructions = str(
        metadata_task.get("mutation_instructions") or metadata.get("mutation_instructions") or ""
    ).strip()
    execution_slice_summary = str(execution_slice.get("summary") or "").strip()
    acceptance_text = "\n".join(_text_items(metadata.get("acceptance_criteria")))
    task_shape_text = "\n".join(
        [
            str(task_payload.get("title") or "").strip(),
            str(task_payload.get("description") or "").strip(),
            acceptance_text,
        ]
    ).lower()
    synthetic_edge_reason = _synthetic_edge_intent_reason(
        planner_packet=planner_packet,
        task_shape_text=task_shape_text,
    )
    if synthetic_edge_reason:
        return synthetic_edge_reason
    proof_shape_markers = (
        "proof",
        "bounded deterministic edit",
        "declared validation command",
        "expected outcome",
    )
    if not any(marker in task_shape_text for marker in proof_shape_markers):
        return None
    combined = "\n".join(
        [
            metadata_instructions,
            bounded_goal,
            metadata_behavior,
            execution_slice_summary,
            acceptance_text,
        ]
    ).lower()
    if metadata_instructions:
        return None
    executable_markers = (
        "assert ",
        "return ",
        "raise ",
        " == ",
        " != ",
        "if ",
        "elif ",
        "branch",
        "condition",
        "logic",
        "flag",
        "input",
        "output",
        "test assertion",
        "behavior-bearing",
        "executable",
    )
    if any(marker in combined for marker in executable_markers):
        return None
    generic_behavior = {
        "",
        "bounded behavior-preserving change required",
        str(task_payload.get("description") or "").strip(),
        str(task_payload.get("title") or "").strip(),
    }
    generic_metadata_behavior = set(generic_behavior)
    generic_metadata_behavior.add(metadata_behavior)
    if bounded_goal in generic_behavior and metadata_behavior in generic_metadata_behavior:
        return "MUTATION_TASK_SHAPING_REQUIRED: explicit behavior-bearing mutation instructions missing"
    return None


def _planner_prompt(task_payload: dict[str, Any], recon: dict[str, Any], planner_packet: dict[str, Any]) -> str:
    validation_target = str(recon.get("validation_target") or "").strip()
    target_file = str(recon.get("target_file") or "").strip()
    target_symbol = str(recon.get("target_symbol") or "").strip()
    objective = str(planner_packet.get("objective") or task_payload.get("title") or "").strip()
    description = str(task_payload.get("description") or "").strip()
    constraints = list(planner_packet.get("constraints") or [])
    prompt_lines = [
        f"Objective: {objective}",
        f"Task description: {description}" if description else "",
        f"Primary target file: {target_file}" if target_file else "",
        f"Primary target symbol: {target_symbol}" if target_symbol else "",
        f"Validation command: {validation_target}" if validation_target else "",
        "Plan strictly from recon and the planner packet.",
        "Return bounded execution steps only for the declared files and validation.",
    ]
    if constraints:
        prompt_lines.append("Constraints: " + "; ".join(str(item) for item in constraints))
    return "\n".join(line for line in prompt_lines if line)


def build_planner_packet(*, task_payload: dict[str, Any], recon: dict[str, Any]) -> PlannerSlicePacketCurrent:
    task_number = int(task_payload.get("task_number") or 0) or 1
    contract = _as_dict(recon.get("execution_contract"))
    anchor_payload = _as_dict(recon.get("anchor"))
    target_file = str(recon.get("target_file") or contract.get("target_file") or "").strip()
    if not target_file:
        raise ValueError("RECON_TARGET_FILE_REQUIRED")
    validation_command = str(recon.get("validation_target") or "").strip()
    if not validation_command:
        raise ValueError("PLANNER_VALIDATION_COMMAND_MISSING")
    target_symbol = str(recon.get("target_symbol") or "").strip() or None
    if str(anchor_payload.get("output_content") or "").strip():
        compact_snippet = _compact_anchor_snippet(anchor_payload.get("output_content"))
    elif target_symbol:
        compact_snippet = f"symbol anchor pending: {target_symbol}"
    else:
        compact_snippet = f"file anchor pending: {target_file}"
    if not compact_snippet:
        compact_snippet = target_file
    objective = _bounded_planner_text(
        contract.get("objective"),
        task_payload.get("title"),
        fallback=f"Task {task_number}",
    )
    bounded_mutation_goal = _bounded_planner_text(
        contract.get("mutation_instructions"),
        contract.get("behavior_change"),
        *_task_metadata_mutation_values(task_payload),
        task_payload.get("description"),
        contract.get("objective"),
        fallback="bounded behavior-preserving change required",
    )
    if bounded_mutation_goal.strip().lower() == objective.strip().lower():
        bounded_mutation_goal = "bounded behavior-preserving change required"
    return PlannerSlicePacketCurrent(
        task_number=task_number,
        slice_id=str(contract.get("slice_id") or f"task-{task_number}-primary"),
        objective=objective,
        target_file=target_file,
        target_symbol=target_symbol,
        bounded_mutation_goal=bounded_mutation_goal,
        validation_command=validation_command,
        allowed_surface=PlannerAllowedSurfaceCurrent(
            anchor=PlannerAllowedSurfaceAnchorCurrent(symbol=target_symbol, compact_snippet=compact_snippet)
        ),
        planner_mode="packet_bound_replace_function" if target_symbol else "generic",
        output_schema=PlannerOutputSchemaCurrent(
            type="replace_function" if target_symbol else "edit",
            fields=["target_file", "target_symbol", "bounded_mutation_goal", "validation_command"],
        ),
        hard_failure_rules=[
            "do not edit outside target_file",
            "do not skip validation_command",
        ],
    )


def plan_task(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    workspace_root: str,
    task_payload: dict[str, Any],
    recon: dict[str, Any],
) -> dict[str, Any]:
    canonical_contract = _as_dict(recon.get("execution_contract"))
    initial_input_summary = {
        "objective": str(task_payload.get("title") or task_payload.get("description") or "").strip(),
        "target_file": str(recon.get("target_file") or "").strip() or None,
        "target_symbol": str(recon.get("target_symbol") or "").strip() or None,
        "validation_command": str(recon.get("validation_target") or "").strip() or None,
    }
    try:
        planner_packet = build_planner_packet(task_payload=task_payload, recon=recon)
    except ValueError as exc:
        reason = str(exc).strip() or "PLANNER_PACKET_INVALID"
        return PlannerResult(
            status="blocked",
            blocked_reason=reason,
            planner_packet={},
            planner_trace={"validation_error": reason},
            steps=[],
            assumptions=[],
            input_summary=initial_input_summary,
            raw_output_text=None,
            normalized_plan=[],
            validation_failures=[reason],
            trace_artifact_ref=None,
        ).model_dump(mode="python")
    active_packet = build_active_packet(
        control={
            "run_id": correlation_id,
            "planner_requires_recon": True,
            "phase": "planner",
        },
        canonical_task_contract=canonical_contract,
        canonical_recon=compact_recon_for_active_packet(recon),
        current_state_summary={
            "task_number": task_payload.get("task_number"),
            "objective": initial_input_summary["objective"],
            "status": "planner_precheck",
            "current_phase": "planner",
            "task_status": str(task_payload.get("status") or "").strip() or None,
            "readiness_state": str(
                _as_dict(_as_dict(task_payload.get("metadata_json")).get("readiness")).get("readiness_state") or ""
            ).strip()
            or None,
        },
    )
    packet_validation = validate_active_packet(active_packet)
    if not packet_validation.ok:
        reason = str(packet_validation.reason_code or "ACTIVE_PACKET_INVALID")
        return PlannerResult(
            status="blocked",
            blocked_reason=reason,
            planner_packet=active_packet,
            planner_trace={"validation_error": reason, "packet_validation": packet_validation.payload or {}},
            steps=[],
            assumptions=[],
            input_summary={
                **initial_input_summary,
                "active_packet_validator_sha": packet_validation.validator_sha,
                "active_packet_bytes": int(_as_dict(packet_validation.payload).get("packet_bytes") or 0),
            },
            raw_output_text=None,
            normalized_plan=[],
            validation_failures=[reason],
            trace_artifact_ref=None,
        ).model_dump(mode="python")
    shaping_reason = _mutation_shaping_reason(task_payload=task_payload, planner_packet=planner_packet)
    if shaping_reason:
        return PlannerResult(
            status="blocked",
            blocked_reason=shaping_reason,
            planner_packet=planner_packet.model_dump(mode="python"),
            planner_trace={"validation_error": shaping_reason, "shaping_precheck": "mutation_task_contract"},
            steps=[],
            assumptions=[],
            input_summary={
                **initial_input_summary,
                "active_packet_validator_sha": packet_validation.validator_sha,
                "active_packet_bytes": int(_as_dict(packet_validation.payload).get("packet_bytes") or 0),
            },
            raw_output_text=None,
            normalized_plan=[],
            validation_failures=[shaping_reason],
            trace_artifact_ref=None,
        ).model_dump(mode="python")
    planner_precheck = run_planner_prechecks(
        task_payload=task_payload,
        canonical_contract=canonical_contract,
        recon=recon,
        planner_packet=planner_packet.model_dump(mode="python"),
        workspace_root=workspace_root,
    )
    if not planner_precheck.ok:
        reason = str(planner_precheck.reason_code or "PLANNER_PRECHECK_FAILED")
        classification_prefix = (
            "POLICY_FAIL" if reason in {"move_to_proposal_workspace", "oversized_anchor"} else "SHAPING_FAIL"
        )
        blocked_reason = f"{classification_prefix}: {reason}"
        return PlannerResult(
            status="blocked",
            blocked_reason=blocked_reason,
            planner_packet=planner_packet.model_dump(mode="python"),
            planner_trace={"validation_error": blocked_reason, "planner_precheck": planner_precheck.payload or {}},
            steps=[],
            assumptions=[],
            input_summary={
                **initial_input_summary,
                "active_packet_validator_sha": packet_validation.validator_sha,
                "active_packet_bytes": int(_as_dict(packet_validation.payload).get("packet_bytes") or 0),
                "planner_precheck_sha": planner_precheck.validator_sha,
            },
            raw_output_text=None,
            normalized_plan=[],
            validation_failures=[blocked_reason],
            trace_artifact_ref=None,
        ).model_dump(mode="python")
    from backend.shared.monorepo_imports import ensure_hyeve_core_import_path

    ensure_hyeve_core_import_path()
    from backend.services.devexecutor_planner_bridge_service import PlannerBridgeError, plan_steps_via_llm

    prompt = _planner_prompt(task_payload, recon, planner_packet.model_dump(mode="python"))
    planner_input_summary: dict[str, Any] = {
        "planner_prompt": prompt,
        "target_file": planner_packet.target_file,
        "target_symbol": planner_packet.target_symbol,
        "validation_command": planner_packet.validation_command,
        "active_packet_validator_sha": packet_validation.validator_sha,
        "active_packet_bytes": int(_as_dict(packet_validation.payload).get("packet_bytes") or 0),
        "planner_precheck_sha": planner_precheck.validator_sha,
        "packet_warning_codes": list(packet_validation.warning_codes),
    }
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            workspace_root=workspace_root,
            prompt=prompt,
            planner_packet=planner_packet.model_dump(mode="python"),
            planner_mode=str(getattr(planner_packet, "planner_mode", None) or "generic"),
        )
        steps = _materialize_replace_function_steps(
            steps=steps,
            planner_packet=planner_packet.model_dump(mode="python"),
            workspace_root=workspace_root,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            provider=str(diagnostics.get("provider") or "").strip() or "openrouter",
            model=str(diagnostics.get("model") or "").strip() or None,
        )
    except PlannerBridgeError as exc:
        trace = dict(getattr(exc, "diagnostics", {}) or {})
        trace_payload = _read_trace_payload(trace.get("local_trace_ref"))
        return PlannerResult(
            status="blocked",
            blocked_reason=str(exc),
            planner_packet=planner_packet.model_dump(mode="python"),
            planner_trace=trace,
            steps=[],
            assumptions=[],
            input_summary=planner_input_summary,
            raw_output_text=str(trace_payload.get("raw_output_text") or json.dumps(trace, sort_keys=True)).strip()
            or None,
            normalized_plan=list(trace_payload.get("normalized_steps") or []),
            validation_failures=[str(exc)],
            trace_artifact_ref=str(trace.get("local_trace_ref") or "").strip() or None,
        ).model_dump(mode="python")
    except ValueError as exc:
        reason = str(exc).strip() or "REPLACE_FUNCTION_MATERIALIZATION_FAILED"
        return PlannerResult(
            status="blocked",
            blocked_reason=reason,
            planner_packet=planner_packet.model_dump(mode="python"),
            planner_trace={"validation_error": reason, "materialization_error": reason},
            steps=[],
            assumptions=[],
            input_summary=planner_input_summary,
            raw_output_text=reason,
            normalized_plan=steps if "steps" in locals() else [],
            validation_failures=[reason],
            trace_artifact_ref=None,
        ).model_dump(mode="python")
    trace_payload = _read_trace_payload(diagnostics.get("local_trace_ref"))
    return PlannerResult(
        status="planned",
        steps=steps,
        planner_packet=planner_packet.model_dump(mode="python"),
        planner_trace=diagnostics,
        declared_validation=str(planner_packet.validation_command),
        file_targets=[planner_packet.target_file],
        symbol_targets=[planner_packet.target_symbol] if planner_packet.target_symbol else [],
        assumptions=[],
        blocked_reason=None,
        input_summary=planner_input_summary,
        raw_output_text=str(trace_payload.get("raw_output_text") or json.dumps(diagnostics, sort_keys=True)).strip()
        or None,
        normalized_plan=list(trace_payload.get("normalized_steps") or steps),
        validation_failures=[],
        trace_artifact_ref=str(diagnostics.get("local_trace_ref") or "").strip() or None,
    ).model_dump(mode="python")
