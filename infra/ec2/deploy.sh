#!/usr/bin/env bash
# Deploys the production branch on the EC2 server (EC2 PRD, WP-E; #246).
#
# kaagaz-deploy.timer runs this as ubuntu, 2 minutes after boot and then every
# 5 minutes. When origin/production has a commit that is not deployed yet:
#   1. It moves the checkout to it and builds kaagaz-backend:<commit> and
#      kaagaz-frontend:<commit>. The running containers keep serving.
#   2. It runs the migrations in a throwaway container from the new backend
#      image. The running containers still keep serving.
#   3. Only then it points kaagaz-backend:local and kaagaz-frontend:local (the
#      images compose runs) at the new images, starts them and waits for api
#      to be healthy.
#   4. If api is not healthy in time, it points :local back at the previous
#      images and starts those again.
# A failed build or migration changes no container and is tried again on the
# next run. A release that was rolled back is held: not tried again, while the
# next commit on production is. Otherwise each run puts :local back on the
# live images recorded in IMAGES_FILE (undoing a hand `dc build` or a rollback
# cut short), starts anything that is down, and hands Caddy the checkout's
# Caddyfile if it changed (CADDY_FILE). Each run prints one line: the
# commit, then deployed, unchanged, held, failed or "rolled back to <commit>".
#
# `deploy.sh --rollback` puts the previous images back (run it again to go
# forward): docs/running-locally.md, "Deploys and rollback".
#
# ASSUMPTION: a rollback never runs a downgrade migration, so the older code
# runs on the newer schema. That is safe only while migrations only add
# (ADR 0052, M1); nothing checks it yet. A rollback also runs with the
# checkout's compose files and Caddyfile, those of the newest commit, so every
# release must keep them working for the release before it.
#
# The live and previous images are kept; older commit images are removed after
# a good deploy. Docker's build cache is pruned after each good deploy, keeping
# what the current images use and anything used in the last 7 days, and also
# before a build when / is low on space (a full disk once cut a pip download
# short). Volumes are never pruned. PROVISIONAL: 7 days and 20 GB, watch on the
# server.
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
# The commit whose images are live, written only once api is healthy on them.
DEPLOYED_FILE=/home/ubuntu/.kaagaz-deployed
# Two lines: the commit whose images are live, then the one to roll back to.
# This is what :local must point at.
IMAGES_FILE=/home/ubuntu/.kaagaz-images
# A production commit that was rolled back, so the timer does not deploy it again.
ROLLED_BACK_FILE=/home/ubuntu/.kaagaz-rolled-back
# A copy of the Caddyfile Caddy last loaded from this script (#246 D8). Caddy's
# container mounts infra/caddy/Caddyfile, but a checkout replaces a changed file
# with a new one, which a running container never sees.
CADDY_FILE=/home/ubuntu/.kaagaz-caddyfile
LOCK_FILE=/tmp/kaagaz-deploy.lock
COMPOSE=(-f docker-compose.yml -f docker-compose.prod.yml)
# Every service but migrate. They are always started with --no-deps, so no
# `up -d` runs migrate through depends_on: an older image's migrate fails on a
# newer database and leaves the app stopped (24 Oct 2026). Only step 2 runs it.
INFRA=(postgres seaweedfs elasticmq clamav caddy)
APPS=(api worker frontend)
BUILD_CACHE_KEEP=168h
MIN_FREE_GB=20
# api's healthcheck (docker-compose.yml) gives up after about 140 s.
HEALTH_TIMEOUT_SECONDS=180

rollback=false
case "${1:-}" in
  "") ;;
  --rollback) rollback=true ;;
  *)
    echo "usage: deploy.sh [--rollback]" >&2
    exit 2
    ;;
esac

# Held for the whole run. If another deploy still holds it, leave quietly.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  if [[ "$rollback" == true ]]; then
    echo "a deploy is running; try --rollback again in a few minutes" >&2
    exit 1
  fi
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

# Gives both app images tagged $1 the tag $2 as well.
tag_images() {
  docker tag "kaagaz-backend:$1" "kaagaz-backend:$2" && docker tag "kaagaz-frontend:$1" "kaagaz-frontend:$2"
}

# Records commit $1 as live and $2 as the one to roll back to. Called only once
# api is healthy on $1.
save_state() {
  printf '%s\n%s\n' "$1" "$2" >"$IMAGES_FILE.tmp"
  mv -f "$IMAGES_FILE.tmp" "$IMAGES_FILE"
  printf '%s\n' "$1" >"$DEPLOYED_FILE.tmp"
  mv -f "$DEPLOYED_FILE.tmp" "$DEPLOYED_FILE"
}

api_healthy() {
  local id
  id="$(docker compose "${COMPOSE[@]}" ps -q api)" || return 1
  [[ -n "$id" && "$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null)" == healthy ]]
}

wait_for_api() {
  local waited=0
  until api_healthy; do
    if ((waited >= HEALTH_TIMEOUT_SECONDS)); then
      return 1
    fi
    sleep 5
    waited=$((waited + 5))
  done
}

# Starts the app on the images of commit $1 again.
roll_back_to() {
  tag_images "$1" local && docker compose "${COMPOSE[@]}" up -d --no-deps "${APPS[@]}" && wait_for_api
}

