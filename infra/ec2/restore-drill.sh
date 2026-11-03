#!/usr/bin/env bash
# The monthly restore drill (#476, 3 Nov 2026): one night's off-server backup,
# restored into a throwaway database and file store, then checked.
#
#   infra/ec2/restore-drill.sh <bucket> <YYYYMMDD> <private-key.pem>
#
# Run it from a checkout on a laptop or a throwaway machine, never on the
# production server: it needs docker, openssl, the aws CLI signed in with the
# restore role (docs/running-locally.md, "Backups off the server"), and the
# kaagaz-backend:local image (`docker compose build migrate` makes it).
#   1. Downloads <day>/done, then the three parts pgdump.sh uploaded, and
#      decrypts each with the private key (openssl asks for its passphrase).
#      The plain copies go into a private temporary folder, removed at the end.
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
  echo "usage: restore-drill.sh <bucket> <YYYYMMDD> <private-key.pem>" >&2
  exit 2
fi
bucket="$1"
day="$2"
key="$3"
if [[ ! "$day" =~ ^[0-9]{8}$ ]]; then
  echo "the day is 8 digits, YYYYMMDD" >&2
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

# 1. Only a finished night: done goes up last.
aws s3 cp --quiet "s3://$bucket/$day/done" "$work/done"
for part in kaagaz.dump roles.sql files.tar; do
  aws s3 cp --quiet "s3://$bucket/$day/$part.cms" - |
    openssl cms -decrypt -binary -inform DER -inkey "$key" -out "$work/$part"
done
echo "downloaded and decrypted the backup of $day"

# 2. The throwaway database and file store.
docker network create "$NETWORK" >/dev/null
docker run -d --name "$DB" --network "$NETWORK" \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 24)" "$DB_IMAGE" >/dev/null
docker run -d --name "$FILES" --network "$NETWORK" \
  -v "$PWD/infra/seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro" "$FILES_IMAGE" \
  server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json >/dev/null
for _ in $(seq 60); do
  if docker exec "$DB" pg_isready -q -U postgres >/dev/null 2>&1 &&
    docker exec "$FILES" wget -q -O /dev/null http://127.0.0.1:8333/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# 3. Logins first, so the restored tables keep their owner and grants. The
# throwaway's own superuser is postgres; a role that already exists is reported
# and skipped.
docker exec -i "$DB" psql -q -U postgres -d postgres <"$work/roles.sql" >/dev/null
docker cp "$work/kaagaz.dump" "$DB:/tmp/kaagaz.dump" >/dev/null
db_name="$(docker exec "$DB" pg_restore -l /tmp/kaagaz.dump | sed -n 's/^;[[:space:]]*dbname: //p')"
docker exec "$DB" pg_restore -U postgres -d postgres --create --exit-on-error /tmp/kaagaz.dump
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
found="$(docker exec -i "$DB" psql -q -tA -U postgres -d "$db_name" -v ON_ERROR_STOP=1 \
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
if [[ "$rows" == 0 ]]; then
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
echo "DRILL PASS $day"
