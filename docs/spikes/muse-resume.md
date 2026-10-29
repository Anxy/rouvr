# R2 spike: headless `muse exec` resume — verdict: RESUME

Date: 29 Oct 2026. CLI: Muse Code 1.4.14. Model: meta `muse-spark-1.3-contributor`.
Reproduce: `META_MODEL_API_KEY=... SPIKE_FILES=60 bash scripts/spike_resume.sh`
(key from env only; the script kills only its own `setsid` child and redacts
transcripts before hashing).

## Protocol

1. Run 1: `muse exec --json --provider meta --session-id S --workspace W
   --max-model-steps 120 --no-parallel-tool-calls` on a toy task: write
   `nonce.txt` (random `r2-<epoch>-<rand>`) first, then 60 `fNN.txt` files.
2. The script polls and `kill -KILL`s the run's process group once `nonce.txt`
   plus ≥1 `fNN.txt` exist with work still incomplete (genuinely mid-run).
3. `nonce.txt` is deleted. Run 2 reuses `--session-id S` with a prompt that
   does NOT contain the nonce: report the nonce "from memory" and continue.
4. Gate: the run-1-only nonce appears in run 2's output → RESUME, else RESPAWN.

## Evidence (29 Oct run, sid `03cc493e-…-d1865592b3ad`)

- Run 1 killed mid-run with `nonce.txt` + `f01.txt`–`f11.txt` on disk.
- Run 2 (same sid, `nonce.txt` absent from disk) answered from memory with the
  exact run-1 nonce and the exact run-1 progress (`f01`–`f11` done, `f12`–`f60`
  remaining), then stopped on its 20-step budget. Prior context and tool
  history are both present across the kill.
- Transcript hashes (redacted JSONL, key never in prompt or output):
  - run1: `sha256:310722a258b6d9d3d0c134b8a69645786820e6dbcd7ba2802095672a3e01f87a` (52,042 bytes)
  - run2: `sha256:914e5fc146098a4cdb6db2b1e63c7c5dad0be0207985ebe8bbc8c872f57cd2fe` (234,138 bytes)
- The nonce value itself is withheld (present iff RESUME); rerun the script to
  re-verify. Earlier attempts also showed: no approval blocks on workspace
  writes; `--max-model-steps` ends a run as `completed` even with work
  remaining (R6 launcher must treat step-budget exhaustion as incomplete).

## Consequences

- `RESUME_SUPPORTED=true`. R6 implements resume-by-same-`--session-id` on the
  same attempt; respawn stays as the fallback path only.
- Headless MCP (R7 dependency): `muse exec --help` exposes no MCP flags and
  `muse mcp` only manages OAuth login for configured servers, so MCP-tool
  availability inside a headless turn is UNVERIFIED from the CLI surface.
  Recommendation: R7 ships the fallback (small tool loop around Muse) unless a
  live MCP-call probe passes first.
