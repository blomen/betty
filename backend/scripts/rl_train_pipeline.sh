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

set -e
LOG=/app/data/rl/pipeline.log
exec > >(tee -a "$LOG") 2>&1

renice -n 19 $$ >/dev/null 2>&1 || true

echo "=========================================="
echo "  RL TRAINING PIPELINE v5 — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "  PID: $$ (nice 19 — low priority)"
echo "=========================================="

cd /app/backend

# Step 0: Merge live episodes
echo ""
echo "[0/8] Merging live episodes..."
python -m src.app rl merge-live 2>&1 || echo "  No live episodes to merge."

# Step 1: Parallel replay → base episodes
echo ""
echo "[1/8] Replaying historical ticks → base episodes..."
nice -n 19 python -m src.app rl replay --all
echo "[1/8] Replay complete."

# Step 2: Label setups
echo ""
echo "[2/8] Labeling episodes with setup types..."
python -m src.app rl label-setups
echo "[2/8] Setup labeling complete."

# Step 3: Train Narrative GBT
echo ""
echo "[3/8] Training Narrative GBT v5..."
nice -n 19 python -m src.app rl train-narrative-gbt --checkpoint v5 --trees 500 --depth 5 --lr 0.05
echo "[3/8] Narrative GBT trained."

# Step 4: Train Trigger GBT
echo ""
echo "[4/8] Training Trigger GBT v5..."
nice -n 19 python -m src.app rl train-trigger-gbt --checkpoint v5 --trees 1000 --depth 6 --lr 0.05
echo "[4/8] Trigger GBT trained."

# Step 5: Re-replay with GBT augmentation → hybrid trigger episodes
echo ""
echo "[5/8] Re-replaying with GBT augmentation → hybrid trigger episodes..."
nice -n 19 python -m src.app rl replay --all --gbt trigger_gbt_v5.joblib
echo "[5/8] Augmented replay complete."

# Step 6: Train Trigger DQN
echo ""
echo "[6/8] Training Trigger DQN v5 (30 epochs, batch 4096)..."
nice -n 19 python -m src.app rl train --epochs 30 --checkpoint v5
echo "[6/8] DQN v5 trained."

# Step 7: Evaluate
echo ""
echo "[7/8] Evaluating DQN v5..."
python -m src.app rl eval --checkpoint v5 --skip-threshold 0.15
echo "[7/8] Evaluation complete."

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
