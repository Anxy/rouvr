# Rouvr sprint index summary

Generated from `ROUVR-SPRINT-INDEX.json` — 13 sprints.

| ID | Title | Status | Confidence | Known gaps |
|----|-------|--------|------------|------------|
| S001 | Bootstrap, hygiene gates, package base + ledger parity | PASS | high | Stage arm of the contract suite needs the attempt-scope grant. |
| S002 | R2 resume spike + A1 staging confirmation | PASS | high | workhub:attempt:open grant missing (human gate). |
| S003 | R4 TaskWorkflow + R5 lease/fencing | PASS | high | — |
| S004 | R6 build leg (personas, launcher, gitops) | PASS | high | Headless MCP unverified; file transports stand (accepted). |
| S005 | R7 submit gate + R8 hidden tests, runner, cheat-scan | PASS | high | — |
| S006 | R9 verifier + close | PASS | high | ed25519 receipt signing deferred to P2 (shortcut marker in code). |
| S007 | R10 rehearsal + record + A2 gates | PASS | high | A2 gates 1-4 need humans: grant, credential, stage run, back-port. |
| S008 | Stage promotion (A2, human-gated) | PASS | medium | workhub:attempt:open grant still pending for external stage arm; local ledger arm PASS 2026-10-30 (docs/r10-runs/20261030T1247). |
| S009 | hyeve-lane back-port (A2) | PASS | medium | Lane-host run against external staging deferred with S008 stage arm; local ledger arm PASS 2026-10-30. |
| S010 | P2 receipt signing | PASS | high | — |
| S017 | Loop-integrity fixes (review-004 item 4 + T1/T2/T6/T7-small) | RUNNING | medium | T3/T4/T5 queued in S018; risks (secrets, host coder) noted not fixed. |
| S018 | Governed ops + story gaps (queued) | RUNNING | medium | — |
| S019 | Scoped stack secrets, grant revocation, spec path config | RUNNING | medium | Coder still host-run (shortcut); api-token 0600 residual. |
