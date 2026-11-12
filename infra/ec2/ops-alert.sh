#!/usr/bin/env bash
# Emails one operations alert (stability pass A22, 7 Nov 2026):
#   ops-alert.sh KEY SUBJECT TEXT
#
# kaagaz-alert@.service runs it when the deploy or the nightly dump fails (their
# OnFailure=), and containers-alert.sh when a container is unhealthy or restarting.
# As disk-alert.sh: one email to OPS_ALERT_EMAIL through the backend's SES mailer
# (kaagaz.cli ops-alert), from the backend image on the host network, with .env as
# Docker reads it and the owner's password and the storage admin key blanked. The
# same SUBJECT goes at most once a UTC day for each KEY (SENT_DIR/.kaagaz-alert-KEY
# holds the day and the subject, written only once it was sent), so a deploy that
# fails every 5 minutes sends one email a day, not 288. When the email cannot go
# (OPS_ALERT_EMAIL or EMAIL_FROM unset, SES refused), it says so in the journal and
# fails, so `systemctl --failed` lists it.
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
SENT_DIR=/home/ubuntu

if (($# != 3)); then
  echo "usage: ops-alert.sh KEY SUBJECT TEXT" >&2
  exit 2
fi
key="$1"
subject="$2"
body="$3"
sent_file="$SENT_DIR/.kaagaz-alert-$key"
stamp="$(date -u +%Y%m%d) $subject"
if [[ -f "$sent_file" && "$(cat "$sent_file")" == "$stamp" ]]; then
  echo "already emailed today: $subject"
  exit 0
fi
cd "$KAAGAZ_DIR"
if ! docker run --rm --network host --env-file .env \
  -e POSTGRES_PASSWORD= -e STORAGE_ADMIN_SECRET_KEY= -e DATABASE_URL=none \
  -e APP_DATABASE_URL= -e OPS_DATABASE_URL= \
  kaagaz-backend:local python -m kaagaz.cli ops-alert --subject "$subject" --text "$body"; then
  echo "ALERT NOT SENT: $subject: the lines above say why" >&2
  exit 1
fi
printf '%s\n' "$stamp" >"$sent_file"
echo "emailed: $subject"
