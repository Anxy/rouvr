# Rouvr ledger (WorkHub-lite) + Temporal worker / MCP runtime image.
FROM python:3.12-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && DOCKER_CLI_VERSION=27.3.1 \
    && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m | sed 's/aarch64/aarch64/;s/x86_64/x86_64/')/docker-${DOCKER_CLI_VERSION}.tgz" \
         | tar -xz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && chmod 755 /usr/local/bin/docker \
    && rm -rf /tmp/docker \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Ledger seals and workers import these trees via PYTHONPATH.
COPY ledger/ /app/ledger/
COPY lite/ /app/lite/
COPY rouvr/ /app/rouvr/
COPY personas/ /app/personas/
COPY docker/personas-tenants.yaml /app/personas/tenants.yaml
COPY docker/personas/coder.md /app/personas/coder.md
COPY docker/personas/reviewer.md /app/personas/reviewer.md
COPY docker/demo_runner.py /app/docker/demo_runner.py
# Workers/sealing import `backend.rouvr_models` via PYTHONPATH=.../core
COPY core/backend/__init__.py /app/core/backend/__init__.py
COPY core/backend/rouvr_models/ /app/core/backend/rouvr_models/

ENV PYTHONPATH=/app:/app/core:/app/core/src:/app/core/libs \
    PYTHONUNBUFFERED=1 \
    ROUVR_LEDGER_DATABASE_URL=sqlite:////data/ledger.db \
    HIDDEN_TESTS_ROOT=/data/hidden-tests \
    RECEIPT_SIGNING_KEY_PATH=/data/keys/receipt_ed25519.pem \
    ROUVR_BIND_HOST=127.0.0.1

RUN mkdir -p /data/hidden-tests /data/keys /data/workspaces /data/grants \
        /data/evidence /data/specs \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin rouvr \
    && chown -R rouvr:rouvr /data /app

COPY docker/entrypoint-ledger.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER rouvr
EXPOSE 8221 8231
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ledger"]
