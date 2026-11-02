"""Export the ledger FastAPI OpenAPI spec for the web typed client.

Usage: python scripts/export_openapi.py [output_path]
Default output: ledger/openapi.json (consumed by `gen:api` in web/).

Builds the app against a throwaway SQLite file so exporting never touches
the real ledger database.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "ledger" / "openapi.json"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    with tempfile.TemporaryDirectory(prefix="rouvr-openapi-") as tmp:
        os.environ["ROUVR_LEDGER_DATABASE_URL"] = f"sqlite:///{tmp}/export.db"
        sys.path.insert(0, str(ROOT))
        from ledger.app import make_app

        spec = make_app().openapi()
    out.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote {out} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
