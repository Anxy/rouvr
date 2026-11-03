#!/usr/bin/env python3
"""Export the ledger FastAPI OpenAPI spec for the typed TS client (FL-A1).

Writes the spec served by ``ledger.app`` to ``web/src/lib/api/openapi.json``,
then the web client is regenerated with::

    pnpm --filter web gen:api

The app is built against a throwaway SQLite file so exporting never touches
a real ledger database.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "src" / "lib" / "api" / "openapi.json"


def main() -> None:
    sys.path.insert(0, str(ROOT))
    fd, tmp = tempfile.mkstemp(prefix="rouvr-openapi-", suffix=".db")
    os.close(fd)
    try:
        from ledger.app import make_app

        spec = make_app(f"sqlite:///{tmp}").openapi()
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp + suffix)
            except OSError:
                pass
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
