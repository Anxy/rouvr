from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _default_artifact_dir() -> Path:
    current = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in current.parents:
        candidate = parent / "artifacts" / "run_fire"
        if candidate.exists() and candidate.is_dir():
            candidates.append(candidate)
    if candidates:
        return candidates[-1]
    return current.parents[3] / "artifacts" / "run_fire"


def _default_runlogs_root() -> Path:
    return Path(__file__).resolve().parents[1] / "_runlogs"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_first(payload: Any, key: str) -> Any | None:
    if isinstance(payload, dict):
        if key in payload:
            return payload.get(key)
        for value in payload.values():
            found = _find_first(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first(item, key)
            if found is not None:
                return found
    return None


def _find_all(payload: Any, key: str) -> list[Any]:
    results: list[Any] = []
    if isinstance(payload, dict):
        if key in payload:
            results.append(payload.get(key))
        for value in payload.values():
            results.extend(_find_all(value, key))
    elif isinstance(payload, list):
        for item in payload:
            results.extend(_find_all(item, key))
    return results


def _payload_contains_project_task(payload: Any, *, project_id: str, task_number: int) -> bool:
    if isinstance(payload, dict):
        candidate_project = str(payload.get("project_id") or "").strip()
        candidate_task = payload.get("task_number")
        if candidate_project == project_id:
            try:
                if int(str(candidate_task)) == int(task_number):
                    return True
            except Exception:
                pass
        for value in payload.values():
            if _payload_contains_project_task(value, project_id=project_id, task_number=task_number):
                return True
        return False
    if isinstance(payload, list):
        return any(
            _payload_contains_project_task(item, project_id=project_id, task_number=task_number) for item in payload
        )
    return False


def _artifact_matches(path: Path, *, project_id: str, task_number: int) -> bool:
    request_path = path / "request.json"
    response_path = path / "response.json"
    request_payload = _read_json(request_path)
    response_payload = _read_json(response_path)
    return _payload_contains_project_task(
        request_payload, project_id=project_id, task_number=task_number
    ) or _payload_contains_project_task(response_payload, project_id=project_id, task_number=task_number)


def _find_latest_artifact_dir(*, artifact_dir: Path, project_id: str, task_number: int) -> Path | None:
    candidates = []
    for child in artifact_dir.iterdir():
        if not child.is_dir():
            continue
        if not (child / "request.json").exists() or not (child / "response.json").exists():
            continue
        if not _artifact_matches(child, project_id=project_id, task_number=task_number):
            continue
        candidates.append(child)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _normalize_local_artifact_path(ref: str, *, runlogs_root: Path) -> Path | None:
    ref_text = str(ref or "").strip()
    if not ref_text:
        return None
    if ref_text.startswith("local:///app/_runlogs/"):
        suffix = ref_text.split("/app/_runlogs/", 1)[1]
        return runlogs_root / suffix
    if ref_text.startswith("/app/_runlogs/"):
        suffix = ref_text.split("/app/_runlogs/", 1)[1]
        return runlogs_root / suffix
    if ref_text.startswith("local://planner-traces/"):
        suffix = ref_text.split("local://planner-traces/", 1)[1]
        return runlogs_root / "planner-traces" / suffix
    return None


def _extract_trace_ids(refs: list[str]) -> list[str]:
    trace_ids: list[str] = []
    for ref in refs:
        match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27,})", str(ref or ""))
        if match:
            trace_id = match.group(1)
            if trace_id not in trace_ids:
                trace_ids.append(trace_id)
    return trace_ids


def _extract_quality_gate_code(payload: dict[str, Any]) -> str | None:
    for item in _find_all(payload, "quality_gate"):
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            if code:
                return code
    return None


def _extract_evidence_refs(payload: dict[str, Any]) -> list[str]:
    for item in _find_all(payload, "evidence_refs"):
        if isinstance(item, list):
            refs = [str(ref).strip() for ref in item if str(ref).strip()]
            if refs:
                return refs
    return []


def _load_phase_files(
    *, runlogs_root: Path, project_id: str, task_number: int, correlation_id: str, include_raw: bool
) -> list[dict[str, Any]]:
    phase_dir = (
        runlogs_root
        / "aix-execution-phase-ledger"
        / project_id
        / f"task-{task_number}"
        / f"invocation-{correlation_id}"
    )
    if not phase_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(phase_dir.glob("*.json")):
        entry = {"path": str(path), "name": path.name}
        if include_raw:
            entry["content"] = _read_text(path)
        results.append(entry)
    return results


def _load_proposal_artifacts(
    *, evidence_refs: list[str], runlogs_root: Path, include_raw: bool
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in evidence_refs:
        resolved = _normalize_local_artifact_path(ref, runlogs_root=runlogs_root)
        if resolved is None or "proposal-review" not in str(resolved):
            continue
        if resolved.is_dir():
            directory = resolved
        else:
            directory = resolved.parent
        key = str(directory)
        if key in seen or not directory.exists():
            continue
        seen.add(key)
        entry: dict[str, Any] = {"directory": str(directory), "files": []}
        for name in ("diff.patch", "changed_files.json", "git-status.txt", "metadata.json"):
            path = directory / name
            if path.exists():
                item = {"path": str(path), "name": name}
                if include_raw:
                    item["content"] = _read_text(path)
                entry["files"].append(item)
        results.append(entry)
    return results


def _summarize_request_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at_utc": payload.get("generated_at_utc"),
        "project_id": payload.get("project_id"),
        "task_number": payload.get("task_number"),
        "url": payload.get("url"),
        "http_status": payload.get("http_status"),
        "success": payload.get("success"),
        "top_level_keys": list(payload.keys()),
    }


