"""Compute per-model daily metrics + promotion criterion.

Promotion criterion: candidate model must beat production by:
  - win_rate margin >= 0.01 (1 percentage point)
  - mean_R margin >= 0.05 (5 basis points of R)
For min_consecutive days (default 30).

A single losing day resets the streak. This is intentionally strict —
shadow promotions are high-stakes (changing the production predictor)
and should be supported by strong empirical evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyMetrics:
    win_rate: float
    mean_R: float
    n: int


@dataclass(frozen=True)
class PromotionDecision:
    should_promote: bool
    consecutive_days: int
    reason: str


def compute_daily_metrics(predictions: list[dict]) -> dict[str, DailyMetrics]:
    """Group predictions by model_name, compute WR/mean_R."""
    by_model: dict[str, list[dict]] = {}
    for p in predictions:
        if p.get("action") == "SKIP":
            continue
        by_model.setdefault(p["model_name"], []).append(p)

    out: dict[str, DailyMetrics] = {}
    for model_name, preds in by_model.items():
        if not preds:
            continue
        wins = sum(1 for p in preds if p.get("realized_R", 0) > 0)
        total_R = sum(p.get("realized_R", 0) for p in preds)
        out[model_name] = DailyMetrics(
            win_rate=wins / len(preds),
            mean_R=total_R / len(preds),
            n=len(preds),
        )
    return out


def evaluate_promotion(
    days: list[dict[str, DailyMetrics]],
    production: str,
    candidate: str,
    min_consecutive: int = 30,
    wr_margin: float = 0.01,
    mean_R_margin: float = 0.05,
) -> PromotionDecision:
    """Count consecutive days at the END of the days list where
    candidate beats production by both margins."""
    consecutive = 0
    for day in reversed(days):
        prod_m = day.get(production)
        cand_m = day.get(candidate)
        if prod_m is None or cand_m is None:
            break
        wr_diff = cand_m.win_rate - prod_m.win_rate
        r_diff = cand_m.mean_R - prod_m.mean_R
        if wr_diff >= wr_margin and r_diff >= mean_R_margin:
            consecutive += 1
        else:
            break

    should_promote = consecutive >= min_consecutive
    reason = (
        f"candidate beat production for {consecutive}/{min_consecutive} consecutive days"
        if should_promote
        else f"only {consecutive}/{min_consecutive} consecutive winning days — wait"
    )
    return PromotionDecision(
        should_promote=should_promote,
        consecutive_days=consecutive,
        reason=reason,
    )
