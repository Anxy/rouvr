# Rouvr sprint index summary

Generated from `ROUVR-SPRINT-INDEX.json` — 20 sprints.

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
| S011 | Auth on local ports, fenced persona windows, clone credential discipline | PASS | medium | Retrospective registration 2026-10-31: scope verified present at e2b493c with green gates; no contemporaneous ledger record; sheet SS11 PASS closeout stands as the review record. |
| S012 | MCP reconnect, stuck-task visibility, supervisor waiting-on-you rollup | PASS | medium | Retrospective registration 2026-10-31: scope verified present at e2b493c with green gates; no contemporaneous ledger record; sheet SS11 PASS closeout stands as the review record. |
| S013 | One verdict vocabulary, PR hygiene on rework, readable evidence | PASS | medium | Retrospective registration 2026-10-31: scope verified present at e2b493c with green gates; no contemporaneous ledger record; sheet SS11 PASS closeout stands as the review record. |
| S014 | Indexed evidence dedupe and WAL-mode ledger | PASS | medium | Retrospective registration 2026-10-31: scope verified present at e2b493c with green gates; no contemporaneous ledger record; sheet SS11 PASS closeout stands as the review record. |
| S015 | Crash-recovery test, repo slimming decision, task cost visibility | PASS | medium | Retrospective registration 2026-10-31: scope verified present at e2b493c with green gates; no contemporaneous ledger record; sheet SS11 PASS closeout stands as the review record. |
| S016 | Saved user profiles, per-user WorkHub tokens, and task ownership | PASS | medium | Retrospective registration 2026-10-31: scope verified present at e2b493c with green gates; no contemporaneous ledger record; sheet SS11 PASS closeout stands as the review record. |
| S017 | Loop-integrity fixes (review-004 item 4 + T1/T2/T6/T7-small) | PASS | medium | T3/T4/T5 shipped in S018; secrets scoped in S019; host-coder default remains an explicit documented choice. |
| S018 | Governed ops + story gaps (queued) | PASS | medium | — |
| S019 | Scoped stack secrets, grant revocation, spec path config | PASS | medium | Coder still host-run (shortcut); api-token 0600 residual. |
| S020 | Review-10-31 trust + packaging fixes (F1/F2/F3/F6/F7) | PASS | medium | F5 default-container (post-demo per review); F8 machine defaults (dev ergonomics); F4/F9/F10 live Meta / real WorkHub / recordings (owner/external) |
