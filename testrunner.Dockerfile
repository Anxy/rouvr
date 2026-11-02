# R8 clean-room test image: stock python slim + a pinned pytest (offline at run time).
# Build once per host: docker build -f testrunner.Dockerfile -t rouvr-runner:py313-p9 .
#
# FL-A5/A6: the image also carries the repo's test-time dependencies (pinned to
# the versions in .venv) so `repo` and hidden suites that exercise backend code
# can import it. Still offline at run time: everything is baked at build time.
FROM python:3.13-slim
# git: crash-recovery / gitops tests shell out to it (offline at run time).
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists
RUN pip install --no-cache-dir "pytest==9.1.1" \
    "pytest-asyncio==1.4.0" \
    "pytest-timeout==2.4.0" \
    "fastapi==0.162.0" \
    "httpx==0.28.1" \
    "SQLAlchemy==2.0.52" \
    "pydantic==2.14.2" \
    "pydantic-settings==2.15.0" \
    "respx==0.23.1" \
    "Faker==40.44.0" \
    "hypothesis==6.170.14" \
    "rapidfuzz==3.14.6" \
    "python-multipart==0.0.33" \
    "python-ulid==4.0.1" \
    "PyJWT==2.15.1" \
    "jsonschema==4.26.0" \
    "bleach==6.4.0" \
    "prometheus_client==0.26.0" \
    "mcp==2.3.0" \
    "redis==5.0.8" \
    "PyYAML==6.0.3" \
    "cryptography==50.0.2" \
    "asyncpg==0.31.0" \
    "temporalio==1.23.0" \
    "structlog==26.1.0" \
    "libcst==1.8.6"
