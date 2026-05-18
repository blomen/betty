"""One-shot training of the first FT-Transformer model.

Reads /app/data/rl/episodes/observations.npy + reward arrays, builds
the multi-task targets, optionally splits into train/holdout, trains
on the train portion only, saves model + holdout indices.

Default split: 20% holdout, random_state=42. Holdout indices are saved
to /app/data/rl/models/ft_v1.holdout.npy so the offline backtest can
evaluate strictly on samples the model never saw.

Run inside the docker container:

    docker compose exec -T backend python /app/backend/scripts/train_ft_v1.py
    # or with custom split:
    docker compose exec -T backend python /app/backend/scripts/train_ft_v1.py --val-split 0.3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Path fix when run from any cwd inside the container
sys.path.insert(0, "/app/backend")

from src.rl.signal.training import train_ft_transformer

ep = Path("/app/data/rl/episodes")
md = Path("/app/data/rl/models")
md.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--val-split", type=float, default=0.2, help="Holdout fraction")
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--max-epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args()

    obs = np.load(ep / "observations.npy")
    rc = np.load(ep / "rewards_cont.npy")
    rr = np.load(ep / "rewards_rev.npy")
    n = len(obs)

    # Build multi-task targets from realized CONT/REV rewards.
    # direction: 0=CONT (higher reward), 1=REV, 2=SKIP (both rewards negative)
    direction = np.where(rc > rr, 0, 1).astype(np.int64)
    both_negative = (rc < 0) & (rr < 0)
    direction[both_negative] = 2

    # magnitude: realized R of the chosen action (0 for SKIP)
    magnitude = np.where(
        direction == 0,
        rc,
        np.where(direction == 1, rr, 0.0),
    ).astype(np.float32)

    # win: chosen action's reward > 0 (binary 0/1)
    win = (magnitude > 0).astype(np.int64)

    # duration: not in the existing pool — placeholder constant 5.0
    duration = np.full(n, 5.0, dtype=np.float32)

    # ---- Train/holdout split ----
    rng = np.random.default_rng(args.random_seed)
    all_idx = np.arange(n)
    rng.shuffle(all_idx)
    n_val = int(round(n * args.val_split))
    val_idx = np.sort(all_idx[:n_val])
    train_idx = np.sort(all_idx[n_val:])

    print(f"Pool: {n} episodes")
    print(f"  train: {len(train_idx)}  holdout: {len(val_idx)} ({100 * args.val_split:.0f}%)")
    print(
        f"  direction (overall): CONT={int((direction == 0).sum())} "
        f"REV={int((direction == 1).sum())} SKIP={int((direction == 2).sum())}"
    )
    print(f"  magnitude: mean={magnitude.mean():+.3f}R std={magnitude.std():.3f}R")
    print(f"  win rate (non-SKIP): {win[direction != 2].mean():.3f}")

    # Save holdout indices alongside the model so backtest can use them
    holdout_path = md / "ft_v1.holdout.npy"
    np.save(holdout_path, val_idx)
    print(f"  saved holdout indices → {holdout_path} ({len(val_idx)} samples)")

    out_path = md / "ft_v1.pt"
    train_ft_transformer(
        obs=obs[train_idx],
        direction=direction[train_idx],
        magnitude=magnitude[train_idx],
        win_outcomes=win[train_idx],
        durations=duration[train_idx],
        out_path=out_path,
        max_epochs=args.max_epochs,
        batch_size=args.batch_size,
    )
    print(f"Saved model → {out_path}")
    print("On next container restart, live_inference will auto-load this as shadow.")


if __name__ == "__main__":
    main()
