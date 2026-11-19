#!/usr/bin/env bash
# Deploys one service from an ECR image on this server (CI/CD Phase 3).
#
#   deploy.sh backend|frontend <commit> [--rollback]
#
# GitHub Actions runs it through SSM (.github/workflows/_deploy.yml → the
# Kaagaz-Deploy SSM document), as ubuntu. Nothing is built here, and no migration
# runs: with one central database a schema change is a deliberate, scheduled act
# (`docker compose run --rm migrate`), never a side effect of a deploy.
#
#   1. Room on the disk.
#   2. Config: a deploy checks out <commit>'s compose files and Caddyfile; a rollback
#      keeps the current ones, so every release must keep its compose files working for
#      the release before it. An infra service whose compose config changed is updated
#      now: clamav with docker rollout (the workers keep scanning on the old one until
#      the new one is healthy), caddy recreated (a few seconds without the site; rare).
#      Unchanged ones are left alone.
#   3. Pull <registry>/kaagaz/<service>:<commit>.
#   4. Backend only, from the new image, before anything is switched:
#      `kaagaz schema-check` (read-only, backend/kaagaz/cli.py). It stops on a
#      database BEHIND the image (run the migration first) and, unless --rollback, on
#      one AHEAD of it. A rollback accepts AHEAD: older code on a newer schema, safe
#      only while migrations only add (ADR 0052, M1). Then `kaagaz ensure-bucket`.
#   5. Roll out api or frontend with docker rollout: the old container serves until
#      the new one is healthy; if it never is, the new one is removed and nothing
#      has changed.
#   6. A changed Caddyfile is reloaded, after the switch, so a new route never points
#      at an app that is not there yet. Caddy reloads without dropping connections, and
#      keeps its running config if it refuses the file.
#   7. Record the tag in .env (BACKEND_TAG, FRONTEND_TAG), which every docker compose
#      command on the server reads, so `docker compose ps` and the rest just work.
#   8. Backend only: recreate the workers (each finishes the job in hand first,
#      stop_grace_period 600 s).
#   9. Keep the live image and the newest other one (the quick way back) per
#      service; remove the rest.
#
# A changed deploy.sh takes effect on the next run (git replaces files, so this
# running copy is never edited under bash's feet).
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
ENV_FILE="$KAAGAZ_DIR/.env"
# A copy of the Caddyfile Caddy last loaded (#246 D8): a checkout replaces a changed
# file with a new one, which Caddy's running container never sees.
CADDY_FILE=/home/ubuntu/.kaagaz-caddyfile
LOCK_FILE=/tmp/kaagaz-deploy.lock
AWS_REGION=ap-south-1
# Room for one pulled release (nothing is built here).
MIN_FREE_GB=5
# api's healthcheck: start_period 20 s, then up to 12 tries 10 s apart.
ROLLOUT_TIMEOUT=180
# clamav loads its signatures before it is healthy (start_period 6m in docker-compose.yml).
CLAMAV_ROLLOUT_TIMEOUT=420
COMPOSE=(-f docker-compose.yml -f docker-compose.prod.yml)
# kaagaz schema-check's exit codes (backend/kaagaz/cli.py: SCHEMA_BEHIND, SCHEMA_AHEAD).
SCHEMA_BEHIND=3
SCHEMA_AHEAD=4

log() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
die() {
  log "FAILED: $*" >&2
  exit 1
}
usage() {
  echo "usage: deploy.sh backend|frontend <commit> [--rollback]" >&2
  exit 2
}

service="${1:-}"
tag="${2:-}"
rollback=false
[[ "$service" =~ ^(backend|frontend)$ && "$tag" =~ ^[0-9a-f]{40}$ ]] || usage
case "${3:-}" in
  "") ;;
  --rollback) rollback=true ;;
  *) usage ;;
