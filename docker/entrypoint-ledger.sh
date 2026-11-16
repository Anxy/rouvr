#!/bin/sh
# Ledger / worker / MCP process entry. Generates a receipt key when missing.
set -eu

mkdir -p /data/keys /data/hidden-tests /data/workspaces /data/grants \
  /data/evidence /data/specs /data/tmp
chmod 700 /data/grants 2>/dev/null || true
export TMPDIR="${TMPDIR:-/data/tmp}"

if [ ! -f "${RECEIPT_SIGNING_KEY_PATH:-/data/keys/receipt_ed25519.pem}" ]; then
  python - <<'PY'
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

path = Path(os.environ.get("RECEIPT_SIGNING_KEY_PATH", "/data/keys/receipt_ed25519.pem"))
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(
    Ed25519PrivateKey.generate().private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
)
path.chmod(0o600)
print(f"wrote receipt key {path}", flush=True)
PY
fi

# Persona credential JSON (0600). Required by PersonaRegistry; ledger-backend
# workers use workhub_token=ledger and pull META_API_KEY for Muse.
python - <<'PY'
import json, os
from pathlib import Path

base = Path("/app/personas")
base.mkdir(parents=True, exist_ok=True)
payload = {
    "meta_api_key": os.environ.get("META_API_KEY", ""),
    "workhub_token": "ledger",
    "github_token": "",
}
for name in ("coder-1", "reviewer-1"):
    path = base / f"{name}.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
print("wrote persona credential files", flush=True)
PY

# Host Muse CLI (Meta launcher + muse-bin-*) bind-mounted at /opt/muse-host.
# Prefer a private copy under /data/muse. Never fail boot when a sibling
# worker already has muse-bin executing ("Text file busy").
if [ -x /opt/muse-host/muse ]; then
  mkdir -p /data/muse
  _muse_sync() {
    src="$1"; dest="$2"
    if [ -e "$dest" ]; then
      return 0
    fi
    tmp="${dest}.new.$$"
    cp -a "$src" "$tmp" && mv -f "$tmp" "$dest"
  }
  _muse_sync /opt/muse-host/muse /data/muse/muse || true
  for f in /opt/muse-host/muse-bin-*; do
    [ -e "$f" ] || continue
    _muse_sync "$f" "/data/muse/$(basename "$f")" || true
  done
  for f in /opt/muse-host/.muse-version /opt/muse-host/.muse-release-info.json; do
    [ -e "$f" ] || continue
    _muse_sync "$f" "/data/muse/$(basename "$f")" || true
  done
  if [ -x /data/muse/muse ]; then
    chmod -R a+rX /data/muse 2>/dev/null || true
    export PATH="/data/muse:${PATH:-/usr/local/bin:/usr/bin:/bin}"
    export MUSE_BIN="${MUSE_BIN:-/data/muse/muse}"
  else
    export PATH="/opt/muse-host:${PATH:-/usr/local/bin:/usr/bin:/bin}"
    export MUSE_BIN="${MUSE_BIN:-/opt/muse-host/muse}"
  fi
  export MUSE_UPDATE_INTERVAL_SECONDS="${MUSE_UPDATE_INTERVAL_SECONDS:-86400}"
  echo "muse wired at $MUSE_BIN" >&2
fi

role="${1:-ledger}"
shift || true

case "$role" in
  ledger)
    exec uvicorn ledger.app:app --host 0.0.0.0 --port "${ROUVR_LEDGER_PORT:-8221}" --log-level warning
    ;;
  worker)
    exec python -m rouvr.worker "${1:-all}"
    ;;
  demo-runner)
    exec python /app/docker/demo_runner.py
    ;;
  mcp)
    # MCP intentionally binds loopback inside the container; publish via
    # compose network alias / sidecar if you need host access.
    exec python -m rouvr.mcp_server
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
