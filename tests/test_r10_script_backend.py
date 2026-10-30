"""r10_rehearsal.sh must refuse non-ledger backends, never silently substitute ledger."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "r10_rehearsal.sh"


def test_stage_backend_refused():
    r = subprocess.run(["bash", str(SCRIPT)], env={"WORKHUB_BACKEND": "stage", "PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "ledger only" in r.stderr


def test_ledger_backend_not_refused():
    # Ledger must fall through to the pre-existing META_MODEL_API_KEY check, not exit 2.
    r = subprocess.run(["bash", str(SCRIPT)], env={"WORKHUB_BACKEND": "ledger", "PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 2
    assert "ledger only" not in r.stderr
