#!/usr/bin/env python3
"""Export the ledger FastAPI OpenAPI spec for the web typed client.

Regenerates web/src/lib/api/openapi.json from ledger.app.make_app, then run
`pnpm --filter web gen:api` to rebuild the generated TypeScript schema from it.
Run from the repo root:  python scripts/export_ledger_openapi.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ledger.app import make_app  # noqa: E402

OUT = ROOT / "web" / "src" / "lib" / "api" / "openapi.json"


def main() -> None:
    app = make_app("sqlite:///:memory:")
    spec = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
