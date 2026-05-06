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

# Pre-swap broker flatten — calls /api/stocks/halt?flatten=true on the
# running backend so any in-flight entry from the outgoing container
# is closed before SIGTERM. Belt-and-suspenders with the shutdown
# handler's flatten path (which queries broker directly post-2026-04-30
# commit 8a1a27f8). Skipped silently if backend isn't running yet
# (first-time deploy) or the API is unreachable. Never fails the deploy.
preswap_flatten_backend() {
    if ! docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"'; then
        echo ">>> pre-swap flatten: backend not running, skipping"
        return 0
    fi
    if [ ! -f "$DEPLOY_DIR/.env.docker" ]; then
        echo ">>> pre-swap flatten: .env.docker missing, skipping"
        return 0
    fi
    local api_key
    api_key=$(grep -E '^ARNOLD_API_KEY=' "$DEPLOY_DIR/.env.docker" | cut -d= -f2- | tr -d '\r\n')
    if [ -z "$api_key" ]; then
        echo ">>> pre-swap flatten: ARNOLD_API_KEY not found, skipping"
        return 0
    fi
    echo ">>> pre-swap flatten: calling /api/stocks/halt?flatten=true"
    local resp
    resp=$(curl -s --max-time 10 -X POST -H "X-API-Key: $api_key" \
        "http://localhost:8000/api/stocks/halt?flatten=true" 2>/dev/null || echo '{"flattened":"unknown"}')
    echo ">>> pre-swap flatten response: $resp"
    # Brief wait so any closing fill can persist via the broker_trades
    # write path before container shutdown begins.
    sleep 2
    # Clear the trading_paused flag we just set — the next container
    # boots with a clean slate. (halt sets _TRADING_PAUSED_FLAG which
    # is a file in /app/data/rl/trading_paused; surviving across the
    # restart would mute every signal at conf=0.99.)
    docker compose exec -T backend bash -c \
        'rm -f /app/data/rl/trading_paused 2>/dev/null && echo "trading_paused flag cleared"' \
        || true
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

# Count rl_train_pipeline processes inside the container.
# `grep -c` returns exit 1 when count=0, which made the outer
# `|| echo "0"` fire and APPEND a second "0" to the already-correct
# stdout — the resulting "0\n0" then failed every `[ ... -eq 0 ]`
# integer comparison and the deploy spun in wait_for_rl_training
# until the 7200s timeout (observed 2026-05-05). Using `pgrep -fc`
# inside `bash -c '...; true'` ensures the inner command exits 0
# regardless of count, so the outer fallback never fires.
#
# 2026-05-07: pgrep self-match wedge. `bash -c 'pgrep -fc rl_train_pipeline'`
# spawns a bash whose argv contains the literal string "rl_train_pipeline"
# (in the -c argument). pgrep -f matches against full command line, so it
# matches the wrapping bash. _rl_pipeline_count always returned ≥1, even
# when no real pipeline was running, and every deploy spun the full 7200s.
# Fix: use the `[r]l_train_pipeline` regex-bracket trick — bash's argv
# contains the literal `[r]l_train_pipeline` (with brackets), but pgrep's
# regex evaluates `[r]l_train_pipeline` as "char-class matching r" + the
# rest, which doesn't match the bracketed string in argv. The actual
# rl_train_pipeline.sh process (no brackets in its name) still matches.
_rl_pipeline_count() {
    docker compose exec -T backend bash -c 'pgrep -fc "[r]l_train_pipeline" 2>/dev/null || echo 0' 2>/dev/null | tr -d '[:space:]' || echo "0"
}

wait_for_rl_training() {
    # Check if RL pipeline is running inside the container
    local rl_running
    rl_running=$(_rl_pipeline_count)
    if [ "${rl_running:-0}" -gt 0 ]; then
        echo ""
        echo ">>> RL TRAINING ACTIVE — waiting for pipeline to finish before restart."
        echo "    (The RL pipeline has never completed in 12 days due to deploy interruptions.)"
        echo "    Max wait: ${RL_MAX_WAIT}s. Progress file: pipeline_progress"
        echo ""
        local elapsed=0
        while [ "$elapsed" -lt "$RL_MAX_WAIT" ]; do
            rl_running=$(_rl_pipeline_count)
            if [ "${rl_running:-0}" -eq 0 ]; then
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
        # Pre-swap broker flatten: ensures any in-flight entry from the
        # outgoing container is closed before SIGTERM. The shutdown handler
        # flattens too (and queries broker directly post-2026-04-30 commit
        # 8a1a27f8), but doing it BEFORE container kill avoids relying on
        # the shutdown's grace period and the race where a fill arrives
        # mid-shutdown. Trades 128 / 136 today were inherited orphans
        # because the prior container died with an order in flight.
        if [ "$service" = "backend" ]; then
            preswap_flatten_backend
        fi
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
        if [ "$service" = "backend" ]; then
            preswap_flatten_backend
        fi
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
