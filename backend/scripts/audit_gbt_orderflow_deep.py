"""Deep audit of GBT OF segment: paradox + SHAP + dead-dim investigation.

Step 2 of GBT behavior audit. Builds on audit_gbt_orderflow.py findings:

PART A — Paradox investigation:
  absorption_strength + imbalance_density rank top-5 in GBT importance
  but have INVERSE univariate WR margins (-9.2pt and -5.9pt). Why?
    1. Distribution shape (bimodal? outliers? always 0?)
    2. Correlation with other OF dims (proxy effects)
    3. Conditional WR (paradox disappears when other dims controlled)

PART C — SHAP attribution:
  feature_importances_ shows split-gain (how often dim is split on,
  weighted by gain). SHAP gives per-prediction contribution. The two
  can disagree — a dim with low split-gain may still drive specific
  decisions strongly via interactions. Compute mean |SHAP| per OF dim
  across all episodes + flag dims where SHAP and importance diverge.

DEAD DIMS — investigation:
  vsa_absorption, stop_run_detected, delta_divergence have 0 importance.
    1. Are they always 0? (nonzero %)
    2. If nonzero — what fraction of episodes, what mean value?
    3. Recommend: keep / remove / rebuild
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
DEAD_DIMS = {"vsa_absorption", "stop_run_detected", "delta_divergence"}
PARADOX_DIMS = {"absorption_strength", "imbalance_density"}


def main() -> None:
    print("=" * 90)
    print("GBT OF-segment DEEP audit: paradox + SHAP + dead dims")
    print("=" * 90)

    trig = np.load(EP_DIR / "trigger_observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")
    n = len(trig)
    gbt = TriggerGBT.load(MD_DIR / "trigger_gbt_v5.joblib")
    print(f"\nPool: {n} episodes")

    of_cols = trig[:, _OF_START : _OF_START + _OF_LEN]
    actions, _, _ = gbt.predict_direction_batch(trig.astype(np.float32))
    realized = np.where(actions == 0, rc, rr)
    win = (realized > 0).astype(np.float32)

    # ============ DEAD DIMS ============
    print("\n" + "=" * 90)
    print("DEAD DIMS — data quality investigation")
    print("=" * 90)
    print("\nThe 3 OF dims with 0 LightGBM importance. Are they 0-valued? Or just non-predictive?\n")
    print(f"{'idx':>4} {'label':<26} {'nonzero%':>9} {'mean':>9} {'std':>9} {'min':>8} {'max':>8} {'verdict':<25}")
    print("-" * 100)
    dead_findings = []
    for i, label in enumerate(OF_LABELS):
        if label not in DEAD_DIMS:
            continue
        col = of_cols[:, i]
        nz_frac = float((col != 0).mean())
        mean = float(col.mean())
        std = float(col.std())
        cmin = float(col.min())
        cmax = float(col.max())
        if nz_frac < 0.005:
            verdict = "dead data (always 0)"
        elif std < 1e-6:
            verdict = "no variance"
        elif nz_frac < 0.05:
            verdict = "sparse (<5% nonzero)"
        else:
            verdict = "has signal — unused?"
        print(
            f"{i:>4} {label:<26} {100 * nz_frac:>8.2f}% {mean:>+9.4f} {std:>9.4f} {cmin:>+8.3f} {cmax:>+8.3f} {verdict:<25}"
        )
        dead_findings.append(
            {"label": label, "nonzero_pct": 100 * nz_frac, "mean": mean, "std": std, "verdict": verdict}
        )

    # ============ PARADOX DIMS — distribution ============
    print("\n" + "=" * 90)
    print("PARADOX DIMS — distribution + correlation")
    print("=" * 90)
    print("\nabsorption_strength + imbalance_density rank top-5 importance but have")
    print("INVERSE univariate WR margins. Step 1: look at their distributions.\n")

    print(f"{'idx':>4} {'label':<26} {'nonzero%':>9} {'p10':>7} {'p50':>7} {'p90':>7} {'p99':>7} {'std':>7}")
    print("-" * 85)
    for i, label in enumerate(OF_LABELS):
        if label not in PARADOX_DIMS:
            continue
        col = of_cols[:, i]
        nz_frac = float((col != 0).mean())
        p10, p50, p90, p99 = (float(np.percentile(col, p)) for p in (10, 50, 90, 99))
        std = float(col.std())
        print(
            f"{i:>4} {label:<26} {100 * nz_frac:>8.2f}% {p10:>+7.3f} {p50:>+7.3f} {p90:>+7.3f} {p99:>+7.3f} {std:>7.3f}"
        )

    # Correlation of paradox dims with other OF dims
    print("\nCorrelation of each paradox dim with other OF dims (top |r| 5):")
    for paradox_label in PARADOX_DIMS:
        pi = OF_LABELS.index(paradox_label)
        col = of_cols[:, pi]
        corrs: list[tuple[str, float]] = []
        for i, label in enumerate(OF_LABELS):
            if i == pi:
                continue
            other = of_cols[:, i]
            if other.std() < 1e-9 or col.std() < 1e-9:
                continue
            r = float(np.corrcoef(col, other)[0, 1])
            corrs.append((label, r))
        corrs.sort(key=lambda x: -abs(x[1]))
        print(f"\n  {paradox_label}:")
        for label, r in corrs[:5]:
            print(f"    r={r:+.3f}  ↔  {label}")

    # Conditional WR — does the paradox flip when we control for OTHER strong dims?
    print("\n" + "-" * 90)
    print("Conditional WR — paradox dim Q4 segregated by body_ratio (strongest +ve predictor)")
    print("-" * 90)
    body_idx = OF_LABELS.index("body_ratio")
    body_col = of_cols[:, body_idx]
    body_med = float(np.median(body_col))
    for paradox_label in PARADOX_DIMS:
        pi = OF_LABELS.index(paradox_label)
        col = of_cols[:, pi]
        if col.std() < 1e-9:
            continue
        q1_v, q4_v = np.percentile(col, [25, 75])
        # 4 buckets: dim Q1/Q4 × body low/high
        for body_label, body_mask in [
            ("body LOW (≤median)", body_col <= body_med),
            ("body HIGH (>median)", body_col > body_med),
        ]:
            dim_q1 = (col <= q1_v) & body_mask
            dim_q4 = (col >= q4_v) & body_mask
            q1_wr = float(win[dim_q1].mean()) if dim_q1.sum() else 0.0
            q4_wr = float(win[dim_q4].mean()) if dim_q4.sum() else 0.0
            print(
                f"  {paradox_label:<22} | {body_label:<22} | "
                f"Q1 WR={100 * q1_wr:5.1f}% (n={int(dim_q1.sum()):4d})  "
                f"Q4 WR={100 * q4_wr:5.1f}% (n={int(dim_q4.sum()):4d})  "
                f"margin {100 * (q4_wr - q1_wr):+5.1f}pt"
            )
        print()

    # ============ SHAP attribution ============
    print("=" * 90)
    print("SHAP attribution — per-dim contribution to GBT direction predictions")
    print("=" * 90)
    print("\nLightGBM's pred_contrib=True returns per-feature shap values per row.")
    print("Mean |shap| per dim = how much that dim drives predictions on average.")
    print(
        "Compare to feature_importances_ split-gain — divergence reveals dims that\n"
        "matter via INTERACTIONS rather than raw splits.\n"
    )

    # Use a sample for speed (full pool is fine but ~1-2min)
    sample_n = min(5000, n)
    rng = np.random.default_rng(0)
    sample_idx = rng.choice(n, size=sample_n, replace=False)
    X_sample = trig[sample_idx].astype(np.float32)
    X_alive = X_sample[:, gbt._alive_mask]
    X_scaled = gbt.scaler.transform(X_alive)

    print(f"Computing SHAP on {sample_n} sample episodes...")
    shap_vals = gbt.direction_model.predict(X_scaled, pred_contrib=True)
    # shap_vals shape: (n_samples, n_classes * (n_features + 1)) for multiclass
    # For binary classifier: shape (n_samples, n_features + 1) — last col is bias
    print(f"  shap shape: {shap_vals.shape}")

    if shap_vals.ndim == 2 and shap_vals.shape[1] == X_alive.shape[1] + 1:
        feat_shap = shap_vals[:, :-1]  # drop bias term
    elif shap_vals.ndim == 2 and shap_vals.shape[1] == X_alive.shape[1] * 2 + 2:
        # multiclass — 2 classes here (CONT vs REV), take CONT class
        n_feats = X_alive.shape[1]
        feat_shap = shap_vals[:, :n_feats]
    else:
        feat_shap = shap_vals

    mean_abs_shap_full = np.abs(feat_shap).mean(axis=0)
    # Map alive-indices back to original 118-d positions
    alive_indices = np.where(gbt._alive_mask)[0]
    shap_by_orig: dict[int, float] = {
        int(alive_indices[i]): float(mean_abs_shap_full[i]) for i in range(len(alive_indices))
    }

    imp = gbt.direction_model.feature_importances_
    imp_by_orig: dict[int, float] = {int(alive_indices[i]): float(imp[i]) for i in range(len(imp))}

    total_shap = sum(shap_by_orig.values())
    total_imp = sum(imp_by_orig.values())

    print(f"\n{'idx':>4} {'label':<26} {'split-imp%':>10} {'shap%':>9} {'shap_rank':>9} {'imp_rank':>9} {'note':<20}")
    print("-" * 100)
    of_shap = []
    for i, label in enumerate(OF_LABELS):
        orig_idx = _OF_START + i
        imp_val = imp_by_orig.get(orig_idx, 0.0)
        shap_val = shap_by_orig.get(orig_idx, 0.0)
        of_shap.append(
            {
                "label": label,
                "imp_pct": 100 * imp_val / max(total_imp, 1),
                "shap_pct": 100 * shap_val / max(total_shap, 1),
                "shap": shap_val,
                "imp": imp_val,
            }
        )

    # Rank by SHAP
    shap_sorted = sorted(of_shap, key=lambda d: -d["shap"])
    imp_sorted = sorted(of_shap, key=lambda d: -d["imp"])
    shap_rank = {d["label"]: r for r, d in enumerate(shap_sorted, 1)}
    imp_rank = {d["label"]: r for r, d in enumerate(imp_sorted, 1)}

    for d in shap_sorted:
        label = d["label"]
        sr = shap_rank[label]
        ir = imp_rank[label]
        rank_diff = ir - sr  # positive = SHAP ranks it higher than split-imp
        if rank_diff >= 5:
            note = "→ INTERACTION (high)"
        elif rank_diff <= -5:
            note = "→ split-only (low)"
        else:
            note = ""
        print(
            f"{shap_rank[label]:>4} {label:<26} {d['imp_pct']:>9.2f}% {d['shap_pct']:>8.2f}% {sr:>9} {ir:>9} {note:<20}"
        )

    # ============ Save report ============
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"gbt_of_deep_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": n,
        "shap_sample_n": sample_n,
        "dead_dims": dead_findings,
        "paradox_correlations": "see stdout",
        "shap_by_label": {
            d["label"]: {
                "shap_pct": d["shap_pct"],
                "imp_pct": d["imp_pct"],
                "shap_rank": shap_rank[d["label"]],
                "imp_rank": imp_rank[d["label"]],
            }
            for d in of_shap
        },
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
