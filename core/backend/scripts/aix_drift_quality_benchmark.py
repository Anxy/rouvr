from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUT_DIR = ROOT / "projects" / "AIX" / "artifacts" / "drift_quality"
PYTHON_ROOT = ROOT / "projects" / "AIX" / "hyeve-aix-service"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from backend.services.drift_quality_service import assess_action_map_drift_quality


def _baseline_should_propose(*, provider_before: list[str], provider_after: list[str]) -> bool:
    return set(provider_before) != set(provider_after)


def _samples() -> list[dict[str, Any]]:
    return [
        {
            "name": "high_signal_provider_delta",
            "expected_proposal": True,
            "provider_before": ["slavx"],
            "provider_after": ["slavx", "onlineai"],
            "codeintel_context": {
                "entities_total": 6,
                "citations": [{"f": "a"}, {"f": "b"}, {"f": "c"}],
                "confidence_score": 0.9,
            },
        },
        {
            "name": "low_signal_noise",
            "expected_proposal": False,
            "provider_before": ["slavx"],
            "provider_after": ["slavx", "onlineai"],
            "codeintel_context": {"entities_total": 1, "citations": [], "confidence_score": 0.2},
        },
        {
            "name": "no_change_clean",
            "expected_proposal": False,
            "provider_before": ["slavx"],
            "provider_after": ["slavx"],
            "codeintel_context": {"entities_total": 0, "citations": [], "confidence_score": 0.0},
        },
        {
            "name": "insufficient_confidence",
            "expected_proposal": False,
            "provider_before": ["slavx"],
            "provider_after": ["slavx", "onlineai"],
            "codeintel_context": {"entities_total": 3, "citations": [{"f": "a"}, {"f": "b"}], "confidence_score": 0.3},
        },
        {
            "name": "strong_context_with_delta",
            "expected_proposal": True,
            "provider_before": ["slavx", "local"],
            "provider_after": ["slavx", "onlineai"],
            "codeintel_context": {"entities_total": 5, "citations": [{"f": "a"}, {"f": "b"}], "confidence_score": 0.84},
        },
    ]


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for row in rows:
        expected = bool(row["expected_proposal"])
        predicted = bool(row[key])
        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
        elif (not predicted) and (not expected):
            tn += 1
        else:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    accuracy = (tp + tn) / len(rows) if rows else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "accuracy": round(accuracy, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark drift-quality gate vs MVP3-style baseline.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for report artifacts.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for sample in _samples():
        baseline = _baseline_should_propose(
            provider_before=sample["provider_before"],
            provider_after=sample["provider_after"],
        )
        improved = assess_action_map_drift_quality(
            provider_before=sample["provider_before"],
            provider_after=sample["provider_after"],
            codeintel_context=sample["codeintel_context"],
        )
        rows.append(
            {
                "name": sample["name"],
                "expected_proposal": sample["expected_proposal"],
                "baseline_predicted": baseline,
                "improved_predicted": bool(improved["should_propose"]),
                "quality_score": improved["quality_score"],
                "reasons": improved["reasons"],
            }
        )

    baseline_metrics = _metrics(rows, "baseline_predicted")
    improved_metrics = _metrics(rows, "improved_predicted")
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "benchmark_name": "aix_drift_quality_mvp5_1_2",
        "dataset_size": len(rows),
        "baseline_metrics": baseline_metrics,
        "improved_metrics": improved_metrics,
        "delta": {
            "precision": round(improved_metrics["precision"] - baseline_metrics["precision"], 4),
            "recall": round(improved_metrics["recall"] - baseline_metrics["recall"], 4),
            "accuracy": round(improved_metrics["accuracy"] - baseline_metrics["accuracy"], 4),
            "false_positives_reduction": baseline_metrics["fp"] - improved_metrics["fp"],
        },
        "samples": rows,
    }

    latest_json = out_dir / "drift-quality-benchmark-latest.json"
    latest_md = out_dir / "drift-quality-benchmark-latest.md"
    latest_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest_md.write_text(
        "\n".join(
            [
                "# AIX Drift Quality Benchmark",
                "",
                f"- Generated (UTC): {report['generated_at_utc']}",
                f"- Dataset size: {report['dataset_size']}",
                "",
                "## Baseline (MVP3-style: provider delta only)",
                f"- Precision: {baseline_metrics['precision']}",
                f"- Recall: {baseline_metrics['recall']}",
                f"- Accuracy: {baseline_metrics['accuracy']}",
                f"- False positives: {baseline_metrics['fp']}",
                "",
                "## Improved (MVP5.1.2 quality gate)",
                f"- Precision: {improved_metrics['precision']}",
                f"- Recall: {improved_metrics['recall']}",
                f"- Accuracy: {improved_metrics['accuracy']}",
                f"- False positives: {improved_metrics['fp']}",
                "",
                "## Delta",
                f"- Precision delta: {report['delta']['precision']}",
                f"- Recall delta: {report['delta']['recall']}",
                f"- Accuracy delta: {report['delta']['accuracy']}",
                f"- False-positive reduction: {report['delta']['false_positives_reduction']}",
            ]
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "success": True,
                "json_report": str(latest_json),
                "markdown_report": str(latest_md),
                "baseline_metrics": baseline_metrics,
                "improved_metrics": improved_metrics,
                "delta": report["delta"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
