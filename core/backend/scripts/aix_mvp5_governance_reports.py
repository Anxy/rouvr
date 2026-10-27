from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PYTHON_ROOT = ROOT / "projects" / "AIX" / "hyeve-aix-service"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from backend.services.business_kpi_service import compute_business_kpis
from backend.services.marketplace_contract_service import validate_marketplace_template_record
from backend.services.plugin_runtime_policy_service import evaluate_plugin_runtime_policy
from backend.services.voice_nlp_policy_service import evaluate_voice_nlp_enablement, simulate_voice_nlp_rollback_drill

OUT_DIR = ROOT / "projects" / "AIX" / "artifacts" / "mvp5_governance"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    marketplace = {
        "valid_record": validate_marketplace_template_record(
            record={
                "template_id": "tpl-1",
                "template_name": "Core Template",
                "template_version": "1.0.0",
                "provenance": {"publisher": "hyeve", "signature": "sig-abc"},
                "compatibility": {"min_map_version": 3, "verticals": ["insurance"]},
            }
        ),
        "invalid_record": validate_marketplace_template_record(
            record={
                "template_id": "tpl-2",
                "template_name": "Unsafe Template",
                "template_version": "1.0.0",
                "provenance": {"publisher": "", "signature": ""},
                "compatibility": {"min_map_version": 0, "verticals": []},
            }
        ),
    }
    plugin = {
        "allowed": evaluate_plugin_runtime_policy(
            plugin_manifest={
                "signed": True,
                "sandboxed": True,
                "egress_hosts": ["api.hyeve.local"],
                "token_scopes": ["tasks.read"],
                "quota_per_hour": 100,
            },
            allowed_egress_hosts=["api.hyeve.local"],
            allowed_token_scopes=["tasks.read", "tasks.write"],
            max_quota_per_hour=300,
        ),
        "blocked": evaluate_plugin_runtime_policy(
            plugin_manifest={
                "signed": False,
                "sandboxed": False,
                "egress_hosts": ["evil.example"],
                "token_scopes": ["admin.all"],
                "quota_per_hour": 9000,
            },
            allowed_egress_hosts=["api.hyeve.local"],
            allowed_token_scopes=["tasks.read"],
            max_quota_per_hour=300,
        ),
    }
    kpi = compute_business_kpis(
        definition_version="v1",
        source_metrics={
            "total_dispatches": 25,
            "successful_dispatches": 22,
            "total_cycle_time_ms": 50000,
            "completed_runs": 10,
        },
    )
    voice = {
        "default_gate": evaluate_voice_nlp_enablement(
            feature_flag_enabled=False,
            policy_checks_passed=True,
            rollback_drill_recent=True,
            compliance_approved=True,
        ),
        "rollback_drill": simulate_voice_nlp_rollback_drill(incident_id="inc-voice-1", actor_id="operator-1"),
    }

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "marketplace_contract": marketplace,
        "plugin_sandbox": plugin,
        "business_kpi": kpi,
        "voice_nlp": voice,
    }
    json_path = OUT_DIR / "mvp5-governance-report-latest.json"
    md_path = OUT_DIR / "mvp5-governance-report-latest.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# AIX MVP5 Governance Report",
                "",
                f"- Generated (UTC): {report['generated_at_utc']}",
                "- Marketplace: provenance and compatibility contract checks included (valid + invalid sample).",
                "- Plugin sandbox: allowlist and denial samples recorded.",
                "- KPI layer: versioned KPI output with reproducibility hash generated.",
                "- Voice NLP: default-disabled gate and rollback drill sample recorded.",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"success": True, "json_report": str(json_path), "markdown_report": str(md_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
