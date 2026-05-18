"""One-shot training of the first FT-Transformer model.

Reads /app/data/rl/episodes/observations.npy + reward arrays, builds
the multi-task targets, runs the training loop, saves to
/app/data/rl/models/ft_v1.pt where live_inference auto-loads it as shadow.

GPU required if available (will fall back to CPU; expect ~10x slower).
Run inside the docker container:

    docker compose exec -T backend python /app/backend/scripts/train_ft_v1.py
"""

from __future__ import annotations

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
    obs = np.load(ep / "observations.npy")
    rc = np.load(ep / "rewards_cont.npy")
    rr = np.load(ep / "rewards_rev.npy")

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
    # (Plan 2 spec acknowledges this; duration head won't learn meaningful
    # signal until episode_builder exports bars-to-exit.)
    duration = np.full(len(obs), 5.0, dtype=np.float32)

    print(f"Training FT-Transformer on {len(obs)} episodes...")
    print(
        f"  direction distribution: CONT={int((direction == 0).sum())} "
        f"REV={int((direction == 1).sum())} SKIP={int((direction == 2).sum())}"
    )
    print(f"  magnitude: mean={magnitude.mean():+.3f}R std={magnitude.std():.3f}R")
    print(f"  win rate (non-SKIP): {win[direction != 2].mean():.3f}")

    out_path = md / "ft_v1.pt"
    train_ft_transformer(
        obs=obs,
        direction=direction,
        magnitude=magnitude,
        win_outcomes=win,
        durations=duration,
        out_path=out_path,
        max_epochs=20,
        batch_size=128,
    )
    print(f"Saved to {out_path}")
    print("On next container restart, live_inference will auto-load this as shadow.")


if __name__ == "__main__":
    main()
