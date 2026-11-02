"""Export the ledger FastAPI OpenAPI spec to ledger/openapi.json.

The generated TS client (web/src/lib/api/) is built from this file.
Regenerate after changing routes: python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "ledger" / "openapi.json"


def main() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        os.environ["ROUVR_LEDGER_DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["ROUVR_INSECURE_DEV"] = "1"
        os.environ.pop("ROUVR_LEDGER_TOKEN", None)
        # Module-level app (the same object uvicorn serves); env above must
        # be set before import since make_app() runs at import time.
        from ledger.app import app

        spec = app.openapi()
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