esac
[[ $# -le 3 ]] || usage

# Held for the whole run. A second deploy waits for it rather than racing it.
exec 9>"$LOCK_FILE"
flock -w 1200 9 || die "another deploy has held $LOCK_FILE for 20 minutes"

cd "$KAAGAZ_DIR"

# A KEY=value line of the server's .env (the last one wins, as in compose). Only lines that
# start with KEY= count; anything else is ignored. Quotes around the value are removed.
# .env is read, never sourced: it is compose's format, not a shell script.
env_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

# Sets KEY=value in .env, replacing the line or adding it, in one atomic move.
set_env_value() {
  local tmp
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  awk -v k="$1" -v v="$2" '
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" >"$tmp"
  chmod --reference="$ENV_FILE" "$tmp"
  mv -f "$tmp" "$ENV_FILE"
}

# Every line of .env must be blank, a comment or KEY=value; anything else (a pasted shell
# prompt, a stray command) breaks every docker compose command, so stop here and say where.
if bad="$(grep -nvE '^[[:space:]]*($|#|[A-Za-z_][A-Za-z0-9_]*=)' "$ENV_FILE")"; then
  die "$ENV_FILE has lines that are not KEY=value (fix or delete them):
$bad"
fi

KAAGAZ_REGISTRY="$(env_value KAAGAZ_REGISTRY)"
[[ -n "$KAAGAZ_REGISTRY" ]] || die "KAAGAZ_REGISTRY is not set in $ENV_FILE"

dc() { docker compose "${COMPOSE[@]}" "$@"; }

# Compose must be able to read .env and both files before anything runs that depends on
# them; otherwise every later step fails with a misleading message (a frontend "not
# healthy" after 0 s). Checked once now, and again after the commit's own compose files
# are checked out (step 2), since those may differ.
check_compose() {
  local errors
  errors="$(dc config --quiet 2>&1)" || die "docker compose cannot read its configuration$1:
$errors"
}
check_compose ""

# A kaagaz CLI command (backend/kaagaz/cli.py) in a throwaway container of the
# backend image being deployed, as the migrate service: the owner's DATABASE_URL (it
# can read alembic_version, and schema-check connects the way a migration would) and
# S3 through the instance role. --no-deps, and its own command replaced, so no
# migration runs.
kaagaz_cli() {
  dc run --rm --no-deps -T migrate \
    python -c 'import sys; from kaagaz.cli import main; sys.exit(main(sys.argv[1:]))' "$@"
}

# Hands Caddy the checkout's Caddyfile when it differs from CADDY_FILE: open
# connections finish, and a file Caddy refuses leaves its running config in place.
# Never fatal; a failed reload is tried again on the next deploy.
reload_caddy() {
  if cmp -s infra/caddy/Caddyfile "$CADDY_FILE"; then
    log "Caddyfile: unchanged, no reload"
    return 0
  fi
  log "Caddyfile: changed, reloading caddy"
  if ! dc exec -T caddy sh -c 'cat >/tmp/Caddyfile && caddy reload --config /tmp/Caddyfile --adapter caddyfile' \
    <infra/caddy/Caddyfile >/dev/null; then
    log "Caddyfile: caddy refused or could not reload it; it keeps its previous config (tried again next deploy)"
    return 0
  fi
  cp -f infra/caddy/Caddyfile "$CADDY_FILE" || log "could not record $CADDY_FILE; caddy reloads again next deploy"
  log "Caddyfile: reloaded"
}

# docker rollout replaces running containers; with none running (the first start,
# or a crash) it has nothing to replace, so start the service plainly and wait for
# its healthcheck instead.
roll_out() {
  local svc=$1 timeout=${2:-$ROLLOUT_TIMEOUT}
  if [[ -n "$(dc ps -q "$svc")" ]]; then
    docker rollout "${COMPOSE[@]}" --timeout "$timeout" "$svc"
  else
    dc up -d --no-deps --wait --wait-timeout "$timeout" "$svc"
  fi
}

# The compose config hash a service would get now, and the one its running container was
# made with (compose labels each container with it). Empty when none runs.
wanted_hash() { dc config --hash "$1" | awk '{print $2}'; }
running_hash() {
  local id
  id="$(dc ps -q "$1" | head -n 1)"
  [[ -n "$id" ]] || return 0
  docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$id"
}

# Updates only the infra services that need it, each the least disruptive way, and logs
# every decision (skipped, started, updated), so a deploy's log shows what it touched.
apply_infra() {
  local svc have
  for svc in "${infra[@]}"; do
    have="$(running_hash "$svc")"
    if [[ -n "$have" && "$have" == "$(wanted_hash "$svc")" ]]; then
      log "$svc: configuration unchanged, skipping"
      continue
    fi
    if [[ -z "$have" ]]; then
      log "$svc: not running, starting it"
    else
      log "$svc: configuration changed, applying update"
    fi
    case "$svc" in
      clamav)
        log "clamav: rolling out; the workers scan on the current instance until the new one is healthy (up to ${CLAMAV_ROLLOUT_TIMEOUT}s)"
        roll_out clamav "$CLAMAV_ROLLOUT_TIMEOUT" ||
          die "clamav: the new instance was not healthy within ${CLAMAV_ROLLOUT_TIMEOUT}s; the current one keeps scanning"
        ;;
      caddy)
        log "caddy: recreating the container; the site is unreachable for a few seconds"
        dc up -d --no-deps caddy || die "caddy: did not start; see docker compose logs caddy"
        ;;
      *)
        log "$svc: recreating the container"
        dc up -d --no-deps "$svc" || die "$svc: did not start; see docker compose logs $svc"
        ;;
    esac
    log "$svc: update completed"
  done
}

