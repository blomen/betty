#!/bin/bash
# Server-side deploy script with flock to prevent concurrent deploys
# Usage: ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh <action> [args]"
#
# Actions:
#   pull              - git pull only
#   rebuild [service]  - pull + rebuild + health check (default: backend)
#   restart [service]  - restart container + health check (default: backend)
#   logs [service] [n] - tail logs (default: backend, 30 lines)
#   status            - show deploy lock status + running containers
#   cleanup           - remove old Docker images and build cache

set -euo pipefail

LOCK_FILE="/opt/arnold/.deploy.lock"
STATUS_FILE="/opt/arnold/.deploy-status"
DEPLOY_DIR="/opt/arnold"
DEPLOY_COOLDOWN_FILE="/opt/arnold/.last-deploy"
DEPLOY_COOLDOWN_SECONDS=300  # 5 min minimum between rebuilds

action="${1:-status}"
service="${2:-backend}"
lines="${3:-30}"

# Status and logs don't need a lock
case "$action" in
    status)
        echo "=== Deploy Status ==="
        if [ -f "$STATUS_FILE" ]; then
            cat "$STATUS_FILE"
        else
            echo "No active deploy"
        fi
        if [ -f "$DEPLOY_COOLDOWN_FILE" ]; then
            last=$(cat "$DEPLOY_COOLDOWN_FILE")
            now=$(date +%s)
            ago=$(( now - last ))
            echo "Last deploy: ${ago}s ago"
        fi
        echo ""
        echo "=== Containers ==="
        cd "$DEPLOY_DIR" && docker compose ps
        echo ""
        echo "=== Disk ==="
        docker system df
        exit 0
        ;;
    logs)
        cd "$DEPLOY_DIR" && docker compose logs "$service" --tail "$lines"
        exit 0
        ;;
esac

# All other actions acquire an exclusive lock
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "ERROR: Another deploy is in progress:"
    cat "$STATUS_FILE" 2>/dev/null || echo "(unknown)"
    echo "Try again later or run: ssh root@148.251.40.251 'bash /opt/arnold/scripts/server-deploy.sh status'"
    exit 1
fi

# Deploy cooldown — prevent rapid sequential rebuilds that kill extraction
check_cooldown() {
    if [ -f "$DEPLOY_COOLDOWN_FILE" ]; then
        last=$(cat "$DEPLOY_COOLDOWN_FILE")
        now=$(date +%s)
        elapsed=$(( now - last ))
        if [ "$elapsed" -lt "$DEPLOY_COOLDOWN_SECONDS" ]; then
            remaining=$(( DEPLOY_COOLDOWN_SECONDS - elapsed ))
            echo "WARNING: Last deploy was ${elapsed}s ago (cooldown: ${DEPLOY_COOLDOWN_SECONDS}s)."
            echo "Each rebuild kills extraction for 5-10 minutes."
            echo "Waiting ${remaining}s for cooldown..."
            sleep "$remaining"
        fi
    fi
}

record_deploy_time() {
    date +%s > "$DEPLOY_COOLDOWN_FILE"
}

