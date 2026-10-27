from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.models.evidence_pack_models import (
    EVIDENCE_PACK_SCHEMA_VERSION_Current,
    EvidenceArtifactRefCurrent,
    EvidencePackCurrent,
    validate_evidence_pack,
)


class EvidenceBundleValidationError(ValueError):
    pass


def collect_artifacts(
    *,
    workspace_root: str | Path,
    required: dict[str, str],
    optional: dict[str, str] | None = None,
) -> list[EvidenceArtifactRefCurrent]:
    root = Path(workspace_root).resolve()
    refs: list[EvidenceArtifactRefCurrent] = []
    optional_map = dict(optional or {})

    all_items: list[tuple[str, str, bool]] = []
    for key, rel_path in required.items():
        all_items.append((key, rel_path, False))
    for key, rel_path in optional_map.items():
        all_items.append((key, rel_path, True))

    for key, rel_path, is_optional in sorted(all_items, key=lambda item: item[0]):
        candidate = (root / rel_path).resolve()
        exists = candidate.exists()
        refs.append(
            EvidenceArtifactRefCurrent(
                key=key,
                path=str(rel_path),
                exists=exists,
                optional=is_optional,
                size_bytes=(candidate.stat().st_size if exists and candidate.is_file() else None),
            )
        )
    return refs