# Per service, keeps the live image and the newest other one (docker lists the
# newest first), and removes the rest. Never fatal.
prune_images() {
  local svc live kept image_tag
  for svc in backend frontend; do
    live="$(env_value "${svc^^}_TAG")"
    kept=false
    while read -r image_tag; do
      if [[ "$image_tag" == "<none>" || "$image_tag" == "$live" ]]; then
        continue
      fi
      if [[ "$kept" == false ]]; then
        kept=true
        continue
      fi
      docker image rm "$KAAGAZ_REGISTRY/kaagaz/$svc:$image_tag" >/dev/null 2>&1 ||
        log "could not remove kaagaz/$svc:$image_tag; carrying on"
    done < <(docker image ls --format '{{.Tag}}' "$KAAGAZ_REGISTRY/kaagaz/$svc")
  done
  docker image prune -f >/dev/null || log "docker image prune failed; the deploy itself is done"
}

if [[ "$rollback" == true ]]; then
  log "rolling $service back to $tag (current config kept)"
else
  log "deploying $service $tag"
fi

# ---- 1. Room on the disk ----
free_gb="$(df --output=avail -BG / | tail -n 1 | tr -dc '0-9')"
((free_gb >= MIN_FREE_GB)) || die "only $free_gb GB free on /, need $MIN_FREE_GB GB (try: docker image prune -a, journalctl --vacuum-size=200M)"

# ---- 2. Config (compose files, Caddyfile) from the commit being deployed ----
if [[ "$rollback" == false ]]; then
  git fetch --quiet origin "$tag" || die "git fetch $tag"
  git -c advice.detachedHead=false checkout --quiet --force --detach "$tag" || die "git checkout $tag"
  check_compose " (with $tag's compose files)"
fi
# Every service but the apps (caddy, clamav and whatever else this release's compose
# files run here).
mapfile -t infra < <(dc config --services | grep -vxE 'api|worker|frontend|migrate' || true)
apply_infra

# ---- 3. Pull the image ----
image="$KAAGAZ_REGISTRY/kaagaz/$service:$tag"
aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "$KAAGAZ_REGISTRY" >/dev/null ||
  die "ECR login (does the instance role allow ECR pulls?)"
log "pulling $image"
docker pull --quiet "$image" >/dev/null || die "pull $image (is that commit's image in ECR?)"

# From here compose runs the new tag for this service (a shell variable beats .env).
if [[ "$service" == backend ]]; then
  export BACKEND_TAG="$tag"
else
  export FRONTEND_TAG="$tag"
fi

# ---- 4. Backend: the database must be on the image's migration head ----
if [[ "$service" == backend ]]; then
  rc=0
  kaagaz_cli schema-check || rc=$?
  case "$rc" in
    0) ;;
    "$SCHEMA_AHEAD")
      if [[ "$rollback" == true ]]; then
        log "rollback: older code on a newer schema (migrations only add, ADR 0052 M1)"
      else
        die "the database is ahead of $tag; deploy a newer commit, or use the rollback workflow"
      fi
      ;;
    "$SCHEMA_BEHIND")
      die "run the migration first (BACKEND_TAG=$tag docker compose run --rm migrate, at a safe time, after a dump), then deploy again; nothing was switched"
      ;;
    *) die "schema-check could not answer (exit $rc); nothing was switched" ;;
  esac
  kaagaz_cli ensure-bucket || die "ensure-bucket; nothing was switched"
fi

# ---- 5-8. Switch, Caddyfile, record, workers ----
if [[ "$service" == backend ]]; then
  log "rolling out api"
  roll_out api || die "api was not healthy on $tag within ${ROLLOUT_TIMEOUT}s; the previous api keeps serving"
  reload_caddy
  set_env_value BACKEND_TAG "$tag"
  log "recreating the workers (each finishes its job first, up to 10 minutes)"
  dc up -d --no-deps worker || die "workers did not start on $tag (api is on it); see docker compose ps -a"
else
  log "rolling out frontend"
  roll_out frontend || die "frontend was not healthy on $tag within ${ROLLOUT_TIMEOUT}s; the previous frontend keeps serving"
  reload_caddy
  set_env_value FRONTEND_TAG "$tag"
fi

# ---- 9. Old images ----
prune_images
log "done: $service is on $tag"
