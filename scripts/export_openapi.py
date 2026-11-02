#!/usr/bin/env python3
"""Export the ledger FastAPI OpenAPI spec to web/openapi.json.

Run from the repo root:  python3 scripts/export_openapi.py
Then regenerate types:   pnpm --filter web gen:api

The committed web/openapi.json is the input to openapi-typescript and is
covered by tests/test_typed_client.py, which fails on drift from the live
spec — re-export and regenerate whenever ledger routes change.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "web" / "openapi.json"


def main() -> None:
    os.environ.setdefault("ROUVR_INSECURE_DEV", "1")
    from ledger.app import make_app

    spec = make_app("sqlite:///:memory:").openapi()
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
