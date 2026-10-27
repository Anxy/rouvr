"""AIX Feature 12 (Packet 03) — Unit tests for Runtime Probes & Objective Verifier."""

from pathlib import Path

from backend.aix_core.runtime.verification.objective_truth import (
    derive_canonical_truth,
)
from backend.aix_core.runtime.verification.objective_verification_contract import (
    derive_ovc,
)
from backend.aix_core.runtime.verification.runtime_probe_runner import (
    RuntimeProbeRunner,
)
from backend.aix_core.runtime.verification.objective_verifier import (
    ObjectiveVerifier,
)


def test_runtime_probe_sqlite_detects_postgres(tmp_path: Path):
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    api_file = backend_dir / "api.py"
    api_file.write_text(
        "import os\n"
        "from fastapi import FastAPI\n"
        "DATABASE_URL = 'postgresql+psycopg2://user:pass@localhost:5432/expenses'\n"
        "import psycopg2\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )

    runner = RuntimeProbeRunner(tmp_path)
    result = runner.probe_db_stack(expected_db="sqlite")
    assert not result.passed
    assert "PostgreSQL" in result.observed or "psycopg" in result.observed.lower()


def test_runtime_probe_sqlite_passes_on_sqlite(tmp_path: Path):
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    api_file = backend_dir / "api.py"
    api_file.write_text(
        "import os\n"
        "from fastapi import FastAPI\n"
        "DATABASE_URL = 'sqlite:///./expenses.db'\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )
    (tmp_path / "expenses.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)

    runner = RuntimeProbeRunner(tmp_path)
    result = runner.probe_db_stack(expected_db="sqlite")
    assert result.passed
    assert "SQLite" in result.observed


def test_runtime_probe_uvicorn_startup(tmp_path: Path):
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI(title='Expense Tracker')\n"
        "@app.get('/health')\n"
        "def health(): return {'status': 'ok'}\n",
        encoding="utf-8",
    )

    runner = RuntimeProbeRunner(tmp_path)
    result = runner.probe_server_startup()
    assert result.passed
    assert "200" in result.observed


def test_objective_verifier_fails_on_postgres_divergence(tmp_path: Path):
    app_file = tmp_path / "app.py"
    app_file.write_text(
        "from fastapi import FastAPI\n"
        "DATABASE_URL = 'postgresql://localhost/expenses'\n"
        "import psycopg\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )
    truth = derive_canonical_truth(
        original_text="Build FastAPI + SQLite Expense Tracker",
        objective_id="obj-pg",
    )
    ovc = derive_ovc(truth)
    verifier = ObjectiveVerifier()
    v_res, v_dec, gaps = verifier.verify_objective(ovc=ovc, workspace_path=tmp_path)
    assert v_res.runtime_proof_status == "FAIL"
    assert v_dec.decision in ("REPAIR", "HALT", "BLOCKED")
    assert any(g.ov_id.startswith("OV-") for g in gaps)


def test_objective_verifier_wired_contracts(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/health')\n"
        "def health(): return {'status':'ok'}\n"
        "DATABASE_URL='sqlite:///./app.db'\n",
        encoding="utf-8",
    )
    (tmp_path / "app.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
    truth = derive_canonical_truth(
        original_text="Build FastAPI + SQLite Expense Tracker with uvicorn",
        objective_id="obj-ok",
    )
    ovc = derive_ovc(truth)
    assert any(i.ov_id for i in ovc.ov_items)
