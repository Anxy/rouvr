from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from backend.scripts.aix_run_chain_dump import (
    _default_artifact_dir,
    _default_runlogs_root,
    _find_latest_artifact_dir,
    _normalize_local_artifact_path,
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


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


def _trace_id_from_text(text: Any) -> str | None:
    match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27,})", str(text or ""))
    return match.group(1) if match else None


def _planner_trace_root_candidates(*, artifact_dir: Path, runlogs_root: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    explicit_roots: set[str] = set()

    for explicit in (runlogs_root, _default_runlogs_root()):
        try:
            explicit_roots.add(str(explicit.resolve()))
        except Exception:
            explicit_roots.add(str(explicit))

    def _append(path: Path | None) -> None:
        if path is None:
            return
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if str(resolved) not in explicit_roots and not resolved.exists() and not resolved.parent.exists():
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        candidates.append(resolved)

    _append(runlogs_root)
    _append(_default_runlogs_root())

    seeds = [artifact_dir.resolve(), Path.cwd().resolve(), Path(__file__).resolve().parent]
    for seed in seeds:
        for parent in [seed, *seed.parents]:
            _append(parent / "_runlogs")
            _append(parent / "backend" / "_runlogs")
            _append(parent / "projects" / "AIX" / "hyeve-aix-service" / "backend" / "_runlogs")
    return candidates


def _trace_path_candidates(ref: str, *, runlogs_roots: list[Path]) -> list[Path]:
    text = str(ref or "").strip()
    if not text:
        return []
    candidates: list[Path] = []
    seen: set[str] = set()

    def _append(path: Path | None) -> None:
        if path is None:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    for root in runlogs_roots:
        normalized = _normalize_local_artifact_path(text, runlogs_root=root)
        _append(normalized)
        if "/planner-traces/" in text:
            suffix = text.split("/planner-traces/", 1)[1]
            _append(root / "planner-traces" / suffix)
    trace_id = _trace_id_from_text(text)
    if trace_id:
        for root in runlogs_roots:
            _append(root / "planner-traces" / f"{trace_id}.json")
    try:
        _append(Path(text))
    except Exception:
        return candidates
    return candidates


def _resolve_trace_ref(ref: str, *, runlogs_roots: list[Path]) -> Path | None:
    for candidate in _trace_path_candidates(ref, runlogs_roots=runlogs_roots):
        if candidate.exists():
            return candidate
    return None


def _resolve_local_trace_ref(text: Any, *, runlogs_roots: list[Path]) -> Path | None:
    match = re.search(r"local_trace_ref=([^\s]+)", str(text or ""))
    if not match:
        return None
    return _resolve_trace_ref(match.group(1).strip(), runlogs_roots=runlogs_roots)


def _extract_raw_trace_refs(response_payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def _append(ref: str) -> None:
        text = str(ref or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        refs.append(text)

    for ref in _extract_evidence_refs(response_payload):
        if "/planner-traces/" in ref:
            _append(ref)
    for key in ("local_trace_ref", "trace_ref"):
        for value in _find_all(response_payload, key):
            text = str(value or "").strip()
            if "/planner-traces/" in text:
                _append(text)
    for item in _find_all(response_payload, "model_lane_attempt_failures"):
        if not isinstance(item, list):
            continue
        for failure in item:
            if not isinstance(failure, dict):
                continue
            match = re.search(r"local_trace_ref=([^\s]+)", str(failure.get("error") or ""))
            if match:
                _append(match.group(1).strip())
    return refs


def _collect_unresolved_trace_refs(*, response_payload: dict[str, Any], runlogs_roots: list[Path]) -> list[str]:
    unresolved: list[str] = []
    for ref in _extract_raw_trace_refs(response_payload):
        resolved = _resolve_trace_ref(ref, runlogs_roots=runlogs_roots)
        if resolved is not None:
            continue
        if ref.startswith("/app/_runlogs/") or ref.startswith("local:///app/_runlogs/"):
            unresolved.append(ref)
    return unresolved


def _append_trace_candidate(
    candidates: list[tuple[Path, str]],
    seen: set[str],
    *,
    path: Path | None,
    source: str,
) -> None:
    if path is None:
        return
    key = str(path.resolve()) if path.exists() else str(path)
    if key in seen:
        return
    seen.add(key)
    candidates.append((path, source))


def _extract_quality_gate_code(response_payload: dict[str, Any]) -> str | None:
    for item in _find_all(response_payload, "quality_gate"):
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            if code:
                return code
    return None


def _extract_phase_entries(response_payload: dict[str, Any]) -> list[dict[str, Any]]:
    for item in _find_all(response_payload, "phase_summary"):
        if isinstance(item, dict):
            entries = item.get("entries")
            if isinstance(entries, list):
                return [dict(entry) for entry in entries if isinstance(entry, dict)]
    return []


def _extract_evidence_refs(response_payload: dict[str, Any]) -> list[str]:
    for item in _find_all(response_payload, "evidence_refs"):
        if isinstance(item, list) and item:
            refs = [str(ref).strip() for ref in item if str(ref).strip()]
            if refs:
                return refs
    return []


def _resolve_planner_trace_path(*, response_payload: dict[str, Any], runlogs_roots: list[Path]) -> Path | None:
    refs = _extract_evidence_refs(response_payload)
    for ref in refs:
        path = _resolve_trace_ref(ref, runlogs_roots=runlogs_roots)
        if path is not None:
            return path
    return None


def _collect_evidence_trace_paths(*, response_payload: dict[str, Any], runlogs_roots: list[Path]) -> list[Path]:
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    refs = _extract_evidence_refs(response_payload)
    for ref in refs:
        path = _resolve_trace_ref(ref, runlogs_roots=runlogs_roots)
        if path is not None:
            _append_trace_candidate(candidates, seen, path=path, source="evidence_ref")
    return [path for path, _source in candidates]


def _resolve_planner_trace_from_attempt_failures(
    *, response_payload: dict[str, Any], runlogs_roots: list[Path]
) -> Path | None:
    for item in _find_all(response_payload, "model_lane_attempt_failures"):
        if not isinstance(item, list):
            continue
        for failure in item:
            if not isinstance(failure, dict):
                continue
            path = _resolve_local_trace_ref(failure.get("error"), runlogs_roots=runlogs_roots)
            if path is not None:
                return path
    return None


def _collect_attempt_failure_trace_paths(*, response_payload: dict[str, Any], runlogs_roots: list[Path]) -> list[Path]:
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for item in _find_all(response_payload, "model_lane_attempt_failures"):
        if not isinstance(item, list):
            continue
        for failure in item:
            if not isinstance(failure, dict):
                continue
            _append_trace_candidate(
                candidates,
                seen,
                path=_resolve_local_trace_ref(failure.get("error"), runlogs_roots=runlogs_roots),
                source="attempt_error_ref",
            )
    return [path for path, _source in candidates]


def _resolve_direct_trace_path(*, response_payload: dict[str, Any], runlogs_roots: list[Path]) -> Path | None:
    for key in ("local_trace_ref", "trace_ref"):
        for value in _find_all(response_payload, key):
            if not value:
                continue
            path = (
                _resolve_local_trace_ref(f"local_trace_ref={value}", runlogs_roots=runlogs_roots)
                if key == "local_trace_ref"
                else _resolve_trace_ref(str(value).strip(), runlogs_roots=runlogs_roots)
            )
            if path is not None:
                return path
    return None


def _collect_direct_trace_paths(*, response_payload: dict[str, Any], runlogs_roots: list[Path]) -> list[Path]:
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for key in ("local_trace_ref", "trace_ref"):
        for value in _find_all(response_payload, key):
            if not value:
                continue
            path = (
                _resolve_local_trace_ref(f"local_trace_ref={value}", runlogs_roots=runlogs_roots)
                if key == "local_trace_ref"
                else _resolve_trace_ref(str(value).strip(), runlogs_roots=runlogs_roots)
            )
            if path is not None and path.exists():
                _append_trace_candidate(candidates, seen, path=path, source="direct_trace_ref")
    return [path for path, _source in candidates]


def _find_latest_trace_by_correlation(
    *, runlogs_roots: list[Path], project_id: str, correlation_id: str
) -> Path | None:
    if not correlation_id:
        return None
    matches: list[Path] = []
    for runlogs_root in runlogs_roots:
        trace_root = runlogs_root / "planner-traces"
        if not trace_root.exists():
            continue
        for path in trace_root.glob("*.json"):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("correlation_id") or "").strip() != correlation_id:
                continue
            trace_project_id = str(payload.get("project_id") or "").strip()
            if trace_project_id and trace_project_id != project_id:
                continue
            matches.append(path)
    if not matches:
        return None
    return max(matches, key=lambda item: item.stat().st_mtime)


def _collect_traces_by_correlation(*, runlogs_roots: list[Path], project_id: str, correlation_id: str) -> list[Path]:
    if not correlation_id:
        return []
    matches: list[Path] = []
    seen: set[str] = set()
    for runlogs_root in runlogs_roots:
        trace_root = runlogs_root / "planner-traces"
        if not trace_root.exists():
            continue
        for path in trace_root.glob("*.json"):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("correlation_id") or "").strip() != correlation_id:
                continue
            trace_project_id = str(payload.get("project_id") or "").strip()
            if trace_project_id and trace_project_id != project_id:
                continue
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            matches.append(path)
    return sorted(matches, key=lambda item: item.stat().st_mtime)


def _collect_planner_trace_candidates(
    *,
    response_payload: dict[str, Any],
    runlogs_roots: list[Path],
    project_id: str,
    correlation_id: str,
) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path in _collect_evidence_trace_paths(response_payload=response_payload, runlogs_roots=runlogs_roots):
        _append_trace_candidate(candidates, seen, path=path, source="evidence_ref")
    for path in _collect_direct_trace_paths(response_payload=response_payload, runlogs_roots=runlogs_roots):
        _append_trace_candidate(candidates, seen, path=path, source="direct_trace_ref")
    for path in _collect_attempt_failure_trace_paths(response_payload=response_payload, runlogs_roots=runlogs_roots):
        _append_trace_candidate(candidates, seen, path=path, source="attempt_error_ref")
    for path in _collect_traces_by_correlation(
        runlogs_roots=runlogs_roots,
        project_id=project_id,
        correlation_id=correlation_id,
    ):
        _append_trace_candidate(candidates, seen, path=path, source="correlation_scan")
    return candidates


def _preview_text(value: Any, *, limit: int = 400) -> dict[str, Any] | None:
    text = str(value or "")
    if not text:
        return None
    compact = " ".join(text.split())
    return {
        "chars": len(text),
        "preview": compact[:limit],
        "truncated": len(compact) > limit,
    }


def _summarize_steps(parsed_output: Any) -> dict[str, Any]:
    if not isinstance(parsed_output, dict):
        return {}
    steps = parsed_output.get("steps")
    if not isinstance(steps, list):
        return {}
    tools: list[str] = []
    relative_paths: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "").strip()
        if tool:
            tools.append(tool)
        args = step.get("args")
        if isinstance(args, dict):
            rel = str(args.get("relative_path") or "").strip()
            if rel and rel not in relative_paths:
                relative_paths.append(rel)
    return {
        "step_count": len([step for step in steps if isinstance(step, dict)]),
        "tools": tools,
        "relative_paths": relative_paths,
    }


