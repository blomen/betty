#!/bin/bash
# Full RL training pipeline — runs unattended on the server.
#
# Usage:
#   docker exec -d firev-backend-1 bash /app/backend/scripts/rl_train_pipeline.sh
#
# Resource management:
#   - All RL work runs at low CPU priority (nice 19) so extraction always wins
#   - GBT training bursts are short (~30s with LightGBM)
#   - RAM usage peaks at ~4GB during training, ~2GB during replay
#
# Pipeline:
#   1. Merge any live episodes into the pool
#   2. Replay historical ticks → base episodes
#   3. Train multi-target GBT on base episodes
#   4. Re-replay with GBT augmentation → hybrid episodes
#   5. Train DQN on hybrid episodes
#   6. Evaluate

set -e
LOG=/app/data/rl/pipeline.log
exec > >(tee -a "$LOG") 2>&1

# Low CPU priority — extraction always wins
renice -n 19 $$ >/dev/null 2>&1 || true

echo "=========================================="
echo "  RL TRAINING PIPELINE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "  PID: $$ (nice 19 — low priority)"
echo "=========================================="

cd /app/backend

# Step 0: Merge live episodes if any exist
echo ""
echo "[0/6] Merging live episodes..."
python -m src.app rl merge-live 2>&1 || echo "  No live episodes to merge."

# Step 1: Replay historical ticks → base episodes
echo ""
echo "[1/6] Replaying historical ticks → base episodes..."
nice -n 19 python -m src.app rl replay --all
echo "[1/6] Replay complete."

# Step 2: Train multi-target GBT
echo ""
echo "[2/6] Training multi-target GBT v3..."
nice -n 19 python -m src.app rl train-gbt --checkpoint v3 --trees 500 --depth 5 --lr 0.05
echo "[2/6] GBT v3 trained."

# Step 3: Re-replay with GBT augmentation → hybrid episodes
echo ""
echo "[3/6] Re-replaying with GBT augmentation → hybrid episodes..."
nice -n 19 python -m src.app rl replay --all --gbt gbt_v3.joblib
echo "[3/6] Augmented replay complete."

# Step 4: Train DQN on augmented episodes
echo ""
echo "[4/6] Training DQN v4 on hybrid episodes..."
nice -n 19 python -m src.app rl train --epochs 30 --checkpoint v4
echo "[4/6] DQN v4 trained."

# Step 5: Evaluate
echo ""
echo "[5/6] Evaluating DQN v4..."
python -m src.app rl eval --checkpoint v4 --skip-threshold 0.15
echo "[5/6] Evaluation complete."

# Step 6: Copy latest models for live inference
echo ""
echo "[6/6] Deploying models for live inference..."
cp -f /app/backend/data/rl/models/gbt_v3.joblib /app/backend/data/rl/models/gbt_latest.joblib 2>/dev/null || true
cp -f /app/backend/data/rl/models/dqn_v4.pt /app/backend/data/rl/models/dqn_latest.pt 2>/dev/null || true
echo "[6/6] Models deployed."

echo ""
echo "=========================================="
echo "  PIPELINE COMPLETE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "=========================================="
