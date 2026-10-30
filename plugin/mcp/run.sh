#!/usr/bin/env bash
# Plugin-launched WorkHub MCP server (stdio). The plugin bundle is copied to
# the Muse plugin cache, so locate the real Rouvr checkout explicitly:
# $ROUVR_ROOT, else the known workspace layouts. Loads the supervisor
# operands env when the stack is up, else falls back to ledger defaults.
set -euo pipefail
ROOT="${ROUVR_ROOT:-}"
if [ -z "$ROOT" ]; then
  for cand in "$HOME/Repository/hackathon/rouvr" "$PWD/rouvr" "$PWD"; do
    if [ -f "$cand/rouvr/mcp_server.py" ] && [ -x "$cand/.venv/bin/python" ]; then ROOT="$cand"; break; fi
  done
fi
[ -n "$ROOT" ] || { echo "rouvr checkout not found (set ROUVR_ROOT)" >&2; exit 1; }
[ -f /tmp/rouvr-supervisor/env.sh ] && { set -a; . /tmp/rouvr-supervisor/env.sh; set +a; }
export WORKHUB_BASE_URL="${WORKHUB_BASE_URL:-http://127.0.0.1:8221}"
export ROUVR_MCP_TRANSPORT=stdio
export PYTHONPATH="$ROOT"
# Restart loop (S012): if the server dies (stack restart, crash), bring it
# back so the supervisor session regains its tools without a new chat.
while true; do
  "$ROOT/.venv/bin/python" -m rouvr.mcp_server || true
  sleep 2
done
