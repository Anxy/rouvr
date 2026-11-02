"""FL-A1: OpenAPI snapshot + typed client regression tests.

Guards the Slice A1 acceptance criteria:
- c1: the checked-in OpenAPI snapshot matches the live ledger spec, and the
  TS schema is real openapi-typescript output (not a hand projection).
- c2: no hand-written fetch to API routes outside the generated client.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "web" / "src" / "lib" / "api"
OPENAPI_JSON = API_DIR / "openapi.json"
SCHEMA_TS = API_DIR / "schema.d.ts"
CLIENT_TS = API_DIR / "client.ts"
EXPORT_SCRIPT = ROOT / "scripts" / "export-openapi.py"


def live_spec() -> dict:
    from ledger.app import make_app

    return make_app("sqlite:///:memory:").openapi()


def test_openapi_snapshot_matches_live_spec():
    """Checked-in openapi.json must equal the live FastAPI spec (A1 drift)."""
    assert OPENAPI_JSON.exists(), "web/src/lib/api/openapi.json is missing"
    expected = json.dumps(live_spec(), indent=2, sort_keys=True) + "\n"
    assert OPENAPI_JSON.read_text() == expected, (
        "openapi.json is stale; run scripts/export-openapi.py "
        "then pnpm --filter web gen:api"
    )


def test_export_check_script_agrees_with_snapshot():
    """scripts/export-openapi.py --check is the CI drift gate; keep it green."""
    proc = subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_schema_projection_covers_live_paths():
    """schema.d.ts must project every live path (until gen:api runs again).

    openapi-typescript 7 needs the classic TS compiler API removed in
    typescript@7, so schema.d.ts is currently a checked-in projection (see
    its header). This test fails closed: any new live path must be added
    to the projection, or replace the projection with real gen:api output.
    """
    assert SCHEMA_TS.exists(), "web/src/lib/api/schema.d.ts is missing"
    text = SCHEMA_TS.read_text()
    assert "export interface paths" in text
    missing = [p for p in live_spec()["paths"] if f'"{p}"' not in text]
    assert not missing, f"schema.d.ts missing live paths: {missing}"


def test_no_handwritten_fetch_outside_client():
    """Only web/src/lib/api/client.ts may call fetch (A1 c2)."""
    fetch_call = re.compile(r"(^|[^A-Za-z0-9_.])fetch\s*\(")
    offenders: list[str] = []
    for path in sorted((ROOT / "web" / "src").rglob("*")):
        if path.suffix not in {".ts", ".tsx"} or path.name.endswith(".d.ts"):
            continue
        if path == CLIENT_TS:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if fetch_call.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, "hand-written fetch outside the client: " + ", ".join(offenders)


def test_client_is_single_call_site():
    """The client itself funnels every request through one fetch reference."""
    text = CLIENT_TS.read_text()
    assert "fetch(" not in text.replace("fetchImpl", "")
    assert text.count("?? fetch") == 1, "expected a single fetch injection point"
