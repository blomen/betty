#!/bin/bash
# Continuous RL training daemon — runs forever, retrains periodically.
#
# Usage:
#   docker exec -d firev-backend-1 bash /app/backend/scripts/rl_train_daemon.sh
#
# Behavior:
#   - Runs the full pipeline on startup
#   - Then checks for new live episodes every RETRAIN_INTERVAL seconds
#   - If enough new episodes accumulated (MIN_NEW_EPISODES), retrains
#   - All work runs at nice 19 — never starves extraction
#
# Logs: /app/data/rl/daemon.log

set -e

RETRAIN_INTERVAL=14400  # Check every 4 hours
MIN_NEW_EPISODES=100    # Minimum new live episodes to trigger retrain
LOG=/app/data/rl/daemon.log
LIVE_DIR=/app/data/rl/live_episodes
PIPELINE=/app/backend/scripts/rl_train_pipeline.sh

renice -n 19 $$ >/dev/null 2>&1 || true

log() {
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] $1" | tee -a "$LOG"
}

log "RL training daemon started (PID: $$, interval: ${RETRAIN_INTERVAL}s, min_episodes: ${MIN_NEW_EPISODES})"

# Initial full pipeline run
log "Running initial full pipeline..."
bash "$PIPELINE" 2>&1 | tee -a "$LOG"
log "Initial pipeline complete."

# Continuous loop
while true; do
    log "Sleeping ${RETRAIN_INTERVAL}s until next check..."
    sleep "$RETRAIN_INTERVAL"

    # Count new live episodes
    NEW_CHUNKS=$(ls "$LIVE_DIR"/obs_*.npy 2>/dev/null | wc -l)
    if [ "$NEW_CHUNKS" -ge 1 ]; then
        # Estimate episode count (each chunk has ~100 episodes)
        ESTIMATED=$((NEW_CHUNKS * 100))
        log "Found $NEW_CHUNKS live chunks (~$ESTIMATED episodes)"

        if [ "$ESTIMATED" -ge "$MIN_NEW_EPISODES" ]; then
            log "Threshold reached — starting retrain cycle..."
            bash "$PIPELINE" 2>&1 | tee -a "$LOG"
            log "Retrain cycle complete."
        else
            log "Below threshold ($ESTIMATED < $MIN_NEW_EPISODES) — skipping."
        fi
    else
        log "No new live episodes — skipping."
    fi
done
