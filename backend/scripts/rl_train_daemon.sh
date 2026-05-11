#!/bin/bash
# Scheduled RL training daemon — fires ONCE per day at the configured
# UTC window (default 21:00 UTC = 23:00 Stockholm CEST, aligned with NQ daily
# maintenance start). 2026-05-11: rewrote from "always-on every 4h" to
# "scheduled when market is closed" so live inference + deploys never compete
# with training.
#
# Schedule:
#   - Mon-Thu 21:00 UTC: 2h training (NQ daily maintenance window)
#   - Fri    21:00 UTC: starts the weekend run (caps at 49h, covers Fri close
#                       through Sun reopen)
#   - Sat/Sun: skipped (already in weekend run from Friday)
#
# After each training run: session_cleanup.sh resets consecutive_stops +
# halts, archives broker_trades to parquet, prunes merged live_episodes
# chunks, cancels orphan TopstepX orders.
#
# Logs: /app/data/rl/daemon.log

TRAIN_HOUR_UTC=21          # 21:00 UTC = 23:00 Stockholm CEST (matches NQ daily maintenance start)
DAILY_DURATION_H=2         # Mon-Thu run length
WEEKEND_DURATION_H=49      # Fri 20:00 → Sun 21:00 = ~49h
MAX_RETRIES=3              # Max retries per pipeline run on failure
RETRY_BASE_DELAY=300       # 5 min initial retry delay (doubles each retry)
LOG=/app/data/rl/daemon.log
LIVE_DIR=/app/data/rl/live_episodes
PIPELINE=/app/backend/scripts/rl_train_pipeline.sh
CLEANUP=/app/backend/scripts/session_cleanup.sh
HEARTBEAT=/app/data/rl/daemon_heartbeat
PID_FILE=/app/data/rl/daemon.pid

# Turbo mode: touch /app/data/rl/turbo to use all cores at normal priority.
# Default (no turbo): nice 19, pinned to cores 0-1, leaving cores 2-3 for extraction.
TURBO_FLAG=/app/data/rl/turbo
if [ -f "$TURBO_FLAG" ]; then
    echo "[Daemon] TURBO MODE — all cores, normal priority, 2 workers"
    export RL_WORKERS=2
else
    renice -n 19 $$ >/dev/null 2>&1 || true
    taskset -cp 0,1,4,5 $$ >/dev/null 2>&1 || true
    export RL_WORKERS=1
fi

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

log "RL training daemon started (PID: $$, schedule: ${TRAIN_HOUR_UTC}:00 UTC daily, daily=${DAILY_DURATION_H}h, weekend=${WEEKEND_DURATION_H}h)"

# Compute seconds until the next TRAIN_HOUR_UTC. Returns 0 if we're already
# inside the training window for the current day. Skips Sat/Sun starts
# (those days fall within the Friday-launched weekend run).
seconds_until_next_window() {
    local now_unix
    now_unix=$(date -u +%s)
    local today_window
    today_window=$(date -u -d "today ${TRAIN_HOUR_UTC}:00:00" +%s)
    local target=$today_window
    if [ "$now_unix" -ge "$today_window" ]; then
        target=$(date -u -d "tomorrow ${TRAIN_HOUR_UTC}:00:00" +%s)
    fi
    # Skip Sat/Sun (1=Mon ... 7=Sun, ISO). Friday's weekend run already
    # covers them. If target lands on Sat or Sun, push to Monday.
    local target_dow
    target_dow=$(date -u -d "@${target}" +%u)
    while [ "$target_dow" -eq 6 ] || [ "$target_dow" -eq 7 ]; do
        target=$((target + 86400))
        target_dow=$(date -u -d "@${target}" +%u)
    done
    echo $((target - now_unix))
}

# Determine training duration for today's run. Friday gets the long weekend
# duration; Mon-Thu get the short daily duration.
duration_hours_for_today() {
    local dow
    dow=$(date -u +%u)
    if [ "$dow" -eq 5 ]; then
        echo $WEEKEND_DURATION_H
    else
        echo $DAILY_DURATION_H
    fi
}

# Run pipeline with hard wall-clock timeout (in hours).
run_pipeline_bounded() {
    local hours=$1
    local timeout_s=$((hours * 3600))
    local attempt=1
    local delay=$RETRY_BASE_DELAY

    while [ "$attempt" -le "$MAX_RETRIES" ]; do
        log "Pipeline attempt $attempt/$MAX_RETRIES (max ${hours}h)..."
        heartbeat

        timeout "${timeout_s}s" bash "$PIPELINE" >> "$LOG" 2>&1 &
        PIPELINE_PID=$!
        wait "$PIPELINE_PID" 2>/dev/null
        local exit_code=$?
        PIPELINE_PID=""

        if [ "$exit_code" -eq 0 ]; then
            log "Pipeline completed successfully."
            return 0
        fi
        if [ "$exit_code" -eq 124 ]; then
            log "Pipeline hit wall-clock timeout (${hours}h). Training window ended cleanly."
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

    log "Pipeline FAILED after $MAX_RETRIES attempts."
    return 1
}

# Continuous schedule-driven loop.
while true; do
    sleep_s=$(seconds_until_next_window)
    next_iso=$(date -u -d "@$(($(date -u +%s) + sleep_s))" +%Y-%m-%dT%H:%MZ)
    log "Sleeping ${sleep_s}s until next training window (${next_iso})..."
    heartbeat
    sleep "$sleep_s"

    hours=$(duration_hours_for_today)
    log "=== Training window OPEN (max ${hours}h) ==="
    run_pipeline_bounded "$hours"
    log "=== Training window CLOSED ==="

    log "Running session cleanup..."
    if [ -x "$CLEANUP" ] || [ -f "$CLEANUP" ]; then
        bash "$CLEANUP" >> "$LOG" 2>&1
        log "Session cleanup complete."
    else
        log "WARNING: session_cleanup.sh not found at $CLEANUP — skipping."
    fi
done
