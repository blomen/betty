"""Audit how GBT uses the OF segment.

Step 1 of the GBT behavior audit. Specifically:

1. Feature importance for each of the 21 OF dims (which OF features
   the LightGBM tree-splits weighted the most)
2. L1-derived vs candle-derived split: are the post-Plan-1 dims
   (spread_ticks=6, passive_active_ratio=7) being weighted more than
   the candle-only dims?
3. Per-action OF profile: when GBT predicts CONT vs REV, what's the
   characteristic OF dim signature? (Reveals what OF patterns each
   action class is conditioned on)
4. Marginal WR by OF dim quartile: split episodes by each OF dim's
   quartile, compute realized WR within each. Identifies dims where
   high values strongly predict winning trades.

Output: text report + JSON saved to /app/data/rl/audit_reports/
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/backend")

from src.rl.agent.trigger_gbt import TriggerGBT
from src.rl.features.trigger_features import TRIGGER_SEGMENTS

EP_DIR = Path("/app/data/rl/episodes")
MD_DIR = Path("/app/data/rl/models")
OUT_DIR = Path("/app/data/rl/audit_reports")

# OF segment position in the trigger_obs (118-d) layout per trigger_features.py
# Layout: structural_passthrough(10) + micro(20) + orderflow(21) + candles(15) +
#         zone_features(4) + zone_confluence(5) + zone_composition(31) +
#         approach(1) + trigger_gbt_forecast(8) + exec_passthrough(3)
_OF_START_IN_TRIGGER = TRIGGER_SEGMENTS["structural_passthrough"] + TRIGGER_SEGMENTS["micro"]
_OF_LEN = TRIGGER_SEGMENTS["orderflow"]

# OF label names — 21 dims, must match observation_index._ORDERFLOW_LABELS
OF_LABELS = [
    "delta_pct",
    "delta_norm",
    "cvd_norm",
    "cvd_trend",
    "volume_ratio",
    "body_ratio",
    "spread_ticks",  # [L1]
    "passive_active_ratio",  # [L1]
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
assert len(OF_LABELS) == _OF_LEN, f"OF label count mismatch: {len(OF_LABELS)} vs {_OF_LEN}"

L1_DERIVED_LABELS = {"spread_ticks", "passive_active_ratio"}


def main() -> None:
    print("=" * 90)
    print("GBT OF-segment audit")
    print("=" * 90)

    trig = np.load(EP_DIR / "trigger_observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")
    n = len(trig)
    print(f"\nPool: {n} episodes, trigger_obs shape: {trig.shape}")
    print(f"OF segment range in trigger_obs: [{_OF_START_IN_TRIGGER}, {_OF_START_IN_TRIGGER + _OF_LEN}) = 21 dims")

    gbt = TriggerGBT.load(MD_DIR / "trigger_gbt_v5.joblib")
    print(f"GBT loaded: alive_mask sums to {int(gbt._alive_mask.sum())} dims")

    # ---- Part 1: Raw feature importance for OF dims ----
    print("\n" + "=" * 90)
    print("PART 1: Feature importance per OF dim (LightGBM split-gain)")
    print("=" * 90)

    # GBT's direction_model trained on dims selected by _alive_mask.
    # We need to map model-internal feature indices back to original 118-d
    # positions, then identify OF subset.
    imp_full = gbt.direction_model.feature_importances_
    alive_indices = np.where(gbt._alive_mask)[0]
    # importance for original 118-d positions
    imp_by_orig = {int(alive_indices[i]): float(imp_full[i]) for i in range(len(imp_full))}
    total_imp = sum(imp_by_orig.values())
    of_imp_total = sum(imp_by_orig.get(_OF_START_IN_TRIGGER + i, 0.0) for i in range(_OF_LEN))
    print(f"\nGlobal total importance: {total_imp:.0f}")
    print(
        f"OF segment importance total: {of_imp_total:.0f} "
        f"({100 * of_imp_total / max(total_imp, 1):.1f}% of all features)"
    )

    print(f"\n{'rank':>4} {'idx':>4} {'label':<26} {'imp':>10} {'%global':>8} {'tag':<10}")
    print("-" * 76)
    of_rows = []
    for i, label in enumerate(OF_LABELS):
        orig_idx = _OF_START_IN_TRIGGER + i
        imp_val = imp_by_orig.get(orig_idx, 0.0)
        tag = "[L1]" if label in L1_DERIVED_LABELS else "[candle]"
        of_rows.append((i, orig_idx, label, imp_val, tag))
    # Sort by importance desc
    of_rows_sorted = sorted(of_rows, key=lambda r: -r[3])
    for rank, (i, orig_idx, label, imp_val, tag) in enumerate(of_rows_sorted, 1):
        pct = 100 * imp_val / max(total_imp, 1)
        print(f"{rank:>4} {i:>4} {label:<26} {imp_val:>10.0f} {pct:>7.2f}% {tag:<10}")

    dead_of_dims = [r for r in of_rows if r[3] == 0]
    if dead_of_dims:
        print(f"\nDEAD OF dims (zero importance): {len(dead_of_dims)}")
        for _, orig_idx, label, _, tag in dead_of_dims:
            print(f"  - {label:<26} {tag}")

    # ---- Part 2: L1-derived vs candle-derived split ----
    print("\n" + "=" * 90)
    print("PART 2: L1-derived (spread_ticks, passive_active_ratio) vs candle-derived")
    print("=" * 90)

    l1_imp = sum(imp for *_, label, imp, tag in of_rows if label in L1_DERIVED_LABELS)
    candle_imp = sum(imp for *_, label, imp, tag in of_rows if label not in L1_DERIVED_LABELS)
    print(f"\n  L1-derived (2 dims):    {l1_imp:>10.0f} ({100 * l1_imp / max(of_imp_total, 1):.1f}% of OF)")
    print(f"  Candle-derived (19 dims): {candle_imp:>10.0f} ({100 * candle_imp / max(of_imp_total, 1):.1f}% of OF)")
    print(
        f"  Per-dim avg: L1 {l1_imp / 2:.0f}/dim, candle {candle_imp / 19:.0f}/dim "
        f"({(l1_imp / 2) / max(candle_imp / 19, 1):.2f}x ratio)"
    )

    # ---- Part 3: Per-action OF profile ----
    print("\n" + "=" * 90)
    print("PART 3: Per-action OF profile — mean OF dim value when GBT picks each action")
    print("=" * 90)

    print("\nRunning GBT predictions on all episodes...")
    actions, confs, probs = gbt.predict_direction_batch(trig.astype(np.float32))
    print(f"Action distribution: CONT={int((actions == 0).sum())} REV={int((actions == 1).sum())}")

    of_cols = trig[:, _OF_START_IN_TRIGGER : _OF_START_IN_TRIGGER + _OF_LEN]
    cont_mask = actions == 0
    rev_mask = actions == 1

    print(f"\n{'idx':>4} {'label':<26} {'CONT mean':>10} {'REV mean':>10} {'diff':>8} {'effect':<8}")
    print("-" * 70)
    for i, label in enumerate(OF_LABELS):
        col = of_cols[:, i]
        if cont_mask.sum() == 0 or rev_mask.sum() == 0:
            continue
        cont_mean = float(col[cont_mask].mean())
        rev_mean = float(col[rev_mask].mean())
        diff = cont_mean - rev_mean
        effect = "→CONT" if diff > 0.01 else ("→REV" if diff < -0.01 else "neutral")
        print(f"{i:>4} {label:<26} {cont_mean:>+10.3f} {rev_mean:>+10.3f} {diff:>+8.3f} {effect:<8}")

    # ---- Part 4: Marginal WR by OF dim quartile ----
    print("\n" + "=" * 90)
    print("PART 4: Realized WR by OF dim quartile (high-vs-low predictive power)")
    print("=" * 90)
    print("\nFor each OF dim: split episodes into quartiles by the dim's value,")
    print("compute realized WR within each. Big spread between Q4 and Q1 = strong predictor.\n")

    realized = np.where(actions == 0, rc, rr)
    win = (realized > 0).astype(np.float32)

    print(f"{'idx':>4} {'label':<26} {'Q1 WR':>7} {'Q4 WR':>7} {'Q4-Q1':>8} {'tag':<10}")
    print("-" * 70)
    margins: list[tuple[float, str]] = []
    for i, label in enumerate(OF_LABELS):
        col = of_cols[:, i]
        if col.std() < 1e-8:
            print(f"{i:>4} {label:<26} {'--':>7} {'--':>7} {'--':>8} dead-col")
            continue
        q1, q4 = np.percentile(col, [25, 75])
        q1_mask = col <= q1
        q4_mask = col >= q4
        if q1_mask.sum() == 0 or q4_mask.sum() == 0:
            continue
        q1_wr = float(win[q1_mask].mean())
        q4_wr = float(win[q4_mask].mean())
        margin = q4_wr - q1_wr
        tag = "[L1]" if label in L1_DERIVED_LABELS else "[candle]"
        print(f"{i:>4} {label:<26} {100 * q1_wr:>6.1f}% {100 * q4_wr:>6.1f}% {100 * margin:>+7.1f}pt {tag:<10}")
        margins.append((margin, label))

    margins_sorted = sorted(margins, key=lambda m: -abs(m[0]))
    print("\nTop 5 strongest predictors (by |Q4-Q1| margin):")
    for margin, label in margins_sorted[:5]:
        print(f"  {label:<26} margin {100 * margin:+.1f}pt")

    # ---- Save JSON ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"gbt_of_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": n,
        "of_importance": [
            {
                "idx": i,
                "label": label,
                "orig_idx": _OF_START_IN_TRIGGER + i,
                "importance": imp,
                "is_l1": label in L1_DERIVED_LABELS,
            }
            for i, _, label, imp, _ in of_rows
        ],
        "of_importance_total": of_imp_total,
        "global_importance_total": total_imp,
        "of_pct_of_global": 100 * of_imp_total / max(total_imp, 1),
        "l1_vs_candle_pct": {
            "l1_pct_of_of": 100 * l1_imp / max(of_imp_total, 1),
            "candle_pct_of_of": 100 * candle_imp / max(of_imp_total, 1),
            "l1_per_dim_avg": l1_imp / 2,
            "candle_per_dim_avg": candle_imp / 19,
        },
        "quartile_margins": [{"label": label, "q4_minus_q1_wr": margin} for margin, label in margins_sorted],
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
