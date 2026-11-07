#!/usr/bin/env bash
# Emails when a container is unhealthy or restarting (stability pass A22, 7 Nov 2026).
#
# kaagaz-containers.timer runs this as ubuntu every hour, as kaagaz-disk.timer runs
# disk-alert.sh. Any container Docker reports unhealthy (its healthcheck in
# docker-compose.yml failing) or restarting (crashing again and again) is named in
# one email through ops-alert.sh, which sends the same names at most once a UTC day.
# shortcut: it sees only the moment it runs, so a container that crashed and came
# back between two runs is missed (its RestartCount would show it).
set -euo pipefail

unhealthy="$(docker ps -a --filter health=unhealthy --format '{{.Names}}')"
restarting="$(docker ps -a --filter status=restarting --format '{{.Names}}')"
names="$(printf '%s\n%s\n' "$unhealthy" "$restarting" | awk 'NF' | sort -u | tr '\n' ' ')"
names="${names% }"
if [[ -z "$names" ]]; then
  echo "every container healthy"
  exit 0
fi
"$(dirname "$0")/ops-alert.sh" containers "Kaagaz server: not healthy: $names" \
  "Unhealthy or restarting on the Kaagaz server: $names
On the server, docker compose -f docker-compose.yml -f docker-compose.prod.yml ps -a shows them, and docker logs <name> says why."