def _build_trace_view(trace_payload: dict[str, Any], *, path: Path, source: str, include_raw: bool) -> dict[str, Any]:
    return {
        "trace_path": str(path),
        "trace_source": source,
        "provider": trace_payload.get("provider"),
        "model": trace_payload.get("model"),
        "attempts_used": trace_payload.get("attempts_used"),
        "planner_request_shape": trace_payload.get("planner_request_shape"),
        "planner_packet_fingerprint": trace_payload.get("planner_packet_fingerprint"),
        "request_payload_bytes": trace_payload.get("request_payload_bytes"),
        "request_metadata": trace_payload.get("request_metadata") if include_raw else None,
        "request_metadata_summary": (
            {
                "keys": sorted((trace_payload.get("request_metadata") or {}).keys()),
                "planner_request_shape": (trace_payload.get("request_metadata") or {}).get("planner_request_shape"),
                "planner_packet_schema_name": (trace_payload.get("request_metadata") or {}).get(
                    "planner_packet_schema_name"
                ),
                "planner_packet_schema_version": (trace_payload.get("request_metadata") or {}).get(
                    "planner_packet_schema_version"
                ),
            }
            if isinstance(trace_payload.get("request_metadata"), dict)
            else {}
        ),
        "planner_prompt": trace_payload.get("planner_prompt") if include_raw else None,
        "planner_prompt_preview": _preview_text(trace_payload.get("planner_prompt")),
        "planner_packet_snapshot": trace_payload.get("planner_packet_snapshot") if include_raw else None,
        "planner_packet_summary": (
            {
                "keys": sorted(trace_payload.get("planner_packet_snapshot", {}).keys()),
                "slice_id": (trace_payload.get("planner_packet_snapshot") or {}).get("slice_id"),
                "target_file": (trace_payload.get("planner_packet_snapshot") or {}).get("target_file"),
            }
            if isinstance(trace_payload.get("planner_packet_snapshot"), dict)
            else {}
        ),
        "raw_output_text": trace_payload.get("raw_output_text") if include_raw else None,
        "raw_output_preview": _preview_text(trace_payload.get("raw_output_text")),
        "parsed_output": trace_payload.get("parsed_output") if include_raw else None,
        "parsed_output_summary": _summarize_steps(trace_payload.get("parsed_output")),
        "parse_error": trace_payload.get("parse_error"),
        "upstream_payload": trace_payload.get("upstream_payload") if include_raw else None,
    }


