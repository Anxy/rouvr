# A2 gates: stage R10 + AIX back-port

R10 rehearses on the local ledger. Promoting the same script to staging
(`WORKHUB_BACKEND=stage`) needs these, in order. **Resolution attempts were
made from this host on 29 Oct 2026 and are impossible with the available
identities** — the unblocking actions below need a human who administers
tenant `hyeve-lane-ops`. Exact requests are pre-built so the human step is
copy-paste; everything after it is agent work again.

## Why it's blocked (probed, not assumed)

| Probe | Result |
|---|---|
| `POST attempts` with Rouvr service credential | 403 `attempt_not_permitted` (needs scope `workhub:attempt:open`) |
| Auth admin surface (`auth.betademo.net` has `admin/assign-permission`, `service-principals`, `service-permission-grants`) | reachable, but… |
| Human login (tenant `vmg`) → admin reads for `hyeve-lane-ops` | 403 `SYS.FORBIDDEN` "Authenticated tenant mismatch" |
| Same human, login in tenant `hyeve-lane-ops` | 401 (user doesn't exist there) |
| Rouvr service token on auth admin endpoints | 401 (service tokens not accepted) |

Net: grants are same-tenant operations. We hold no `hyeve-lane-ops` human/admin
identity, so neither gate can be cleared from this host.

## Human step 1 — attempt-open scope grant (≈2 min, hyeve-lane-ops admin)

As a `hyeve-lane-ops` admin, against `https://auth.betademo.net` (tenant
header `X-Hyeve-Tenant-Id: hyeve-lane-ops`):

```http
POST /v1/auth/service-permission-grants
{"principal_id": "<rouvr service principal id>",
 "target_service_key": "workhub", "scope": "workhub:attempt:open"}
```

(If your admin UI prefers `POST /v1/auth/admin/assign-permission` with
`{"permission_key": "workhub:attempt:open", ...}`, that lands equivalently.)
Verify: a freshly minted Rouvr service token's `scopes` claim includes
`workhub:attempt:open`, and `POST /api/v1/projects/{project}/attempts`
returns 2xx instead of 403.

## Human step 2 — verifier credential (≈5 min, same admin)

1. `POST /v1/auth/service-principals`
   `{"service_key": "rouvr-verifier", "display_name": "Rouvr Verifier"}`
2. `POST /v1/auth/service-principals/{id}/credentials`
   `{"purpose": "rouvr verifier persona"}` → store the secret in a 0600 file
   on the Rouvr host (never in git/chat).
3. Point the AIX mapping's `verifier-1` persona at that file. The Rouvr-side
   SELF_REVIEW check then separates real principals (builder sub ≠ verifier sub).

## Agent steps after the human steps (no further gates)

3. **Stage R10 green.** `WORKHUB_BACKEND=stage bash scripts/r10_rehearsal.sh`
   with a scratch staging task + both credentials. Asserts: done + 1 PR +
   activity retry on attempt 1. (S008 closeout.)
4. **Back-port (S009).** Bump hyeve-lane to the Rouvr modules (or vendor at a
   tag), retire the TaskLoop direct launcher, canary untouched until (3) passes.

Non-gates (already done): stage route/body map (`docs/workhub-fixes.md`),
capabilities 403 documented (role gap, non-blocking).
