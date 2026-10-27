from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.hyeve_aix_service.config import settings
from backend.services.repo_profile_resolver_service import resolve_effective_repo_profile

PACKET_BYTE_BUDGET = 65_536
ACTIVE_RECON_ANCHOR_LINE_BUDGET = 12
ACTIVE_RECON_ANCHOR_BYTE_BUDGET = 2_048
ALLOWED_ACTIVE_PACKET_KEYS = {
    "control",
    "canonical_task_contract",
    "canonical_recon",
    "current_state_summary",
}
ALLOWED_ACTIVE_STATE_KEYS = {
    "task_number",
    "objective",
    "status",
    "current_phase",
    "task_status",
    "readiness_state",
    "current_classification",
}
ALLOWED_ACTIVE_RECON_KEYS = {
    "target_file",
    "target_symbol",
    "anchor",
    "validation_target",
}
FORBIDDEN_PACKET_KEYS = {
    "verification",
    "writeback",
    "artifacts",
    "history",
    "historical_state",
    "prior_runs",
    "run_state_dump",
}
SEMANTIC_TERMINAL_STATES = {
    "CODE_FAIL": "needs_fix",
    "INFRA_FAIL": "blocked_by_infra",
    "POLICY_FAIL": "needs_shaping",
    "SHAPING_FAIL": "needs_shaping",
    "VERIFICATION_FAIL": "blocked_by_verifier",
}
PYTHON_VALIDATION_SUFFIXES = {".py"}
TEXT_DOCUMENT_VALIDATION_SUFFIXES = {
    ".adoc",
    ".doc",
    ".docx",
    ".md",
    ".markdown",
    ".mdc",
    ".rst",
    ".rtf",
    ".txt",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    return [str(item).strip().replace("\\", "/") for item in list(value or []) if str(item).strip()]


def _contains_affirmative_marker(task_text: str, markers: tuple[str, ...]) -> bool:
    normalized_text = task_text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    negation_prefixes = ("no ", "not ", "do not ", "don't ", "must not ", "should not ", "without ")
    for raw_line in normalized_text.splitlines():
        line = _text(raw_line).lower()
        if not line:
            continue
        normalized = re.sub(r"\s+", " ", line)
        normalized = normalized.lstrip("-*•: ").strip()
        if normalized.startswith(negation_prefixes):
            continue
        for marker in markers:
            if marker not in normalized:
                continue
            if any(f"{prefix}{marker}" in normalized for prefix in negation_prefixes):
                continue
            return True
    return False


def _normalize_symbol_targets(value: Any) -> list[str]:
    items: list[str] = []
    for item in list(value or []):
        if isinstance(item, dict):
            symbol = _text(item.get("symbol") or item.get("qualified_name") or item.get("name"))
        else:
            symbol = _text(item)
        if symbol and symbol not in items:
            items.append(symbol)
    return items


def _candidate_contract_sources(
    task_payload: dict[str, Any],
    execution_contracts: list[dict[str, Any]] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    metadata = _as_dict(task_payload.get("metadata_json"))
    task_packet = _as_dict(task_payload.get("task_packet"))
    candidates = [
        ("active_execution_contract", _as_dict(task_payload.get("active_execution_contract"))),
        ("active_execution_contract", _as_dict(metadata.get("active_execution_contract"))),
        ("active_execution_contract", _as_dict(task_packet.get("active_execution_contract"))),
        ("canonical_task_contract", _as_dict(metadata.get("canonical_task_contract"))),
        ("canonical_task_contract", _as_dict(metadata.get("ticket_contract_current"))),
    ]
    legacy_contract = {}
    target_files = _text_list(metadata.get("target_files"))
    target_file = _text(metadata.get("target_file"))
    if target_file and target_file not in target_files:
        target_files.append(target_file)
    validation_commands = _text_list(metadata.get("validation_commands"))
    validation_command = _text(metadata.get("validation_command"))
    if validation_command and validation_command not in validation_commands:
        validation_commands.append(validation_command)
    if target_files or validation_commands:
        legacy_contract = {
            "target_surface": {"target_files": target_files},
            "execution_slice": {"target_files": target_files, "validation_commands": validation_commands},
            "acceptance_criteria": list(metadata.get("acceptance_criteria") or []),
            "constraints": list(metadata.get("constraints") or []),
            "stop_conditions": list(metadata.get("stop_conditions") or []),
        }
    candidates.append(("legacy_fields", legacy_contract))
    for item in execution_contracts or []:
        candidates.append(("legacy_fields", _as_dict(item)))
    return [(name, contract) for name, contract in candidates if contract]


def _canonical_contract_fields(contract: dict[str, Any]) -> dict[str, list[str]]:
    target_surface = _as_dict(contract.get("target_surface"))
    execution_slice = _as_dict(contract.get("execution_slice"))
    validation_block = _as_dict(contract.get("validation"))
    validation_commands = _text_list(execution_slice.get("validation_commands")) or _text_list(
        contract.get("validation_commands")
    )
    validation_command = _text(validation_block.get("command"))
    if not validation_commands and validation_command:
        validation_commands = [validation_command]
    return {
        "target_files": _text_list(execution_slice.get("target_files"))
        or _text_list(target_surface.get("target_files")),
        "validation_commands": validation_commands,
        "acceptance_criteria": _text_list(contract.get("acceptance_criteria")),
        "constraints": _text_list(contract.get("constraints")),
        "stop_conditions": _text_list(contract.get("stop_conditions")),
        "owning_symbols": _normalize_symbol_targets(target_surface.get("owning_symbols")),
        "prompt_symbol_targets": _normalize_symbol_targets(target_surface.get("prompt_symbol_targets")),
    }


def _find_forbidden_packet_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = _text(key)
            if normalized_key in FORBIDDEN_PACKET_KEYS:
                return normalized_key
            found = _find_forbidden_packet_key(nested)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_forbidden_packet_key(item)
            if found:
                return found
    return None


def _truncate_anchor_output(text: str, *, max_lines: int, max_bytes: int) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""
    lines = normalized.splitlines()
    limited = "\n".join(lines[:max_lines])
    encoded = limited.encode("utf-8")
    if len(encoded) <= max_bytes:
        return limited
    trimmed = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return trimmed.rstrip()


def compact_anchor_for_active_packet(anchor: dict[str, Any]) -> dict[str, Any]:
    anchor_dict = _as_dict(anchor)
    compact = {
        "requested_file_path": _text(anchor_dict.get("requested_file_path")) or None,
        "file_path": _text(anchor_dict.get("file_path")),
        "symbol": _text(anchor_dict.get("symbol")) or None,
        "start_line": anchor_dict.get("start_line"),
        "end_line": anchor_dict.get("end_line"),
        "snapshot_id": _text(anchor_dict.get("snapshot_id")) or None,
        "commit_sha": _text(anchor_dict.get("commit_sha")) or None,
        "symbol_source_hash": _text(anchor_dict.get("symbol_source_hash")) or None,
        "anchor_source": _text(anchor_dict.get("anchor_source")) or None,
        "output_content": _truncate_anchor_output(
            _text(anchor_dict.get("output_content")),
            max_lines=ACTIVE_RECON_ANCHOR_LINE_BUDGET,
            max_bytes=ACTIVE_RECON_ANCHOR_BYTE_BUDGET,
        )
        or None,
    }
    return {key: value for key, value in compact.items() if value not in ("", None, [], {})}


def compact_recon_for_active_packet(recon: dict[str, Any]) -> dict[str, Any]:
    recon_dict = _as_dict(recon)
    compact = {
        "target_file": _text(recon_dict.get("target_file")) or None,
        "target_symbol": _text(recon_dict.get("target_symbol")) or None,
        "anchor": compact_anchor_for_active_packet(_as_dict(recon_dict.get("anchor"))),
        "validation_target": _text(recon_dict.get("validation_target")) or None,
    }
    return {key: value for key, value in compact.items() if value not in ("", None, [], {})}


def compact_contract_for_active_packet(contract: dict[str, Any]) -> dict[str, Any]:
    normalized_contract = json.loads(json.dumps(_as_dict(contract), default=str))
    normalized_contract.setdefault("target_surface", {})
    normalized_contract.setdefault("execution_slice", {})
    execution_slice = _as_dict(normalized_contract.get("execution_slice"))
    validation_commands = _canonical_contract_fields(normalized_contract)["validation_commands"]
    execution_slice["validation_commands"] = validation_commands
    normalized_contract["execution_slice"] = execution_slice
    for key in ("validation_commands", "validation_command", "validation_plan", "validation"):
        normalized_contract.pop(key, None)
    return normalized_contract


def compact_verification_for_task_metadata(
    verification: dict[str, Any],
    *,
    evidence_ref: str,
    machine_proof_refs: list[str],
) -> dict[str, Any]:
    verification_dict = _as_dict(verification)
    return {
        "classification": _text(verification_dict.get("classification")) or None,
        "phase": _text(verification_dict.get("phase")) or None,
        "reason_code": _text(verification_dict.get("reason_code")) or None,
        "summary": _text(verification_dict.get("summary")) or None,
        "evidence_ref": _text(evidence_ref) or None,
        "machine_proof_refs": [str(item).strip() for item in machine_proof_refs if str(item).strip()],
    }


def compact_writeback_for_task_metadata(writeback: dict[str, Any]) -> dict[str, Any]:
    writeback_dict = _as_dict(writeback)
    return {
        "status": _text(writeback_dict.get("status")) or None,
        "workhub_written": bool(writeback_dict.get("workhub_written")),
        "opsbus_written": bool(writeback_dict.get("opsbus_written")),
        "retry_enqueued": bool(writeback_dict.get("retry_enqueued")),
        "knowledge_written": bool(writeback_dict.get("knowledge_written")),
        "analytics_event_written": bool(writeback_dict.get("analytics_event_written")),
    }


@dataclass(frozen=True)
class ValidationOutcome:
    ok: bool
    reason_code: str | None = None
    message: str | None = None
    warning_codes: tuple[str, ...] = ()
    validator_sha: str | None = None
    payload: dict[str, Any] | None = None


def validator_sha(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def resolve_canonical_contract(
    *,
    task_payload: dict[str, Any],
    execution_contracts: list[dict[str, Any]] | None = None,
) -> ValidationOutcome:
    sources = _candidate_contract_sources(task_payload, execution_contracts)
    if not sources:
        return ValidationOutcome(ok=True, payload={"source": "none", "contract": {}})
    chosen_source, chosen_contract = sources[0]
    chosen_fields = _canonical_contract_fields(chosen_contract)
    conflicts: list[str] = []
    for source_name, source_contract in sources[1:]:
        fields = _canonical_contract_fields(source_contract)
        for key in ("target_files", "validation_commands", "acceptance_criteria", "constraints", "stop_conditions"):
            if chosen_fields.get(key) and fields.get(key) and chosen_fields[key] != fields[key]:
                conflicts.append(f"{source_name}:{key}")
    if conflicts:
        payload = {"conflicts": conflicts, "fields": chosen_fields}
        return ValidationOutcome(
            ok=False,
            reason_code="CONTRACT_AMBIGUOUS",
            message="canonical contract fields disagree across sources",
            validator_sha=validator_sha(payload),
            payload=payload,
        )
    normalized_contract = compact_contract_for_active_packet(chosen_contract)
    normalized_contract["target_surface"]["target_files"] = chosen_fields["target_files"]
    normalized_contract["execution_slice"]["target_files"] = chosen_fields["target_files"]
    normalized_contract["execution_slice"]["validation_commands"] = chosen_fields["validation_commands"]
    if chosen_fields["acceptance_criteria"]:
        normalized_contract["acceptance_criteria"] = chosen_fields["acceptance_criteria"]
    if chosen_fields["constraints"]:
        normalized_contract["constraints"] = chosen_fields["constraints"]
    if chosen_fields["stop_conditions"]:
        normalized_contract["stop_conditions"] = chosen_fields["stop_conditions"]
    payload = {"source": chosen_source, "fields": chosen_fields}
    return ValidationOutcome(
        ok=True,
        validator_sha=validator_sha(payload),
        payload={"source": chosen_source, "contract": normalized_contract, "fields": chosen_fields},
    )


def build_active_packet(
    *,
    control: dict[str, Any],
    canonical_task_contract: dict[str, Any],
    canonical_recon: dict[str, Any],
    current_state_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "control": dict(control),
        "canonical_task_contract": compact_contract_for_active_packet(canonical_task_contract),
        "canonical_recon": json.loads(json.dumps(canonical_recon, default=str)),
        "current_state_summary": json.loads(json.dumps(current_state_summary, default=str)),
    }


def validate_active_packet(active_packet: dict[str, Any]) -> ValidationOutcome:
    warnings: list[str] = []
    payload = json.loads(json.dumps(active_packet, default=str))
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    packet_sha = validator_sha(payload)
    extra_keys = sorted(set(payload) - ALLOWED_ACTIVE_PACKET_KEYS)
    if extra_keys:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_EXTRA_SECTIONS",
            message=f"active packet contains non-canonical sections: {', '.join(extra_keys)}",
            validator_sha=packet_sha,
            payload={"extra_keys": extra_keys},
        )
    if len(serialized.encode("utf-8")) > PACKET_BYTE_BUDGET:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_TOO_LARGE",
            message="planner-facing active packet exceeded 64KB budget",
            validator_sha=packet_sha,
            payload={"packet_bytes": len(serialized.encode("utf-8"))},
        )
    forbidden_key = _find_forbidden_packet_key(payload)
    if forbidden_key:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_HISTORICAL_STATE",
            message="historical or terminal state leaked into active packet",
            validator_sha=packet_sha,
            payload={"forbidden_key": forbidden_key},
        )
    contract = _as_dict(payload.get("canonical_task_contract"))
    recon = _as_dict(payload.get("canonical_recon"))
    current_state = _as_dict(payload.get("current_state_summary"))
    extra_state_keys = sorted(set(current_state) - ALLOWED_ACTIVE_STATE_KEYS)
    if extra_state_keys:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_EXTRA_STATE_FIELDS",
            message=f"active packet current_state_summary contains non-canonical fields: {', '.join(extra_state_keys)}",
            validator_sha=packet_sha,
            payload={"extra_state_keys": extra_state_keys},
        )
    extra_recon_keys = sorted(set(recon) - ALLOWED_ACTIVE_RECON_KEYS)
    if extra_recon_keys:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_EXTRA_RECON_FIELDS",
            message=f"active packet recon contains non-canonical fields: {', '.join(extra_recon_keys)}",
            validator_sha=packet_sha,
            payload={"extra_recon_keys": extra_recon_keys},
        )
    contract_fields = _canonical_contract_fields(contract)
    recon_target = _text(recon.get("target_file"))
    if recon_target and contract_fields["target_files"] and recon_target not in contract_fields["target_files"]:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_CONTRADICTORY_STATE",
            message="recon target file does not match canonical contract target surface",
            validator_sha=packet_sha,
            payload={"recon_target_file": recon_target, "contract_target_files": contract_fields["target_files"]},
        )
    anchor = _as_dict(recon.get("anchor"))
    anchor_output = _text(anchor.get("output_content"))
    anchor_bytes = len(anchor_output.encode("utf-8")) if anchor_output else 0
    anchor_lines = len(anchor_output.splitlines()) if anchor_output else 0
    if anchor_bytes > ACTIVE_RECON_ANCHOR_BYTE_BUDGET or anchor_lines > ACTIVE_RECON_ANCHOR_LINE_BUDGET:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_RECON_ANCHOR_TOO_LARGE",
            message="planner-facing recon anchor exceeded compact anchor budget",
            validator_sha=packet_sha,
            payload={"anchor_bytes": anchor_bytes, "anchor_lines": anchor_lines},
        )
    state_status = _text(current_state.get("status")).lower()
    current_phase = _text(current_state.get("current_phase")).lower()
    readiness_state = _text(current_state.get("readiness_state")).lower()
    task_status = _text(current_state.get("task_status")).lower()
    current_classification = _text(current_state.get("current_classification")).lower()
    if state_status == "planner_precheck" and current_phase and current_phase != "planner":
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_CONTRADICTORY_STATE",
            message="planner precheck packet carries a non-planner current phase",
            validator_sha=packet_sha,
            payload={"status": state_status, "current_phase": current_phase},
        )
    if state_status == "planner_precheck" and readiness_state in {"needs_enrichment", "clarification_required"}:
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_CONTRADICTORY_STATE",
            message="task readiness is not planner-runnable",
            validator_sha=packet_sha,
            payload={"status": state_status, "task_status": task_status, "readiness_state": readiness_state},
        )
    if current_classification and state_status == "planner_precheck":
        return ValidationOutcome(
            ok=False,
            reason_code="ACTIVE_PACKET_CONTRADICTORY_STATE",
            message="terminal classification leaked into planner-facing active state",
            validator_sha=packet_sha,
            payload={
                "status": state_status,
                "current_phase": current_phase,
                "task_status": task_status,
                "current_classification": current_classification,
            },
        )
    if payload.get("canonical_task_contract") == payload.get("canonical_recon"):
        warnings.append("semantic_duplicate_sections")
    return ValidationOutcome(
        ok=True,
        warning_codes=tuple(warnings),
        validator_sha=packet_sha,
        payload={"packet_valid": True, "packet_bytes": len(serialized.encode("utf-8"))},
    )


