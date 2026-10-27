#!/usr/bin/env sh
# Inject 'kill_midstep' into the morning-report demo, then rerun clean to show recovery.
set -u
cd "$(dirname "$0")/../.."
ROUVR_INJECT=kill_midstep python demo/morning_report.py "$@" || true
echo "--- rerun without injection ---"
python demo/morning_report.py "$@"
