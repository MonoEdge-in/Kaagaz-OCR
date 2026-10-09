#!/usr/bin/env bash
# Stops this EC2 server when nobody is using Kaagaz (EC2 PRD, WP-G).
#
# kaagaz-idle.timer runs this as root every minute. It runs systemctl poweroff
# only when all five hold, and otherwise stays on:
#   1. the server has been up for at least 20 minutes;
#   2. in the last 15 minutes Caddy logged no request to /api/... (other than
#      /api/health) that carried a Cookie header, so nobody signed in used the
#      app (Caddy logs the header's name with the value "REDACTED");
#   3. both job queues, kaagaz-jobs and the express lane kaagaz-jobs-express, are
#      empty: no document is waiting or being read (asked from ElasticMQ itself;
#      never from the database);
#   4. no deploy is running (/tmp/kaagaz-deploy.lock, infra/ec2/deploy.sh);
#   5. /etc/kaagaz/no-idle-stop does not exist (touch it to keep the server on).
# Any check that fails to run keeps the server on. Each run prints one line.
# The instance's shutdown behaviour is Stop (EC2 PRD section 8), so poweroff
# stops it, and the Wake link or the weekday schedule starts it again.
#
# Usage: idle-stop.sh [--dry-run]    --dry-run prints the decision and never stops.
set -euo pipefail

KAAGAZ_DIR=/home/ubuntu/kaagaz
KEEP_ON_FILE=/etc/kaagaz/no-idle-stop
DEPLOY_LOCK=/tmp/kaagaz-deploy.lock
MIN_UPTIME_SECONDS=1200
QUEUES="kaagaz-jobs kaagaz-jobs-express"

dry_run=false
case "${1:-}" in
  "") ;;
  --dry-run) dry_run=true ;;
  *)
    echo "usage: idle-stop.sh [--dry-run]" >&2
    exit 2
    ;;
esac

stay_on() {
  echo "idle-stop: staying on: $1"
  exit 0
}

dc() {
  docker compose -f docker-compose.yml -f docker-compose.prod.yml "$@"
}

# Counts the requests in Caddy's JSON access log that show a signed-in person:
# a path under /api/ other than /api/health, with a Cookie header. Other lines
# (Caddy's own messages) are skipped.
SIGNED_IN_REQUESTS_PY='
import json, sys
count = 0
for line in sys.stdin:
    try:
        entry = json.loads(line)
    except ValueError:
        continue
    request = entry.get("request") if isinstance(entry, dict) else None
    if not isinstance(request, dict):
        continue
    path = str(request.get("uri", "")).split("?", 1)[0]
    headers = request.get("headers")
    if not isinstance(headers, dict) or not path.startswith("/api/") or path == "/api/health":
        continue
    if any(str(name).lower() == "cookie" for name in headers):
        count += 1
print(count)
'

# Reads ElasticMQ's GetQueueAttributes XML reply and prints the two counts:
# waiting, then being read. Fails if either is missing.
QUEUE_COUNTS_PY='
import sys
import xml.etree.ElementTree as ET
values = {}
for element in ET.fromstring(sys.stdin.read()).iter():
    if element.tag.rsplit("}", 1)[-1] == "Attribute":
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "") for child in element}
        values[fields.get("Name")] = fields.get("Value")
print(int(values["ApproximateNumberOfMessages"]), int(values["ApproximateNumberOfMessagesNotVisible"]))
'

# 5. The manual switch.
if [[ -e "$KEEP_ON_FILE" ]]; then
  stay_on "$KEEP_ON_FILE exists"
fi

# 1. Uptime, in whole seconds.
if ! read -r uptime _ </proc/uptime; then
  stay_on "could not read /proc/uptime"
fi
uptime="${uptime%%.*}"
if [[ ! "$uptime" =~ ^[0-9]+$ ]]; then
  stay_on "could not read /proc/uptime"
fi
if ((uptime < MIN_UPTIME_SECONDS)); then
  stay_on "up for $((uptime / 60)) minutes, under 20"
fi

# 4. A running deploy holds this lock. The file is opened read-only, without
# creating it: deploy.sh (user ubuntu) owns it, and Ubuntu's protected_regular
# setting refuses even root a create-open of another user's file in /tmp.
# Once taken, the lock is held until this script exits, so no deploy starts
# while the server is being stopped. No file means no deploy since boot.
if [[ -e "$DEPLOY_LOCK" ]]; then
  if ! exec {lock_fd}<"$DEPLOY_LOCK"; then
    stay_on "could not open $DEPLOY_LOCK"
  fi
  lock_status=0
  flock -n "$lock_fd" || lock_status=$?
  if ((lock_status == 1)); then
    stay_on "a deploy is running"
  elif ((lock_status != 0)); then
    stay_on "could not check $DEPLOY_LOCK (flock exit $lock_status)"
  fi
fi

if ! cd "$KAAGAZ_DIR"; then
  stay_on "could not enter $KAAGAZ_DIR"
fi

# 3. Each job queue, asked inside ElasticMQ's own container.
for name in $QUEUES; do
  QUEUE="http://127.0.0.1:9324/000000000000/$name"
  if ! counts="$(dc exec -T elasticmq wget -qO- "$QUEUE?Action=GetQueueAttributes&AttributeName.1=All" 2>/dev/null |
    python3 -c "$QUEUE_COUNTS_PY" 2>/dev/null)"; then
    stay_on "could not read the job queue $name"
  fi
  read -r waiting being_read <<<"$counts"
  if ((waiting > 0 || being_read > 0)); then
    stay_on "job queue $name busy: $waiting waiting, $being_read being read"
  fi
done

# 2. Signed-in use in the last 15 minutes.
if ! signed_in="$(dc logs --since 15m --no-log-prefix caddy 2>/dev/null |
  python3 -c "$SIGNED_IN_REQUESTS_PY" 2>/dev/null)"; then
  stay_on "could not read Caddy's log"
fi
if ((signed_in > 0)); then
  stay_on "$signed_in signed-in API requests in the last 15 minutes"
fi

reason="up $((uptime / 60)) minutes, no signed-in API request in 15 minutes, job queues empty, no deploy running"
if [[ "$dry_run" == true ]]; then
  echo "idle-stop: dry run, would stop: $reason"
  exit 0
fi
echo "idle-stop: stopping: $reason"
systemctl poweroff
