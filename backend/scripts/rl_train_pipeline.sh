#!/bin/bash
# Full RL training pipeline — runs unattended on the server.
# Usage: docker exec -d firev-backend-1 bash /app/backend/scripts/rl_train_pipeline.sh
#
# Pipeline:
#   1. Replay historical ticks → 276-dim episodes
#   2. Train multi-target GBT v3 on 276-dim
#   3. Re-replay with GBT augmentation → 292-dim episodes
#   4. Train DQN v4 on 292-dim (hybrid GBT+DQN)
#   5. Evaluate DQN v4

set -e
LOG=/app/data/rl/pipeline.log
exec > >(tee -a "$LOG") 2>&1

echo "=========================================="
echo "  RL TRAINING PIPELINE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "=========================================="

cd /app/backend

# Step 1: Replay (skip if episodes already exist and are fresh)
echo ""
echo "[1/5] Replaying historical ticks → base episodes..."
python -m src.app rl replay --all
echo "[1/5] Replay complete."

# Step 2: Train multi-target GBT
echo ""
echo "[2/5] Training multi-target GBT v3..."
python -m src.app rl train-gbt --checkpoint v3 --trees 500 --depth 5 --lr 0.05
echo "[2/5] GBT v3 trained."

# Step 3: Re-replay with GBT augmentation
echo ""
echo "[3/5] Re-replaying with GBT augmentation → hybrid episodes..."
python -m src.app rl replay --all --gbt gbt_v3.joblib
echo "[3/5] Augmented replay complete."

# Step 4: Train DQN on augmented episodes
echo ""
echo "[4/5] Training DQN v4 on hybrid episodes..."
python -m src.app rl train --epochs 20 --checkpoint v4
echo "[4/5] DQN v4 trained."

# Step 5: Evaluate
echo ""
echo "[5/5] Evaluating DQN v4..."
python -m src.app rl eval --checkpoint v4 --skip-threshold 0.15
echo "[5/5] Evaluation complete."

echo ""
echo "=========================================="
echo "  PIPELINE COMPLETE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "=========================================="
