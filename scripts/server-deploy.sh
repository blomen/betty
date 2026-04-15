#!/bin/bash
# Server-side deploy script with flock to prevent concurrent deploys
# Usage: ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh <action> [args]"
#
# Actions:
#   pull              - git pull only
#   rebuild [service]  - pull + rebuild + health check (default: backend)
#   restart [service]  - restart container + health check (default: backend)
#   logs [service] [n] - tail logs (default: backend, 30 lines)
#   status            - show deploy lock status + running containers
#   cleanup           - remove old Docker images and build cache

set -euo pipefail

LOCK_FILE="/opt/firev/.deploy.lock"
STATUS_FILE="/opt/firev/.deploy-status"
DEPLOY_DIR="/opt/firev"
DEPLOY_COOLDOWN_FILE="/opt/firev/.last-deploy"
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
    echo "Try again later or run: ssh root@148.251.40.251 'bash /opt/firev/scripts/server-deploy.sh status'"
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

# RL training protection — wait for active pipeline to finish before killing container
RL_PROGRESS="/opt/firev/data/rl/pipeline_progress"
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
        check_cooldown
        echo ">>> git pull + rebuild $service"
        git pull
        # Build image first (doesn't affect running container)
        docker compose build "$service"
        # Wait for RL training before swapping container
        wait_for_rl_training
        docker compose up -d "$service"
        echo ">>> Cleaning up old images and build cache..."
        docker image prune -f
        # Keep only recent build cache (current rebuild just populated fresh layers)
        docker builder prune -f --filter "until=24h" 2>/dev/null || true
        record_deploy_time
        if ! wait_for_health "$service"; then
            echo "DEPLOY FAILED: $service is unhealthy after rebuild"
            exit 1
        fi
        docker compose ps "$service"
        ;;
    restart)
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
        # Remove dangling images (old builds)
        docker image prune -f
        # Remove unused build cache older than 24h
        docker builder prune -f --filter "until=24h"
        # Remove unused volumes (except named ones)
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