def generate_acceptance_mapping(
    *,
    acceptance_items: list[dict[str, str]],
    evidence_by_acceptance_id: dict[str, list[str]],
    proof_by_acceptance_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    proof_map = dict(proof_by_acceptance_id or {})
    for item in acceptance_items:
        acceptance_id = str(item.get("id") or "").strip()
        acceptance_text = str(item.get("text") or "").strip()
        if not acceptance_id or not acceptance_text:
            raise ValueError("Acceptance items require non-empty id and text.")
        evidence_refs = sorted({str(x) for x in evidence_by_acceptance_id.get(acceptance_id, []) if str(x).strip()})
        proof_raw = proof_map.get(acceptance_id)
        proof: dict[str, Any] = proof_raw if isinstance(proof_raw, dict) else {}
        command_refs = sorted({str(x) for x in proof.get("command_refs", []) if str(x).strip()})
        assertion_summary = str(proof.get("assertion_summary") or "").strip() or None
        skipped_count = max(int(proof.get("skipped_count", 0) or 0), 0)
        warning_count = max(int(proof.get("warning_count", 0) or 0), 0)
        explicit_met = proof.get("met") if "met" in proof else None
        met = (
            bool(explicit_met)
            if isinstance(explicit_met, bool)
            else bool(evidence_refs or command_refs or assertion_summary)
        )
        inferred_kind = str(proof.get("proof_kind") or "").strip() or None
        if inferred_kind is None and met:
            inferred_kind = "command" if command_refs else "artifact"
        rows.append(
            {
                "acceptance_id": acceptance_id,
                "acceptance_text": acceptance_text,
                "met": met,
                "evidence_refs": evidence_refs,
                "proof_kind": inferred_kind,
                "command_refs": command_refs,
                "assertion_summary": assertion_summary,
                "skipped_count": skipped_count,
                "warning_count": warning_count,
                "note": None if met else str(proof.get("note") or "UNMET_ACCEPTANCE"),
            }
        )
    return rows


def build_evidence_pack(
    *,
    status: str,
    summary: str,
    diff_artifact: str,
    commands_run: list[dict[str, Any]],
    test_results: dict[str, Any],
    dev_run_proof: dict[str, Any],
    risk_notes: list[str],
    acceptance_mapping: list[dict[str, Any]],
    artifacts: list[EvidenceArtifactRefCurrent] | list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> EvidencePackCurrent:
    payload = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION_Current,
        "status": status,
        "summary": summary,
        "diff_artifact": diff_artifact,
        "commands_run": commands_run,
        "test_results": test_results,
        "dev_run_proof": dev_run_proof,
        "risk_notes": risk_notes,
        "acceptance_mapping": acceptance_mapping,
        "artifacts": [
            a.model_dump(mode="python") if isinstance(a, EvidenceArtifactRefCurrent) else a for a in artifacts
        ],
        "metadata": metadata or {},
    }
    return validate_evidence_pack(payload)


def validate_bundle_for_ready_for_human(evidence_pack: EvidencePackCurrent) -> None:
    missing_required = [a.key for a in evidence_pack.artifacts if (not a.optional and not a.exists)]
    unmet = [row.acceptance_id for row in evidence_pack.acceptance_mapping if not row.met]
    proofless = [
        row.acceptance_id
        for row in evidence_pack.acceptance_mapping
        if row.met
        and not (row.proof_kind and (row.evidence_refs or row.command_refs or (row.assertion_summary or "").strip()))
    ]
    if missing_required:
        raise EvidenceBundleValidationError(f"EVIDENCE_BUNDLE_INVALID missing_required_artifacts={missing_required}")
    if unmet:
        raise EvidenceBundleValidationError(f"EVIDENCE_BUNDLE_INVALID unmet_acceptance={unmet}")
    if proofless:
        raise EvidenceBundleValidationError(f"EVIDENCE_BUNDLE_INVALID proof_semantics_missing={proofless}")


def _pytest_stdout_tail(stdout: str, n: int = 50) -> str:
    if not stdout:
        return ""
    lines = stdout.splitlines()
    return "\n".join(lines[-n:]) if len(lines) > n else stdout


def _extract_failed_tests(stdout: str) -> list[str]:
    return [line.strip() for line in (stdout or "").splitlines() if line.strip().startswith("FAILED ")]


def build_partial_failure_evidence_pack(
    *,
    transcript: list[dict[str, Any]],
    reason_code: str,
    summary: str | None = None,
) -> EvidencePackCurrent:
    commands_run: list[dict[str, Any]] = []
    for item in transcript:
        if str(item.get("tool") or "") != "cmd.run":
            continue
        result_raw = item.get("result")
        result: dict[str, Any] = result_raw if isinstance(result_raw, dict) else {}
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        timed_out = bool(result.get("timed_out"))
        returncode = result.get("returncode")
        try:
            returncode_int = int(returncode) if returncode is not None else None
        except (TypeError, ValueError):
            returncode_int = None
        if not timed_out and returncode_int not in (None, 0) and not stderr and stdout:
            # Many CLI tools emit failure diagnostics on stdout; preserve them in stderr for downstream tails.
            stderr = stdout
        failed_tests = _extract_failed_tests(stdout)
        commands_run.append(
            {
                "command": result.get("command"),
                "command_text": " ".join(str(part) for part in (result.get("command") or [])),
                "returncode": returncode,
                "ok": bool(item.get("ok", False)),
                "timed_out": timed_out,
                "timeout_seconds": result.get("timeout_seconds"),
                "stderr": stderr,
                "stdout": stdout,
                "stdout_tail": _pytest_stdout_tail(stdout),
                "failed_tests": failed_tests,
            }
        )

    timed_out_commands = [row for row in commands_run if row.get("timed_out")]
    failed_commands = [row for row in commands_run if not row.get("ok")]
    terminal_failure: dict[str, Any] = {}
    if timed_out_commands:
        terminal_failure = {
            "code": "CMD_TIMEOUT",
            "tool": "cmd.run",
            "timed_out": True,
            "stdout_tail": timed_out_commands[-1].get("stdout_tail", ""),
        }
    elif failed_commands:
        last = failed_commands[-1]
        terminal_failure = {
            "code": "TEST_FAILURES",
            "tool": "cmd.run",
            "returncode": last.get("returncode"),
            "failed_tests": last.get("failed_tests", []),
            "stdout_tail": last.get("stdout_tail", ""),
        }

    fail_summary = summary or f"Execution terminated before full completion ({reason_code})."
    return build_evidence_pack(
        status="fail",
        summary=fail_summary,
        diff_artifact="partial://none",
        commands_run=commands_run,
        test_results={"passed": False},
        dev_run_proof={
            "reason_code": reason_code,
            "transcript_length": len(transcript),
            "last_step": transcript[-1] if transcript else {},
            "terminal_failure": terminal_failure,
        },
        risk_notes=["partial evidence only", "execution did not satisfy completion criteria"],
        acceptance_mapping=[
            {
                "acceptance_id": "AC-PARTIAL-EXECUTION",
                "acceptance_text": "Execution reached terminal-ready quality bar",
                "met": False,
                "evidence_refs": [],
                "proof_kind": "partial_failure",
                "command_refs": [],
                "assertion_summary": fail_summary,
                "skipped_count": 0,
                "warning_count": 0,
                "note": reason_code,
            }
        ],
        artifacts=[],
        metadata={"partial_failure": True, "reason_code": reason_code},
    )
