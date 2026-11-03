#!/bin/bash
# Smoke-stack helper: start/stop a throwaway ledger + Next pair for e2e runs.
# PIDs go to pidfiles under .data/tmp; stops read the pidfile, never pkill,
# so a stop can never match its own shell or anyone else's server.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDDIR="$ROOT/.data/tmp/smoke"
LEDGER_PORT="${SMOKE_LEDGER_PORT:-18234}"
WEB_PORT="${SMOKE_WEB_PORT:-13002}"

pidfile() { echo "$PIDDIR/$1.pid"; }

start_ledger() { # $1=db path $2=token $3=admin password
  mkdir -p "$PIDDIR"
  (cd "$ROOT" && \
    ROUVR_LEDGER_DATABASE_URL="sqlite:///$1" ROUVR_LEDGER_TOKEN="$2" \
    ROUVR_ADMIN_PASSWORD="$3" ROUVR_AUTH_SECRET="smoke-secret-1234567890" \
    setsid -f "$ROOT/.venv/bin/uvicorn" ledger.app:app --port "$LEDGER_PORT" \
    >>"$PIDDIR/ledger.log" 2>&1)
  for _ in $(seq 1 50); do
    if curl -s -m 2 "http://127.0.0.1:$LEDGER_PORT/health" >/dev/null 2>&1; then
      pgrep -f "uvicorn ledge[r].app:app --port $LEDGER_PORT" | head -n 1 >"$(pidfile ledger)"
      echo "ledger up :$LEDGER_PORT pid $(cat "$(pidfile ledger)")"
      return 0
    fi
    sleep 0.2
  done
  echo "ledger failed to start" >&2; return 1
}

start_web() { # $1=ledger url
  (cd "$ROOT/web" && \
    ROUVR_LEDGER_URL="$1" setsid -f pnpm dev --port "$WEB_PORT" \
    >>"$PIDDIR/web.log" 2>&1)
  for _ in $(seq 1 60); do
    if curl -s -m 2 -o /dev/null "http://localhost:$WEB_PORT/workspace/login" 2>/dev/null; then
      # the node process (its cmdline carries the port; next-server does not)
      pgrep -f "bin/next dev --port $WEB_PORT" | head -n 1 >"$(pidfile web)"
      echo "web up :$WEB_PORT pid $(cat "$(pidfile web)")"
      return 0
    fi
    sleep 0.5
  done
  echo "web failed to start" >&2; return 1
}

stop_one() { # $1=name
  f="$(pidfile "$1")"
  if [ -f "$f" ]; then
    pid="$(cat "$f")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null       # parent first
      sleep 1
      pkill -P "$pid" 2>/dev/null  # then orphaned children, by parent pid
      sleep 1
      if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null; fi
    fi
    rm -f "$f"
    echo "$1 stopped (pid $pid)"
  else
    echo "$1 not running (no pidfile)"
  fi
}

case "${1:-}" in
  start-ledger) start_ledger "$2" "$3" "$4" ;;
  start-web) start_web "$2" ;;
  stop) stop_one web; stop_one ledger ;;
  *) echo "usage: $0 {start-ledger <db> <token> <adminpw> | start-web <ledger-url> | stop}" >&2; exit 2 ;;
esac
