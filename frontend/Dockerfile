# path: frontend/Dockerfile
# The frontend image: Next.js built once, then served with `next start` on 3000.
# Built by CI (GitHub Actions) for amd64 and arm64 and pushed to ECR; servers only pull it.
# One image serves staging and production: both build arguments below are the same for both.
#
# BACKEND_URL is read by next.config.ts when the app is built: Next.js writes
# the /api rewrite into the build, so it is a build argument, not only a
# run-time variable. docker-compose.yml passes http://api:8000.
#
# Versions are pinned: Node 24 LTS (#477, 3 Nov 2026), the same as CI's
# NODE_VERSION. Node 20's support ended in April 2026 and Node 22's ends in
# April 2027.

# ---- Libraries, from the lock file only ----
FROM node:24.22.2-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- Build ----
FROM node:24.22.2-slim AS build
WORKDIR /app
ARG BACKEND_URL=http://api:8000
# Google Analytics (#457 phase 4) is not baked in: the app reads GA_ID when it runs, from
# the container's environment (docker-compose.yml), so one image serves every server.
ENV BACKEND_URL=${BACKEND_URL} \
  NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules node_modules
COPY . .
RUN npm run build

# ---- Run, as the image's non-root "node" user ----
FROM node:24.22.2-slim
WORKDIR /app
ARG BACKEND_URL=http://api:8000
ENV NODE_ENV=production \
  NEXT_TELEMETRY_DISABLED=1 \
  BACKEND_URL=${BACKEND_URL}
# The whole built app: .next, node_modules, next.config.ts (read again by
# `next start`) and public/ if one is added later.
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3000
# Idle connections are kept 75 s, longer than Caddy keeps one (transport keepalive 60s in
# infra/caddy/Caddyfile), so Caddy never reuses a connection Next.js just closed (a 502).
# Node's own default is 5 s.
CMD ["node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000", "--keepAliveTimeout", "75000"]
