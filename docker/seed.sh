#!/bin/sh
# One-shot: register the demo tenant/project against a healthy ledger.
set -eu

LEDGER_URL="${ROUVR_LEDGER_URL:-http://ledger:8221}"
TOKEN="${ROUVR_LEDGER_TOKEN:?ROUVR_LEDGER_TOKEN is required}"
TENANT="${ROUVR_SEED_TENANT:-demo}"
PROJECT="${ROUVR_SEED_PROJECT:-demo}"

echo "waiting for ledger at $LEDGER_URL ..."
i=0
until curl -sf "$LEDGER_URL/health" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "ledger did not become healthy" >&2
    exit 1
  fi
  sleep 2
done

hdr() {
  curl -sf -H "x-rouvr-token: $TOKEN" -H "Content-Type: application/json" "$@"
}

hdr -X POST -d "{\"tenant\":\"$TENANT\"}" "$LEDGER_URL/v1/tenants" >/dev/null || true
hdr -X POST -d "{\"tenant\":\"$TENANT\",\"project\":\"$PROJECT\"}" "$LEDGER_URL/v1/projects" >/dev/null || true
echo "seeded tenant=$TENANT project=$PROJECT"
