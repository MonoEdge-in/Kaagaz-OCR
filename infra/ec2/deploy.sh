#!/usr/bin/env bash
# Deploys the production branch on the EC2 server (EC2 PRD, WP-E).
#
# kaagaz-deploy.timer runs this as ubuntu, 2 minutes after boot and then every
# 5 minutes. When origin/production has a commit that is not deployed yet, it
# moves the checkout to it and rebuilds; otherwise it only starts anything that
# is down. The commit is recorded only after a good build, so a failed build is
# tried again on the next run. Each run prints one line: the commit, then
# deployed, unchanged or failed.
#
# Docker's build cache is pruned after each good deploy, keeping what the
# current images use and anything used in the last 7 days, and also before a
# build when / is low on space (a full disk once cut a pip download short).
# Volumes are never pruned. PROVISIONAL: 7 days and 20 GB, watch on the g4dn.
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
DEPLOYED_FILE=/home/ubuntu/.kaagaz-deployed
LOCK_FILE=/tmp/kaagaz-deploy.lock
COMPOSE=(-f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.prod.yml)
PROFILES=(--profile ollama --profile paddle)
BUILD_CACHE_KEEP=168h
MIN_FREE_GB=20

# Held for the whole run. If another deploy still holds it, leave quietly.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

cd "$KAAGAZ_DIR"

report() {
  printf '%s %s\n' "$1" "$2"
}

# Never fatal: a failed prune leaves the deploy as it was.
prune_build_cache() {
  if ! docker builder prune -f --filter "until=$BUILD_CACHE_KEEP" >/dev/null; then
    echo "docker builder prune failed; carrying on" >&2
  fi
}

deployed=""
if [[ -f "$DEPLOYED_FILE" ]]; then
  read -r deployed <"$DEPLOYED_FILE" || true
fi

if ! git fetch --quiet origin production; then
  report "${deployed:-none}" failed
  exit 1
fi
target="$(git rev-parse origin/production)"

if [[ "$target" != "$deployed" ]]; then
  # git replaces changed files with new ones, so this running copy of the
  # script is not changed under bash's feet.
  if ! git reset --quiet --hard origin/production; then
    report "$target" failed
    exit 1
  fi
  free_gb="$(df --output=avail -BG / | tail -n 1 | tr -dc '0-9' || true)"
  if [[ -n "$free_gb" ]] && ((free_gb < MIN_FREE_GB)); then
    echo "low disk: $free_gb GB free; the OCR build needs about $MIN_FREE_GB GB" >&2
    prune_build_cache
  fi
  if ! docker compose "${COMPOSE[@]}" "${PROFILES[@]}" up -d --build --remove-orphans; then
    report "$target" failed
    exit 1
  fi
  printf '%s\n' "$target" >"$DEPLOYED_FILE.tmp"
  mv -f "$DEPLOYED_FILE.tmp" "$DEPLOYED_FILE"
  if ! docker image prune -f >/dev/null; then
    echo "docker image prune failed; the deploy itself is done" >&2
  fi
  prune_build_cache
  report "$target" deployed
  exit 0
fi

if ! docker compose "${COMPOSE[@]}" "${PROFILES[@]}" up -d; then
  report "$target" failed
  exit 1
fi
report "$target" unchanged
