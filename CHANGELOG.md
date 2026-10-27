# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: SemVer.

## [0.1.0] - 2026-10-27
### Added
- First public release, extracted from eBizneeds' private Hyeve/AIX development.
- `core/` runtime and FastAPI service; pluggable model provider (Meta Model API, Ollama, stub).
- `ledger/` WorkHub-lite: tasks, hashed contracts, append-only evidence, idempotent receipts.
- `lane/` generic lane machinery; `workers/`; `cli/`.
- `demo/` morning-report task with failure injection (mid-step kill, silent delivery, revoked key).
- Docs, CI running `make test-public`, community files.

### Known limitations
- Full stack under docker compose and the live Meta Model API path are not yet verified from this repo. See README "Status".
