#!/usr/bin/env bash
# Superseded by scripts/rouvr (single command). Kept as a shim.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rouvr" up
