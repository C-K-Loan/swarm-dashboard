# Multi-stage build for CCT Fleet Compressor
# Stage 1: build the Astro dashboard
FROM node:22-alpine AS ui-builder
WORKDIR /ui
COPY ui/package.json ./
RUN npm install --silent
COPY ui/ ./
RUN npm run build

# Stage 2: python runtime for classifier + static server for dashboard
FROM python:3.11-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf [REDACTED_PATH]*

WORKDIR /app
# Classifier scripts
COPY scripts/ ./scripts/
COPY other-project/ ./other-project/
# Built dashboard
COPY --from=ui-builder /ui/dist /app/dashboard

# Simple python http server for the static dashboard (replace with Caddy if Nebius Serverless prefers)
RUN pip install --no-cache-dir pyyaml

ENV PORT=4321
EXPOSE 4321

# Entry: serve the static dashboard; classifier runs on-demand via scripts/
CMD ["python3", "-m", "http.server", "4321", "--directory", "/app/dashboard"]
