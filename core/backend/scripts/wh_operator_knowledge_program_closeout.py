from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path("C:/dev/AIX")
RUNLOGS = ROOT / "_runlogs"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _summary_from_report(path: Path, keys: list[str]) -> dict[str, Any]:
    obj = _load_json(path)
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not obj:
        return out
    for k in keys:
        out[k] = obj.get(k)
    return out


def build_closeout() -> dict[str, Any]:
    reports = {
        "search_smoke": RUNLOGS / "wh_knowledge_search_smoke_20260228_r2.json",
        "lint_full": RUNLOGS / "wh_knowledge_lint_full_20260228.json",
        "lint_operator": RUNLOGS / "wh_knowledge_lint_operator_20260228.json",
        "dedupe_map": RUNLOGS / "wh_knowledge_dedupe_map_20260228.json",
        "dedupe_apply": RUNLOGS / "wh_knowledge_dedupe_apply_20260228.json",
        "dedupe_finalize": RUNLOGS / "wh_knowledge_dedupe_finalize_20260228.json",
        "shape_normalize": RUNLOGS / "wh_knowledge_archived_shape_normalize_20260228_r2.json",
        "param_guard": RUNLOGS / "wh_knowledge_search_param_guard_20260228.json",
    }

    children = [
        {"task_number": 643, "name": "K1 canonical bootstrap notes", "status": "needs_review"},
        {"task_number": 644, "name": "K2 metadata contract + lint", "status": "needs_review"},
        {"task_number": 645, "name": "K3 dedupe map", "status": "needs_review"},
        {"task_number": 646, "name": "K4 retrieval smoke suite", "status": "needs_review"},
        {"task_number": 647, "name": "K5 q-parameter hardening", "status": "needs_review"},
        {"task_number": 648, "name": "K6 apply archive candidates", "status": "needs_review"},
        {"task_number": 649, "name": "K7 finalize archived state", "status": "needs_review"},
        {"task_number": 650, "name": "K8 archived metadata normalization", "status": "needs_review"},
        {"task_number": 651, "name": "K9 closeout report", "status": "running"},
    ]

    closeout = {
        "timestamp": datetime.now(UTC).isoformat(),
        "program": {
            "parent_task_number": 642,
            "project_id": "bh-migrated-7-aix",
            "title": "WH Operator Knowledge Hardening Program",
        },
        "children": children,
        "artifacts": {
            "scripts": [
                "hyeve-aix-service/backend/scripts/wh_backfill_knowledge_content.py",
                "hyeve-aix-service/backend/scripts/wh_knowledge_search_smoke.py",
                "hyeve-aix-service/backend/scripts/wh_knowledge_lint.py",
                "hyeve-aix-service/backend/scripts/wh_knowledge_dedupe_map.py",
                "hyeve-aix-service/backend/scripts/wh_knowledge_dedupe_apply.py",
                "hyeve-aix-service/backend/scripts/wh_knowledge_dedupe_finalize_archive.py",
                "hyeve-aix-service/backend/scripts/wh_knowledge_archived_shape_normalize.py",
                "scripts/ci_wh_knowledge_search_param_guard.py",
            ],
            "docs": [
                "docs/ops/SESSION_DOC_ROUTING.md",
                "docs/ops/WH_KNOWLEDGE_METADATA_CONTRACT.md",
                "docs/ops/Bootstrap-new-agent.md",
                "docs/ops/WH_AGENT_GUARDRAILS.md",
                "docs/ops/SCRIPT_CAPABILITY_INDEX.md",
                "docs/ops/WH_HELP_CLI_VERSION.json",
            ],
            "reports": {
                "search_smoke": _summary_from_report(
                    reports["search_smoke"], ["passed", "checks_total", "checks_failed"]
                ),
                "lint_full": _summary_from_report(reports["lint_full"], ["total_findings"]),
                "lint_operator": _summary_from_report(reports["lint_operator"], ["total_findings"]),
                "dedupe_map": _summary_from_report(reports["dedupe_map"], ["totals"]),
                "dedupe_apply": _summary_from_report(reports["dedupe_apply"], ["totals"]),
                "dedupe_finalize": _summary_from_report(reports["dedupe_finalize"], ["totals"]),
                "shape_normalize": _summary_from_report(reports["shape_normalize"], ["totals"]),
                "param_guard": _summary_from_report(reports["param_guard"], ["ok", "total_findings"]),
            },
        },
        "residual_risks": [
            "Some legacy stream notes remain semantically duplicated across projects and are now metadata-archived rather than hard-deleted.",
            "WorkHub service currently forces metadata_json.knowledge=true; lifecycle state should rely on dedupe/archive/retrieval metadata.",
        ],
        "transition_recommendations": {
            "task_651": "needs_review",
            "parent_642": "needs_review",
            "operator_next_actions": [
                "Reviewer verifies runlogs and selected sample records in WH API.",
                "If accepted, transition child tasks and parent to done in batch.",
                "Optionally add service enhancement task to support structured knowledge.status object without overwrite.",
            ],
        },
    }
    return closeout


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate closeout report for WH Operator Knowledge Hardening program."
    )
    parser.add_argument("--report", default=str(RUNLOGS / "wh_operator_knowledge_program_closeout_20260228.json"))
    args = parser.parse_args()

    report = build_closeout()
    path = Path(args.report)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "timestamp": report["timestamp"]}, indent=2))


if __name__ == "__main__":
    main()
