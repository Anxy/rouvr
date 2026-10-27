"""Tenant-safe, versioned KPI computation with source traces."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

KPI_DEFINITIONS = {
    "v1": {
        "dispatch_success_rate": "successful_dispatches / total_dispatches",
        "mean_cycle_time_ms": "total_cycle_time_ms / completed_runs",
    }
}


def compute_business_kpis(*, definition_version: str, source_metrics: dict[str, Any]) -> dict[str, Any]:
    if definition_version not in KPI_DEFINITIONS:
        raise ValueError("KPI_DEFINITION_VERSION_UNSUPPORTED")

    total_dispatches = int(source_metrics.get("total_dispatches") or 0)
    successful_dispatches = int(source_metrics.get("successful_dispatches") or 0)
    total_cycle_time_ms = int(source_metrics.get("total_cycle_time_ms") or 0)
    completed_runs = int(source_metrics.get("completed_runs") or 0)

    success_rate = (successful_dispatches / total_dispatches) if total_dispatches > 0 else 0.0
    mean_cycle_time = (total_cycle_time_ms / completed_runs) if completed_runs > 0 else 0.0

    result = {
        "definition_version": definition_version,
        "kpis": {
            "dispatch_success_rate": round(success_rate, 6),
            "mean_cycle_time_ms": round(mean_cycle_time, 3),
        },
        "trace": {
            "dispatch_success_rate": {
                "formula": KPI_DEFINITIONS[definition_version]["dispatch_success_rate"],
                "inputs": {"successful_dispatches": successful_dispatches, "total_dispatches": total_dispatches},
            },
            "mean_cycle_time_ms": {
                "formula": KPI_DEFINITIONS[definition_version]["mean_cycle_time_ms"],
                "inputs": {"total_cycle_time_ms": total_cycle_time_ms, "completed_runs": completed_runs},
            },
        },
    }
    stable_payload = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    result["reproducibility_hash"] = sha256(stable_payload.encode("utf-8")).hexdigest()
    return result