# When the checkout's Caddyfile differs from CADDY_FILE, hands it to Caddy on
# stdin and reloads it: open connections finish, and a file Caddy refuses leaves
# its running config in place. Recorded only once loaded, so a failed reload is
# tried again on the next run. Never fatal.
reload_caddy() {
  if cmp -s infra/caddy/Caddyfile "$CADDY_FILE"; then
    return 0
  fi
  if ! docker compose "${COMPOSE[@]}" exec -T caddy \
    sh -c 'cat >/tmp/Caddyfile && caddy reload --config /tmp/Caddyfile --adapter caddyfile' \
    <infra/caddy/Caddyfile >/dev/null; then
    echo "caddy reload failed; Caddy keeps its previous config" >&2
    return 0
  fi
  cp -f infra/caddy/Caddyfile "$CADDY_FILE" || echo "could not record $CADDY_FILE; caddy reloads again next run" >&2
  echo "caddy reloaded the changed Caddyfile" >&2
}

# Removes the commit images other than the live one ($1) and the previous one
# ($2), then untagged leftovers. Never fatal.
prune_images() {
  local repo tag
  for repo in kaagaz-backend kaagaz-frontend; do
    while read -r tag; do
      if [[ "$tag" != local && "$tag" != "$1" && "$tag" != "$2" ]]; then
        docker image rm "$repo:$tag" >/dev/null || echo "could not remove $repo:$tag; carrying on" >&2
      fi
    done < <(docker image ls --format '{{.Tag}}' "$repo")
  done
  if ! docker image prune -f >/dev/null; then
    echo "docker image prune failed; the deploy itself is done" >&2
  fi
}

current=""
previous=""
if [[ -f "$IMAGES_FILE" ]]; then
  { read -r current && read -r previous; } <"$IMAGES_FILE" || true
fi

if [[ "$rollback" == true ]]; then
  if [[ -z "$previous" ]]; then
    echo "no previous images to roll back to (see $IMAGES_FILE)" >&2
    exit 1
  fi
  if ! roll_back_to "$previous"; then
    echo "api is not healthy on $previous within ${HEALTH_TIMEOUT_SECONDS}s; see docker compose ps -a" >&2
    report "$current" failed
    exit 1
  fi
  # Hold the production commit the checkout is on, so the timer leaves it.
  git rev-parse HEAD >"$ROLLED_BACK_FILE"
  save_state "$previous" "$current"
  report "$current" "rolled back to $previous"
  exit 0
fi

deployed=""
if [[ -f "$DEPLOYED_FILE" ]]; then
  read -r deployed <"$DEPLOYED_FILE" || true
fi
rolled_back=""
if [[ -f "$ROLLED_BACK_FILE" ]]; then
  read -r rolled_back <"$ROLLED_BACK_FILE" || true
fi

if ! git fetch --quiet origin production; then
  report "${deployed:-none}" failed
  exit 1
fi
target="$(git rev-parse origin/production)"

if [[ "$target" != "$deployed" && "$target" != "$rolled_back" ]]; then
  # git replaces changed files with new ones, so this running copy of the
  # script is not changed under bash's feet.
  if ! git reset --quiet --hard origin/production; then
    report "$target" failed
    exit 1
  fi
  free_gb="$(df --output=avail -BG / | tail -n 1 | tr -dc '0-9' || true)"
  if [[ -n "$free_gb" ]] && ((free_gb < MIN_FREE_GB)); then
    echo "low disk: $free_gb GB free; a build needs about $MIN_FREE_GB GB" >&2
    prune_build_cache
  fi
  # The first deploy with this script: if api is healthy, name the running
  # images after the deployed commit, so there is something to roll back to.
  if [[ -z "$current" ]]; then
    if [[ -n "$deployed" ]] && api_healthy && tag_images local "$deployed"; then
      current="$deployed"
      save_state "$current" ""
    else
      echo "no previous release to roll back to: no $DEPLOYED_FILE, or api is not healthy" >&2
    fi
  fi
  # 1. Build the new images beside the running ones.
  if ! KAAGAZ_TAG="$target" docker compose "${COMPOSE[@]}" build; then
    report "$target" failed
    exit 1
  fi
  # 2. Migrate first, from the new image, while the old containers serve.
  if ! KAAGAZ_TAG="$target" docker compose "${COMPOSE[@]}" run --rm -T migrate; then
    echo "migrate failed; site still on ${current:-the running images}" >&2
    report "$target" failed
    exit 1
  fi
  # 3. Switch: compose runs :local, now the new images, then the health check.
  # No --remove-orphans: a service the release before needs is never removed.
  if tag_images "$target" local && docker compose "${COMPOSE[@]}" up -d --no-deps "${INFRA[@]}" "${APPS[@]}" &&
    wait_for_api; then
    if [[ "$current" != "$target" ]]; then
      previous="$current"
    fi
    save_state "$target" "$previous"
    prune_images "$target" "$previous"
    prune_build_cache
    report "$target" deployed
    exit 0
  fi
  # 4. Not healthy in time: hold the commit first, so even if this run is cut
  # short the next one puts :local back on the live images, then roll back.
  echo "api is not healthy on $target within ${HEALTH_TIMEOUT_SECONDS}s" >&2
  printf '%s\n' "$target" >"$ROLLED_BACK_FILE"
  if [[ -n "$current" ]] && roll_back_to "$current"; then
    report "$target" "rolled back to $current"
    exit 1
  fi
  echo "no healthy previous images to roll back to; see docker compose ps -a" >&2
  report "$target" failed
  exit 1
fi

# :local follows the recorded live images, then anything down is started.
if [[ -n "$current" ]] && ! tag_images "$current" local; then
  report "$target" failed
  exit 1
fi
if ! docker compose "${COMPOSE[@]}" up -d --no-deps "${INFRA[@]}" "${APPS[@]}"; then
  report "$target" failed
  exit 1
fi
reload_caddy
if [[ "$target" == "$rolled_back" && "$target" != "$deployed" ]]; then
  report "$target" held
else
  report "$target" unchanged
fi
