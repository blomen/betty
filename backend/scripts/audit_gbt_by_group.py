"""GROUP-level audit of GBT behavior — methodology-category lens.

User directive: "audit the dims in group relation to model behavior."
Per-group view of GBT predictions, not per-dim. The 9 methodology
categories (OF, VSA, PROFILE, AMT, DOW_STRUCTURE, MICRO, ZONE_MEMORY,
MACRO, EXECUTION) are the canonical unit of behavior analysis since
Plan 1/2.

For each group:
  1. EXISTS-IN-TRIGGER coverage — does GBT even see this category?
     (trigger_obs is 118-d subset; some methodology groups have 0 dims
     represented. Those are STRUCTURAL BLIND SPOTS for GBT.)
  2. R-impact when group is "hot" (Q4 of mean-|dim|) vs "cold" (Q1)
  3. Runner rate when group is hot
  4. Action distribution by group strength (does GBT lean CONT or REV
     when this group is hot?)
  5. Per-group SHAP contribution (total across all dims in group)

Output: leaderboard ranking groups by predictive power + structural
gaps to feed the next-segment audit cycle.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/backend")

from src.rl.agent.trigger_gbt import TriggerGBT
from src.rl.features.observation_index import (
    _CATEGORY_SEGMENTS,
    _SEGMENT_OFFSETS,
)

EP_DIR = Path("/app/data/rl/episodes")
MD_DIR = Path("/app/data/rl/models")
OUT_DIR = Path("/app/data/rl/audit_reports")

REWARD_MAX_CAP = 2.5
RUNNER_THRESHOLD = REWARD_MAX_CAP * 0.9

# Trigger_obs layout (118-d, in order):
#   0:10    structural_passthrough   (top features from structure/tpo/amt_dynamics)
#   10:30   micro                    (MICRO category)
#   30:51   orderflow                (OF category)
#   51:66   candles                  (VSA category)
#   66:70   zone_features            (PROFILE category)
#   70:75   zone_confluence          (PROFILE category)
#   75:106  zone_composition         (PROFILE category)
#  106:107  approach                 (EXECUTION category)
#  107:115  trigger_gbt_forecast     (model self-forecast)
#  115:118  exec_passthrough         (EXECUTION category)

# Map methodology category → (start, end) in trigger_obs (or None if absent)
TRIGGER_GROUP_RANGES: dict[str, tuple[int, int] | None] = {
    "OF": (30, 51),
    "VSA": (51, 66),
    "PROFILE": (66, 106),  # zone_features + zone_confluence + zone_composition
    "MICRO": (10, 30),
    "EXECUTION": (106, 107),  # approach only; exec_passthrough is special
    # Passthrough subset (10 dims at start) mixes structure/tpo/amt — partial coverage
    "DOW_STRUCTURE": (0, 5),  # approx — top 5 of passthrough come from structure
    "PROFILE_TPO": (5, 8),  # 3 tpo passthrough dims (overlap with PROFILE — separate label)
    "AMT": (8, 10),  # 2 amt_dynamics passthrough dims
    # NOT IN TRIGGER:
    "ZONE_MEMORY": None,  # GBT BLIND
    "MACRO": None,  # GBT BLIND
}


def _r_metrics(realized: np.ndarray) -> dict:
    if len(realized) == 0:
        return {"n": 0}
    winners = realized[realized > 0]
    losers = realized[realized < 0]
    runners = realized[realized >= RUNNER_THRESHOLD]
    return {
        "n": len(realized),
        "mean_R": float(realized.mean()),
        "wr": float(len(winners) / len(realized)),
        "runner_rate": float(len(runners) / len(realized)),
        "asym": float(winners.mean() / abs(losers.mean())) if len(losers) > 0 and losers.mean() < 0 else 0.0,
    }


def main() -> None:
    print("=" * 100)
    print("GROUP-level GBT audit — methodology categories vs model behavior")
    print("=" * 100)

    trig = np.load(EP_DIR / "trigger_observations.npy")
    obs_full = np.load(EP_DIR / "observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")
    n = len(trig)
    gbt = TriggerGBT.load(MD_DIR / "trigger_gbt_v5.joblib")

    actions, confs, _ = gbt.predict_direction_batch(trig.astype(np.float32))
    realized = np.where(actions == 0, rc, rr)
    base = _r_metrics(realized)
    print(f"\nPool: {n} episodes | trigger_obs: {trig.shape} | full_obs: {obs_full.shape}")
    print(
        f"BASELINE: mean_R={base['mean_R']:+.3f}  WR={100 * base['wr']:.1f}%  runner_rate={100 * base['runner_rate']:.1f}%  asym={base['asym']:.2f}"
    )

    # ---- PART A: STRUCTURAL COVERAGE ----
    print("\n" + "=" * 100)
    print("PART A: Group coverage — what GBT sees vs what the full obs has")
    print("=" * 100)
    print(f"\n{'Category':<16} {'dims (full)':>12} {'dims (trigger)':>16} {'coverage':<25}")
    print("-" * 75)
    coverage_findings = []
    for cat, segs in sorted(_CATEGORY_SEGMENTS.items()):
        full_dims = sum(s["size"] for s in segs)
        trigger_range = TRIGGER_GROUP_RANGES.get(cat)
        if trigger_range is None:
            trigger_dims = 0
            coverage = "GBT BLIND ❌"
        else:
            trigger_dims = trigger_range[1] - trigger_range[0]
            pct = 100 * trigger_dims / full_dims if full_dims else 0
            coverage = f"{trigger_dims}/{full_dims} = {pct:.0f}%"
        print(f"{cat:<16} {full_dims:>12} {trigger_dims:>16} {coverage:<25}")
        coverage_findings.append({"cat": cat, "full_dims": full_dims, "trigger_dims": trigger_dims})

    # ---- PART B: PER-GROUP R IMPACT (on trigger_obs subset that GBT sees) ----
    print("\n" + "=" * 100)
    print("PART B: GBT R-impact per group (on trigger_obs subset)")
    print("=" * 100)
    print("\nFor each group: mean of |dim values| → split episodes by Q4 vs Q1.")
    print("Compare R metrics. Big Q4-Q1 delta = group strength predicts outcomes.\n")
    print(
        f"{'Category':<16} {'mean_R Q1':>10} {'mean_R Q4':>10} {'ΔR':>8} {'runner% Q4':>11} {'Δrunner':>9} {'GBT lean':<20}"
    )
    print("-" * 96)
    group_findings = []
    for cat in sorted(TRIGGER_GROUP_RANGES.keys()):
        rng = TRIGGER_GROUP_RANGES[cat]
        if rng is None:
            continue
        cols = trig[:, rng[0] : rng[1]]
        if cols.shape[1] == 0:
            continue
        group_strength = np.abs(cols).mean(axis=1)
        if group_strength.std() < 1e-9:
            print(f"{cat:<16} {'--':>10} {'--':>10} {'--':>8} {'--':>11} {'--':>9} {'flat (no variance)':<20}")
            continue
        q1, q4 = np.percentile(group_strength, [25, 75])
        q1_mask = group_strength <= q1
        q4_mask = group_strength >= q4
        m1 = _r_metrics(realized[q1_mask])
        m4 = _r_metrics(realized[q4_mask])
        # GBT lean: when group is hot, does GBT pick more CONT or REV?
        cont_pct_q4 = float((actions[q4_mask] == 0).mean())
        cont_pct_q1 = float((actions[q1_mask] == 0).mean())
        lean = f"hot→{'CONT' if cont_pct_q4 > cont_pct_q1 + 0.02 else ('REV' if cont_pct_q4 < cont_pct_q1 - 0.02 else 'neutral')}"
        dR = m4["mean_R"] - m1["mean_R"]
        d_runner = m4["runner_rate"] - m1["runner_rate"]
        print(
            f"{cat:<16} {m1['mean_R']:>+10.3f} {m4['mean_R']:>+10.3f} {dR:>+8.3f} {100 * m4['runner_rate']:>10.1f}% {100 * d_runner:>+8.1f}pt {lean:<20}"
        )
        group_findings.append(
            {
                "category": cat,
                "trigger_dims": rng[1] - rng[0],
                "mean_R_q1": m1["mean_R"],
                "mean_R_q4": m4["mean_R"],
                "delta_R": dR,
                "runner_rate_q4": m4["runner_rate"],
                "delta_runner": d_runner,
                "lean": lean,
            }
        )

    # ---- PART C: PER-GROUP SHAP CONTRIBUTION ----
    print("\n" + "=" * 100)
    print("PART C: Per-group SHAP contribution (5000-episode sample)")
    print("=" * 100)
    print("\nMean |shap| summed across all dims in the group = how much the group as a whole")
    print("drives GBT predictions. Compare across groups for relative importance.\n")

    sample_n = min(5000, n)
    rng_np = np.random.default_rng(0)
    idx = rng_np.choice(n, size=sample_n, replace=False)
    X_sample = trig[idx].astype(np.float32)
    X_alive = X_sample[:, gbt._alive_mask]
    X_scaled = gbt.scaler.transform(X_alive)
    shap_vals = gbt.direction_model.predict(X_scaled, pred_contrib=True)
    feat_shap = shap_vals[:, :-1] if shap_vals.ndim == 2 else shap_vals
    mean_abs_shap_alive = np.abs(feat_shap).mean(axis=0)
    alive_indices = np.where(gbt._alive_mask)[0]
    shap_by_orig: dict[int, float] = {
        int(alive_indices[i]): float(mean_abs_shap_alive[i]) for i in range(len(alive_indices))
    }
    total_shap = sum(shap_by_orig.values())

    print(f"{'Category':<16} {'trigger dims':>12} {'shap%':>9} {'shap/dim':>10}")
    print("-" * 55)
    shap_rows = []
    for cat in sorted(TRIGGER_GROUP_RANGES.keys()):
        rng = TRIGGER_GROUP_RANGES[cat]
        if rng is None:
            print(f"{cat:<16} {'0 (BLIND)':>12} {'0.00%':>9} {'--':>10}")
            shap_rows.append({"cat": cat, "trigger_dims": 0, "shap_pct": 0.0, "per_dim": 0.0})
            continue
        group_shap = sum(shap_by_orig.get(i, 0.0) for i in range(rng[0], rng[1]))
        n_dims = rng[1] - rng[0]
        pct = 100 * group_shap / max(total_shap, 1)
        per_dim = group_shap / max(n_dims, 1)
        print(f"{cat:<16} {n_dims:>12} {pct:>8.2f}% {per_dim:>10.2f}")
        shap_rows.append({"cat": cat, "trigger_dims": n_dims, "shap_pct": pct, "per_dim": per_dim})

    # Sort by SHAP %
    print("\nLeaderboard (by SHAP % of total):")
    for r in sorted(shap_rows, key=lambda x: -x["shap_pct"]):
        if r["trigger_dims"] == 0:
            continue
        print(f"  {r['cat']:<16} {r['shap_pct']:>5.2f}% total  ({r['per_dim']:.2f}/dim)")

    # ---- PART D: BLIND-SPOT FULL-OBS COMPARISON ----
    print("\n" + "=" * 100)
    print("PART D: What GBT's blind spots have (ZONE_MEMORY + MACRO from full obs)")
    print("=" * 100)
    print("\nIf these blind-spot groups show R-impact in the FULL obs (which FT-T sees),")
    print("GBT is structurally missing real signal that a different model could exploit.\n")
    for cat in ("ZONE_MEMORY", "MACRO"):
        segs = _CATEGORY_SEGMENTS.get(cat, [])
        if not segs:
            print(f"  {cat}: no segments found")
            continue
        # Gather full-obs dim indices for this category
        col_indices = []
        for seg in segs:
            start, end = _SEGMENT_OFFSETS[seg["name"]]
            col_indices.extend(range(start, end))
        if not col_indices:
            continue
        cols = obs_full[:, col_indices]
        group_strength = np.abs(cols).mean(axis=1)
        if group_strength.std() < 1e-9:
            print(f"  {cat}: all-zero in full obs (no signal there either — extractor not wired?)")
            continue
        q1, q4 = np.percentile(group_strength, [25, 75])
        q1_mask = group_strength <= q1
        q4_mask = group_strength >= q4
        m1 = _r_metrics(realized[q1_mask])
        m4 = _r_metrics(realized[q4_mask])
        dR = m4["mean_R"] - m1["mean_R"]
        print(
            f"  {cat:<16} ({len(col_indices)} dims): mean_R Q1={m1['mean_R']:+.3f} Q4={m4['mean_R']:+.3f} ΔR={dR:+.3f}  "
            f"runner_rate Q1→Q4: {100 * m1['runner_rate']:.1f}%→{100 * m4['runner_rate']:.1f}%"
        )
        if abs(dR) >= 0.05:
            print("     ⚠  GBT MISSING SIGNAL — this group has |ΔR|≥0.05 but GBT can't see it")
        else:
            print("     ✓  Blind spot is OK — group has no R signal in full obs either")

    # ---- Save report ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"gbt_group_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": n,
        "baseline": base,
        "coverage": coverage_findings,
        "groups": group_findings,
        "shap": shap_rows,
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