def _looks_like_test_path(path_text: str) -> bool:
    lowered = path_text.lower()
    return "/tests/" in lowered or lowered.endswith("_test.py") or Path(lowered).name.startswith("test_")


def _required_target_files(contract: dict[str, Any]) -> list[str]:
    return _canonical_contract_fields(contract)["target_files"]


def _target_file_covered_by_anchor(*, required_file: str, covered_files: set[str]) -> bool:
    required = _text(required_file).replace("\\", "/").strip("/")
    if not required:
        return True
    for covered_raw in covered_files:
        covered = _text(covered_raw).replace("\\", "/").strip("/")
        if not covered:
            continue
        if covered == required or covered.endswith(f"/{required}") or required.endswith(f"/{covered}"):
            return True
    return False


def validate_recon_admissibility(*, recon: dict[str, Any], canonical_contract: dict[str, Any]) -> ValidationOutcome:
    target_files = _required_target_files(canonical_contract)
    anchor_ranges = [item for item in list(recon.get("anchor_ranges") or []) if isinstance(item, dict)]
    anchor_covered = {
        _text(item.get("file_path") or item.get("target_file"))
        for item in anchor_ranges
        if _text(item.get("file_path") or item.get("target_file"))
    }
    if _text(recon.get("target_file")):
        anchor_covered.add(_text(recon.get("target_file")))
    missing_files = [
        item
        for item in target_files
        if not _target_file_covered_by_anchor(required_file=item, covered_files=anchor_covered)
    ]
    symbol_required = _normalize_symbol_targets(
        _as_dict(canonical_contract.get("target_surface")).get("prompt_symbol_targets")
    )
    if not symbol_required:
        symbol_required = _normalize_symbol_targets(
            _as_dict(canonical_contract.get("target_surface")).get("owning_symbols")
        )
    if symbol_required and not _text(recon.get("target_symbol")):
        symbol_payload = {"missing_symbol": symbol_required[0]}
        return ValidationOutcome(
            ok=False,
            reason_code="required_target_symbol_missing",
            message="symbol-bounded task lacked grounded target symbol",
            validator_sha=validator_sha(symbol_payload),
            payload=symbol_payload,
        )
    if missing_files:
        missing_payload: dict[str, Any] = {
            "missing_target_files": missing_files,
            "covered_target_files": sorted(anchor_covered),
        }
        return ValidationOutcome(
            ok=False,
            reason_code="insufficient_context",
            message="recon coverage did not populate all required target files",
            validator_sha=validator_sha(missing_payload),
            payload=missing_payload,
        )
    if _text(recon.get("context_error")):
        # A raw workspace-file read (anchor_source == "workspace_file_slice")
        # is not CodeIntel grounding -- it carries no citation, no commit
        # verification, no cross-file impact analysis. If CodeIntel itself
        # failed and every anchor is only a local-file fallback slice, this
        # must not be admitted as "ready": that would let a run proceed as if
        # grounded while it has zero real citations behind it. Only a real
        # CodeIntel-sourced anchor (anchor_source == "codeintel_context")
        # counts as valid fallback coverage after a CodeIntel failure.
        has_codeintel_anchor = any(
            _text(item.get("anchor_source")) == "codeintel_context" for item in anchor_ranges
        )
        if not has_codeintel_anchor:
            context_payload = {
                "context_error": _text(recon.get("context_error")),
                "anchor_sources": sorted({_text(item.get("anchor_source")) for item in anchor_ranges} - {""}),
            }
            return ValidationOutcome(
                ok=False,
                reason_code="insufficient_context",
                message="CodeIntel failed and no CodeIntel-grounded anchor is available; a local file read alone is not sufficient grounding",
                validator_sha=validator_sha(context_payload),
                payload=context_payload,
            )
    success_payload: dict[str, Any] = {"target_files": target_files, "anchor_count": len(anchor_ranges)}
    return ValidationOutcome(ok=True, validator_sha=validator_sha(success_payload), payload=success_payload)


