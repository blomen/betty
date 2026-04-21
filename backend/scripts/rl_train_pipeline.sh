#!/bin/bash
# Full RL training pipeline v5 — hierarchical observation architecture.
#
# RESUME-SAFE: Each completed step is recorded in a progress file.
# If the container restarts mid-pipeline, re-running picks up where it
# left off instead of starting from scratch.  Step 1 (replay) is also
# internally resume-safe via per-file chunks.
#
# Pipeline:
#   0. Merge live episodes
#   1. Replay historical ticks → base episodes (parallel)
#   2. Label episodes with setup types (rule-based + clustering)
#   3. Train Narrative GBT (day type + setup probs)
#   4. Train Trigger GBT (direction/reward on trigger features)
#   5. Re-replay with both GBTs → hybrid trigger episodes (parallel)
#   6. Train Trigger DQN on hybrid episodes
#   7. Evaluate
#   8. Deploy models
#
# Error handling:
#   - Each step checks exit code and logs failure
#   - Critical failures abort the pipeline with non-zero exit
#   - Non-critical failures log warning and continue
#   - Pipeline returns 0 only if all critical steps succeed

LOG=/app/data/rl/pipeline.log
PROGRESS=/app/data/rl/pipeline_progress
exec > >(tee -a "$LOG") 2>&1

# Turbo mode inherited from daemon; fallback for manual runs
TURBO_FLAG=/app/data/rl/turbo
WORKERS=${RL_WORKERS:-1}
if [ -f "$TURBO_FLAG" ]; then
    WORKERS=${RL_WORKERS:-2}
    echo "TURBO MODE: $WORKERS workers, all cores, normal priority"
else
    renice -n 19 $$ >/dev/null 2>&1 || true
    taskset -cp 0,1,4,5 $$ >/dev/null 2>&1 || true
fi

FAILED=0

step_done() {
    # Check if step $1 was already completed in a previous run
    [ -f "$PROGRESS" ] && grep -qx "$1" "$PROGRESS"
}

step_mark() {
    # Record step $1 as completed
    echo "$1" >> "$PROGRESS"
}

step_run() {
    local step_num="$1"
    local step_name="$2"
    local critical="$3"  # "critical" or "optional"
    shift 3

    if step_done "$step_num"; then
        echo ""
        echo "[$step_num] $step_name — already done, skipping."
        return 0
    fi

    echo ""
    echo "[$step_num] $step_name..."
    if "$@"; then
        echo "[$step_num] Done."
        step_mark "$step_num"
    else
        local ec=$?
        echo "[$step_num] FAILED (exit code $ec)."
        if [ "$critical" = "critical" ]; then
            echo "PIPELINE ABORTED — critical step $step_num failed."
            FAILED=1
            return 1
        else
            echo "  (non-critical — continuing)"
            step_mark "$step_num"  # skip on next resume
        fi
    fi
    return 0
}

echo "=========================================="
echo "  RL TRAINING PIPELINE v5 — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "  PID: $$ (nice 19 — low priority)"
if [ -f "$PROGRESS" ]; then
    echo "  RESUMING from: $(cat "$PROGRESS" | tr '\n' ' ')"
fi
echo "=========================================="

cd /app/backend

# Step 0a: Export TopstepX trades from DB to parquet (always runs — new trades accumulate)
echo ""
echo "[0/8] Exporting DB trades to parquet..."
python -m src.app rl export-trades || echo "[0/8] Trade export failed (non-critical)."

# Step 0b: Merge live episodes (always runs — new episodes accumulate between cycles)
echo ""
echo "[0/8] Merging live episodes..."
python -m src.app rl merge-live || echo "[0/8] No live episodes to merge (non-critical)."

# Step 1: Parallel replay → base episodes (CRITICAL)
# Internally resume-safe: skips parquet files that already have chunks
step_run "1/8" "Replaying historical ticks → base episodes" "critical" \
    nice -n 19 python -m src.app rl replay --all --workers $WORKERS
[ $FAILED -eq 1 ] && exit 1

# Step 2: Label setups (optional — pipeline can continue without labels)
step_run "2/8" "Labeling episodes with setup types" "optional" \
    python -m src.app rl label-setups

# Step 3: Train Narrative GBT (critical for v5)
step_run "3/8" "Training Narrative GBT v5" "critical" \
    nice -n 19 python -m src.app rl train-narrative-gbt --checkpoint v5 --trees 500 --depth 5 --lr 0.05
[ $FAILED -eq 1 ] && exit 1

# Step 4: Train Trigger GBT (critical for v5)
step_run "4/8" "Training Trigger GBT v5" "critical" \
    nice -n 19 python -m src.app rl train-trigger-gbt --checkpoint v5 --trees 1000 --depth 6 --lr 0.05
[ $FAILED -eq 1 ] && exit 1

# Step 5: Augment trigger observations with GBT forecast (FAST — ~1 min)
# Replaces the old "re-replay all 39 parquets with --gbt" which took ~5h.
# Instead: load saved trigger_observations.npy, run GBT inference in batch,
# write 8-dim forecast into the trigger_gbt slot (schema-derived). Same
# result, 300x faster.
step_run "5/8" "Augmenting trigger obs with GBT forecast (fast, batch inference)" "critical" \
    python -m src.app rl augment-trigger-obs --gbt-name trigger_gbt_v5.joblib
[ $FAILED -eq 1 ] && exit 1

