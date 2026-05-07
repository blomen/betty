#!/bin/bash
# Container liveness watchdog — runs via cron every 5 minutes.
# Checks if backend container is running and healthy, restarts if not.
#
# Install: echo "*/5 * * * * root bash /opt/arnold/scripts/container-watchdog.sh >> /var/log/arnold-watchdog.log 2>&1" > /etc/cron.d/arnold-watchdog
#
# This catches the case where a deploy fails or the container crashes
# and nobody notices for hours (13h gap on 2026-04-10).

DEPLOY_DIR="/opt/arnold"
LOCK_FILE="/opt/arnold/.deploy.lock"
LOG_PREFIX="[$(date -u '+%Y-%m-%d %H:%M UTC')]"

cd "$DEPLOY_DIR" || exit 1

# Don't interfere if a deploy is in progress
if ! flock -n 200 200>"$LOCK_FILE"; then
    echo "$LOG_PREFIX Deploy in progress, skipping watchdog check"
    exit 0
fi
# Release the lock immediately — we just checked if it was held
exec 200>&-

# Check if backend container exists and is running
backend_status=$(docker compose ps backend --format json 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    print(d.get('State', d.get('state', 'unknown')))
    break
" 2>/dev/null || echo "missing")

if [ "$backend_status" = "running" ]; then
    # Container is running — check health endpoint
    # 2026-05-07: probe /health/live (trivial async endpoint) instead of
    # /health, matching the docker-compose.yml healthcheck change. Until
    # the tick-path event-loop blocker is fixed, /health was timing out
    # often enough to trigger this watchdog into restart-looping the
    # container during RTH. /health/live is the smallest possible probe
    # — if it times out, the loop is genuinely starved.
    if docker compose exec -T backend curl -sf -m 30 http://localhost:8000/health/live >/dev/null 2>&1; then
        exit 0  # Healthy, nothing to do
    fi

    # Running but not responding to health — check Docker health status
    health=$(docker compose ps backend --format json 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    print(d.get('Health', d.get('health', 'unknown')))
    break
" 2>/dev/null || echo "unknown")

    if [ "$health" = "starting" ]; then
        echo "$LOG_PREFIX Backend is starting up (health: $health), giving it time"
        exit 0
    fi

    echo "$LOG_PREFIX WARNING: Backend running but unhealthy (health: $health)"
    echo "$LOG_PREFIX Restarting backend..."
    docker compose restart backend
    echo "$LOG_PREFIX Backend restarted"
else
    echo "$LOG_PREFIX CRITICAL: Backend container not running (state: $backend_status)"
    echo "$LOG_PREFIX Starting backend..."
    docker compose up -d backend
    echo "$LOG_PREFIX Backend started"
fi
