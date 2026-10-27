from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PYTHON_ROOT = ROOT / "projects" / "AIX" / "hyeve-aix-service"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from backend.services.template_pack_governance_service import validate_template_pack_compatibility

OUT_DIR = ROOT / "projects" / "AIX" / "artifacts" / "template_pack_validation"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    samples = [
        {
            "name": "compatible_insurance_pack",
            "result": validate_template_pack_compatibility(
                pack_definition={
                    "vertical": "insurance",
                    "capabilities": {"model_routing": {"allowed_providers": ["slavx"]}},
                },
                active_map_definition={"capabilities": {"model_routing": {"allowed_providers": ["slavx", "onlineai"]}}},
                active_map_version=4,
                min_map_version=3,
                max_map_version=6,
            ),
        },
        {
            "name": "incompatible_healthcare_pack",
            "result": validate_template_pack_compatibility(
                pack_definition={
                    "vertical": "",
                    "capabilities": {"model_routing": {"allowed_providers": ["onlineai"]}},
                },
                active_map_definition={"capabilities": {"model_routing": {"allowed_providers": ["slavx"]}}},
                active_map_version=2,
                min_map_version=3,
                max_map_version=6,
            ),
        },
    ]
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "benchmark_name": "aix_template_pack_governance_mvp5_1_3",
        "samples": samples,
    }
    json_path = OUT_DIR / "template-pack-validation-latest.json"
    md_path = OUT_DIR / "template-pack-validation-latest.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# AIX Template Pack Validation Report",
        "",
        f"- Generated (UTC): {report['generated_at_utc']}",
        "",
    ]
    for sample in samples:
        lines.append(f"## {sample['name']}")
        lines.append(f"- valid: {sample['result']['valid']}")
        lines.append(f"- active_map_version: {sample['result']['active_map_version']}")
        if sample["result"]["violations"]:
            lines.append("- violations:")
            for item in sample["result"]["violations"]:
                lines.append(f"  - {item['code']}")
        else:
            lines.append("- violations: none")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "success": True,
                "json_report": str(json_path),
                "markdown_report": str(md_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
