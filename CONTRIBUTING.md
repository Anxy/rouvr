# Contributing

Thanks for your interest in Rouvr.

1. Fork and branch from `main`.
2. `cp .env.example .env && make setup`
3. Make your change; add or update tests.
4. `make test-public` must pass (CI runs the same).
5. Open a PR using the template. Keep PRs small and describe how you verified the change.

Rules: no secrets in code, tests or logs; read keys from the environment. Never mark a task done without evidence — the same applies to PRs.