# Step 5b: Train SizeModel — Phase 3c trained position-sizing head.
# Optional so it can't block DQN training if it fails on an edge case.
step_run "5b/8" "Training SizeModel v5 (Phase 3c)" "optional" \
    nice -n 19 python -m src.app rl train-size-model --checkpoint v5 --trees 400 --depth 4 --lr 0.05

# Step 6: Train Trigger DQN (critical)
step_run "6/8" "Training Trigger DQN v5 (30 epochs, batch 4096)" "critical" \
    nice -n 19 python -m src.app rl train --epochs 30 --checkpoint v5
[ $FAILED -eq 1 ] && exit 1

# Step 7: Evaluate (optional — nice to have but not required)
# Threshold 0.15 matches the live gate after we lowered it from 0.30.
# Sweep on last training showed 7× total R at 0.15 vs 0.30 with only +4R DD.
step_run "7/8" "Evaluating DQN v5" "optional" \
    python -m src.app rl eval --checkpoint v5 --skip-threshold 0.15

# Step 8: Deploy + archive
if ! step_done "8/8"; then
    echo ""
    echo "[8/8] Deploying v5 models..."
    MODELS=/app/backend/data/rl/models
    ARCHIVE_ROOT=/app/backend/data/rl/archive
    TS=$(date -u '+%Y%m%d_%H%M%S')
    ARCHIVE_DIR="$ARCHIVE_ROOT/$TS"

    # Archive this run: models + eval report extracted from pipeline.log.
    # Keeps full history for A/B comparison across training iterations.
    mkdir -p "$ARCHIVE_DIR"
    cp -f "$MODELS/narrative_gbt_v5.joblib" "$ARCHIVE_DIR/" 2>/dev/null || true
    cp -f "$MODELS/trigger_gbt_v5.joblib" "$ARCHIVE_DIR/" 2>/dev/null || true
    cp -f "$MODELS/size_model_v5.joblib" "$ARCHIVE_DIR/" 2>/dev/null || true
    cp -f "$MODELS/dqn_v5.pt" "$ARCHIVE_DIR/" 2>/dev/null || true
    cp -f "$MODELS/dqn_v5_best.pt" "$ARCHIVE_DIR/" 2>/dev/null || true
    # Extract the most recent RL AGENT EVALUATION REPORT from pipeline.log
    awk '/RL AGENT EVALUATION REPORT/{flag=1; buf=""} flag{buf=buf $0 ORS} /^\[7\/8\] Done\./{if (flag){print buf; flag=0}}' \
        "$LOG" 2>/dev/null | tail -n +$(awk 'END{c=0; for(i=1;i<=NR;i++) if($0 ~ /RL AGENT EVALUATION REPORT/) c=i; print c}' "$LOG" 2>/dev/null || echo 1) \
        > "$ARCHIVE_DIR/eval_report.txt" 2>/dev/null || true
    # Also save a machine-readable metrics line (grepped from report)
    python -c "
import re
from pathlib import Path
log = Path('$LOG').read_text() if Path('$LOG').exists() else ''
# find last RL AGENT EVALUATION REPORT block
blocks = log.split('RL AGENT EVALUATION REPORT')
if len(blocks) > 1:
    last = blocks[-1]
    def _grab(pat):
        m = re.search(pat, last)
        return m.group(1) if m else ''
    metrics = {
        'timestamp': '$TS',
        'episodes': _grab(r'Episodes\s*:\s*([\d,]+)'),
        'trades': _grab(r'Trades taken\s*:\s*([\d,]+)'),
        'win_rate_pct': _grab(r'Win rate\s*:\s*([\d.]+)'),
        'avg_r': _grab(r'Avg R / trade\s*:\s*([+\-\d.]+)'),
        'total_r': _grab(r'Total R\s*:\s*([+\-\d.]+)'),
        'profit_factor': _grab(r'Profit factor\s*:\s*([\d.]+)'),
        'max_dd_r': _grab(r'Max drawdown\s*:\s*([\d.]+)'),
    }
    import json
    Path('$ARCHIVE_DIR/metrics.json').write_text(json.dumps(metrics, indent=2))
    print('metrics.json saved:', metrics)
" 2>/dev/null || true

    # Deploy latest pointers (prod)
    cp -f "$MODELS/narrative_gbt_v5.joblib" "$MODELS/narrative_gbt_latest.joblib" 2>/dev/null || true
    cp -f "$MODELS/trigger_gbt_v5.joblib" "$MODELS/trigger_gbt_latest.joblib" 2>/dev/null || true
    cp -f "$MODELS/size_model_v5.joblib" "$MODELS/size_model_latest.joblib" 2>/dev/null || true
    cp -f "$MODELS/dqn_v5.pt" "$MODELS/dqn_latest.pt" 2>/dev/null || true

    # Prune archive: keep newest 10 runs
    if [ -d "$ARCHIVE_ROOT" ]; then
        cd "$ARCHIVE_ROOT" && ls -1t | tail -n +11 | xargs -r rm -rf
    fi

    echo "[8/8] Models deployed + archived to $ARCHIVE_DIR"
    step_mark "8/8"
fi

# Pipeline complete — clean up
rm -f "$PROGRESS"
rm -f "$STEP5_STARTED"
# Clear merged live episodes so they aren't double-counted next cycle
rm -f /app/backend/data/rl/live_episodes/*.npy 2>/dev/null
echo "Cleared live episode buffer (merged into training data)."

echo ""
echo "=========================================="
echo "  PIPELINE v5 COMPLETE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "=========================================="
exit 0
