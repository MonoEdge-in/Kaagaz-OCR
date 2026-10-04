#!/usr/bin/env bash
# Deploys the production branch on the EC2 server (EC2 PRD, WP-E).
#
# kaagaz-deploy.timer runs this as ubuntu, 2 minutes after boot and then every
# 5 minutes. When origin/production has a commit that is not deployed yet, it
# moves the checkout to it and rebuilds; otherwise it only starts anything that
# is down. The commit is recorded only after a good build, so a failed build is
# tried again on the next run. Each run prints one line: the commit, then
# deployed, unchanged or failed.
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
DEPLOYED_FILE=/home/ubuntu/.kaagaz-deployed
LOCK_FILE=/tmp/kaagaz-deploy.lock
COMPOSE=(-f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.prod.yml)
PROFILES=(--profile ollama --profile paddle)

# Held for the whole run. If another deploy still holds it, leave quietly.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

cd "$KAAGAZ_DIR"

report() {
  printf '%s %s\n' "$1" "$2"
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
  if ! docker compose "${COMPOSE[@]}" "${PROFILES[@]}" up -d --build --remove-orphans; then
    report "$target" failed
    exit 1
  fi
  printf '%s\n' "$target" >"$DEPLOYED_FILE.tmp"
  mv -f "$DEPLOYED_FILE.tmp" "$DEPLOYED_FILE"
  if ! docker image prune -f >/dev/null; then
    echo "docker image prune failed; the deploy itself is done" >&2
  fi
  report "$target" deployed
  exit 0
fi

if ! docker compose "${COMPOSE[@]}" "${PROFILES[@]}" up -d; then
  report "$target" failed
  exit 1
fi
report "$target" unchanged
