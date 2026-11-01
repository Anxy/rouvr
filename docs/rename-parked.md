# Rename (aix/hyeve_aix → rouvr): PARKED until after the hackathon

Status: frozen after 0968c3e (2026-11-01). Do not start hyeve_core dedupe
or aix_core→rouvr_core until the hackathon demo path is done.

Landed: `backend.hyeve_aix_service` canonical home is now
`backend.rouvr_service` (`RouvrServiceSettings`, compat alias kept);
old package is a re-export shim. Env prefix `HYEVE_AIX_SERVICE_*`
unchanged; `/api/aix*` route aliases kept.

Remaining plan (recon 2026-11-01, est. 8–12 days with shims):
1. Dedupe the triple-copied `hyeve_core` to one canonical copy (1–2d,
   precondition for everything else).
2. `aix_core → rouvr_core` with sys.modules alias + codemod (~426 files).
3. `core/backend/aix_model_gateway_policies_service.py` and siblings
   grouped by family + codemod.
4. `backend.hyeve_aix_service` reference codemod to `backend.rouvr_service`;
   roll env names forward with dual-read.
5. `core/backend/tests/...aix...` renames paired with steps 2–4.
6. Dead-stack + monorepo-only copy deletion (reserved; needs Owner call):
   `backend/observability`, `backend/safety`, `backend/services/aix_task_execution`.
7. Optional polish only after: route prefixes, ENV names, tests dir,
   VS Code extension ids (kept as aliases — external contract).

Per-step green gates: full-suite failure count flat-or-better,
`make test-public` green, hygiene clean. Unfreezing needs an Owner call.
