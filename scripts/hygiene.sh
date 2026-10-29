#!/usr/bin/env bash
# R1 tenant-neutrality gate: fail on unapproved private-tenant/host strings.
# Scans tracked files plus untracked-but-not-ignored files, minus the allowlist.
# Usage: scripts/hygiene.sh   (run from anywhere inside the repo)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && git rev-parse --show-toplevel)"
cd "$ROOT"

ALLOW=".github/hygiene-allowlist.txt"
PATTERN='vmg|betademo|hawks|/home/anxy|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|115\.246|llama\.com|Maverick'

mapfile -t ALLOWLIST < <(grep -vE '^\s*(#|$)' "$ALLOW" || true)

# Nul-delimited candidate file list with allowlist entries removed.
# Allowlist lines are exact repo-relative paths or "<dir>/*" prefixes.
list_files() {
  { git ls-files -z; git ls-files --others --exclude-standard -z; } \
    | while IFS= read -r -d '' f; do
        skip=0
        for a in ${ALLOWLIST[@]+"${ALLOWLIST[@]}"}; do
          case "$a" in
            */\*) prefix="${a%/\*}"; [ "${f#$prefix}" != "$f" ] && skip=1 ;;
            *) [ "$f" = "$a" ] && skip=1 ;;
          esac
        done
        (( skip )) || printf '%s\0' "$f"
      done
}

mapfile -d '' -t FILES < <(list_files)
if ((${#FILES[@]} == 0)); then
  echo "hygiene: no files to scan"
  exit 0
fi

errfile="$(mktemp)"
out=$(printf '%s\0' "${FILES[@]}" | xargs -0 grep -HniE -I "$PATTERN" -- 2>"$errfile" || true)

if [ -n "$out" ]; then
  printf '%s\n' "$out"
  echo "hygiene: FAIL — unapproved private strings above" >&2
  rm -f "$errfile"
  exit 1
elif [ -s "$errfile" ]; then
  cat "$errfile" >&2
  echo "hygiene: scan error" >&2
  rm -f "$errfile"
  exit 2
else
  echo "hygiene: clean (${#FILES[@]} files)"
  rm -f "$errfile"
fi