# Open-trade protection — abort deploy if a TopstepX position is live, unless
# the operator passes ALLOW_OPEN_POSITION_DEPLOY=1. Every rebuild/restart kills
# the broker subprocess; the shutdown handler then flattens the live trade —
# a real PnL event, not a deploy artifact. Default-deny so an agent can't
# silently force a deploy through it.
check_open_position() {
    [ "$service" != "backend" ] && return 0
    if [ "${ALLOW_OPEN_POSITION_DEPLOY:-0}" = "1" ]; then
        echo ">>> ALLOW_OPEN_POSITION_DEPLOY=1 — skipping open-position check."
        return 0
    fi
    if ! docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"'; then
        echo ">>> Backend not running — open-position check skipped."
        return 0
    fi
    local pos_json
    pos_json=$(docker compose exec -T backend bash -c 'cd /app/backend && python3 - <<PY 2>/dev/null
import asyncio, json, sys
from src.stocks.config import TopstepXConfig
from src.stocks.topstepx_client import TopstepXClient
async def main():
    c = TopstepXClient(TopstepXConfig.from_env())
    if not await c.connect():
        print("AUTH_FAIL"); return
    try:
        pos = await c.search_open_positions()
        print(json.dumps(pos or []))
    finally:
        await c.close()
asyncio.run(main())
PY
') || pos_json=""
    if [ -z "$pos_json" ] || [ "$pos_json" = "AUTH_FAIL" ]; then
        echo "ERROR: could not verify TopstepX position state (auth failed or container exec error)."
        echo "Re-run with ALLOW_OPEN_POSITION_DEPLOY=1 if you've manually verified the account is flat."
        exit 1
    fi
    local count
    count=$(echo "$pos_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else 0)')
    if [ "$count" -gt 0 ]; then
        echo ""
        echo "BLOCKED: TopstepX has ${count} open position(s) — rebuild/restart would flatten the live trade."
        echo "$pos_json" | python3 -m json.tool 2>/dev/null || echo "$pos_json"
        echo ""
        echo "If you genuinely want to deploy through an open trade (e.g. paper account, or you accept the flatten):"
        echo "  ssh root@148.251.40.251 \"ALLOW_OPEN_POSITION_DEPLOY=1 bash /opt/arnold/scripts/server-deploy.sh $action $service\""
        exit 1
    fi
    echo ">>> No open positions — proceeding."
}

# RL training protection — wait for active pipeline to finish before killing container
RL_PROGRESS="/opt/arnold/data/rl/pipeline_progress"
RL_MAX_WAIT=7200  # 2 hours max wait

wait_for_rl_training() {
    # Check if RL pipeline is running inside the container
    local rl_running
    rl_running=$(docker compose exec -T backend bash -c 'ps aux | grep -c "[r]l_train_pipeline"' 2>/dev/null || echo "0")
    if [ "$rl_running" -gt 0 ]; then
        echo ""
        echo ">>> RL TRAINING ACTIVE — waiting for pipeline to finish before restart."
        echo "    (The RL pipeline has never completed in 12 days due to deploy interruptions.)"
        echo "    Max wait: ${RL_MAX_WAIT}s. Progress file: pipeline_progress"
        echo ""
        local elapsed=0
        while [ "$elapsed" -lt "$RL_MAX_WAIT" ]; do
            rl_running=$(docker compose exec -T backend bash -c 'ps aux | grep -c "[r]l_train_pipeline"' 2>/dev/null || echo "0")
            if [ "$rl_running" -eq 0 ]; then
                echo ">>> RL pipeline finished. Proceeding with deploy."
                return 0
            fi
            # Show progress
            local progress
            progress=$(docker compose exec -T backend cat /app/data/rl/pipeline_progress 2>/dev/null || echo "step 1")
            local chunks
            chunks=$(docker compose exec -T backend bash -c 'ls /app/data/rl/episodes/_chunks/obs_*.npy 2>/dev/null | wc -l' 2>/dev/null || echo "?")
            echo "    ... RL training in progress (${elapsed}s, chunks: ${chunks}/38, steps done: ${progress})"
            sleep 30
            elapsed=$(( elapsed + 30 ))
        done
        echo ">>> WARNING: RL training still running after ${RL_MAX_WAIT}s — proceeding with deploy."
    fi
}

# Health check — verify container is healthy after deploy
wait_for_health() {
    local svc="$1"
    local max_wait=120  # 2 minutes max
    local interval=5

    echo ">>> Waiting for $svc health (up to ${max_wait}s)..."
    local elapsed=0
    while [ "$elapsed" -lt "$max_wait" ]; do
        # Check Docker health status
        local health
        health=$(docker compose ps "$svc" --format json 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    print(d.get('Health', d.get('health', 'unknown')))
    break
" 2>/dev/null || echo "unknown")

        if [ "$health" = "healthy" ]; then
            echo ">>> $svc is healthy (${elapsed}s)"
            return 0
        fi

        # Also try curl directly as fallback
        if docker compose exec -T "$svc" curl -sf http://localhost:8000/health >/dev/null 2>&1; then
            echo ">>> $svc responding to /health (${elapsed}s)"
            return 0
        fi

        sleep "$interval"
        elapsed=$(( elapsed + interval ))
        echo "    ... waiting (${elapsed}s, status: $health)"
    done

    echo "ERROR: $svc failed health check after ${max_wait}s!"
    echo ">>> Recent logs:"
    docker compose logs "$svc" --tail 20
    return 1
}

# Write status so other agents can see what's happening
write_status() {
    echo "action=$action service=$service started=$(date -u +%Y-%m-%dT%H:%M:%SZ) agent=${DEPLOY_AGENT:-unknown}" > "$STATUS_FILE"
}

clear_status() {
    rm -f "$STATUS_FILE"
}

trap clear_status EXIT

write_status

cd "$DEPLOY_DIR"

case "$action" in
    pull)
        echo ">>> git pull"
        git pull
        ;;
    rebuild)
        check_open_position
        check_cooldown
        echo ">>> git pull + rebuild $service"
        git pull
        # Build image first (doesn't affect running container)
        docker compose build "$service"
        # Wait for RL training before swapping container
        wait_for_rl_training
        docker compose up -d "$service"
        echo ">>> Pruning unused images and build cache..."
        # -a removes ALL unused images, not just dangling. Running containers
        # keep their images (Docker won't drop a referenced image), so this is
        # safe — it only frees layers from previous builds. Without -a we
        # accumulated 94GB of orphaned arnold-backend layers in 24h.
        docker image prune -af
        # Build cache from the current rebuild can be kept, but old caches
        # snowball fast (62GB observed in 24h). Drop everything; the next
        # rebuild repopulates only what it needs.
        docker builder prune -af 2>/dev/null || true
        record_deploy_time
        if ! wait_for_health "$service"; then
            echo "DEPLOY FAILED: $service is unhealthy after rebuild"
            exit 1
        fi
        docker compose ps "$service"
        ;;
    restart)
        check_open_position
        check_cooldown
        echo ">>> git pull + restart $service"
        git pull
        wait_for_rl_training
        docker compose restart "$service"
        record_deploy_time
        if ! wait_for_health "$service"; then
            echo "DEPLOY FAILED: $service is unhealthy after restart"
            exit 1
        fi
        docker compose ps "$service"
        ;;
    cleanup)
        echo ">>> Docker cleanup"
        echo "Before:"
        docker system df
        echo ""
        # Aggressive: remove ALL unused images and ALL build cache. Active
        # container images stay (referenced); only orphans are dropped.
        docker image prune -af
        docker builder prune -af
        # Volumes are gated separately — they hold real data (rl/ data,
        # postgres data, chrome profile). Only prune if explicitly listed.
        docker volume prune -f
        echo ""
        echo "After:"
        docker system df
        ;;
    *)
        echo "Unknown action: $action"
        echo "Usage: server-deploy.sh {pull|rebuild|restart|logs|status|cleanup} [service] [lines]"
        exit 1
        ;;
esac

echo ">>> Deploy complete"
