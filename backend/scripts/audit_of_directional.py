"""OF directional + velocity deep-dive.

For each of 21 OF dims, classify what HIGH (Q4) values predict:
  - Direction: CONT-win vs REV-win vs stop-out (per the realized rc/rr)
  - Magnitude: stop-out / small / big-runner buckets
  - Velocity (proxy): |realized R| when our chosen action ran
  - Acceleration (proxy): runner rate (R ≥ 2.25R, near +2.5 cap)

Reveals per-OF-dim role:
  - CONT-RUNNER predictor:    Q4 → high P(rc ≥ 2.25)
  - REV-RUNNER predictor:     Q4 → high P(rr ≥ 2.25)
  - STOP predictor:           Q4 → high P(realized ≤ -1.25)
  - DIRECTIONAL bias:         Q4 → CONT-win > REV-win or vice versa
  - VELOCITY booster:         Q4 → |realized R| > baseline

For the runner strategy this tells us:
  - When dim X is hot, full-send a CONT trade (X predicts CONT runner)
  - When dim Y is hot, SKIP entirely (Y predicts stop-out)
  - When dim Z is hot, take REV trade with larger size (Z predicts REV runner)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/backend")

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

# Magnitude bands (per episode_builder caps of [-1.5, +2.5])
RUNNER_THRESHOLD = 2.25  # near +2.5 cap = true runner
STOP_THRESHOLD = -1.25  # near -1.5 cap = stop-out


def _outcome_buckets(rc: np.ndarray, rr: np.ndarray, mask: np.ndarray) -> dict:
    """For episodes in mask, classify the OUTCOME both directionally and by magnitude.

    Uses rc/rr (NOT actions) to avoid GBT-decision contamination — we want
    to know: of these episodes that had OF dim in Q4, what GENUINELY happened?
    """
    rc_m, rr_m = rc[mask], rr[mask]
    n = len(rc_m)
    if n == 0:
        return {"n": 0}

    # Direction: which side WOULD HAVE WON had we traded it?
    cont_wins = rc_m > 0  # CONT would have profited
    rev_wins = rr_m > 0  # REV would have profited
    cont_runners = rc_m >= RUNNER_THRESHOLD  # CONT direction had a runner
    rev_runners = rr_m >= RUNNER_THRESHOLD  # REV direction had a runner
    cont_stops = rc_m <= STOP_THRESHOLD  # CONT was a stop-out
    rev_stops = rr_m <= STOP_THRESHOLD  # REV was a stop-out

    # Best-of-two realized R (what an oracle would pick)
    best_R = np.where(rc_m > rr_m, rc_m, rr_m)
    velocity = np.abs(best_R).mean()

    return {
        "n": n,
        "p_cont_win": float(cont_wins.mean()),
        "p_rev_win": float(rev_wins.mean()),
        "p_cont_runner": float(cont_runners.mean()),
        "p_rev_runner": float(rev_runners.mean()),
        "p_cont_stop": float(cont_stops.mean()),
        "p_rev_stop": float(rev_stops.mean()),
        "p_either_runner": float((cont_runners | rev_runners).mean()),
        "velocity_proxy": float(velocity),
        "mean_best_R": float(best_R.mean()),
    }


def main() -> None:
    print("=" * 100)
    print("OF DIRECTIONAL + VELOCITY deep-dive — per-dim outcome classification")
    print("=" * 100)

    trig = np.load(EP_DIR / "trigger_observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")
    n = len(trig)
    print(
        f"\nPool: {n} episodes  |  reward caps [-1.5, +2.5]  |  runner threshold {RUNNER_THRESHOLD}R  stop {STOP_THRESHOLD}R"
    )

    of_cols = trig[:, _OF_START : _OF_START + _OF_LEN]

    # Baseline (all episodes)
    base = _outcome_buckets(rc, rr, np.ones(n, dtype=bool))
    print("\nBASELINE OUTCOMES (no filtering):")
    print(f"  CONT wins: {100 * base['p_cont_win']:5.1f}%   REV wins: {100 * base['p_rev_win']:5.1f}%")
    print(
        f"  CONT runners (R≥{RUNNER_THRESHOLD}): {100 * base['p_cont_runner']:5.1f}%   "
        f"REV runners: {100 * base['p_rev_runner']:5.1f}%   "
        f"either: {100 * base['p_either_runner']:5.1f}%"
    )
    print(
        f"  CONT stops (R≤{STOP_THRESHOLD}): {100 * base['p_cont_stop']:5.1f}%   "
        f"REV stops: {100 * base['p_rev_stop']:5.1f}%"
    )
    print(f"  Velocity proxy (mean |best_R|): {base['velocity_proxy']:.3f}")

    # ---- Per-OF-dim deltas (Q4 - baseline) ----
    print("\n" + "=" * 100)
    print("Per-OF-dim outcome deltas: Q4 (high) vs baseline (all episodes)")
    print("=" * 100)
    print(
        f"\n{'idx':>3} {'label':<26} {'cont-runner Δ':>13} {'rev-runner Δ':>13} {'stop Δ':>9} {'velocity Δ':>11} {'tag':<8}"
    )
    print("-" * 90)
    findings = []
    for i, label in enumerate(OF_LABELS):
        col = of_cols[:, i]
        if col.std() < 1e-8:
            print(f"{i:>3} {label:<26} {'--':>13} {'--':>13} {'--':>9} {'--':>11} dead")
            continue
        q4_thresh = np.percentile(col, 75)
        q4_mask = col >= q4_thresh
        m4 = _outcome_buckets(rc, rr, q4_mask)
        d_cont_runner = m4["p_cont_runner"] - base["p_cont_runner"]
        d_rev_runner = m4["p_rev_runner"] - base["p_rev_runner"]
        d_stop = (m4["p_cont_stop"] + m4["p_rev_stop"]) / 2 - (base["p_cont_stop"] + base["p_rev_stop"]) / 2
        d_velocity = m4["velocity_proxy"] - base["velocity_proxy"]
        tag = "[L1]" if label in L1_DERIVED_LABELS else ""
        print(
            f"{i:>3} {label:<26} {100 * d_cont_runner:>+11.1f}pt {100 * d_rev_runner:>+11.1f}pt "
            f"{100 * d_stop:>+7.1f}pt {d_velocity:>+10.3f} {tag:<8}"
        )
        findings.append(
            {
                "label": label,
                "is_l1": label in L1_DERIVED_LABELS,
                "q4_n": m4["n"],
                "d_cont_runner": d_cont_runner,
                "d_rev_runner": d_rev_runner,
                "d_stop": d_stop,
                "d_velocity": d_velocity,
                "p_cont_win_q4": m4["p_cont_win"],
                "p_rev_win_q4": m4["p_rev_win"],
            }
        )

    # ---- LEADERBOARDS ----
    print("\n" + "=" * 100)
    print("LEADERBOARDS by role")
    print("=" * 100)

    print("\n🚀 CONT-RUNNER PREDICTORS (top 5) — high Q4 → CONT trade likely to run:")
    for r in sorted(findings, key=lambda x: -x["d_cont_runner"])[:5]:
        tag = " [L1]" if r["is_l1"] else ""
        print(
            f"  {r['label']:<26} P(cont-runner) Δ={100 * r['d_cont_runner']:+5.1f}pt   "
            f"P(rev-runner) Δ={100 * r['d_rev_runner']:+5.1f}pt{tag}"
        )

    print("\n🔄 REV-RUNNER PREDICTORS (top 5) — high Q4 → REV trade likely to run:")
    for r in sorted(findings, key=lambda x: -x["d_rev_runner"])[:5]:
        tag = " [L1]" if r["is_l1"] else ""
        print(
            f"  {r['label']:<26} P(rev-runner) Δ={100 * r['d_rev_runner']:+5.1f}pt   "
            f"P(cont-runner) Δ={100 * r['d_cont_runner']:+5.1f}pt{tag}"
        )

    print("\n⚠ STOP PREDICTORS (top 5) — high Q4 → higher likelihood of stop-out either side:")
    for r in sorted(findings, key=lambda x: -x["d_stop"])[:5]:
        tag = " [L1]" if r["is_l1"] else ""
        print(
            f"  {r['label']:<26} avg P(stop) Δ={100 * r['d_stop']:+5.1f}pt   "
            f"P(cont-runner) Δ={100 * r['d_cont_runner']:+5.1f}pt{tag}"
        )

    print("\n⚡ VELOCITY BOOSTERS (top 5) — high Q4 → bigger price moves (either direction):")
    for r in sorted(findings, key=lambda x: -x["d_velocity"])[:5]:
        tag = " [L1]" if r["is_l1"] else ""
        print(
            f"  {r['label']:<26} velocity Δ={r['d_velocity']:+.3f}R   "
            f"P(either-runner) {r['d_cont_runner'] + r['d_rev_runner']:+.3f}pt-pair{tag}"
        )

    print("\n📉 ANTI-VELOCITY (top 5) — high Q4 → DAMPED price moves:")
    for r in sorted(findings, key=lambda x: x["d_velocity"])[:5]:
        tag = " [L1]" if r["is_l1"] else ""
        print(f"  {r['label']:<26} velocity Δ={r['d_velocity']:+.3f}R   stop Δ={100 * r['d_stop']:+5.1f}pt{tag}")

    print("\n➡ DIRECTIONAL BIAS — Q4 P(cont-win) vs P(rev-win):")
    for r in findings:
        dir_bias = r["p_cont_win_q4"] - r["p_rev_win_q4"]
        if abs(dir_bias) >= 0.03:
            arrow = "→CONT" if dir_bias > 0 else "→REV"
            tag = " [L1]" if r["is_l1"] else ""
            print(
                f"  {r['label']:<26} P(cont)={100 * r['p_cont_win_q4']:.1f}%  P(rev)={100 * r['p_rev_win_q4']:.1f}%  bias {arrow} ({100 * abs(dir_bias):.1f}pt){tag}"
            )

    # Save JSON
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"of_directional_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": n,
        "baseline": base,
        "findings": findings,
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