def build_planner_io_view(
    *,
    artifact_dir: Path,
    runlogs_root: Path,
    project_id: str,
    task_number: int,
    include_raw: bool = False,
) -> dict[str, Any]:
    if not artifact_dir.exists() or not artifact_dir.is_dir():
        return {
            "project_id": project_id,
            "task_number": int(task_number),
            "found": False,
            "error": "RUN_ARTIFACT_NOT_FOUND",
            "artifact_dir": str(artifact_dir),
        }
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
    invocation_id = _find_first(response_payload, "invocation_id")
    final_status = _find_first(response_payload, "final_status")
    correlation_id = _find_first(response_payload, "correlation_id")
    run_id = _find_first(response_payload, "run_id")
    transcript_ref = _find_first(response_payload, "transcript_ref")
    logs_ref = _find_first(response_payload, "logs_ref")
    tests_ref = _find_first(response_payload, "tests_ref")
    tool_sequence = _find_first(response_payload, "tool_sequence")
    failing_tool = _find_first(response_payload, "failing_tool")
    reason_summary = _find_first(response_payload, "reason_summary")
    repair_retry_history = _find_first(response_payload, "repair_retry_history")
    model_lane_attempt_failures = _find_first(response_payload, "model_lane_attempt_failures")
    trace_roots = _planner_trace_root_candidates(artifact_dir=artifact_dir, runlogs_root=runlogs_root)
    trace_candidates = _collect_planner_trace_candidates(
        response_payload=response_payload,
        runlogs_roots=trace_roots,
        project_id=project_id,
        correlation_id=str(correlation_id or "").strip(),
    )
    trace_views = [
        _build_trace_view(
            (_read_json(path) if path.exists() else None) or {},
            path=path,
            source=source,
            include_raw=include_raw,
        )
        for path, source in trace_candidates
    ]
    unresolved_trace_refs = _collect_unresolved_trace_refs(response_payload=response_payload, runlogs_roots=trace_roots)
    primary_trace = (
        trace_views[-1]
        if trace_views
        else {
            "trace_path": None,
            "trace_source": None,
            "provider": None,
            "model": None,
            "attempts_used": None,
            "planner_request_shape": None,
            "planner_packet_fingerprint": None,
            "request_payload_bytes": None,
            "request_metadata": None,
            "request_metadata_summary": {},
            "planner_prompt": None,
            "planner_prompt_preview": None,
            "planner_packet_snapshot": None,
            "planner_packet_summary": {},
            "raw_output_text": None,
            "raw_output_preview": None,
            "parsed_output": None,
            "parsed_output_summary": {},
            "parse_error": None,
            "upstream_payload": None,
        }
    )

    return {
        "project_id": project_id,
        "task_number": int(task_number),
        "found": True,
        "artifact_run_dir": str(run_dir),
        "request": {
            "path": str(request_path),
            "generated_at_utc": request_payload.get("generated_at_utc"),
            "url": request_payload.get("url"),
            "payload_preview": request_payload.get("payload")
            if include_raw
            else _preview_text(request_payload.get("payload")),
            "raw": request_payload if include_raw else None,
        },
        "response": {
            "path": str(response_path),
            "generated_at_utc": response_payload.get("generated_at_utc"),
            "http_status": response_payload.get("http_status"),
            "elapsed_ms": response_payload.get("elapsed_ms"),
            "success": response_payload.get("success"),
            "invocation_id": invocation_id,
            "final_status": final_status,
            "quality_gate_code": _extract_quality_gate_code(response_payload),
            "raw": response_payload if include_raw else None,
        },
        "planner_io": {
            **primary_trace,
            "trace_count": len(trace_views),
            "attempt_traces": trace_views,
            "trace_resolution_roots": [str(path) for path in trace_roots],
            "unresolved_trace_refs": unresolved_trace_refs,
            "trace_resolution_warnings": (
                [
                    "planner trace refs were present but could not be resolved under any scanned host runlogs root; "
                    "these refs may still be container-scoped or missing from the host workspace"
                ]
                if unresolved_trace_refs
                else []
            ),
        },
        "repair_loop": {
            "phase_entries": _extract_phase_entries(response_payload),
            "repair_retry_history": repair_retry_history if isinstance(repair_retry_history, list) else [],
            "model_lane_attempt_failures": (
                model_lane_attempt_failures if isinstance(model_lane_attempt_failures, list) else []
            ),
        },
        "executor_io": {
            "run_id": run_id,
            "tool_sequence": tool_sequence if isinstance(tool_sequence, list) else [],
            "transcript_ref": transcript_ref,
            "logs_ref": logs_ref,
            "tests_ref": tests_ref,
            "failing_tool": failing_tool,
            "reason_summary": reason_summary,
        },
        "evidence_refs": _extract_evidence_refs(response_payload),
        "correlation_id": correlation_id,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dump planner and executor I/O for one or more AIX runs.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--task-number", dest="task_numbers", action="append", type=int, required=True)
    parser.add_argument("--artifact-dir", default=str(_default_artifact_dir()))
    parser.add_argument("--runlogs-root", default=str(_default_runlogs_root()))
    parser.add_argument("--mode", choices=("slim", "raw"), default="slim")
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
            build_planner_io_view(
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
