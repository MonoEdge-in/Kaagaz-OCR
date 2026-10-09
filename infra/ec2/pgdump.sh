#!/usr/bin/env bash
# Writes the nightly database dump on the EC2 server (EC2 PRD, WP-E).
#
# kaagaz-pgdump.timer runs this as ubuntu at 20:00 UTC. It writes
# /home/ubuntu/backups/kaagaz-YYYYMMDD.dump (pg_dump custom format, mode 600)
# and then deletes dumps more than 7 days old. The dump goes to a temporary
# file first, so a failed dump never sits under a good dump's name, and old
# dumps are deleted only after a good one.
#
# POSTGRES_USER and POSTGRES_DB are read from .env as plain text: the file is
# never run, and the values are never printed. When a key is not set, the
# default is docker-compose.yml's (kaagaz).
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
BACKUP_DIR=/home/ubuntu/backups
KEEP_DAYS=7
COMPOSE=(-f docker-compose.yml -f docker-compose.prod.yml)

# One key's value from .env, as docker compose reads it: the last line that
# sets it wins, one pair of surrounding quotes is removed, and an unquoted
# value ends at " #".
env_value() {
  local key="$1" line value="" pattern
  pattern="^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=[[:space:]]*(.*)$"
  if [[ -f .env ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      if [[ "$line" =~ $pattern ]]; then
        value="${BASH_REMATCH[2]}"
      fi
    done <.env
  fi
  if [[ "$value" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ || "$value" =~ ^\'(.*)\'[[:space:]]*(#.*)?$ ]]; then
    value="${BASH_REMATCH[1]}"
  else
    value="${value%%[[:space:]]#*}"
    value="${value%"${value##*[![:space:]]}"}"
  fi
  printf '%s' "$value"
}

cd "$KAAGAZ_DIR"
pg_user="$(env_value POSTGRES_USER)"
pg_db="$(env_value POSTGRES_DB)"
pg_user="${pg_user:-kaagaz}"
pg_db="${pg_db:-kaagaz}"

umask 077
mkdir -p "$BACKUP_DIR"
day="$(date -u +%Y%m%d)"
dump="$BACKUP_DIR/kaagaz-$day.dump"
partial="$(mktemp "$BACKUP_DIR/.partial-kaagaz-$day.XXXXXX")"
trap 'rm -f "$partial"' EXIT

# A dump missed while the server was stopped runs right after boot, before
# postgres may be up: wait for it for up to 10 minutes.
for _ in $(seq 60); do
  if docker compose "${COMPOSE[@]}" exec -T postgres pg_isready -q >/dev/null 2>&1; then
    break
  fi
  sleep 10
done

docker compose "${COMPOSE[@]}" exec -T postgres \
  pg_dump -U "$pg_user" -Fc "$pg_db" >"$partial"
if [[ ! -s "$partial" ]]; then
  echo "pg_dump wrote nothing; no dump saved" >&2
  exit 1
fi
chmod 600 "$partial"
mv -f "$partial" "$dump"
trap - EXIT

# Keep today's dump and the 6 before it, by the date in the name.
oldest_kept="$(date -u -d "$((KEEP_DAYS - 1)) days ago" +%Y%m%d)"
for old in "$BACKUP_DIR"/kaagaz-*.dump; do
  name="${old##*/}"
  if [[ "$name" =~ ^kaagaz-([0-9]{8})\.dump$ && "${BASH_REMATCH[1]}" < "$oldest_kept" ]]; then
    rm -f -- "$old"
  fi
done

echo "saved $dump"
