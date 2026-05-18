"""OF-only Phase 1 — per-dim outcome profile.

For each of the 21 orderflow dims, classify what HIGH and LOW values
predict in terms of realized price outcomes. This is the "alphabet"
analysis — what does each individual dim say about the next move,
before we look at combinations.

Binning strategy adapts to the dim's firing density:
  - sparse (<5% nonzero):     binary fires/doesn't-fire
  - medium (5%-50% nonzero):  zero band + 3 nonzero quartiles
  - dense  (>50% nonzero):    4 quartiles of the full distribution

For each bin we measure:
  - n (sample size)
  - mean rc, mean rr (per-direction realized R)
  - mean best_R (oracle = max(rc, rr))
  - P(best_R >= R) at R in {0.5, 1.0, 1.5, 2.0, 2.25}  (runner ladder)
  - P(min(rc, rr) <= -R) at R in {0.5, 1.0, 1.25, 1.5}  (stop ladder)
  - velocity proxy = mean |best_R|
  - cont win rate = P(rc > 0), rev win rate = P(rr > 0)
  - directional bias = P(rc > 0) - P(rr > 0)
  - bootstrap 95% CI on mean_best_R (n=1000 resamples)

Run on the treated pool:
  docker compose exec -T backend python \\
      /app/backend/scripts/of_dim_outcome_profile.py \\
      /app/data/rl/profile_audit_2026_05_18/pool_treated

Outputs:
  - stdout: tables per dim + global summary
  - JSON: /app/data/rl/audit_reports/of_dim_outcome_profile_<ts>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Trig layout: 10 passthrough + 20 micro + 21 OF (idx 30-50) + ...
OF_START = 10 + 20
OF_LEN = 21

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
assert len(OF_LABELS) == OF_LEN

L1_DIMS = {"spread_ticks", "passive_active_ratio"}
FIXED_DIMS = {"vsa_absorption", "stop_run_detected", "delta_divergence"}

RUNNER_LADDER = [0.5, 1.0, 1.5, 2.0, 2.25]
STOP_LADDER = [0.5, 1.0, 1.25, 1.5]
N_BOOTSTRAP = 1000
SPARSE_THRESHOLD = 0.05
DENSE_THRESHOLD = 0.50


def _binning_strategy(col: np.ndarray) -> tuple[str, list[tuple[str, np.ndarray]]]:
    """Adaptive binning. Detects categorical-like dims (few unique values)
    where percentile boundaries collide and produce empty bins. Falls back
    to one bin per unique value in that case. Otherwise quartile or
    zero+3q based on firing density."""
    nonzero_pct = (col != 0).mean()
    unique_vals = np.unique(col)
    n_unique = len(unique_vals)

    # Case A: categorical-like (small number of distinct values) — bin per value.
    # Many OF dims are discrete (-1/0/+1, integer counts, ratios on small grids).
    if n_unique <= 8:
        bins = [(f"v={v:+.3g}", col == v) for v in sorted(unique_vals)]
        return f"categorical({n_unique})", bins

    # Case B: sparse — binary fires/doesn't.
    if nonzero_pct < SPARSE_THRESHOLD:
        return "binary", [("zero", col == 0), ("fires", col != 0)]

    # Case C: medium-density — zero + 3 nonzero quantiles, with collision check.
    if nonzero_pct < DENSE_THRESHOLD:
        nz = col[col != 0]
        if len(nz) < 20:
            return "binary", [("zero", col == 0), ("fires", col != 0)]
        q33, q67 = np.percentile(nz, [33, 67])
        if q33 == q67 or q33 == nz.max() or q67 == nz.min():
            # Boundaries collide — fall back to binary.
            return "binary (collision)", [("zero", col == 0), ("fires", col != 0)]
        return "zero+3q", [
            ("zero", col == 0),
            ("nz_low", (col != 0) & (col <= q33)),
            ("nz_mid", (col != 0) & (col > q33) & (col <= q67)),
            ("nz_high", (col != 0) & (col > q67)),
        ]

    # Case D: dense — true quartiles, with collision detection.
    q25, q50, q75 = np.percentile(col, [25, 50, 75])
    if q25 == q50 or q50 == q75 or q25 == q75:
        # Quartile boundaries collide — too many duplicates for clean quartiles.
        # Fall back to a more robust approach: median split + zero handling.
        median = q50
        if (col == median).mean() > 0.25:
            # Median is over-represented — split as < median / = median / > median
            return "median-split", [
                ("below", col < median),
                ("at", col == median),
                ("above", col > median),
            ]
        # Otherwise use deduplicated boundaries
        unique_bounds = sorted(set([q25, q50, q75]))
        bins = []
        prev = -np.inf
        for ub in unique_bounds:
            bins.append((f"≤{ub:.2g}", (col > prev) & (col <= ub)))
            prev = ub
        bins.append((f">{prev:.2g}", col > prev))
        return "dedup-quartile", bins

    return "quartile", [
        ("Q1", col <= q25),
        ("Q2", (col > q25) & (col <= q50)),
        ("Q3", (col > q50) & (col <= q75)),
        ("Q4", col > q75),
    ]


def _bin_outcomes(rc: np.ndarray, rr: np.ndarray, mask: np.ndarray, n_boot: int) -> dict:
    rc_m, rr_m = rc[mask], rr[mask]
    n = int(mask.sum())
    if n == 0:
        return {"n": 0}
    best_r = np.maximum(rc_m, rr_m)
    worst_r = np.minimum(rc_m, rr_m)

    out = {
        "n": n,
        "mean_rc": float(rc_m.mean()),
        "mean_rr": float(rr_m.mean()),
        "mean_best_R": float(best_r.mean()),
        "velocity": float(np.abs(best_r).mean()),
        "p_cont_win": float((rc_m > 0).mean()),
        "p_rev_win": float((rr_m > 0).mean()),
        "directional_bias": float((rc_m > 0).mean() - (rr_m > 0).mean()),
    }
    for r in RUNNER_LADDER:
        out[f"p_best_ge_{r}R"] = float((best_r >= r).mean())
    for r in STOP_LADDER:
        out[f"p_worst_le_-{r}R"] = float((worst_r <= -r).mean())

    if n >= 30 and n_boot > 0:
        rng = np.random.default_rng(42)
        samples = np.empty(n_boot, dtype=np.float64)
        for i in range(n_boot):
            idx = rng.integers(0, n, size=n)
            samples[i] = best_r[idx].mean()
        out["mean_best_R_ci"] = (
            float(np.percentile(samples, 2.5)),
            float(np.percentile(samples, 97.5)),
        )
    else:
        out["mean_best_R_ci"] = (None, None)

    return out


def _classify_role(bin_stats: list[dict], baseline: dict) -> str:
    """Identify the dim's role by comparing the highest-value bin to baseline.
    'Highest' is the last non-empty bin in sorted order — works for quartile,
    categorical, zero+3q, binary, median-split all uniformly."""
    populated = [b for b in bin_stats if b["n"] >= 30]
    if not populated:
        n_max = max((b["n"] for b in bin_stats), default=0)
        return f"low-sample (max bin n={n_max})"
    high_bin = populated[-1]  # last bin in declared order = highest-value bin

    roles = []
    d_runner_2 = high_bin["p_best_ge_2.0R"] - baseline["p_best_ge_2.0R"]
    d_runner_225 = high_bin["p_best_ge_2.25R"] - baseline["p_best_ge_2.25R"]
    d_stop_125 = high_bin["p_worst_le_-1.25R"] - baseline["p_worst_le_-1.25R"]
    d_vel = high_bin["velocity"] - baseline["velocity"]
    d_bias = high_bin["directional_bias"] - baseline["directional_bias"]

    if d_runner_225 > 0.03:
        roles.append(f"RUNNER+{100 * d_runner_225:+.1f}pt")
    if d_stop_125 > 0.03:
        roles.append(f"STOP+{100 * d_stop_125:+.1f}pt")
    if d_stop_125 < -0.03:
        roles.append(f"STOP{100 * d_stop_125:+.1f}pt(prot)")
    if d_vel > 0.05:
        roles.append(f"VEL+{d_vel:+.3f}")
    if d_vel < -0.05:
        roles.append(f"VEL{d_vel:+.3f}(damp)")
    if abs(d_bias) > 0.04:
        roles.append(f"→{'CONT' if d_bias > 0 else 'REV'}({100 * abs(d_bias):.1f}pt)")

    return ", ".join(roles) if roles else "neutral"


def _tag(label: str) -> str:
    if label in L1_DIMS:
        return "[L1]"
    if label in FIXED_DIMS:
        return "[FIX]"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pool_dir", type=Path, help="treated pool directory")
    parser.add_argument("--n-boot", type=int, default=N_BOOTSTRAP)
    parser.add_argument("--out", type=Path, default=Path("/app/data/rl/audit_reports"))
    args = parser.parse_args()

    trig = np.load(args.pool_dir / "trigger_observations.npy")
    rc = np.load(args.pool_dir / "rewards_cont.npy").astype(np.float32)
    rr = np.load(args.pool_dir / "rewards_rev.npy").astype(np.float32)
    n = len(trig)
    assert n == len(rc) == len(rr)

    of_cols = trig[:, OF_START : OF_START + OF_LEN]

    print("=" * 110)
    print(f"OF-ONLY PHASE 1 — per-dim outcome profile  (pool: {args.pool_dir.name}, n={n:,})")
    print("=" * 110)

    baseline = _bin_outcomes(rc, rr, np.ones(n, dtype=bool), args.n_boot)
    print("\nBaseline (all episodes):")
    print(
        f"  mean_best_R={baseline['mean_best_R']:+.3f}  velocity={baseline['velocity']:.3f}  "
        f"cont-win={100 * baseline['p_cont_win']:.1f}%  rev-win={100 * baseline['p_rev_win']:.1f}%"
    )
    print("  runner ladder: " + "  ".join(f"P(R≥{r})={100 * baseline[f'p_best_ge_{r}R']:.1f}%" for r in RUNNER_LADDER))
    print("  stop ladder:   " + "  ".join(f"P(R≤-{r})={100 * baseline[f'p_worst_le_-{r}R']:.1f}%" for r in STOP_LADDER))

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool": str(args.pool_dir),
        "n_episodes": n,
        "baseline": baseline,
        "dims": [],
    }

    for i, label in enumerate(OF_LABELS):
        col = of_cols[:, i]
        nonzero_pct = (col != 0).mean()
        std = col.std()
        tag = _tag(label)

        print()
        print("─" * 110)
        print(f"OF[{i:>2}] {label:<26} {tag:>6}  nz%={100 * nonzero_pct:>5.1f}  std={std:>6.3f}")
        print("─" * 110)

        if std < 1e-9:
            print(f"  DEAD — std={std:.2e}, skipping bin analysis")
            report["dims"].append({"idx": i, "label": label, "dead": True})
            continue

        strategy, bins = _binning_strategy(col)
        print(f"  binning strategy: {strategy}")

        bin_stats = []
        header = (
            f"  {'bin':<10} {'n':>6}  {'mean_R':>7} {'CI':>16} {'vel':>6} "
            f"{'cont-w':>7} {'rev-w':>7} {'bias':>7} "
            + " ".join(f"P(R≥{r}R)" for r in RUNNER_LADDER)
            + " "
            + " ".join(f"P(R≤-{r}R)" for r in STOP_LADDER)
        )
        print(header)

        for bin_name, mask in bins:
            stats = _bin_outcomes(rc, rr, mask, args.n_boot)
            stats["bin_name"] = bin_name
            bin_stats.append(stats)
            if stats["n"] == 0:
                print(f"  {bin_name:<10} {0:>6}  (empty)")
                continue
            ci = stats["mean_best_R_ci"]
            ci_str = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci[0] is not None else "(n<30)"
            row = (
                f"  {bin_name:<10} {stats['n']:>6,}  {stats['mean_best_R']:>+7.3f} {ci_str:>16} "
                f"{stats['velocity']:>6.3f} "
                f"{100 * stats['p_cont_win']:>6.1f}% {100 * stats['p_rev_win']:>6.1f}% "
                f"{100 * stats['directional_bias']:>+6.1f}pt "
                + " ".join(f"{100 * stats[f'p_best_ge_{r}R']:>6.1f}%" for r in RUNNER_LADDER)
                + " "
                + " ".join(f"{100 * stats[f'p_worst_le_-{r}R']:>6.1f}%" for r in STOP_LADDER)
            )
            print(row)

        role = _classify_role(bin_stats, baseline)
        print(f"  ROLE: {role}")

        report["dims"].append(
            {
                "idx": i,
                "label": label,
                "tag": tag.strip("[]") if tag else "",
                "nonzero_pct": float(nonzero_pct),
                "std": float(std),
                "binning_strategy": strategy,
                "bins": bin_stats,
                "role": role,
                "dead": False,
            }
        )

    # Global summary
    print()
    print("=" * 110)
    print("GLOBAL OF SUMMARY")
    print("=" * 110)

    runners = []
    stops = []
    velocity_boosters = []
    velocity_dampers = []
    cont_biased = []
    rev_biased = []
    low_sample = []
    sparse = []

    for d in report["dims"]:
        if d.get("dead"):
            continue
        # Find the highest-value populated bin (last non-empty in declared order).
        populated = [b for b in d["bins"] if b["n"] >= 30]
        if not populated:
            n_max = max((b["n"] for b in d["bins"]), default=0)
            low_sample.append((d["label"], n_max))
            continue
        high = populated[-1]
        if d["nonzero_pct"] < SPARSE_THRESHOLD:
            sparse.append((d["label"], d["nonzero_pct"]))

        d_runner = high["p_best_ge_2.25R"] - baseline["p_best_ge_2.25R"]
        d_stop = high["p_worst_le_-1.25R"] - baseline["p_worst_le_-1.25R"]
        d_vel = high["velocity"] - baseline["velocity"]
        d_bias = high["directional_bias"] - baseline["directional_bias"]

        if d_runner > 0.02:
            runners.append((d["label"], d_runner))
        if d_stop > 0.02:
            stops.append((d["label"], d_stop))
        if d_vel > 0.03:
            velocity_boosters.append((d["label"], d_vel))
        if d_vel < -0.03:
            velocity_dampers.append((d["label"], d_vel))
        if d_bias > 0.04:
            cont_biased.append((d["label"], d_bias))
        elif d_bias < -0.04:
            rev_biased.append((d["label"], d_bias))

    def _print_list(title: str, items: list, sort_desc: bool = True, unit: str = "pt"):
        print(f"\n{title}: ({len(items)})")
        if not items:
            print("  (none)")
            return
        items_sorted = sorted(items, key=lambda x: -x[1] if sort_desc else x[1])
        for name, val in items_sorted:
            if unit == "pt":
                print(f"  {name:<26} {100 * val:+6.1f}pt")
            else:
                print(f"  {name:<26} {val:+.3f}")

    _print_list("RUNNER PREDICTORS (Q4 → P(R≥2.25R) lifted)", runners)
    _print_list("STOP PREDICTORS   (Q4 → P(R≤-1.25R) lifted)", stops)
    _print_list("VELOCITY BOOSTERS (Q4 → |best_R| > baseline)", velocity_boosters, unit="R")
    _print_list("VELOCITY DAMPERS  (Q4 → |best_R| < baseline)", velocity_dampers, sort_desc=False, unit="R")
    _print_list("DIRECTIONAL BIAS → CONT", cont_biased)
    _print_list("DIRECTIONAL BIAS → REV", rev_biased, sort_desc=False)

    print("\nSPARSE dims (<5% nonzero, limited signal):")
    if sparse:
        for name, nz in sparse:
            print(f"  {name:<26} nz={100 * nz:.1f}%")
    else:
        print("  (none)")

    print("\nLOW-SAMPLE high bins (< 30 samples in high bin):")
    if low_sample:
        for name, n in low_sample:
            print(f"  {name:<26} high_bin_n={n}")
    else:
        print("  (none)")

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"of_dim_outcome_profile_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
