#!/usr/bin/env bash
# A1: WorkHub readback suite. Safe GETs only; mutating happy path is a
# documented sequence below (run it by hand with --mutate helpers).
# Env: WORKHUB_URL, WORKHUB_JWT, TENANT, PROJECT, SCRATCH_TASK_NUMBER.
# Key/JWT are never printed (only HTTP codes + redacted key names).
set -euo pipefail

: "${WORKHUB_URL:?set WORKHUB_URL}"; : "${WORKHUB_JWT:?set WORKHUB_JWT}"
: "${TENANT:?set TENANT}"; : "${PROJECT:?set PROJECT}"
: "${SCRATCH_TASK_NUMBER:?set SCRATCH_TASK_NUMBER}"

get() { # $1=path -> prints "HTTP_CODE path"
  code=$(curl -sS -o /tmp/wh_body.json -w '%{http_code}' \
    -H "Authorization: Bearer $WORKHUB_JWT" \
    -H "X-Hyeve-Tenant-Id: $TENANT" -H "X-Project-ID: $PROJECT" \
    "$WORKHUB_URL$1") || code="000"
  echo "$code $1"
}

fail=0
check() { # $1=expected $2=path
  out=$(get "$2"); code="${out%% *}"
  [ "$code" = "$1" ] || { echo "FAIL: expected $1 got $out"; fail=1; }
  echo "ok: $out"
}

check 200 /openapi.json
check 200 "/api/v1/projects/$PROJECT/tasks/by-number/$SCRATCH_TASK_NUMBER"
check 403 /api/v1/capabilities  # known role gap (see workhub-fixes.md); flip to 200 after the grant
echo "A1 readbacks exit=$fail (0 = all 2xx)"
exit $fail

# Mutating happy path (run by hand once GETs pass; needs FIX items 1-2):
#  1. POST /api/v1/projects/$PROJECT/assignments/claim
#  2. POST /api/v1/projects/$PROJECT/attempts
#  3. POST /api/v1/projects/$PROJECT/leases/renew
#  4. POST /api/v1/projects/$PROJECT/results
# Each with Idempotency-Key + X-Project-ID; expect 200/201 and stable codes.
