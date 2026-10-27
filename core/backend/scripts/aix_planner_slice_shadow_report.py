from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _default_artifact_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "artifacts" / "run_fire"


def _iter_response_paths(artifact_dir: Path, limit: int) -> list[Path]:
    candidates = [
        path / "response.json" for path in artifact_dir.iterdir() if path.is_dir() and (path / "response.json").exists()
    ]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, limit)]


def _load_payloads(artifact_dir: Path, limit: int) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in _iter_response_paths(artifact_dir, limit):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            payloads.append({"run_id": path.parent.name, "response": payload})
    return payloads


def _planner_metrics(response: dict[str, Any]) -> dict[str, Any]:
    payload = dict(response)
    result = payload.get("result")
    if isinstance(result, dict):
        nested = result.get("data")
        if isinstance(nested, dict):
            payload = nested
    inner_result = payload.get("result")
    if isinstance(inner_result, dict):
        payload = inner_result
    planner = dict(payload.get("planner") or {})
    metrics = dict(planner.get("planner_packet_metrics") or {})
    packet = dict(planner.get("planner_packet") or {})
    slice_packet_current = dict(packet.get("slice_packet_current") or {})
    return {
        "status": str(metrics.get("status") or "missing").strip() or "missing",
        "strict_packet_present": bool(metrics.get("strict_packet_present")),
        "planner_packet_forwarded": bool(planner.get("planner_packet_forwarded")),
        "clarification_gap_codes": [
            str(item).strip() for item in metrics.get("clarification_gap_codes") or [] if str(item).strip()
        ],
        "validation_error": str(packet.get("validation_error") or "").strip() or None,
        "slice_packet_status": str(slice_packet_current.get("status") or "").strip() or None,
        "quality_gate_code": str((payload.get("quality_gate") or {}).get("code") or "").strip() or None,
        "final_status": str(payload.get("final_status") or "").strip() or None,
    }


def build_shadow_snapshot(*, payloads: list[dict[str, Any]]) -> dict[str, Any]:
    run_rows: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    quality_gate_counter: Counter[str] = Counter()
    clarification_gap_counter: Counter[str] = Counter()
    forwarded_true = 0
    strict_present = 0

    for item in payloads:
        run_id = str(item.get("run_id") or "").strip()
        response = dict(item.get("response") or {})
        metrics = _planner_metrics(response)
        status_counter[metrics["status"]] += 1
        if metrics["quality_gate_code"]:
            quality_gate_counter[metrics["quality_gate_code"]] += 1
        for code in metrics["clarification_gap_codes"]:
            clarification_gap_counter[code] += 1
        if metrics["planner_packet_forwarded"]:
            forwarded_true += 1
        if metrics["strict_packet_present"]:
            strict_present += 1
        run_rows.append(
            {
                "run_id": run_id,
                "planner_packet_status": metrics["status"],
                "planner_packet_forwarded": metrics["planner_packet_forwarded"],
                "strict_packet_present": metrics["strict_packet_present"],
                "clarification_gap_codes": metrics["clarification_gap_codes"],
                "quality_gate_code": metrics["quality_gate_code"],
                "validation_error": metrics["validation_error"],
                "final_status": metrics["final_status"],
            }
        )

    total = len(run_rows)
    return {
        "sample_size": total,
        "counts": {
            "planner_packet_status": dict(status_counter),
            "quality_gate_code": dict(quality_gate_counter),
            "clarification_gap_codes": dict(clarification_gap_counter),
        },
        "rates": {
            "strict_packet_present_rate": round(strict_present / total, 6) if total else 0.0,
            "planner_packet_forwarded_rate": round(forwarded_true / total, 6) if total else 0.0,
            "ready_rate": round(status_counter.get("ready", 0) / total, 6) if total else 0.0,
            "needs_clarification_rate": round(status_counter.get("needs_clarification", 0) / total, 6)
            if total
            else 0.0,
        },
        "runs": run_rows,
    }


def _write_markdown(path: Path, snapshot: dict[str, Any]) -> None:
    lines = [
        "# AIX Planner Slice Shadow Report",
        "",
        f"- Generated: {datetime.now(UTC).isoformat()}",
        f"- Sample size: {snapshot.get('sample_size', 0)}",
        "",
        "## Rates",
    ]
    for key, value in sorted((snapshot.get("rates") or {}).items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Packet Status Counts"])
    for key, value in sorted((snapshot.get("counts") or {}).get("planner_packet_status", {}).items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Quality Gate Counts"])
    for key, value in sorted((snapshot.get("counts") or {}).get("quality_gate_code", {}).items()):
        lines.append(f"- {key}: `{value}`")
    gap_counts = (snapshot.get("counts") or {}).get("clarification_gap_codes", {})
    if gap_counts:
        lines.extend(["", "## Clarification Gap Counts"])
        for key, value in sorted(gap_counts.items()):
            lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Recent Runs"])
    for row in snapshot.get("runs") or []:
        lines.append(
            f"- {row['run_id']}: status={row['planner_packet_status']} forwarded={row['planner_packet_forwarded']} "
            f"strict={row['strict_packet_present']} quality_gate={row['quality_gate_code']} final_status={row['final_status']}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize strict planner slice packet shadow metrics from run_fire artifacts."
    )
    parser.add_argument("--artifact-dir", default=str(_default_artifact_dir()))
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--out-json", default="C:/dev/hyeve/reports/aix-planner-slice-shadow-report-latest.json")
    parser.add_argument("--out-md", default="C:/dev/hyeve/reports/aix-planner-slice-shadow-report-latest.md")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir).resolve()
    payloads = _load_payloads(artifact_dir=artifact_dir, limit=max(1, int(args.limit)))
    snapshot = build_shadow_snapshot(payloads=payloads)
    output = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "suite": "aix_planner_slice_shadow_report",
        "artifact_dir": str(artifact_dir),
        "snapshot": snapshot,
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    _write_markdown(out_md, snapshot)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
