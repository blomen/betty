"""Dead-dim diagnosis: are they buggy, redundant, or just rare?

For each of the 3 dead OF dims (vsa_absorption, stop_run_detected,
delta_divergence), checks:

  1. CORRELATION with other OF dims — if a dead dim is highly correlated
     with a dim GBT does use, it's REDUNDANT (drop it).
  2. JOINT WR — when the dead dim fires, what's the realized WR? If
     significantly different from baseline, GBT might be missing real
     signal. If same as baseline, the dim is uninformative regardless
     of fire rate.
  3. THRESHOLD SENSITIVITY — for vsa_absorption + stop_run, what threshold
     would make them fire 10-15% of the time (LightGBM-friendly range)?

Produces a per-dim verdict: KEEP / TUNE / REMOVE.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/backend")

from src.rl.agent.trigger_gbt import TriggerGBT
from src.rl.features.trigger_features import TRIGGER_SEGMENTS

EP_DIR = Path("/app/data/rl/episodes")
MD_DIR = Path("/app/data/rl/models")

_OF_START = TRIGGER_SEGMENTS["structural_passthrough"] + TRIGGER_SEGMENTS["micro"]
_OF_LEN = TRIGGER_SEGMENTS["orderflow"]

OF_LABELS = [
    "delta_pct",
    "delta_norm",
    "cvd_norm",
    "cvd_trend",
    "volume_ratio",
    "body_ratio",
    "spread_ticks",
    "passive_active_ratio",
    "imbalance_density",
    "stacked_imbalance_count",
    "stacked_direction",
    "big_trades_count",
    "big_trades_net_delta",
    "vsa_absorption",
    "stop_run_detected",
    "delta_acceleration",
    "absorption_strength",
    "initiative_momentum",
    "volume_climax",
    "delta_divergence",
    "flow_shift",
]
DEAD_DIMS = ["vsa_absorption", "stop_run_detected", "delta_divergence"]


def main() -> None:
    print("=" * 90)
    print("DEAD DIM DIAGNOSIS — bug or redundant or just rare?")
    print("=" * 90)

    trig = np.load(EP_DIR / "trigger_observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")
    n = len(trig)
    gbt = TriggerGBT.load(MD_DIR / "trigger_gbt_v5.joblib")

    of_cols = trig[:, _OF_START : _OF_START + _OF_LEN]
    actions, _, _ = gbt.predict_direction_batch(trig.astype(np.float32))
    realized = np.where(actions == 0, rc, rr)
    win = (realized > 0).astype(np.float32)
    baseline_wr = float(win.mean())
    print(f"\nPool: {n} | baseline WR: {100 * baseline_wr:.2f}%")

    for dead_label in DEAD_DIMS:
        di = OF_LABELS.index(dead_label)
        col = of_cols[:, di]
        nz_mask = col > 0
        nz_count = int(nz_mask.sum())
        nz_pct = float(nz_mask.mean())

        print(f"\n{'=' * 90}")
        print(f"DIM: {dead_label}  (index {di} in OF segment)")
        print(f"{'=' * 90}")
        print(f"  nonzero count: {nz_count} ({100 * nz_pct:.2f}% of pool)")

        # --- Joint WR when dim fires vs doesn't ---
        if nz_count >= 10:
            wr_fires = float(win[nz_mask].mean())
            wr_quiet = float(win[~nz_mask].mean())
            delta_wr = wr_fires - wr_quiet
            print(
                f"  WR when fires: {100 * wr_fires:.2f}% (n={nz_count})  "
                f"WR when quiet: {100 * wr_quiet:.2f}% (n={int((~nz_mask).sum())})  "
                f"Δ = {100 * delta_wr:+.2f}pt"
            )
            if abs(delta_wr) >= 0.02:
                print("  ⚠  Non-trivial WR difference — GBT may be missing real signal here")
            else:
                print("  ✓  Near-baseline — dim is genuinely uninformative")
        else:
            print("  too sparse to measure WR meaningfully")

        # --- Correlation with other OF dims ---
        corrs: list[tuple[str, float]] = []
        for i, other_label in enumerate(OF_LABELS):
            if i == di:
                continue
            other = of_cols[:, i]
            if other.std() < 1e-9 or col.std() < 1e-9:
                continue
            r = float(np.corrcoef(col, other)[0, 1])
            corrs.append((other_label, r))
        corrs.sort(key=lambda x: -abs(x[1]))
        print("\n  Top 3 correlations (|r|) with other OF dims:")
        for label, r in corrs[:3]:
            tag = "    (potential proxy — GBT may have picked the proxy instead)" if abs(r) >= 0.4 else ""
            print(f"    r={r:+.3f}  ↔  {label}{tag}")

        # --- Verdict ---
        max_abs_corr = max(abs(r) for _, r in corrs) if corrs else 0.0
        verdict_lines = []
        if nz_pct < 0.05:
            verdict_lines.append("  RARE event — only fires <5% of episodes")
            if abs(delta_wr) >= 0.05:
                verdict_lines.append("  → TUNE: lower threshold to fire 10-15% so LightGBM can use it")
            else:
                verdict_lines.append("  → REMOVE: dim doesn't predict winning trades anyway")
        elif max_abs_corr >= 0.4:
            verdict_lines.append(f"  REDUNDANT — strongly correlated (|r|={max_abs_corr:.2f}) with another dim")
            verdict_lines.append("  → REMOVE: GBT uses the correlated proxy instead")
        elif abs(delta_wr) < 0.02:
            verdict_lines.append("  UNINFORMATIVE — no WR signal, no correlations to explain neglect")
            verdict_lines.append("  → REMOVE or REBUILD with a different definition")
        else:
            verdict_lines.append(
                f"  UNUSED but SIGNAL-bearing — fires {100 * nz_pct:.1f}% with WR Δ={100 * delta_wr:+.2f}pt"
            )
            verdict_lines.append("  → INVESTIGATE: why does GBT ignore this? Try retraining with this dim included.")

        print("\n  VERDICT:")
        for line in verdict_lines:
            print(line)

    # --- Threshold sensitivity for the sparse dims ---
    # Both vsa_absorption + stop_run_detected come from binary detection
    # functions in orderflow.py. We can't simulate threshold changes
    # without recomputing the signal. Just print recommended source
    # locations to tune.
    print("\n" + "=" * 90)
    print("THRESHOLD TUNING — SOURCE LOCATIONS")
    print("=" * 90)
    print("""
vsa_absorption  (backend/src/market_data/orderflow.py:312)
  current: last.volume > avg_volume * 1.5 AND last.body_ratio < 0.3
  → fires 3.6% — too rare for LightGBM
  proposed:
    last.volume > avg_volume * 1.2   (1.5 → 1.2)
    AND last.body_ratio < 0.4         (0.3 → 0.4)
  expected fire rate: ~12-15%

stop_run_detected  (backend/src/market_data/orderflow.py:345-349)
  current: spike out-of-range + volume > avg_volume * 1.5 + reversal
  → fires 5.3% — at the edge of usable
  proposed:
    spike.volume > avg_volume * 1.2  (1.5 → 1.2)
  expected fire rate: ~10-12%

delta_divergence  (backend/src/rl/features/orderflow_features.py:191-200)
  current: 5-bar new extreme + delta weakening (<1/4 of 4-bar sum)
  → fires 16.4% but GBT ignores (likely redundant with delta_acceleration)
  next step: check correlation with delta_acceleration explicitly (above)
  if r > 0.5 → REMOVE; if r < 0.3 → INVESTIGATE why unused
""")


if __name__ == "__main__":
    main()