def _is_managed_workspace(workspace_root: str) -> bool:
    workspace = Path(str(workspace_root or "").strip())
    if not workspace.exists():
        return False
    if (workspace / ".devexecutor_proposal_workspace.json").exists():
        return True
    normalized_workspace = workspace.resolve().as_posix()
    for raw_root in (
        str(settings.DEVEXECUTOR_PROPOSAL_WORKSPACE_ROOT or "").strip(),
        str(settings.RUNTIME_POOL_CONTAINER_WORKSPACE_ROOT or "").strip(),
    ):
        candidate = raw_root.replace("\\", "/").rstrip("/")
        if candidate and (normalized_workspace == candidate or normalized_workspace.startswith(f"{candidate}/")):
            return True
    return False


def _is_mutation_required(
    contract: dict[str, Any], task_payload: dict[str, Any], planner_packet: dict[str, Any]
) -> bool:
    mutation_kind = _text(planner_packet.get("mutation_kind") or contract.get("mutation_kind")).lower()
    if mutation_kind in {"read_only", "no_mutation"}:
        return False
    workspace_policy = _text(
        contract.get("mutation_workspace_policy")
        or contract.get("workspace_policy")
        or _as_dict(task_payload.get("metadata_json")).get("mutation_workspace_policy")
    ).lower()
    if workspace_policy in {"proposal_required", "managed_proposal_workspace"}:
        return True
    text = "\n".join(
        [
            _text(task_payload.get("title")),
            _text(task_payload.get("description")),
            _text(contract.get("behavior_change")),
            _text(contract.get("mutation_instructions")),
            _text(_as_dict(contract.get("execution_slice")).get("summary")),
            "\n".join(_text_list(contract.get("constraints"))),
            "\n".join(_text_list(contract.get("acceptance_criteria"))),
            "\n".join(_text_list(contract.get("non_goals"))),
        ]
    ).lower()
    if any(marker in text for marker in ("read-only", "readonly", "no mutation", "inspect only")):
        return False
    explicit_markers = [
        _text(contract.get("mutation_instructions")),
        _text(contract.get("behavior_change")),
        _text(planner_packet.get("mutation_instructions")),
        _text(planner_packet.get("behavior_change")),
    ]
    generic_behavior_markers = {
        "",
        "bounded behavior-preserving change required",
        "complete the bounded code change safely",
        _text(task_payload.get("title")),
        _text(task_payload.get("description")),
    }
    explicit_markers = [item for item in explicit_markers if item and item not in generic_behavior_markers]
    if any(marker for marker in explicit_markers):
        return True
    return any(marker in text for marker in ("mutate", "mutation", "change behavior", "behavior-bearing", "executable"))


