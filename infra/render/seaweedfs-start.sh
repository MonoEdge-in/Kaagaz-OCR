#!/bin/sh
# Writes the S3 identity from the environment, then starts SeaweedFS through the
# image's own /entrypoint.sh, which hands /data to the seaweed user and drops
# root. The command after "server" is docker-compose.yml's.
set -eu
: "${STORAGE_ACCESS_KEY:?STORAGE_ACCESS_KEY is not set}"
: "${STORAGE_SECRET_KEY:?STORAGE_SECRET_KEY is not set}"
umask 077
printf '{"identities":[{"name":"kaagaz-render","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}\n' \
  "$STORAGE_ACCESS_KEY" "$STORAGE_SECRET_KEY" > /etc/seaweedfs/s3.json
chown seaweed:seaweed /etc/seaweedfs/s3.json
exec /entrypoint.sh server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json
