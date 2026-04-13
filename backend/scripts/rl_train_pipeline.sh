#!/bin/bash
# Full RL training pipeline v5 — hierarchical observation architecture.
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
#   - Critical failures (step 1) abort the pipeline with non-zero exit
#   - Non-critical failures (step 2, 7) log warning and continue
#   - Pipeline returns 0 only if all critical steps succeed

LOG=/app/data/rl/pipeline.log
exec > >(tee -a "$LOG") 2>&1

renice -n 19 $$ >/dev/null 2>&1 || true

# Pin to cores 0-1 (threads 0,1,4,5) — fallback for manual runs (daemon sets this too)
taskset -cp 0,1,4,5 $$ >/dev/null 2>&1 || true

FAILED=0

step_run() {
    local step_num="$1"
    local step_name="$2"
    local critical="$3"  # "critical" or "optional"
    shift 3

    echo ""
    echo "[$step_num] $step_name..."
    if "$@"; then
        echo "[$step_num] Done."
    else
        local ec=$?
        echo "[$step_num] FAILED (exit code $ec)."
        if [ "$critical" = "critical" ]; then
            echo "PIPELINE ABORTED — critical step $step_num failed."
            FAILED=1
            return 1
        else
            echo "  (non-critical — continuing)"
        fi
    fi
    return 0
}

echo "=========================================="
echo "  RL TRAINING PIPELINE v5 — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "  PID: $$ (nice 19 — low priority)"
echo "=========================================="

cd /app/backend

# Step 0: Merge live episodes (optional — may have none)
step_run "0/8" "Merging live episodes" "optional" \
    python -m src.app rl merge-live

# Step 1: Parallel replay → base episodes (CRITICAL)
step_run "1/8" "Replaying historical ticks → base episodes" "critical" \
    nice -n 19 python -m src.app rl replay --all --workers 1
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

# Step 5: Re-replay with GBT augmentation → hybrid trigger episodes (critical)
# --clean: must wipe base chunks since augmented obs have different dims
step_run "5/8" "Re-replaying with GBT augmentation → hybrid trigger episodes" "critical" \
    nice -n 19 python -m src.app rl replay --all --gbt trigger_gbt_v5.joblib --workers 1 --clean
[ $FAILED -eq 1 ] && exit 1

# Step 6: Train Trigger DQN (critical)
step_run "6/8" "Training Trigger DQN v5 (30 epochs, batch 4096)" "critical" \
    nice -n 19 python -m src.app rl train --epochs 30 --checkpoint v5
[ $FAILED -eq 1 ] && exit 1

# Step 7: Evaluate (optional — nice to have but not required)
step_run "7/8" "Evaluating DQN v5" "optional" \
    python -m src.app rl eval --checkpoint v5 --skip-threshold 0.15

# Step 8: Deploy
echo ""
echo "[8/8] Deploying v5 models..."
cp -f /app/backend/data/rl/models/narrative_gbt_v5.joblib /app/backend/data/rl/models/narrative_gbt_latest.joblib 2>/dev/null || true
cp -f /app/backend/data/rl/models/trigger_gbt_v5.joblib /app/backend/data/rl/models/trigger_gbt_latest.joblib 2>/dev/null || true
cp -f /app/backend/data/rl/models/dqn_v5.pt /app/backend/data/rl/models/dqn_latest.pt 2>/dev/null || true
echo "[8/8] Models deployed."

echo ""
echo "=========================================="
echo "  PIPELINE v5 COMPLETE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "=========================================="
exit 0
