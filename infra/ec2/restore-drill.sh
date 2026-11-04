#!/usr/bin/env bash
# The monthly restore drill (#476, 3 Nov 2026): one run of the off-server
# backup, restored into a throwaway database and file store, then checked.
#
#   infra/ec2/restore-drill.sh <bucket> <run> <private-key.pem>
#
# <run> is one of the bucket's folders, YYYYMMDDTHHMMSSZ (pgdump.sh).
# Run it from a checkout on a laptop or a throwaway machine, never on the
# production server: it needs docker, openssl, the aws CLI signed in with the
# restore role (docs/running-locally.md, "Backups off the server"), and the
# kaagaz-backend:local image (`docker compose build migrate` makes it).
#   1. Downloads <run>/done, which lists each part's object, size and sha256.
#      For each part it opens the sealed key with the private key (openssl
#      asks for its passphrase), then streams the part down, decrypting as it
#      reads (openssl enc, so memory stays small at any size), and checks its
#      size and sha256 against done. The plain copies go into a private
#      temporary folder, removed at the end.
#   2. Starts pgvector/pgvector:0.8.7-pg17 and chrislusf/seaweedfs:4.48, the
#      versions docker-compose.yml runs, on their own network and no host port.
#   3. Restores the logins (roles.sql), then the database (pg_restore --create,
#      as pgdump.sh's dump keeps the owner and the grants), then every file
#      (kaagaz.cli restore-files, into the empty bucket).
#   4. Takes the first document original in the files, and checks, as the app
#      role with that customer set (row level security on), that its document
#      row exists, then that the original and the first page image it lists
#      are in the restored store.
# Prints ids and counts only, and ends "DRILL PASS" or exits non-zero. The
# throwaway containers, network and files are removed on every exit.
set -euo pipefail

DB_IMAGE=pgvector/pgvector:0.8.7-pg17
FILES_IMAGE=chrislusf/seaweedfs:4.48
BACKEND_IMAGE=kaagaz-backend:local
NETWORK=kaagaz-drill
DB=kaagaz-drill-db
FILES=kaagaz-drill-files
# The laptop bucket and admin key of .env.example and infra/seaweedfs/s3.json.
DRILL_BUCKET=kaagaz-local
ADMIN_KEY=kaagaz-local-admin
ADMIN_SECRET=kaagaz-local-admin-secret-laptop-only

