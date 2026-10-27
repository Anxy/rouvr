#!/usr/bin/env sh
# Inject 'revoked_key' into the morning-report demo, then rerun clean to show recovery.
set -u
cd "$(dirname "$0")/../.."
ROUVR_INJECT=revoked_key python demo/morning_report.py "$@" || true
echo "--- rerun without injection ---"
python demo/morning_report.py "$@"
