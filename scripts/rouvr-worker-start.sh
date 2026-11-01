#!/bin/bash
# Restart one rouvr supervisor worker with the stack env plus the live-model
# provider env (0600). Usage: rouvr-worker-start.sh <orch|build|test|verify>
# Part of the demo bring-up (see demo/RUNBOOK.md). Never prints secrets.
set -euo pipefail
q="${1:?queue required: orch|build|test|verify}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPS="${ROUVR_OPS_DIR:-$ROOT/.data}"
# shellcheck disable=SC1090
source "$OPS/env.sh"
if [ -f "$OPS/meta-provider-env.sh" ]; then
  # shellcheck disable=SC1090
  source "$OPS/meta-provider-env.sh"  # live-model legs; absent => stub provider
fi
cd "$ROOT"
echo $$ > "$OPS/pids/worker-$q.pid"
exec .venv/bin/python -m rouvr.worker "$q"
