#!/bin/bash
# Continuous RL training daemon — runs forever, retrains periodically.
#
# Usage:
#   docker exec -d firev-backend-1 bash /app/backend/scripts/rl_train_daemon.sh
#
# Behavior:
#   - Runs the full pipeline on startup (with retry on failure)
#   - Then checks for new live episodes every RETRAIN_INTERVAL seconds
#   - If enough new episodes accumulated (MIN_NEW_EPISODES), retrains
#   - All work runs at nice 19 — never starves extraction
#   - Self-heals: retries failed pipeline runs with exponential backoff
#   - Writes heartbeat file every check cycle for external monitoring
#
# Logs: /app/data/rl/daemon.log

RETRAIN_INTERVAL=14400  # Check every 4 hours
MIN_NEW_EPISODES=100    # Minimum new live episodes to trigger retrain
MAX_RETRIES=3           # Max retries per pipeline run
RETRY_BASE_DELAY=300    # 5 min initial retry delay (doubles each retry)
LOG=/app/data/rl/daemon.log
LIVE_DIR=/app/data/rl/live_episodes
PIPELINE=/app/backend/scripts/rl_train_pipeline.sh
HEARTBEAT=/app/data/rl/daemon_heartbeat
PID_FILE=/app/data/rl/daemon.pid

renice -n 19 $$ >/dev/null 2>&1 || true

# Pin to physical cores 0-1 (threads 0,1,4,5 on i7-7700 HT) — leaves cores 2-3
# for extraction browsers. All child processes (pipeline, python workers) inherit.
taskset -cp 0,1,4,5 $$ >/dev/null 2>&1 || true

# Write PID for external monitoring
# Check for disable flag (touch /app/data/rl/daemon_disabled to prevent auto-start)
DISABLE_FLAG=/app/data/rl/daemon_disabled
if [ -f "$DISABLE_FLAG" ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Daemon disabled (${DISABLE_FLAG} exists). Remove to re-enable." >&2
    exit 0
fi

echo $$ > "$PID_FILE"

log() {
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] $1" | tee -a "$LOG"
}

heartbeat() {
    date -u '+%Y-%m-%d %H:%M:%S UTC' > "$HEARTBEAT"
}

run_pipeline_with_retry() {
    local attempt=1
    local delay=$RETRY_BASE_DELAY

    while [ "$attempt" -le "$MAX_RETRIES" ]; do
        log "Pipeline attempt $attempt/$MAX_RETRIES..."
        heartbeat

        # Run pipeline WITHOUT set -e so failures don't kill the daemon.
        # Pipeline has its own tee to pipeline.log; also append to daemon log.
        bash "$PIPELINE" >> "$LOG" 2>&1 &
        PIPELINE_PID=$!
        wait "$PIPELINE_PID" 2>/dev/null
        local exit_code=$?
        PIPELINE_PID=""

        if [ "$exit_code" -eq 0 ]; then
            log "Pipeline completed successfully."
            return 0
        fi

        log "Pipeline FAILED (exit code $exit_code) on attempt $attempt/$MAX_RETRIES."

        if [ "$attempt" -lt "$MAX_RETRIES" ]; then
            log "Retrying in ${delay}s..."
            sleep "$delay"
            delay=$((delay * 2))
        fi
        attempt=$((attempt + 1))
    done

    log "Pipeline FAILED after $MAX_RETRIES attempts. Will retry next cycle."
    return 1
}

# Cleanup on exit — kill pipeline subprocess if running
PIPELINE_PID=""
trap 'log "Daemon shutting down (PID $$)."; [ -n "$PIPELINE_PID" ] && kill "$PIPELINE_PID" 2>/dev/null; rm -f "$PID_FILE"' EXIT SIGTERM SIGINT

log "RL training daemon started (PID: $$, interval: ${RETRAIN_INTERVAL}s, min_episodes: ${MIN_NEW_EPISODES}, max_retries: ${MAX_RETRIES})"

# Initial full pipeline run (with retry)
log "Running initial full pipeline..."
run_pipeline_with_retry
log "Initial pipeline phase complete."

# Continuous loop — never exits
while true; do
    heartbeat
    log "Sleeping ${RETRAIN_INTERVAL}s until next check..."
    sleep "$RETRAIN_INTERVAL"

    heartbeat

    # Count new live episodes
    NEW_CHUNKS=$(ls "$LIVE_DIR"/obs_*.npy 2>/dev/null | wc -l)
    if [ "$NEW_CHUNKS" -ge 1 ]; then
        # Estimate episode count (each chunk has ~100 episodes)
        ESTIMATED=$((NEW_CHUNKS * 100))
        log "Found $NEW_CHUNKS live chunks (~$ESTIMATED episodes)"

        if [ "$ESTIMATED" -ge "$MIN_NEW_EPISODES" ]; then
            log "Threshold reached — starting retrain cycle..."
            run_pipeline_with_retry
            log "Retrain cycle complete."
        else
            log "Below threshold ($ESTIMATED < $MIN_NEW_EPISODES) — skipping."
        fi
    else
        log "No new live episodes — skipping."
    fi
done
