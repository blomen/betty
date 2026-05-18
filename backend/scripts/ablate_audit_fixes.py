"""MC ablation harness for PROFILE-audit fix verification (2026-05-18).

Trains 4 GBT variants on the SAME treated pool (post-fix-1+3 episodes),
varying only the feature masking. This isolates each fix's R impact
without rebuilding-and-replaying per commit.

Variants:
  V0_baseline:   FVG/OB composition slots (75+31..75+34) zeroed AND
                 weekly/monthly swing slots (75+28..75+30) zeroed.
                 Simulates "pre-fix" data on the treated pool.
  V1_fvg_ob:     Only swing slots zeroed. Bug 1 active.
  V2_fvg_ob_swing: Nothing zeroed. Bugs 1+3 active.
  V3_full:       V2 + 4 TPO indices from base obs concatenated as extra
                 passthrough dims. Bugs 1+3+2 all active.

For each variant: train 90/10 split, evaluate on holdout, bootstrap
1000 resamples for mean-R/runner-rate/stop-rate confidence intervals.
Reports per-fix R deltas with statistical significance.

Usage (on server):
  docker compose exec backend python backend/scripts/ablate_audit_fixes.py \\
      /app/data/rl/profile_audit_2026_05_18/pool_treated
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Slot offsets in trigger obs (122-dim, post-Bug 1+3 replay):
# 0-9 passthrough, 10-29 micro, 30-50 of, 51-65 candles, 66-69 zone_feats,
# 70-74 zone_conf, 75-109 zone_composition (35 LevelType slots),
# 110 approach, 111-118 gbt_forecast, 119-121 exec_passthrough.
ZONE_COMP_START = 75
# Within zone_composition (LevelType enum order):
LEVELTYPE_OFFSETS = {
    "WEEKLY_SWING_LOW": 28,
    "MONTHLY_SWING_HIGH": 29,
    "MONTHLY_SWING_LOW": 30,
    "FVG_BULL": 31,
    "FVG_BEAR": 32,
    "ORDER_BLOCK_BULL": 33,
    "ORDER_BLOCK_BEAR": 34,
}
SWING_SLOTS = [
    ZONE_COMP_START + LEVELTYPE_OFFSETS[k] for k in ("WEEKLY_SWING_LOW", "MONTHLY_SWING_HIGH", "MONTHLY_SWING_LOW")
]
FVG_OB_SLOTS = [
    ZONE_COMP_START + LEVELTYPE_OFFSETS[k] for k in ("FVG_BULL", "FVG_BEAR", "ORDER_BLOCK_BULL", "ORDER_BLOCK_BEAR")
]

# Base obs indices for the 4 new TPO passthrough dims (PROFILE audit picks)
TPO_BASE_INDICES = [126, 132, 144, 145]
TPO_NAMES = ["tokyo_opening_direction", "london_ib_range", "ny_ib_range", "ny_price_vs_ib_mid"]

RUNNER_R = 2.25
STOP_R = -1.25
N_BOOTSTRAP = 1000
SEED = 42

VARIANTS = [
    ("V0_baseline", "pre-fix simulated: FVG/OB + swing slots zeroed"),
    ("V1_fvg_ob", "Bug 1 only: FVG/OB unmasked, swing still zeroed"),
    ("V2_fvg_ob_swing", "Bugs 1+3: both unmasked"),
    ("V3_full", "Bugs 1+3+2: V2 plus 4 TPO indices in passthrough (input grows 122→126)"),
]


def _load_pool(root: Path):
    base = np.load(root / "observations.npy")
    trig = np.load(root / "trigger_observations.npy")
    rc = np.load(root / "rewards_cont.npy").astype(np.float32)
    rr = np.load(root / "rewards_rev.npy").astype(np.float32)
    assert len(base) == len(trig) == len(rc) == len(rr)
    return base, trig, rc, rr


def _mask_slots(trig: np.ndarray, slots: list[int]) -> np.ndarray:
    out = trig.copy()
    out[:, slots] = 0.0
    return out


def _build_variant_inputs(base: np.ndarray, trig: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "V0_baseline": _mask_slots(trig, SWING_SLOTS + FVG_OB_SLOTS),
        "V1_fvg_ob": _mask_slots(trig, SWING_SLOTS),
        "V2_fvg_ob_swing": trig.copy(),
        "V3_full": np.concatenate([trig, base[:, TPO_BASE_INDICES]], axis=1),
    }


def _train_eval_variant(X: np.ndarray, rc: np.ndarray, rr: np.ndarray, rng: np.random.Generator) -> dict:
    """Train a LightGBM direction classifier + expected-best-R regressor.

    Same architecture sketch as TriggerGBT but stripped to the two heads we
    care about for ablation. Returns per-episode predicted action + expected R
    on the holdout fold so the bootstrap can resample them.
    """
    try:
        import lightgbm as lgb
    except ImportError:
        print("ERROR: lightgbm not available", file=sys.stderr)
        sys.exit(1)

    # Direction label: 0 = CONT wins, 1 = REV wins (best of two)
    cont_wins = (rc > rr).astype(int)
    best_r = np.maximum(rc, rr)

    # 90/10 split, shuffled
    n = len(X)
    perm = rng.permutation(n)
    split = int(n * 0.9)
    tr_idx, ho_idx = perm[:split], perm[split:]

    # Direction head
    direction = lgb.LGBMClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        num_leaves=31,
        min_data_in_leaf=20,
        random_state=SEED,
        verbosity=-1,
    )
    direction.fit(X[tr_idx], cont_wins[tr_idx])

    # Expected-best-R head
    er_model = lgb.LGBMRegressor(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        num_leaves=31,
        min_data_in_leaf=20,
        random_state=SEED,
        verbosity=-1,
    )
    er_model.fit(X[tr_idx], best_r[tr_idx])

    # Holdout predictions
    pred_cont = direction.predict_proba(X[ho_idx])[:, 0]
    pred_action = (pred_cont >= 0.5).astype(int)  # 0=CONT, 1=REV (matches cont_wins encoding)
    realized_R = np.where(pred_action == 0, rc[ho_idx], rr[ho_idx])

    return {
        "n_train": split,
        "n_holdout": n - split,
        "realized_R": realized_R,
        "expected_R": er_model.predict(X[ho_idx]),
        "rc_ho": rc[ho_idx],
        "rr_ho": rr[ho_idx],
        "pred_action": pred_action,
        "ho_idx": ho_idx,
    }


def _bootstrap_ci(realized_R: np.ndarray, n_boot: int, rng: np.random.Generator) -> dict:
    """Bootstrap mean R, runner rate, stop rate over realized R on holdout."""
    n = len(realized_R)
    means = np.empty(n_boot, dtype=np.float64)
    runners = np.empty(n_boot, dtype=np.float64)
    stops = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        sample_idx = rng.integers(0, n, size=n)
        s = realized_R[sample_idx]
        means[i] = s.mean()
        runners[i] = (s >= RUNNER_R).mean()
        stops[i] = (s <= STOP_R).mean()
    return {
        "mean_R": (float(np.percentile(means, 2.5)), float(means.mean()), float(np.percentile(means, 97.5))),
        "runner_rate": (float(np.percentile(runners, 2.5)), float(runners.mean()), float(np.percentile(runners, 97.5))),
        "stop_rate": (float(np.percentile(stops, 2.5)), float(stops.mean()), float(np.percentile(stops, 97.5))),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("pool_dir", type=Path, help="treated pool directory (post-fix replay)")
    p.add_argument("--n-boot", type=int, default=N_BOOTSTRAP)
    args = p.parse_args()

    print("=" * 80)
    print(f"MC ABLATION — PROFILE-audit fixes (pool: {args.pool_dir.name})")
    print("=" * 80)

    base, trig, rc, rr = _load_pool(args.pool_dir)
    print(f"\nPool: n={len(base)}, base_obs={base.shape[1]}-dim, trig_obs={trig.shape[1]}-dim")
    print(f"Reward range: rc[{rc.min():.2f}, {rc.max():.2f}], rr[{rr.min():.2f}, {rr.max():.2f}]")
    print("\nSlot masks:")
    print(f"  swing slots (trig idx): {SWING_SLOTS}")
    print(f"  FVG/OB slots (trig idx): {FVG_OB_SLOTS}")
    print(f"  TPO base indices: {TPO_BASE_INDICES} ({TPO_NAMES})")

    inputs = _build_variant_inputs(base, trig)
    rng = np.random.default_rng(SEED)

    results = {}
    for variant_id, desc in VARIANTS:
        X = inputs[variant_id]
        print(f"\n[{variant_id}] {desc}")
        print(f"  input shape: {X.shape}")
        r = _train_eval_variant(X, rc, rr, np.random.default_rng(SEED))
        ci = _bootstrap_ci(r["realized_R"], args.n_boot, rng)
        results[variant_id] = {**r, **ci, "input_dim": X.shape[1]}
        print(f"  train={r['n_train']:,}  holdout={r['n_holdout']:,}")
        print(f"  mean_R     95%CI: [{ci['mean_R'][0]:+.4f}, {ci['mean_R'][1]:+.4f}, {ci['mean_R'][2]:+.4f}]")
        print(
            f"  runner%    95%CI: [{ci['runner_rate'][0] * 100:.2f}, {ci['runner_rate'][1] * 100:.2f}, {ci['runner_rate'][2] * 100:.2f}]"
        )
        print(
            f"  stop%      95%CI: [{ci['stop_rate'][0] * 100:.2f}, {ci['stop_rate'][1] * 100:.2f}, {ci['stop_rate'][2] * 100:.2f}]"
        )

    print("\n" + "=" * 80)
    print("PER-FIX R DELTAS (mean of bootstrap, with CI overlap test)")
    print("=" * 80)

    pairs = [
        ("V0_baseline", "V1_fvg_ob", "Bug 1 (FVG/OB)"),
        ("V1_fvg_ob", "V2_fvg_ob_swing", "Bug 3 (swing fallback)"),
        ("V2_fvg_ob_swing", "V3_full", "Bug 2 (TPO passthrough)"),
    ]
    for a_id, b_id, fix_name in pairs:
        a, b = results[a_id], results[b_id]
        d_mean = b["mean_R"][1] - a["mean_R"][1]
        d_runner = (b["runner_rate"][1] - a["runner_rate"][1]) * 100
        d_stop = (b["stop_rate"][1] - a["stop_rate"][1]) * 100
        # CI overlap test (rough significance)
        overlap = not (b["mean_R"][0] > a["mean_R"][2] or b["mean_R"][2] < a["mean_R"][0])
        sig = "  (NS — CIs overlap)" if overlap else "  *** (CIs disjoint)"
        print(f"\n  {fix_name}: {a_id} → {b_id}")
        print(f"    mean R Δ:    {d_mean:+.4f}{sig}")
        print(f"    runner%  Δ:  {d_runner:+.2f}pt")
        print(f"    stop%    Δ:  {d_stop:+.2f}pt")

    print("\n" + "=" * 80)
    print("CUMULATIVE: pre-fix simulated → all-fixes")
    print("=" * 80)
    a, b = results["V0_baseline"], results["V3_full"]
    print(f"  V0_baseline mean R: {a['mean_R'][1]:+.4f}  [95%CI {a['mean_R'][0]:+.4f}..{a['mean_R'][2]:+.4f}]")
    print(f"  V3_full     mean R: {b['mean_R'][1]:+.4f}  [95%CI {b['mean_R'][0]:+.4f}..{b['mean_R'][2]:+.4f}]")
    print(f"  Δ mean R: {b['mean_R'][1] - a['mean_R'][1]:+.4f}")
    print(f"  Δ runner%: {(b['runner_rate'][1] - a['runner_rate'][1]) * 100:+.2f}pt")
    print(f"  Δ stop%:   {(b['stop_rate'][1] - a['stop_rate'][1]) * 100:+.2f}pt")

    return 0


if __name__ == "__main__":
    sys.exit(main())
