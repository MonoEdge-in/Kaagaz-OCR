# The frontend image: Next.js built once, then served with `next start` on 3000.
#
# BACKEND_URL is read by next.config.ts when the app is built: Next.js writes
# the /api rewrite into the build, so it is a build argument, not only a
# run-time variable. docker-compose.yml passes http://api:8000.
#
# Versions are pinned; CI fails on :latest. PROVISIONAL: Node 20 reached its
# end of life in April 2026 (20.20.2 is its last release); moving to Node 22
# or 24 is Vrushit's call.

# ---- Libraries, from the lock file only ----
FROM node:20.20.2-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- Build ----
FROM node:20.20.2-slim AS build
WORKDIR /app
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=${BACKEND_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules node_modules
COPY . .
RUN npm run build

# ---- Run, as the image's non-root "node" user ----
FROM node:20.20.2-slim
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
CMD ["node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000"]
