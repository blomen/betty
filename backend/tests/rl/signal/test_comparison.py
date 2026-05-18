import numpy as np

from src.rl.signal.comparison import (
    DailyMetrics,
    PromotionDecision,
    compute_daily_metrics,
    evaluate_promotion,
)


def test_compute_daily_metrics():
    predictions = [
        {"model_name": "gbt", "action": "CONTINUATION", "win_probability": 0.7, "p_cont": 0.7, "p_rev": 0.2, "p_skip": 0.1, "expected_R": 1.5},
        {"model_name": "gbt", "action": "REVERSAL", "win_probability": 0.4, "p_cont": 0.3, "p_rev": 0.5, "p_skip": 0.2, "expected_R": 0.5},
        {"model_name": "ft", "action": "CONTINUATION", "win_probability": 0.8, "p_cont": 0.8, "p_rev": 0.15, "p_skip": 0.05, "expected_R": 2.0},
        {"model_name": "ft", "action": "CONTINUATION", "win_probability": 0.6, "p_cont": 0.6, "p_rev": 0.3, "p_skip": 0.1, "expected_R": 1.0},
    ]
    realized_R_by_request = {0: 1.5, 1: -0.5, 2: 2.5, 3: 1.0}
    for i, p in enumerate(predictions):
        p["request_id"] = i
        p["realized_R"] = realized_R_by_request[i]

    metrics = compute_daily_metrics(predictions)
    assert "gbt" in metrics
    assert "ft" in metrics
    # GBT: both non-SKIP. WR = 1/2 = 50% (1.5 > 0, -0.5 < 0)
    assert metrics["gbt"].win_rate == 0.5
    # FT: 2 wins out of 2 = 100%
    assert metrics["ft"].win_rate == 1.0


def test_promotion_requires_30_consecutive_days_meeting_threshold():
    """Promotion requires N consecutive days where FT beats GBT by margin."""
    days = [
        {
            "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
            "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
        }
        for _ in range(30)
    ]
    decision = evaluate_promotion(days, production="gbt", candidate="ft", min_consecutive=30)
    assert decision.should_promote is True
    assert decision.consecutive_days == 30


def test_promotion_rejects_when_fewer_consecutive_days():
    days = [
        {
            "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
            "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
        }
        for _ in range(10)
    ]
    decision = evaluate_promotion(days, production="gbt", candidate="ft", min_consecutive=30)
    assert decision.should_promote is False
    assert decision.consecutive_days < 30


def test_promotion_resets_on_a_losing_day():
    """A single day where FT doesn't beat GBT resets the streak."""
    days = []
    for _ in range(20):
        days.append(
            {
                "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
                "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
            }
        )
    days.append(
        {
            "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
            "ft": DailyMetrics(win_rate=0.40, mean_R=0.10, n=10),
        }
    )
    for _ in range(10):
        days.append(
            {
                "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
                "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
            }
        )
    decision = evaluate_promotion(days, production="gbt", candidate="ft", min_consecutive=30)
    assert decision.should_promote is False
    assert decision.consecutive_days == 10