def run_planner_prechecks(
    *,
    task_payload: dict[str, Any],
    canonical_contract: dict[str, Any],
    recon: dict[str, Any],
    planner_packet: dict[str, Any],
    workspace_root: str,
) -> ValidationOutcome:
    payload = {
        "task_number": task_payload.get("task_number"),
        "target_file": recon.get("target_file"),
        "workspace_root": workspace_root,
    }
    workspace_policy = _text(
        canonical_contract.get("mutation_workspace_policy")
        or canonical_contract.get("workspace_policy")
        or _as_dict(task_payload.get("metadata_json")).get("mutation_workspace_policy")
    ).lower()
    requires_managed_workspace = workspace_policy in {"proposal_required", "managed_proposal_workspace"}
    if (
        requires_managed_workspace
        and bool(settings.DEVEXECUTOR_MUTATION_SANDBOX_REQUIRED)
        and _is_mutation_required(canonical_contract, task_payload, planner_packet)
    ):
        if not _is_managed_workspace(workspace_root):
            return ValidationOutcome(
                ok=False,
                reason_code="move_to_proposal_workspace",
                message="mutation task must move to managed proposal workspace before planner invocation",
                validator_sha=validator_sha(payload),
                payload=payload,
            )
    task_text = "\n".join(
        [
            _text(task_payload.get("title")),
            _text(task_payload.get("description")),
            "\n".join(_text_list(canonical_contract.get("acceptance_criteria"))),
            _text(canonical_contract.get("mutation_instructions")),
            _text(canonical_contract.get("behavior_change")),
        ]
    ).lower()
    if _is_mutation_required(canonical_contract, task_payload, planner_packet):
        if _contains_affirmative_marker(
            task_text,
            (
                "comment-only",
                "comment only",
                "docstring-only",
                "docstring only",
                "format-only",
                "format only",
                "style-only",
                "style only",
            ),
        ):
            return ValidationOutcome(
                ok=False,
                reason_code="non_executable_mutation_intent",
                message="behavior-bearing mutation task cannot be satisfied by comment/docstring/format-only intent",
                validator_sha=validator_sha(payload | {"task_text": task_text}),
                payload={"task_text": task_text},
            )
        if not any(
            marker in task_text
            for marker in (
                "assert ",
                "return ",
                "raise ",
                "branch",
                "logic",
                "condition",
                "behavior",
                "flag",
                "value",
                "causal",
                "executable",
            )
        ):
            return ValidationOutcome(
                ok=False,
                reason_code="vague_mutation_intent",
                message="mutation-required task lacks explicit executable mutation intent",
                validator_sha=validator_sha(payload | {"task_text": task_text}),
                payload={"task_text": task_text},
            )
    requires_paired = any(
        marker in task_text for marker in ("source + test", "source+test", "paired source", "causal linkage")
    )
    target_files = _required_target_files(canonical_contract)
    validation_command = _text(planner_packet.get("validation_command") or recon.get("validation_target"))
    if validation_command:
        repo_key = _text(
            _as_dict(canonical_contract.get("provenance")).get("repo_key")
            or canonical_contract.get("repo_key")
            or "hyeve-aix-service"
        )
        try:
            profile, _ = resolve_effective_repo_profile(
                repo_key=repo_key,
                explicit_profile_id=None,
                profiles_dir=Path(__file__).resolve().parent.parent / "config" / "repo_profiles",
            )
        except Exception:
            profile = None
        argv = [str(item).strip() for item in shlex.split(validation_command, posix=True) if str(item).strip()]
        allow_prefixes = [
            list(item) for item in getattr(getattr(profile, "command_policy", None), "allow_prefixes", [])
        ]
        if argv and allow_prefixes:
            allowed = False
            for prefix in allow_prefixes:
                expected = [str(item).strip() for item in list(prefix or []) if str(item).strip()]
                if len(argv) >= len(expected) and argv[: len(expected)] == expected:
                    allowed = True
                    break
            if not allowed:
                return ValidationOutcome(
                    ok=False,
                    reason_code="validation_command_not_runtime_allowed",
                    message="declared validation command is not allowed by runtime policy",
                    validator_sha=validator_sha(
                        {"validation_command": validation_command, "allow_prefixes": allow_prefixes}
                    ),
                    payload={"validation_command": validation_command, "allow_prefixes": allow_prefixes},
                )
    if requires_paired:
        has_test = any(_looks_like_test_path(item) for item in target_files)
        has_source = any(not _looks_like_test_path(item) for item in target_files)
        if not (has_test and has_source):
            return ValidationOutcome(
                ok=False,
                reason_code="paired_change_required",
                message="task requires paired source and test mutation but contract does not target both",
                validator_sha=validator_sha(payload | {"target_files": target_files}),
                payload={"target_files": target_files},
            )
    anchor = _as_dict(recon.get("anchor"))
    start_line = int(anchor.get("start_line") or 0) if str(anchor.get("start_line") or "").strip() else 0
    end_line = int(anchor.get("end_line") or 0) if str(anchor.get("end_line") or "").strip() else 0
    anchor_size = max(0, end_line - start_line + 1) if start_line and end_line else 0
    single_line_hint = any(marker in task_text for marker in ("single-line", "single line", "one-line", "one line"))
    if single_line_hint and anchor_size > 10:
        return ValidationOutcome(
            ok=False,
            reason_code="oversized_anchor",
            message="single-line anticipated edit exceeded anchor policy budget",
            validator_sha=validator_sha(payload | {"anchor_size": anchor_size}),
            payload={"anchor_size": anchor_size, "edit_size": 1, "anchor_policy_status": "blocked"},
        )
    return ValidationOutcome(
        ok=True,
        validator_sha=validator_sha(payload | {"anchor_size": anchor_size}),
        payload={
            "anchor_size": anchor_size,
            "edit_size": 1 if single_line_hint else None,
            "anchor_policy_status": "ok",
        },
    )


