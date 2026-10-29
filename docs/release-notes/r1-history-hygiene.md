# R1 history hygiene note (29 Oct 2026)

The public cut keeps full history. One private-tenant line existed and was
removed without a rewrite:

- `1d8e8e4` ("Rouvr public cut", 27 Oct 2026) added to `README.md` an example
  line naming "VMG Hawks PR #163", plus `tenant_id="vmg"` fixtures in
  `core/backend/tests/runtime/objective_cycle/test_p27_p28_p29.py`.
- `1702c6a` ("Docs, community files, CI for public tests", 27 Oct 2026)
  removed the README example line.
- R1 (29 Oct 2026) renamed the fixtures to `tenant-a` / "Tenant A" and added
  the `scripts/hygiene.sh` gate plus this note.

Decision: history is left intact (no force-push, no squash). The design docs
that document this scrub (`docs/rouvr-roadmap.md`, `docs/release-notes/*`)
are allowlisted in `.github/hygiene-allowlist.txt`.

Follow-up (29 Oct 2026): review-001 found the `meta` preset in
`core/backend/rouvr_models/providers.py` still defaulted to the Llama API
endpoint and model — the gate never scanned for Llama strings. Fixed to
env-only (`META_MODEL_API_BASE_URL`, `META_MODEL_NAME`, fail-fast when
unset), widened `scripts/hygiene.sh` with `llama\.com|Maverick`, and added
`.gitleaksignore` for 12 test-placeholder false positives (gitleaks clean
on tree + history).