def build_run_chain(
    *,
    artifact_dir: Path,
    runlogs_root: Path,
    project_id: str,
    task_number: int,
    include_raw: bool = False,
) -> dict[str, Any]:
    run_dir = _find_latest_artifact_dir(artifact_dir=artifact_dir, project_id=project_id, task_number=task_number)
    if run_dir is None:
        return {
            "project_id": project_id,
            "task_number": int(task_number),
            "found": False,
            "error": "RUN_ARTIFACT_NOT_FOUND",
            "artifact_dir": str(artifact_dir),
        }

    request_path = run_dir / "request.json"
    response_path = run_dir / "response.json"
    request_payload = _read_json(request_path) or {}
    response_payload = _read_json(response_path) or {}
    digest = next(
        (dict(item) for item in _find_all(response_payload, "execution_digest") if isinstance(item, dict)),
        {},
    )
    refs = dict(digest.get("refs") or {})
    runtime = dict(digest.get("runtime") or {})
    phase_summary = dict(runtime.get("phase_summary") or {})
    minimal = next(
        (dict(item) for item in _find_all(response_payload, "minimal_run_outcome") if isinstance(item, dict)),
        {},
    )
    correlation_id = str(minimal.get("correlation_id") or "").strip()
    evidence_refs = _extract_evidence_refs(response_payload)

    run_ledger_path = runlogs_root / "aix-run-ledger" / f"{correlation_id}.json" if correlation_id else None
    run_ledger_content = _read_text(run_ledger_path) if run_ledger_path and run_ledger_path.exists() else None

    planner_refs = [
        ref for ref in evidence_refs if "/planner-traces/" in ref or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27,}", ref)
    ]
    planner_trace_files: list[dict[str, Any]] = []
    for trace_id in _extract_trace_ids(planner_refs):
        trace_path = runlogs_root / "planner-traces" / f"{trace_id}.json"
        if trace_path.exists():
            entry = {"trace_id": trace_id, "path": str(trace_path)}
            if include_raw:
                entry["content"] = _read_text(trace_path)
            planner_trace_files.append(entry)
    for ref in planner_refs:
        resolved = _normalize_local_artifact_path(ref, runlogs_root=runlogs_root)
        if resolved and resolved.exists():
            key = str(resolved)
            if all(item["path"] != key for item in planner_trace_files):
                entry = {"trace_id": resolved.stem, "path": key}
                if include_raw:
                    entry["content"] = _read_text(resolved)
                planner_trace_files.append(entry)

    return {
        "project_id": project_id,
        "task_number": int(task_number),
        "found": True,
        "artifact_run_dir": str(run_dir),
        "request": {
            "path": str(request_path),
            "summary": _summarize_request_response(request_payload),
            "raw": _read_text(request_path) if include_raw else None,
            "json": request_payload if include_raw else None,
        },
        "response": {
            "path": str(response_path),
            "summary": _summarize_request_response(response_payload),
            "raw": _read_text(response_path) if include_raw else None,
            "json": response_payload if include_raw else None,
        },
        "derived": {
            "invocation_id": str(_find_first(response_payload, "invocation_id") or "").strip() or None,
            "correlation_id": correlation_id or None,
            "final_status": str(_find_first(response_payload, "final_status") or "").strip() or None,
            "quality_gate_code": _extract_quality_gate_code(response_payload),
            "phase_summary": phase_summary,
            "refs": refs,
            "evidence_refs": evidence_refs,
        },
        "run_ledger": {
            "path": str(run_ledger_path) if run_ledger_path else None,
            "raw": run_ledger_content if include_raw else None,
        },
        "phase_ledgers": (
            _load_phase_files(
                runlogs_root=runlogs_root,
                project_id=project_id,
                task_number=int(task_number),
                correlation_id=correlation_id,
                include_raw=include_raw,
            )
            if correlation_id
            else []
        ),
        "planner_traces": planner_trace_files,
        "proposal_review": _load_proposal_artifacts(
            evidence_refs=evidence_refs,
            runlogs_root=runlogs_root,
            include_raw=include_raw,
        ),
        "devexecutor_refs": {
            "transcript_ref": refs.get("transcript_ref"),
            "logs_ref": refs.get("logs_ref"),
            "tests_ref": refs.get("tests_ref"),
        },
        "loop_revision": {
            "planner_trace_refs": planner_refs,
            "phase_entries": phase_summary.get("entries") or [],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dump the raw AIX run chain for one or more project/task pairs.")
    parser.add_argument("--project-id", required=True, help="Project id to inspect.")
    parser.add_argument(
        "--task-number",
        dest="task_numbers",
        action="append",
        type=int,
        required=True,
        help="Task number to inspect. Repeat for batch mode.",
    )
    parser.add_argument("--artifact-dir", default=str(_default_artifact_dir()))
    parser.add_argument("--runlogs-root", default=str(_default_runlogs_root()))
    parser.add_argument(
        "--mode",
        choices=("slim", "raw"),
        default="slim",
        help="slim emits an indexed chain with refs and summaries; raw also embeds full file bodies.",
    )
    parser.add_argument("--out", help="Optional output JSON path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact_dir = Path(args.artifact_dir).resolve()
    runlogs_root = Path(args.runlogs_root).resolve()
    payload = {
        "project_id": str(args.project_id),
        "artifact_dir": str(artifact_dir),
        "runlogs_root": str(runlogs_root),
        "tasks": [
            build_run_chain(
                artifact_dir=artifact_dir,
                runlogs_root=runlogs_root,
                project_id=str(args.project_id),
                task_number=int(task_number),
                include_raw=args.mode == "raw",
            )
            for task_number in args.task_numbers
        ],
    }
    rendered = json.dumps(payload, indent=2, ensure_ascii=True)
    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