def _command_path(path_value: str) -> str:
    return shlex.quote(path_value.strip().replace("\\", "/"))


def _targets_with_suffixes(changed_targets: list[str], suffixes: set[str]) -> list[str]:
    targets: list[str] = []
    for item in changed_targets:
        target = str(item or "").strip().replace("\\", "/")
        if not target:
            continue
        if Path(target).suffix.lower() in suffixes and target not in targets:
            targets.append(target)
    return sorted(targets)


def inject_file_type_validation_commands(*, changed_targets: list[str], existing_commands: list[str]) -> list[str]:
    normalized_existing = [str(item).strip() for item in existing_commands if str(item).strip()]
    python_targets = _targets_with_suffixes(changed_targets, PYTHON_VALIDATION_SUFFIXES)
    if python_targets:
        lint_command = "python -m ruff check " + " ".join(_command_path(item) for item in python_targets)
        if lint_command not in normalized_existing:
            normalized_existing.append(lint_command)
    text_document_targets = _targets_with_suffixes(changed_targets, TEXT_DOCUMENT_VALIDATION_SUFFIXES)
    if text_document_targets:
        diff_check_command = "git diff --check -- " + " ".join(_command_path(item) for item in text_document_targets)
        if diff_check_command not in normalized_existing:
            normalized_existing.append(diff_check_command)
    return normalized_existing


