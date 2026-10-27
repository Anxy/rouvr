from __future__ import annotations

import json
from pathlib import Path

from backend.models.persona_manifest_models import (
    get_persona_manifest_schema_current,
    get_tenant_overlay_schema_current,
)


def main() -> int:
    root = Path(__file__).resolve().parents[5]
    outputs = [
        (
            root / "projects" / "AIX" / "schemas" / "persona_manifest" / "v1" / "persona_manifest.schema.json",
            get_persona_manifest_schema_current(),
        ),
        (
            root / "projects" / "AIX" / "schemas" / "tenant_overlay" / "v1" / "tenant_overlay.schema.json",
            get_tenant_overlay_schema_current(),
        ),
    ]
    for output, payload in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