if [[ $# -ne 3 ]]; then
  echo "usage: restore-drill.sh <bucket> <run> <private-key.pem>" >&2
  exit 2
fi
bucket="$1"
run="$2"
key="$3"
if [[ ! "$run" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "the run is a folder of the bucket, YYYYMMDDTHHMMSSZ" >&2
  exit 2
fi
cd "$(dirname "$0")/../.."

work="$(mktemp -d)"
chmod 700 "$work"
cleanup() {
  docker rm -f "$DB" "$FILES" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# 1. Only a finished run: done goes up last. Each object is the one done names.
aws s3 cp --quiet "s3://$bucket/$run/done" "$work/done"
for part in kaagaz.dump roles.sql files.tar; do
  read -r object size sha <<<"$(awk -v want="$part.enc" '{ name = $1; sub(/.*\//, "", name) }
    name == want { print; exit }' "$work/done")"
  if [[ -z "$sha" ]]; then
    echo "DRILL FAIL: done lists no $part" >&2
    exit 1
  fi
  aws s3 cp --quiet "s3://$bucket/${object%.enc}.key.cms" "$work/$part.key.cms"
  sealed="$(openssl cms -decrypt -binary -inform DER -inkey "$key" -in "$work/$part.key.cms")"
  read -r secret iv <<<"$sealed"
  # The sha256 of what comes down, read beside the decryption through a pipe.
  rm -f "$work/stream"
  mkfifo "$work/stream"
  openssl dgst -sha256 -r <"$work/stream" >"$work/$part.sha256" &
  hasher=$!
  aws s3 cp --quiet "s3://$bucket/$object" - | tee "$work/stream" |
    openssl enc -d -aes-256-ctr -K "$secret" -iv "$iv" -out "$work/$part"
  wait "$hasher"
  read -r got _ <"$work/$part.sha256"
  # aes-256-ctr adds nothing, so the plain copy is as long as the object.
  if [[ "$got" != "$sha" || "$(wc -c <"$work/$part" | tr -d ' ')" != "$size" ]]; then
    echo "DRILL FAIL: $object is not the size and sha256 done lists" >&2
    exit 1
  fi
done
echo "downloaded, decrypted and checked backup $run"

# 2. The throwaway database and file store. Its superuser has the server's
# superuser's name (roles.sql's first SUPERUSER), so the grants that one made
# restore as made. No database of that name yet: pg_restore --create makes it.
superuser="$(awk '$1 == "ALTER" && $2 == "ROLE" && $4 == "WITH" && $5 == "SUPERUSER" { print $3; exit }' \
  "$work/roles.sql")"
superuser="${superuser:-kaagaz}"
docker network create "$NETWORK" >/dev/null
docker run -d --name "$DB" --network "$NETWORK" -e POSTGRES_USER="$superuser" -e POSTGRES_DB=postgres \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 24)" "$DB_IMAGE" >/dev/null
docker run -d --name "$FILES" --network "$NETWORK" \
  -v "$PWD/infra/seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro" "$FILES_IMAGE" \
  server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json >/dev/null
ready=false
for _ in $(seq 60); do
  if docker exec "$DB" pg_isready -q -U "$superuser" -d postgres >/dev/null 2>&1 &&
    docker exec "$FILES" wget -q -O /dev/null http://127.0.0.1:8333/healthz >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  echo "DRILL FAIL: the throwaway database or file store did not start in 2 minutes" >&2
  exit 1
fi

# 3. Logins first, so the restored tables keep their owner and grants. Any
# error stops it; the superuser's own CREATE ROLE, already there, is left out.
awk -v own="CREATE ROLE $superuser;" '$0 != own' "$work/roles.sql" |
  docker exec -i "$DB" psql -q -v ON_ERROR_STOP=1 -U "$superuser" -d postgres >/dev/null
docker cp "$work/kaagaz.dump" "$DB:/tmp/kaagaz.dump" >/dev/null
db_name="$(docker exec "$DB" pg_restore -l /tmp/kaagaz.dump | sed -n 's/^;[[:space:]]*dbname: //p')"
docker exec "$DB" pg_restore -U "$superuser" -d postgres --create --exit-on-error /tmp/kaagaz.dump
docker run --rm -i --network "$NETWORK" --env-file .env.example \
  -e SESSION_SECRET="$(openssl rand -hex 32)" -e TOTP_ENCRYPTION_KEY="$(openssl rand -hex 32)" \
  -e STORAGE_ENDPOINT_URL="http://$FILES:8333" -e STORAGE_BUCKET="$DRILL_BUCKET" \
  -e STORAGE_ACCESS_KEY="$ADMIN_KEY" -e STORAGE_SECRET_KEY="$ADMIN_SECRET" \
  "$BACKEND_IMAGE" python -m kaagaz.cli restore-files <"$work/files.tar"

# 4. One document, through the customer filter, and its files.
original="$(tar -tf "$work/files.tar" |
  awk '/^customers\/[0-9a-f-]+\/documents\/[0-9a-f-]+\/original$/ && !found { found = $0 } END { print found }')"
if [[ -z "$original" ]]; then
  echo "DRILL FAIL: the files hold no document original" >&2
  exit 1
fi
IFS=/ read -r _ customer _ document _ <<<"$original"
found="$(docker exec -i "$DB" psql -q -tA -U "$superuser" -d "$db_name" -v ON_ERROR_STOP=1 \
  -v customer="$customer" -v document="$document" <<'SQL'
BEGIN;
SET LOCAL ROLE kaagaz_app;
SELECT set_config('kaagaz.customer_id', :'customer', true) \g /dev/null
SELECT count(DISTINCT d.id) || ' ' || coalesce(min(p.page_no)::text, '-')
FROM documents d LEFT JOIN pages p ON p.document_id = d.id AND p.has_image
WHERE d.id = :'document';
COMMIT;
SQL
)"
read -r rows page <<<"$found"
if [[ "$rows" != 1 ]]; then
  echo "DRILL FAIL: document $document has no row in the restored database" >&2
  exit 1
fi
stored() {
  docker exec "$FILES" wget -q -O /dev/null "http://127.0.0.1:8888/buckets/$DRILL_BUCKET/$1"
}
if ! stored "$original"; then
  echo "DRILL FAIL: the original of document $document is not in the restored files" >&2
  exit 1
fi
echo "document $document of customer $customer: row and original found"
if [[ "$page" != - ]]; then
  if ! stored "customers/$customer/documents/$document/pages/$page.webp"; then
    echo "DRILL FAIL: page $page image of document $document is not in the restored files" >&2
    exit 1
  fi
  echo "page $page image found"
fi
echo "DRILL PASS $run"