def inject_python_lint_commands(*, changed_targets: list[str], existing_commands: list[str]) -> list[str]:
    return inject_file_type_validation_commands(changed_targets=changed_targets, existing_commands=existing_commands)


def classify_failure_semantics(
    *,
    phase: str,
    reason_code: str,
    message: str,
    execution_attempted: bool = False,
) -> str:
    phase_key = _text(phase).lower()
    reason_key = _text(reason_code).lower()
    message_key = _text(message).lower()
    if phase_key in {"recon", "load_task"} and any(
        marker in reason_key for marker in ("timeout", "transport", "unavailable", "connection")
    ):
        return "INFRA_FAIL"
    if phase_key in {"recon", "planner"} and any(
        marker in reason_key
        for marker in (
            "contract_ambiguous",
            "insufficient_context",
            "move_to_proposal_workspace",
            "oversized_anchor",
        )
    ):
        return "POLICY_FAIL"
    if phase_key == "planner" and any(
        marker in reason_key
        for marker in (
            "vague_mutation_intent",
            "non_executable_mutation_intent",
            "paired_change_required",
            "mutation_task_shaping_required",
        )
    ):
        return "SHAPING_FAIL"
    if phase_key == "execution" and any(
        marker in reason_key for marker in ("proposal_workspace", "timeout", "docker", "permission", "mount")
    ):
        return "INFRA_FAIL"
    if phase_key == "planner":
        return "POLICY_FAIL"
    if phase_key == "verification":
        return "VERIFICATION_FAIL"
    if execution_attempted or phase_key == "execution":
        return "CODE_FAIL"
    if "verification" in message_key or "validation" in message_key:
        return "VERIFICATION_FAIL"
    return "POLICY_FAIL" if phase_key in {"recon", "planner"} else "CODE_FAIL"


def semantic_state_for_classification(classification: str) -> str | None:
    return SEMANTIC_TERMINAL_STATES.get(_text(classification).upper())
