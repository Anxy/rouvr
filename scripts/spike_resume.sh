#!/usr/bin/env bash
# R2: muse exec resume spike. Exit 0 with RESULT=RESUME|RESPAWN, 2/3 if inconclusive.
# Kill target is only this script's own setsid child. The API key is never printed.
set -euo pipefail

: "${META_MODEL_API_KEY:?set META_MODEL_API_KEY first (do not pass it as an argument)}"
OUT="${SPIKE_OUT:-$(mktemp -d /tmp/r2-spike.XXXXXX)}"
WORK="$OUT/work"
SID="${SPIKE_SESSION_ID:-$(cat /proc/sys/kernel/random/uuid)}"
NONCE="r2-$(date +%s)-$RANDOM"
NFILES="${SPIKE_FILES:-12}"
PROGRESS_TIMEOUT_S="${SPIKE_PROGRESS_TIMEOUT_S:-240}"
MUSE_BIN="${MUSE_BIN:-muse}"
mkdir -p "$WORK"

redact() { # stdin -> stdout with the key replaced; never fails the pipe
  META_MODEL_API_KEY="$META_MODEL_API_KEY" python3 -c \
    'import os,sys; k=os.environ.get("META_MODEL_API_KEY",""); sys.stdout.write(sys.stdin.read().replace(k,"[REDACTED]") if k else sys.stdin.read())'
}

echo "out=$OUT sid=$SID nonce=[hidden] files=$NFILES"

# --- run 1: start a long-enough task, kill its process group mid-run ---
PROMPT1="Crash-recovery test. Step 1 (do this first): write nonce.txt containing exactly '$NONCE' on a single line. Step 2: one tool call per file, no batching and no shell loops, write files f01.txt..f$(printf '%02d' "$NFILES") into the workspace, each one line 'lake: <a different invented lake name>'. When every file exists, reply DONE."
setsid "$MUSE_BIN" exec --json --provider meta --session-id "$SID" \
  --workspace "$WORK" --max-model-steps 120 --no-parallel-tool-calls \
  "$PROMPT1" >"$OUT/run1.jsonl" 2>"$OUT/run1.stderr" &
PID=$!
# Wait for genuine mid-run progress, then kill: nonce + >=1 file present, work incomplete.
DEADLINE=$((SECONDS + PROGRESS_TIMEOUT_S))
while true; do
  if ! kill -0 "$PID" 2>/dev/null; then
    wait "$PID" 2>/dev/null || true
    echo "run1=completed-or-died-before-kill (raise SPIKE_FILES and rerun)"
    exit 2
  fi
  nfiles=$(ls "$WORK"/f??.txt 2>/dev/null | wc -l || true)
  if [ -s "$WORK/nonce.txt" ] && [ "$nfiles" -ge 1 ] && [ "$nfiles" -lt "$NFILES" ]; then
    break
  fi
  if [ "$nfiles" -ge "$NFILES" ]; then
    echo "run1=completed-before-kill (raise SPIKE_FILES and rerun)"
    exit 2
  fi
  if (( SECONDS >= DEADLINE )); then
    kill -KILL -- "-$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    echo "run1 made no workspace progress within $PROGRESS_TIMEOUT_S: INCONCLUSIVE"
    exit 3
  fi
  sleep 5
done
kill -KILL -- "-$PID" 2>/dev/null || true  # own process group only (setsid)
wait "$PID" 2>/dev/null || true
echo "run1=killed-mid-run"
[ -s "$WORK/nonce.txt" ] || { echo "run1 made no workspace progress: INCONCLUSIVE"; exit 3; }
rm -f "$WORK/nonce.txt"  # a resumed session knows it from context; a fresh one cannot
redact <"$OUT/run1.jsonl" >"$OUT/run1.redacted.jsonl"

# --- run 2: same session id, nonce NOT in the prompt ---
PROMPT2="Continue your interrupted task. First question, answer from memory only: what was the exact nonce string you wrote earlier? Then finish any missing fNN.txt files and reply DONE."
timeout 240 "$MUSE_BIN" exec --json --provider meta --session-id "$SID" \
  --workspace "$WORK" --max-model-steps 20 --no-parallel-tool-calls \
  "$PROMPT2" >"$OUT/run2.jsonl" 2>"$OUT/run2.stderr" || echo "run2 exit=$? (timeout/failure recorded, continuing to verdict)"
redact <"$OUT/run2.jsonl" >"$OUT/run2.redacted.jsonl"

# --- verdict: run-1-only context reproduced in run 2? ---
if grep -qF "$NONCE" "$OUT/run2.jsonl"; then RESULT=RESUME; else RESULT=RESPAWN; fi
{
  echo "RESULT=$RESULT"
  echo "session_id=$SID"
  sha256sum "$OUT/run1.redacted.jsonl" "$OUT/run2.redacted.jsonl"
} | tee "$OUT/RESULT"
echo "evidence: $OUT (nonce value withheld; present iff RESUME)"
