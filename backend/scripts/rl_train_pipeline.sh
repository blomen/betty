#!/bin/bash
# Full RL training pipeline — runs unattended on the server.
#
# Usage:
#   docker exec -d firev-backend-1 bash /app/backend/scripts/rl_train_pipeline.sh
#
# Resource management:
#   - All RL work runs at low CPU priority (nice 19) so extraction always wins
#   - Replay uses half the CPU cores (--workers 0 = auto)
#   - GBT training via LightGBM (~30s, all cores burst)
#   - DQN training uses 1 core + ~4GB RAM
#
# Pipeline:
#   0. Merge live episodes
#   1. Replay historical ticks → base episodes (parallel)
#   2. Train multi-target GBT on base episodes
#   3. Re-replay with GBT augmentation → hybrid episodes (parallel)
#   4. Train DQN on hybrid episodes
#   5. Evaluate
#   6. Deploy models

set -e
LOG=/app/data/rl/pipeline.log
exec > >(tee -a "$LOG") 2>&1

renice -n 19 $$ >/dev/null 2>&1 || true

echo "=========================================="
echo "  RL TRAINING PIPELINE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "  PID: $$ (nice 19 — low priority)"
echo "=========================================="

cd /app/backend

# Step 0: Merge live episodes
echo ""
echo "[0/6] Merging live episodes..."
python -m src.app rl merge-live 2>&1 || echo "  No live episodes to merge."

# Step 1: Parallel replay → base episodes
echo ""
echo "[1/6] Replaying historical ticks → base episodes (parallel)..."
nice -n 19 python -m src.app rl replay --all
echo "[1/6] Replay complete."

# Step 2: Train multi-target GBT (LightGBM, ~30s)
echo ""
echo "[2/6] Training multi-target GBT v3 (LightGBM)..."
nice -n 19 python -m src.app rl train-gbt --checkpoint v3 --trees 1000 --depth 6 --lr 0.05
echo "[2/6] GBT v3 trained."

# Step 3: Re-replay with GBT augmentation (parallel)
echo ""
echo "[3/6] Re-replaying with GBT augmentation → hybrid episodes (parallel)..."
nice -n 19 python -m src.app rl replay --all --gbt gbt_v3.joblib
echo "[3/6] Augmented replay complete."

# Step 4: Train DQN on hybrid episodes (bigger batch, more epochs)
echo ""
echo "[4/6] Training DQN v4 on hybrid episodes (30 epochs, batch 1024)..."
nice -n 19 python -m src.app rl train --epochs 30 --checkpoint v4
echo "[4/6] DQN v4 trained."

# Step 5: Evaluate
echo ""
echo "[5/6] Evaluating DQN v4..."
python -m src.app rl eval --checkpoint v4 --skip-threshold 0.15
echo "[5/6] Evaluation complete."

# Step 6: Deploy models for live inference
echo ""
echo "[6/6] Deploying models..."
cp -f /app/backend/data/rl/models/gbt_v3.joblib /app/backend/data/rl/models/gbt_latest.joblib 2>/dev/null || true
cp -f /app/backend/data/rl/models/dqn_v4.pt /app/backend/data/rl/models/dqn_latest.pt 2>/dev/null || true
echo "[6/6] Models deployed."

echo ""
echo "=========================================="
echo "  PIPELINE COMPLETE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "=========================================="
