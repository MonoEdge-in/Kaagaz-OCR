#!/usr/bin/env bash
# Emails when the server's disk is nearly full (#480, 3 Nov 2026).
#
# kaagaz-disk.timer runs this as ubuntu every hour. Over LIMIT_PERCENT used on
# /, it sends one email to OPS_ALERT_EMAIL through the backend's SES mailer
# (kaagaz.cli disk-alert, in a one-off worker container with the .env
# settings), at most once a UTC day: SENT_FILE holds the day of the last one,
# written only once it was sent. No CloudWatch agent (no new tool).
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
LIMIT_PERCENT=80
SENT_FILE=/home/ubuntu/.kaagaz-disk-alert
COMPOSE=(-f docker-compose.yml -f docker-compose.prod.yml)

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
# --no-deps: a plain run would start migrate first (deploy.sh, 24 Oct 2026).
docker compose "${COMPOSE[@]}" run --rm -T --no-deps worker \
  python -m kaagaz.cli disk-alert --used "$used"
printf '%s\n' "$day" >"$SENT_FILE"
echo "disk ${used}% used; emailed"
