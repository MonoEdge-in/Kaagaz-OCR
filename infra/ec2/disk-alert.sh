#!/usr/bin/env bash
# Emails when the server's disk is nearly full (#480, 3 Nov 2026).
#
# kaagaz-disk.timer runs this as ubuntu every hour. Over LIMIT_PERCENT used on
# /, it sends one email to OPS_ALERT_EMAIL through the backend's SES mailer
# (kaagaz.cli disk-alert), at most once a UTC day: SENT_FILE holds the day of
# the last one, written only once it was sent. No CloudWatch agent (no new
# tool). The command runs in the backend image on the host network, as
# pgdump.sh's backup-upload does, so boto3 reaches the server's own role with
# the instance metadata hop limit left at 1. --env-file hands it .env as Docker
# reads it: each value exactly as written, so quotes there stay in the value.
# It reads no database and no bucket, so the owner's password and the storage
# admin key are blanked, and DATABASE_URL (the settings need one) names no login.
# When the email cannot go (OPS_ALERT_EMAIL or EMAIL_FROM unset, SES refused,
# or the disk too full to start a container), it says so in the journal and
# the unit fails, so `systemctl --failed` lists it.
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
LIMIT_PERCENT=80
SENT_FILE=/home/ubuntu/.kaagaz-disk-alert

used="$(df --output=pcent / | tail -n 1 | tr -dc '0-9')"
if ((used <= LIMIT_PERCENT)); then
  echo "disk ${used}% used"
  exit 0
fi
day="$(date -u +%Y%m%d)"
if [[ -f "$SENT_FILE" && "$(cat "$SENT_FILE")" == "$day" ]]; then
  echo "disk ${used}% used; already emailed today"
  exit 0
fi
cd "$KAAGAZ_DIR"
if ! docker run --rm --network host --env-file .env \
  -e POSTGRES_PASSWORD= -e STORAGE_ADMIN_SECRET_KEY= -e DATABASE_URL=none \
  kaagaz-backend:local python -m kaagaz.cli disk-alert --used "$used"; then
  echo "DISK ${used}% USED and the alert email was NOT sent: the lines above say why" >&2
  exit 1
fi
printf '%s\n' "$day" >"$SENT_FILE"
echo "disk ${used}% used; emailed"
