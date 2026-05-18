"""Offline backtest: GBT (production) vs FT-Transformer (shadow) on the
existing episode pool.

Why this exists: the live shadow path (Plan 2) only logs predictions when
ticks → zone touches → signal dispatches happen. With TopstepX subscription
inactive there are no live ticks, so shadow_predictions stays empty and
the 30-day promotion gate can't progress. This script answers the same
question offline by running both models on every stored observation in
the training pool and comparing predictions against realized outcomes.

Outputs:
  - Per-model action distribution (CONT/REV/SKIP)
  - Per-model win rate + mean R (excluding SKIPs)
  - Side-by-side comparison + simulated promotion verdict
  - JSON report at /app/data/rl/shadow_reports/backtest_<timestamp>.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/backend")

from src.rl.signal.comparison import (
    compute_daily_metrics,
    evaluate_promotion,
)
from src.rl.signal.ft_predictor import FTTransformerPredictor
from src.rl.signal.gbt_predictor import GBTPredictor

EP_DIR = Path("/app/data/rl/episodes")
MD_DIR = Path("/app/data/rl/models")
OUT_DIR = Path("/app/data/rl/shadow_reports")


def _build_predictions(
    model_name: str,
    predictor,
    obs_full: np.ndarray,
    obs_trigger: np.ndarray,
    rc: np.ndarray,
    rr: np.ndarray,
    use_trigger: bool,
) -> list[dict]:
    """Run predictor on every episode, attach realized_R for the chosen action."""
    n = len(obs_full)
    preds: list[dict] = []
    print(f"  predicting on {n} episodes...", flush=True)
    for i in range(n):
        if i % 2000 == 0 and i > 0:
            print(f"    {i}/{n}", flush=True)
        obs_in = obs_trigger[i] if use_trigger else obs_full[i]
        try:
            sig = predictor.predict(obs_in.astype(np.float32), zone_id=i, timestamp=float(i))
        except Exception as e:
            # FT-T expects 313-dim full obs; GBT expects 122-dim trigger obs.
            # If we mis-route, fall back to the other and log once.
            if i == 0:
                print(f"    {model_name} predict failed on first sample: {e}", flush=True)
            raise

        # realized_R for the action the model picked
        if sig.action == "CONTINUATION":
            realized = float(rc[i])
        elif sig.action == "REVERSAL":
            realized = float(rr[i])
        else:  # SKIP
            realized = 0.0  # SKIP doesn't trade

        preds.append(
            {
                "model_name": model_name,
                "action": sig.action,
                "p_cont": sig.p_cont,
                "p_rev": sig.p_rev,
                "p_skip": sig.p_skip,
                "confidence": sig.confidence,
                "expected_R": sig.expected_R,
                "win_probability": sig.win_probability,
                "realized_R": realized,
                "request_id": i,
            }
        )
    return preds


def _action_distribution(preds: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {"CONTINUATION": 0, "REVERSAL": 0, "SKIP": 0}
    for p in preds:
        out[p["action"]] = out.get(p["action"], 0) + 1
    return out


def main() -> None:
    print("=" * 80)
    print("Offline backtest: GBT (production) vs FT-Transformer (shadow)")
    print("=" * 80)

    obs_full = np.load(EP_DIR / "observations.npy")
    obs_trigger = np.load(EP_DIR / "trigger_observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")

    # OOS evaluation: load holdout indices saved by train_ft_v1.py if present
    holdout_path = MD_DIR / "ft_v1.holdout.npy"
    if holdout_path.exists():
        holdout_idx = np.load(holdout_path)
        obs_full = obs_full[holdout_idx]
        obs_trigger = obs_trigger[holdout_idx]
        rc = rc[holdout_idx]
        rr = rr[holdout_idx]
        print(f"\nOOS evaluation: using holdout indices from {holdout_path}")
    else:
        print(
            f"\nWARNING: no holdout indices at {holdout_path} — evaluating on full pool "
            "(in-sample, results will be inflated by training memorization)"
        )

    n = len(obs_full)
    print(f"\nPool: {n} episodes")
    print(f"  obs_full shape: {obs_full.shape}  (GBT will use trigger_obs {obs_trigger.shape})")
    print(f"  rewards: rc mean={rc.mean():+.3f}R  rr mean={rr.mean():+.3f}R")
    print(f"  base rates: cont_wins={(rc > 0).mean():.2%}  rev_wins={(rr > 0).mean():.2%}")

    # Load both predictors
    print("\nLoading predictors...")
    gbt = GBTPredictor.load(MD_DIR / "trigger_gbt_v5.joblib")
    gbt.name = "gbt_v5"
    print(f"  gbt_v5 loaded (input_dim={gbt.trigger_obs_dim})")

    ft = FTTransformerPredictor.load(MD_DIR / "ft_v1.pt")
    ft.name = "ft_v1"
    print("  ft_v1 loaded")

    # Run both
    print("\nGBT (production):")
    gbt_preds = _build_predictions("gbt_v5", gbt, obs_full, obs_trigger, rc, rr, use_trigger=True)

    print("\nFT-Transformer (shadow):")
    ft_preds = _build_predictions("ft_v1", ft, obs_full, obs_trigger, rc, rr, use_trigger=False)

    # Action distributions
    print("\n" + "=" * 80)
    print("Action distribution")
    print("=" * 80)
    for name, preds in [("gbt_v5", gbt_preds), ("ft_v1", ft_preds)]:
        dist = _action_distribution(preds)
        total = sum(dist.values())
        print(
            f"  {name:8s}: CONT={dist['CONTINUATION']:5d} ({100 * dist['CONTINUATION'] / total:5.1f}%)  "
            f"REV={dist['REVERSAL']:5d} ({100 * dist['REVERSAL'] / total:5.1f}%)  "
            f"SKIP={dist['SKIP']:5d} ({100 * dist['SKIP'] / total:5.1f}%)"
        )

    # Per-model metrics (SKIPs excluded by compute_daily_metrics)
    print("\n" + "=" * 80)
    print("Per-model metrics (non-SKIP only)")
    print("=" * 80)
    all_preds = gbt_preds + ft_preds
    metrics = compute_daily_metrics(all_preds)
    for name in ("gbt_v5", "ft_v1"):
        m = metrics.get(name)
        if m is None:
            print(f"  {name}: no non-SKIP predictions")
            continue
        print(f"  {name:8s}: n={m.n:5d}  WR={100 * m.win_rate:5.2f}%  mean_R={m.mean_R:+.4f}R")

    # Promotion verdict — treat the whole pool as a single "day"
    print("\n" + "=" * 80)
    print("Promotion verdict (whole pool as one day)")
    print("=" * 80)
    days = [{"gbt_v5": metrics["gbt_v5"], "ft_v1": metrics["ft_v1"]}]
    decision = evaluate_promotion(days, production="gbt_v5", candidate="ft_v1", min_consecutive=1)
    print(f"  should_promote: {decision.should_promote}")
    print(f"  reason: {decision.reason}")
    print(
        f"  margins required: wr>=+1.00pt mean_R>=+0.0500R | "
        f"actual: wr={100 * (metrics['ft_v1'].win_rate - metrics['gbt_v5'].win_rate):+.2f}pt "
        f"mean_R={metrics['ft_v1'].mean_R - metrics['gbt_v5'].mean_R:+.4f}R"
    )

    # Agreement rate (sanity — high overlap means models see the same patterns)
    print("\n" + "=" * 80)
    print("Action agreement")
    print("=" * 80)
    agree = sum(1 for g, f in zip(gbt_preds, ft_preds, strict=True) if g["action"] == f["action"])
    print(f"  GBT and FT agree on action: {agree}/{n} ({100 * agree / n:.1f}%)")

    # Save JSON report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": n,
        "action_distribution": {
            "gbt_v5": _action_distribution(gbt_preds),
            "ft_v1": _action_distribution(ft_preds),
        },
        "metrics": {name: {"win_rate": m.win_rate, "mean_R": m.mean_R, "n": m.n} for name, m in metrics.items()},
        "agreement_rate": agree / n,
        "promotion_decision": {
            "should_promote": decision.should_promote,
            "consecutive_days": decision.consecutive_days,
            "reason": decision.reason,
        },
    }
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
