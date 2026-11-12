#!/usr/bin/env bash
# Writes the nightly database dump on the EC2 server (EC2 PRD, WP-E).
#
# kaagaz-pgdump.timer runs this as ubuntu at 20:00 UTC. It writes
# /home/ubuntu/backups/kaagaz-YYYYMMDD.dump (pg_dump custom format, mode 600)
# and then deletes dumps more than 7 days old. The dump goes to a temporary
# file first, so a failed dump never sits under a good dump's name, and old
# dumps are deleted only after a good one.
#
# Then, when .env sets BACKUP_BUCKET and BACKUP_CERT, the copy off the server
# (#476, 3 Nov 2026): this dump, the database logins (pg_dumpall --roles-only
# --no-role-passwords) and every stored file (kaagaz.cli backup-files, a tar
# read through the S3 API), into the bucket under one folder per run,
# <run> = YYYYMMDDTHHMMSSZ, so a second run never writes over the first.
# Each part is encrypted as it streams (openssl enc -aes-256-ctr) with its own
# random 256-bit key, and only that key is sealed with openssl cms to
# BACKUP_CERT, the public certificate of a key kept offline: cms reads its
# whole input into memory to decrypt it, so a large part sealed with it could
# not be restored (400 MB took 1.07 GB). Each part goes up as <part>.enc beside
# <part>.key.cms, through kaagaz.cli backup-upload in the backend image on the
# host network (the server's own AWS role), which prints its size and sha256.
# Nothing encrypted is written to this disk. <run>/done goes last, only when
# every part is in: one line per part (object, size, sha256), then the file
# count. The monitor (infra/aws/monitor) and the restore drill
# (restore-drill.sh) read it. Without the two settings it stops after the
# local dump, as before.
#
# The whole run holds the deploy lock, so no deploy swaps images and the idle
# stop (idle-stop.sh) does not power off half way.
#
# POSTGRES_USER, POSTGRES_DB, BACKUP_BUCKET and BACKUP_CERT are read from .env
# as plain text: the file is never run, and the values are never printed. When
# a POSTGRES_ key is not set, the default is docker-compose.yml's (kaagaz).
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
BACKUP_DIR=/home/ubuntu/backups
KEEP_DAYS=7
COMPOSE=(-f docker-compose.yml -f docker-compose.prod.yml)
LOCK_FILE=/tmp/kaagaz-deploy.lock
LOCK_WAIT_SECONDS=1800

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

# A deploy holds the lock for minutes at most; wait for it.
exec 9>"$LOCK_FILE"
if ! flock -w "$LOCK_WAIT_SECONDS" 9; then
  echo "a deploy held $LOCK_FILE for ${LOCK_WAIT_SECONDS}s; no dump saved" >&2
  exit 1
fi

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

bucket="$(env_value BACKUP_BUCKET)"
cert="$(env_value BACKUP_CERT)"
if [[ -z "$bucket" || -z "$cert" ]]; then
  echo "BACKUP_BUCKET or BACKUP_CERT is not set in .env; nothing copied off the server" >&2
  exit 0
fi

run="$(date -u +%Y%m%dT%H%M%SZ)"

# stdin to the bucket as <run>/$1, never over an object already there; prints
# its size and sha256.
send() {
  docker run --rm -i --network host kaagaz-backend:local \
    python -m kaagaz.cli backup-upload --bucket "$bucket" --key "$run/$1"
}

# stdin to <run>/$1.enc, encrypted as it is read, and its key to
# <run>/$1.key.cms; prints done's line for the part. Each step is checked
# here: set -e does not reach into a $(...).
seal() {
  local secret iv sent
  secret="$(openssl rand -hex 32)" && iv="$(openssl rand -hex 16)" || return 1
  printf '%s %s\n' "$secret" "$iv" |
    openssl cms -encrypt -binary -aes256 -outform DER "$cert" | send "$1.key.cms" >/dev/null || return 1
  sent="$(openssl enc -aes-256-ctr -K "$secret" -iv "$iv" | send "$1.enc")" || return 1
  printf '%s/%s.enc %s\n' "$run" "$1" "$sent"
}

dump_line="$(seal kaagaz.dump <"$dump")"
roles_line="$(docker compose "${COMPOSE[@]}" exec -T postgres \
  pg_dumpall -U "$pg_user" --roles-only --no-role-passwords | seal roles.sql)"
# backup-files prints its file count on stderr (stdout is the tar): kept for
# done, then shown.
counted="$(mktemp "$BACKUP_DIR/.files-count.XXXXXX")"
trap 'rm -f "$counted"' EXIT
# --no-deps: a plain run would start migrate first (deploy.sh, 24 Oct 2026).
if ! files_line="$(docker compose "${COMPOSE[@]}" run --rm -T --no-deps worker \
  python -m kaagaz.cli backup-files 2>"$counted" | seal files.tar)"; then
  cat "$counted" >&2
  echo "the stored files were not copied; no done written" >&2
  exit 1
fi
cat "$counted" >&2
files="$(sed -n 's/^backup-files: \([0-9][0-9]*\) files$/\1/p' "$counted")"
if [[ -z "$files" ]]; then
  echo "backup-files printed no file count; no done written" >&2
  exit 1
fi
printf '%s\n' "$dump_line" "$roles_line" "$files_line" "files $files" | send "done" >/dev/null
echo "copied backup $run off the server ($files files)"
