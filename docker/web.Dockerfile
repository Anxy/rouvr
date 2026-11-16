# Rouvr operator workspace (Next.js).
FROM node:22-bookworm-slim AS deps
WORKDIR /repo
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY web/package.json ./web/
RUN pnpm install --frozen-lockfile --filter web...

FROM node:22-bookworm-slim AS builder
WORKDIR /repo
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY --from=deps /repo /repo
COPY web/ ./web/
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter web build

FROM node:22-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    ROUVR_LEDGER_URL=http://ledger:8221

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin rouvr
# Monorepo standalone layout: server.js lives under web/.
COPY --from=builder --chown=rouvr:rouvr /repo/web/.next/standalone ./
COPY --from=builder --chown=rouvr:rouvr /repo/web/.next/static ./web/.next/static
COPY --from=builder --chown=rouvr:rouvr /repo/web/public ./web/public

USER rouvr
EXPOSE 3000
CMD ["node", "web/server.js"]
