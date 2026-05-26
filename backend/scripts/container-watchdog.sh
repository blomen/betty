#!/bin/bash
# Container liveness watchdog — runs via cron every 5 minutes.
# Checks if backend container is running and healthy, restarts if not.
#
# Install: echo "*/5 * * * * root bash /opt/arnold/backend/scripts/container-watchdog.sh >> /var/log/arnold-watchdog.log 2>&1" > /etc/cron.d/arnold-watchdog
#
# This catches the case where a deploy fails or the container crashes
# and nobody notices for hours (13h gap on 2026-04-10).

DEPLOY_DIR="/opt/arnold"
COMPOSE_DIR="/opt/arnold/backend"  # docker-compose.yml lives here after PR A2b
COMPOSE_ENV_FLAG="--env-file ../.env"  # .env stays at /opt/arnold/.env
LOCK_FILE="/opt/arnold/.deploy.lock"
LOG_PREFIX="[$(date -u '+%Y-%m-%d %H:%M UTC')]"

cd "$COMPOSE_DIR" || exit 1

# Don't interfere if a deploy is in progress
if ! flock -n 200 200>"$LOCK_FILE"; then
    echo "$LOG_PREFIX Deploy in progress, skipping watchdog check"
    exit 0
fi
# Release the lock immediately — we just checked if it was held
exec 200>&-

# Check if backend container exists and is running
backend_status=$(docker compose $COMPOSE_ENV_FLAG ps backend --format json 2>/dev/null | python3 -c "
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
    if docker compose $COMPOSE_ENV_FLAG exec -T backend curl -sf -m 30 http://localhost:8000/health/live >/dev/null 2>&1; then
        # Reset the consecutive-unhealthy counter on every healthy reading
        # so a previous transient unhealthy spike doesn't accumulate
        # toward the restart threshold.
        rm -f /var/lib/arnold-watchdog/unhealthy_count 2>/dev/null
        exit 0  # Healthy, nothing to do
    fi

    # Running but not responding to health — check Docker health status
    health=$(docker compose $COMPOSE_ENV_FLAG ps backend --format json 2>/dev/null | python3 -c "
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

    # 2026-05-07: trust Docker's healthcheck. If Docker says `healthy` but
    # our curl timed out, that's event-loop starvation under load — typically
    # the RL pipeline's CPU-bound steps (label-zone-outcomes, tick replay,
    # DQN training) hogging the loop for 30+ seconds at a time. Restarting
    # mid-pipeline killed every cycle: 5 attempts in 21min today, none
    # reaching step 6. Docker's healthcheck has its own grace + retry
    # built in; if IT says healthy, the container is fine and the loop
    # will recover when the heavy step finishes.
    if [ "$health" = "healthy" ]; then
        echo "$LOG_PREFIX Curl timed out but Docker reports healthy — assuming event-loop starvation under load (RL pipeline?), skipping restart"
        exit 0
    fi

    # Require N consecutive unhealthy readings before restart. A single
    # unhealthy reading is often a transient load spike (extraction burst,
    # GC pause) and restarting nukes the autonomous broker mid-trade for
    # ~60s. 2026-05-15: watchdog restarted the container 3 times in 20 min
    # (14:15 / 14:25 / 14:35 UTC), killing every active position and
    # blocking trading during a clean 100+ point bullish move. The 3rd
    # restart in particular fired during a session where the model was
    # emitting valid conf=0.43-0.98 signals that all got rejected because
    # the broker was either restarting or freshly halted on orphan_position.
    # Three consecutive unhealthy readings = 15 minutes of true unhealth;
    # transient spikes resolve on their own well within that window.
    STATE_DIR="/var/lib/arnold-watchdog"
    STATE_FILE="$STATE_DIR/unhealthy_count"
    UNHEALTHY_THRESHOLD=3
    mkdir -p "$STATE_DIR" 2>/dev/null
    current=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
    if ! [[ "$current" =~ ^[0-9]+$ ]]; then current=0; fi
    current=$((current + 1))
    echo "$current" > "$STATE_FILE"
    if [ "$current" -lt "$UNHEALTHY_THRESHOLD" ]; then
        echo "$LOG_PREFIX WARNING: Backend unhealthy (health: $health) — count $current/$UNHEALTHY_THRESHOLD, not restarting yet"
        exit 0
    fi
    echo "$LOG_PREFIX WARNING: Backend unhealthy (health: $health) for $current consecutive readings — restarting"
    docker compose $COMPOSE_ENV_FLAG restart backend
    rm -f "$STATE_FILE"
    echo "$LOG_PREFIX Backend restarted"
else
    echo "$LOG_PREFIX CRITICAL: Backend container not running (state: $backend_status)"
    echo "$LOG_PREFIX Starting backend..."
    docker compose $COMPOSE_ENV_FLAG up -d backend
    echo "$LOG_PREFIX Backend started"
fi
