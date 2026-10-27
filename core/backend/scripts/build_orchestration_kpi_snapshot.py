from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any


def _find_runlog_dir() -> Path:
    current = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in current.parents:
        candidate = parent / "_runlogs"
        if candidate.exists() and candidate.is_dir():
            candidates.append(candidate)
    if candidates:
        ranked = sorted(
            candidates,
            key=lambda path: len(list(path.glob("wh-bridge-*.json"))),
            reverse=True,
        )
        if len(list(ranked[0].glob("wh-bridge-*.json"))) > 0:
            return ranked[0]
        return candidates[0]
    return Path.cwd() / "_runlogs"


def _load_wh_bridge_runlogs(runlog_dir: Path, limit: int) -> list[dict[str, Any]]:
    files = sorted(runlog_dir.glob("wh-bridge-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    payloads: list[dict[str, Any]] = []
    for path in files[: max(1, limit)]:
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return payloads


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = int((len(ordered) - 1) * percentile)
    return int(ordered[rank])


def _compute_mttr_minutes(events: list[tuple[datetime, bool]]) -> float | None:
    if not events:
        return None
    failures: list[datetime] = []
    recoveries: list[float] = []
    for ts, ok in sorted(events, key=lambda item: item[0]):
        if not ok:
            failures.append(ts)
            continue
        if failures:
            start = failures.pop(0)
            recoveries.append((ts - start).total_seconds() / 60.0)
    return round(mean(recoveries), 3) if recoveries else None


def build_snapshot(*, runlogs: list[dict[str, Any]]) -> dict[str, Any]:
    total_runs = len(runlogs)
    successful_runs = sum(1 for item in runlogs if bool(item.get("success")))
    workflow_success_rate = (successful_runs / total_runs) if total_runs > 0 else 0.0

    duplicate_dispatch_incidents = 0
    planner_latencies: list[int] = []
    coder_latencies: list[int] = []
    result_count = 0
    correlated_count = 0
    timed_events: list[tuple[datetime, bool]] = []

    for item in runlogs:
        raw_ts = str(item.get("generated_at_utc") or "").strip()
        if raw_ts:
            try:
                timed_events.append((datetime.fromisoformat(raw_ts.replace("Z", "+00:00")), bool(item.get("success"))))
            except Exception:
                pass
        results = item.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            result_count += 1
            if str(result.get("correlation_id") or "").strip():
                correlated_count += 1
            error_code = str(result.get("error_code") or "").upper()
            if error_code in {"IDEMPOTENCY_CONFLICT", "DUPLICATE_DISPATCH", "DUPLICATE_DISPATCH_INCIDENT"}:
                duplicate_dispatch_incidents += 1

            action = str(result.get("action") or "").lower()
            elapsed = int(result.get("elapsed_ms") or 0)
            if elapsed <= 0:
                continue
            if "plan" in action or "planner" in action:
                planner_latencies.append(elapsed)
            if "coder" in action or "devexecutor" in action or "execute" in action:
                coder_latencies.append(elapsed)

    traceability_coverage = (correlated_count / result_count) if result_count > 0 else 0.0
    mttr_minutes = _compute_mttr_minutes(timed_events)
    planner_p95 = _percentile(planner_latencies, 0.95)
    coder_p95 = _percentile(coder_latencies, 0.95)

    targets = {
        "workflow_success_rate_gte_97pct": workflow_success_rate >= 0.97,
        "duplicate_dispatch_incidents_lte_0_5pct": duplicate_dispatch_incidents <= max(1, int(total_runs * 0.005)),
        "p95_planner_latency_lte_6000ms": planner_p95 is not None and planner_p95 <= 6000,
        "p95_coder_step_latency_lte_12000ms": coder_p95 is not None and coder_p95 <= 12000,
        "mttr_minutes_lte_15": mttr_minutes is not None and mttr_minutes <= 15.0,
        "traceability_coverage_gte_95pct": traceability_coverage >= 0.95,
    }
    unknowns = [
        key
        for key, value in {
            "p95_planner_latency_lte_6000ms": planner_p95,
            "p95_coder_step_latency_lte_12000ms": coder_p95,
            "mttr_minutes_lte_15": mttr_minutes,
        }.items()
        if value is None
    ]
    return {
        "sample_size": {"runlogs": total_runs, "result_rows": result_count},
        "kpis": {
            "workflow_success_rate": round(workflow_success_rate, 6),
            "duplicate_dispatch_incidents": duplicate_dispatch_incidents,
            "p95_planner_latency_ms": planner_p95,
            "p95_coder_step_latency_ms": coder_p95,
            "mttr_minutes": mttr_minutes,
            "traceability_coverage": round(traceability_coverage, 6),
        },
        "targets": targets,
        "unknown_targets": unknowns,
    }


def _write_markdown(path: Path, snapshot: dict[str, Any]) -> None:
    kpi = snapshot.get("kpis", {})
    lines = [
        "# AIX Orchestration KPI Snapshot",
        "",
        f"- Generated: {datetime.now(UTC).isoformat()}",
        f"- Runlogs sampled: {snapshot.get('sample_size', {}).get('runlogs')}",
        f"- Result rows sampled: {snapshot.get('sample_size', {}).get('result_rows')}",
        "",
        "## KPI Values",
        f"- workflow_success_rate: `{kpi.get('workflow_success_rate')}`",
        f"- duplicate_dispatch_incidents: `{kpi.get('duplicate_dispatch_incidents')}`",
        f"- p95_planner_latency_ms: `{kpi.get('p95_planner_latency_ms')}`",
        f"- p95_coder_step_latency_ms: `{kpi.get('p95_coder_step_latency_ms')}`",
        f"- mttr_minutes: `{kpi.get('mttr_minutes')}`",
        f"- traceability_coverage: `{kpi.get('traceability_coverage')}`",
        "",
        "## Target Checks",
    ]
    targets = snapshot.get("targets", {})
    for key in sorted(targets):
        lines.append(f"- {key}: `{targets[key]}`")
    unknown = snapshot.get("unknown_targets") or []
    if unknown:
        lines.extend(["", "## Unknown Targets (insufficient runlog data)"])
        for item in unknown:
            lines.append(f"- {item}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build orchestration KPI snapshot from wh-bridge runlogs.")
    parser.add_argument("--runlog-dir", default="")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out-json", default="C:/dev/hyeve/reports/aix-kpi-snapshot-latest.json")
    parser.add_argument("--out-md", default="C:/dev/hyeve/reports/aix-kpi-snapshot-latest.md")
    args = parser.parse_args()

    runlog_dir = Path(args.runlog_dir).resolve() if args.runlog_dir else _find_runlog_dir()
    runlogs = _load_wh_bridge_runlogs(runlog_dir, limit=max(1, int(args.limit)))
    snapshot = build_snapshot(runlogs=runlogs)
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "suite": "aix_orchestration_kpi_snapshot",
        "runlog_dir": str(runlog_dir),
        "snapshot": snapshot,
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_markdown(out_md, snapshot)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
