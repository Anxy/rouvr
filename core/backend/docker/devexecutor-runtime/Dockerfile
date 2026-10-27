FROM python:3.12-slim

ARG PYRIGHT_NPM_VERSION=1.1.408

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc git nodejs npm ca-certificates libpq-dev postgresql-client \
    && npm install -g "pyright@${PYRIGHT_NPM_VERSION}" \
    && pip install --no-cache-dir \
        fastapi==0.115.0 \
        python-multipart==0.0.9 \
        uvicorn==0.30.6 \
        asyncpg==0.29.0 \
        pydantic==2.9.2 \
        pydantic-settings==2.5.2 \
        httpx==0.27.2 \
        litellm==1.52.8 \
        'sqlalchemy[asyncio]==2.0.36' \
        email-validator==2.1.1 \
        mjml==0.11.0 \
        pybars3==0.9.7 \
        asyncio-mqtt==0.16.2 \
        prisma==0.11.0 \
        psycopg2-binary==2.9.9 \
        'redis[hiredis]==5.0.4' \
        aioredis==2.0.1 \
        celery==5.4.0 \
        python-ulid==3.0.0 \
        python-json-logger==2.0.7 \
        orjson==3.10.7 \
        python-dotenv==1.0.1 \
        pyyaml==6.0.3 \
        asyncio-throttle==1.0.2 \
        python-dateutil==2.8.2 \
        pytz==2023.3.post1 \
        jsonschema==4.22.0 \
        bleach==6.1.0 \
        structlog==24.4.0 \
        sentry-sdk==2.13.0 \
        prometheus-client==0.20.0 \
        ulid-py==1.1.0 \
        inflection==0.5.1 \
        libcst==1.4.0 \
        tree-sitter==0.25.2 \
        tree-sitter-python==0.25.0 \
        tenacity==8.5.0 \
        GitPython==3.1.43 \
        pathspec==0.12.1 \
        charset-normalizer==3.4.0 \
        psutil==6.1.0 \
        python-patch==0.0.1 \
        unidiff==0.7.5 \
        pytest==8.3.2 \
        pytest-asyncio==0.23.8 \
        ruff==0.13.2 \
        opentelemetry-distro==0.48b0 \
        opentelemetry-exporter-jaeger==1.21.0 \
        opentelemetry-instrumentation==0.48b0 \
        opentelemetry-instrumentation-fastapi==0.48b0 \
        opentelemetry-sdk==1.27.0 \
        temporalio==1.23.0 \
        langgraph==0.3.34 \
        haystack-ai==2.25.1 \
    && python -m venv /opt/aider-cli \
    && /opt/aider-cli/bin/pip install --no-cache-dir aider-chat==0.86.1 \
    && ln -s /opt/aider-cli/bin/aider /usr/local/bin/aider \
    && rm -rf /var/lib/apt/lists/*

COPY mutate_workspace.py /opt/devexecutor-runtime/mutate_workspace.py

WORKDIR /workspace
