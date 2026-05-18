"""R-focused OF audit — runner strategy framework.

Replaces the WR-based audit. The Arnold strategy is runner-capture:
  - Phase 1: SL placed from dims, target 1.5R (max loss = -1R)
  - At 1.5R: BE-lock (worst case = flat-plus-pennies)
  - Phase 2: trail SL below each crossed zone, ride until reversal
With this structure, WR is misleading. A 40% WR with +1.5R mean
is BETTER than 55% WR with +0.3R mean. What matters:
  1. Mean R per trade (expectancy)
  2. Runner rate (frequency of big winners)
  3. Asymmetry (avg win vs avg loss magnitude)
  4. Tail capture (does the dim find +2.5R cap-hits?)

Reward labels are capped at [-1.5, +2.5] per episode_builder.py:433-436.
Cap hits at +2.5 are the proxy for "would have been a real runner".

Output: per-dim R leaderboard + dead-dim re-evaluation under R lens.
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
L1_DERIVED_LABELS = {"spread_ticks", "passive_active_ratio"}
DEAD_DIMS = {"vsa_absorption", "stop_run_detected", "delta_divergence"}

# Reward cap thresholds per episode_builder
REWARD_MAX_CAP = 2.5
REWARD_MIN_CAP = -1.5
# "Runner" = realized R within 90% of cap (≥ 2.25R) — proxy for setups
# that genuinely had follow-through and would have continued beyond the cap
RUNNER_THRESHOLD = REWARD_MAX_CAP * 0.9


def _r_profile(realized: np.ndarray) -> dict:
    """Per-dim R metrics for one subset of episodes."""
    n = len(realized)
    if n == 0:
        return {"n": 0}
    winners = realized[realized > 0]
    losers = realized[realized < 0]
    runners = realized[realized >= RUNNER_THRESHOLD]
    cap_hits = realized[realized >= REWARD_MAX_CAP - 1e-6]
    max_losses = realized[realized <= REWARD_MIN_CAP + 1e-6]
    return {
        "n": n,
        "mean_R": float(realized.mean()),
        "median_R": float(np.median(realized)),
        "p10": float(np.percentile(realized, 10)),
        "p90": float(np.percentile(realized, 90)),
        "winners_n": len(winners),
        "losers_n": len(losers),
        "wr": float(len(winners) / n),
        "avg_win": float(winners.mean()) if len(winners) else 0.0,
        "avg_loss": float(losers.mean()) if len(losers) else 0.0,
        "asymmetry": float(winners.mean() / abs(losers.mean())) if len(losers) > 0 and losers.mean() < 0 else 0.0,
        "runner_rate": float(len(runners) / n),
        "cap_hit_rate": float(len(cap_hits) / n),
        "max_loss_rate": float(len(max_losses) / n),
    }


def main() -> None:
    print("=" * 100)
    print("R-focused OF audit (runner strategy)")
    print("=" * 100)

    trig = np.load(EP_DIR / "trigger_observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")
    n = len(trig)
    gbt = TriggerGBT.load(MD_DIR / "trigger_gbt_v5.joblib")

    of_cols = trig[:, _OF_START : _OF_START + _OF_LEN]
    actions, _, _ = gbt.predict_direction_batch(trig.astype(np.float32))
    realized = np.where(actions == 0, rc, rr)

    base = _r_profile(realized)
    print(f"\nPool: {n} episodes | reward range: [{REWARD_MIN_CAP}, +{REWARD_MAX_CAP}]")
    print(
        f"BASELINE: mean R={base['mean_R']:+.3f}  median={base['median_R']:+.3f}  "
        f"WR={100 * base['wr']:.1f}%  asym={base['asymmetry']:.2f}  "
        f"runner_rate={100 * base['runner_rate']:.1f}%  cap_hits={100 * base['cap_hit_rate']:.1f}%"
    )

    # ============ R metrics by dim Q1 vs Q4 ============
    print("\n" + "=" * 100)
    print("R-METRICS BY DIM QUARTILE — Q4 (high) vs Q1 (low) per OF dim")
    print("=" * 100)
    print("\nDim    Label                       mean_R Q1→Q4    runner% Q1→Q4   asym Q1→Q4    cap_hit% Q4   tag")
    print("-" * 110)

    leaderboard = []
    for i, label in enumerate(OF_LABELS):
        col = of_cols[:, i]
        if col.std() < 1e-8:
            continue
        q1, q4 = np.percentile(col, [25, 75])
        q1_mask = col <= q1
        q4_mask = col >= q4
        p1 = _r_profile(realized[q1_mask])
        p4 = _r_profile(realized[q4_mask])
        if p1["n"] == 0 or p4["n"] == 0:
            continue
        meanR_delta = p4["mean_R"] - p1["mean_R"]
        runner_delta = p4["runner_rate"] - p1["runner_rate"]
        tag = "[L1]" if label in L1_DERIVED_LABELS else ("[dead]" if label in DEAD_DIMS else "")
        print(
            f"{i:>3}    {label:<26} {p1['mean_R']:+.3f}→{p4['mean_R']:+.3f} ({meanR_delta:+.3f})    "
            f"{100 * p1['runner_rate']:4.1f}%→{100 * p4['runner_rate']:4.1f}% ({100 * runner_delta:+.1f}pt)   "
            f"{p1['asymmetry']:.2f}→{p4['asymmetry']:.2f}    {100 * p4['cap_hit_rate']:4.1f}%   {tag}"
        )
        leaderboard.append(
            {
                "label": label,
                "meanR_q1": p1["mean_R"],
                "meanR_q4": p4["mean_R"],
                "meanR_delta": meanR_delta,
                "runner_q1": p1["runner_rate"],
                "runner_q4": p4["runner_rate"],
                "runner_delta": runner_delta,
                "asym_q4": p4["asymmetry"],
                "cap_hit_q4": p4["cap_hit_rate"],
                "tag": tag,
            }
        )

    # ============ LEADERBOARDS ============
    print("\n" + "=" * 100)
    print("LEADERBOARDS — Q4 (high-value-of-dim) predictive power")
    print("=" * 100)

    print("\nTop 10 by mean-R Δ (Q4 - Q1) — expectancy lift when dim is high:")
    for r in sorted(leaderboard, key=lambda x: -x["meanR_delta"])[:10]:
        print(f"  {r['label']:<26} {r['meanR_delta']:+.3f}R   (Q4 mean {r['meanR_q4']:+.3f}R)   {r['tag']}")

    print("\nBottom 10 by mean-R Δ — high-dim signals SHOULD-NOT-TRADE:")
    for r in sorted(leaderboard, key=lambda x: x["meanR_delta"])[:10]:
        print(f"  {r['label']:<26} {r['meanR_delta']:+.3f}R   (Q4 mean {r['meanR_q4']:+.3f}R)   {r['tag']}")

    print("\nTop 10 by runner-rate at Q4 — dims that find +2.25R+ trades:")
    for r in sorted(leaderboard, key=lambda x: -x["runner_q4"])[:10]:
        print(
            f"  {r['label']:<26} runner@Q4={100 * r['runner_q4']:5.1f}%   (Q1 {100 * r['runner_q1']:.1f}%, Δ={100 * r['runner_delta']:+.1f}pt)   {r['tag']}"
        )

    # ============ DEAD-DIM RE-EVALUATION ============
    print("\n" + "=" * 100)
    print("DEAD DIM RE-EVALUATION — under R lens, not WR")
    print("=" * 100)
    print(
        "\nFor each 'dead' dim (0 GBT importance), check what the R-profile looks like"
        "\nwhen the dim fires (binary 0/1). If asymmetric or runner-heavy, the dim has"
        "\nreal value the WR-based audit missed.\n"
    )
    for label in DEAD_DIMS:
        di = OF_LABELS.index(label)
        col = of_cols[:, di]
        fires_mask = col > 0
        if fires_mask.sum() < 10:
            print(f"  {label:<26} too sparse (only {int(fires_mask.sum())} fires)")
            continue
        pf = _r_profile(realized[fires_mask])
        pq = _r_profile(realized[~fires_mask])
        print(f"  {label:<26} fires {100 * fires_mask.mean():5.2f}% of pool")
        print(
            f"    when fires: mean R={pf['mean_R']:+.3f}  WR={100 * pf['wr']:.1f}%  "
            f"asym={pf['asymmetry']:.2f}  runner_rate={100 * pf['runner_rate']:.1f}%  "
            f"avg_win={pf['avg_win']:+.3f}  avg_loss={pf['avg_loss']:+.3f}"
        )
        print(
            f"    when quiet: mean R={pq['mean_R']:+.3f}  WR={100 * pq['wr']:.1f}%  "
            f"asym={pq['asymmetry']:.2f}  runner_rate={100 * pq['runner_rate']:.1f}%"
        )
        # R-lens verdict
        mean_delta = pf["mean_R"] - pq["mean_R"]
        runner_delta = pf["runner_rate"] - pq["runner_rate"]
        asym_delta = pf["asymmetry"] - pq["asymmetry"]
        if abs(mean_delta) >= 0.1 or abs(runner_delta) >= 0.03:
            print(
                f"    ⚠  REAL SIGNAL: mean_R Δ={mean_delta:+.3f}R, runner_rate Δ={100 * runner_delta:+.2f}pt, asym Δ={asym_delta:+.2f}"
            )
            if mean_delta < 0:
                print("        → use as ANTI-signal / SKIP filter (high value = avoid)")
            else:
                print("        → use as PRO-signal (high value = trade more aggressively)")
        else:
            print("    ✓  uninformative under R lens too — safe to remove or rebuild")
        print()

    # ============ Save report ============
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"gbt_of_R_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": n,
        "reward_caps": [REWARD_MIN_CAP, REWARD_MAX_CAP],
        "runner_threshold": RUNNER_THRESHOLD,
        "baseline": base,
        "leaderboard": leaderboard,
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
