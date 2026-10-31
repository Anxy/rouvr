"""scripts/rouvr: stop only kills pids this checkout started (pidfiles)."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_rouvr_script_bash_syntax():
    subprocess.run(["bash", "-n", "scripts/rouvr"], cwd=ROOT, check=True)


def test_stop_adopts_untracked_own_worker(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    ops = tmp_path / "ops"; ops.mkdir()
    own = subprocess.Popen(["python3", "-c", "import time; time.sleep(60)  # rouvr.worker orch"],
                           cwd=ROOT)
    env = {**os.environ, "HOME": str(home), "ROUVR_OPS_DIR": str(ops)}
    try:
        for _ in range(3):  # another own matching pid may be adopted first
            r = subprocess.run(["bash", "scripts/rouvr", "stop"], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, r.stderr
            if own.poll() is not None:
                break
        own.wait(timeout=5)  # adopted via pattern, then killed despite no pidfile
    finally:
        if own.poll() is None:
            own.kill()


def test_script_operands_and_token_scoping():
    rouvr = (ROOT / "scripts/rouvr").read_text()
    assert 'mkdir -p "$OPS/personas"' in rouvr and '"$OPS/keys"' in rouvr
    assert "receipt_ed25519.pem" in rouvr and "RECEIPT_SIGNING_KEY_PATH=$OPS/keys/receipt_ed25519.pem" in rouvr
    assert 'ROUVR_PERSONA_CREDS_DIR:-$HOME/Repository/hackathon/.secrets/rouvr/personas' in rouvr
    assert "foreign process" in rouvr and "_adopt ledger" in rouvr and "_adopt mcp" in rouvr
    assert 'OPS="${ROUVR_OPS_DIR:-/tmp/rouvr-supervisor}"' in (ROOT / "scripts/seed-user").read_text()
    run = (ROOT / "plugin/mcp/run.sh").read_text()
    assert 'OPS="${ROUVR_OPS_DIR:-/tmp/rouvr-supervisor}"' in run
    assert '_pre_token="${ROUVR_LEDGER_TOKEN:-}"' in run
    assert run.index("_pre_token") < run.index('. "$OPS/env.sh"') < run.index('ROUVR_LEDGER_TOKEN="$_pre_token"')


def test_script_scopes_secrets_and_spec():
    rouvr = (ROOT / "scripts/rouvr").read_text()
    assert "export GH_TOKEN=" not in rouvr  # never stack-wide: scoped file + per-task grant
    assert "export META_MODEL_API_KEY=" not in rouvr  # persona turns use 0600 creds files
    assert "ROUVR_GH_TOKEN_FILE=$OPS/gh-token" in rouvr
    assert "ROUVR_SPEC_REPO:-" in rouvr and "ROUVR_SPEC_BASE:-" in rouvr


def test_stop_leaves_sibling_path_process(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    ops = tmp_path / "ops"; pids = ops / "pids"; pids.mkdir(parents=True)
    # cmdline contains "$ROOT-lane/..." (prefix match) but not "$ROOT/..." and cwd is elsewhere
    sibling = subprocess.Popen(["python3", "-c", f"import time; time.sleep(60)  # {ROOT}-lane/worker"],
                               cwd=tmp_path)
    (pids / "worker-orch.pid").write_text(str(sibling.pid))
    env = {**os.environ, "HOME": str(home), "ROUVR_OPS_DIR": str(ops)}
    try:
        r = subprocess.run(["bash", "scripts/rouvr", "stop"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert sibling.poll() is None
        assert (pids / "worker-orch.pid").exists()
    finally:
        sibling.kill()


def test_creds_dir_rewritten_on_every_write_operands(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    ops = tmp_path / "ops"; (ops / "personas").mkdir(parents=True)
    (ops / "personas" / "tenants.yaml").write_text(
        'tenants:\n  supervisor:\n    personas:\n      coder-1: {role: coder, principal: svc-sup-coder, muse_model: "", credential_file: "/old/coder-1.json"}\n')
    new = tmp_path / "newcreds"
    env = {**os.environ, "HOME": str(home), "ROUVR_OPS_DIR": str(ops), "ROUVR_PERSONA_CREDS_DIR": str(new)}
    r = subprocess.run(["bash", "scripts/rouvr", "status"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert f'credential_file: "{new}/coder-1.json"' in (ops / "personas" / "tenants.yaml").read_text()


def test_stop_kills_every_duplicate_worker(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    ops = tmp_path / "ops"; ops.mkdir()
    procs = [subprocess.Popen(["python3", "-c", "import time; time.sleep(60)  # rouvr.worker orch"],
                              cwd=ROOT) for _ in range(2)]
    env = {**os.environ, "HOME": str(home), "ROUVR_OPS_DIR": str(ops)}
    try:
        r = subprocess.run(["bash", "scripts/rouvr", "stop"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        for p in procs:
            p.wait(timeout=5)
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


def test_up_health_and_plugin_order_static():
    rouvr = (ROOT / "scripts/rouvr").read_text()
    assert "Temporal did not answer" in rouvr and "temporal_ok" in rouvr
    assert "_kill_matching" in rouvr
    assert "ledger did not answer /health" in rouvr and "MCP did not answer" in rouvr
    assert 'grep -qF "$ROOT/"' in rouvr
    run = (ROOT / "plugin/mcp/run.sh").read_text()
    assert run.index('"$PWD/rouvr"') < run.index('"$HOME/Repository/hackathon/rouvr"')


def test_stop_kills_only_own_checkout_pids(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ops = tmp_path / "ops"
    pids = ops / "pids"
    pids.mkdir(parents=True)
    own = subprocess.Popen(["sleep", "60"], cwd=ROOT)       # runs from this checkout
    foreign = subprocess.Popen(["sleep", "60"], cwd=tmp_path)  # another checkout's worker
    (pids / "worker-orch.pid").write_text(str(own.pid))
    (pids / "worker-build.pid").write_text(str(foreign.pid))
    env = {**os.environ, "HOME": str(home), "ROUVR_OPS_DIR": str(ops)}
    try:
        r = subprocess.run(["bash", "scripts/rouvr", "stop"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        own.wait(timeout=5)  # killed by stop
        assert foreign.poll() is None  # untouched
        assert not (pids / "worker-orch.pid").exists()
        assert (pids / "worker-build.pid").exists()
    finally:
        own.kill()
        foreign.kill()


def test_compose_ledger_uses_sqlite_not_postgres():
    compose = (ROOT / "demo/docker-compose.yml").read_text()
    ledger = compose.split("ledger:")[1].split("\n  ", 1)[1] if "ledger:" in compose else compose
    assert "postgresql" not in ledger and "psycopg" not in ledger
    assert "sqlite" in ledger


def test_schedule_path_documented_coding_only():
    starter = (ROOT / "rouvr/starter.py").read_text()
    assert "coding-only" in starter
