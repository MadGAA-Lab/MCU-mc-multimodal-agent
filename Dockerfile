FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY mc-multimodal-agent/package*.json ./
RUN npm ci

COPY mc-multimodal-agent/tsconfig.json mc-multimodal-agent/README.md mc-multimodal-agent/soul.md ./
COPY mc-multimodal-agent/src ./src
COPY mc-multimodal-agent/data ./data
COPY mc-multimodal-agent/blueprints ./blueprints

RUN npm run build && npm prune --omit=dev

FROM node:22-bookworm-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        dnsutils \
        iproute2 \
        netcat-openbsd \
        openssl \
        procps \
        tini \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    OPENAI_API_MODE=chat \
    OPENAI_STRUCTURED_OUTPUTS=true \
    OPENAI_REQUEST_TIMEOUT_MS=180000 \
    OPENAI_MAX_RETRIES=6 \
    AGENTBEATS_HOST=0.0.0.0 \
    AGENTBEATS_PORT=9009

COPY --from=build /app/package*.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/data ./data
COPY --from=build /app/blueprints ./blueprints
COPY --from=build /app/soul.md /app/README.md ./

EXPOSE 9009

ENTRYPOINT ["tini", "--", "node", "dist/index.js", "agentbeats"]
CMD ["--host", "0.0.0.0", "--port", "9009"]
