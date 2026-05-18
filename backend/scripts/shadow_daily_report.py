"""Daily shadow vs production comparison. Run via cron at 00:05 UTC
after correlate_shadow_predictions.py runs. Prints summary; writes JSON
to /app/data/rl/shadow_reports/YYYY-MM-DD.json for tracking.

Output keys:
  - generated_at: ISO timestamp
  - days: list of {date, metrics: {model_name: DailyMetrics dict}}
  - promotion_decision: {should_promote, consecutive_days, reason}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Path fix when run from any cwd inside the container
sys.path.insert(0, "/app/backend")

from src.db.models import BrokerTrade, ShadowPrediction, get_session_factory
from src.rl.signal.comparison import (
    DailyMetrics,
    compute_daily_metrics,
    evaluate_promotion,
)


def daily_report(days_back: int = 30) -> dict:
    """Build per-day per-model metrics + check promotion criterion.

    Walks `days_back` UTC calendar days backward from today, queries
    shadow_predictions for each, joins to broker_trades for realized R
    on production rows.
    """
    now = datetime.now(timezone.utc)
    out: dict = {"generated_at": now.isoformat(), "days": []}

    Session = get_session_factory()
    with Session() as session:
        for d in range(days_back, 0, -1):
            date_start = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
            date_end = date_start + timedelta(days=1)
            rows = (
                session.query(ShadowPrediction)
                .filter(ShadowPrediction.ts >= date_start, ShadowPrediction.ts < date_end)
                .all()
            )
            preds = []
            for r in rows:
                d_dict = {
                    "model_name": r.model_name,
                    "action": r.action,
                    "win_probability": r.win_probability,
                    "p_cont": r.p_cont,
                    "p_rev": r.p_rev,
                    "p_skip": r.p_skip,
                    "expected_R": r.expected_R,
                    "request_id": r.request_id,
                    "broker_trade_id": r.broker_trade_id,
                }
                if r.broker_trade_id is not None:
                    t = session.query(BrokerTrade).filter_by(id=r.broker_trade_id).first()
                    if t is not None and t.pnl_r is not None:
                        d_dict["realized_R"] = float(t.pnl_r)
                preds.append(d_dict)
            metrics = compute_daily_metrics(preds)
            out["days"].append(
                {
                    "date": date_start.strftime("%Y-%m-%d"),
                    "metrics": {k: {"win_rate": v.win_rate, "mean_R": v.mean_R, "n": v.n} for k, v in metrics.items()},
                }
            )

    # Promotion check: build list of DailyMetrics per day where BOTH models
    # have predictions
    days_metrics: list[dict[str, DailyMetrics]] = []
    for d in out["days"]:
        ms = d["metrics"]
        if "gbt_v5" in ms and "ft_v1" in ms:
            days_metrics.append(
                {
                    "gbt_v5": DailyMetrics(**ms["gbt_v5"]),
                    "ft_v1": DailyMetrics(**ms["ft_v1"]),
                }
            )
    decision = evaluate_promotion(days_metrics, production="gbt_v5", candidate="ft_v1", min_consecutive=30)
    out["promotion_decision"] = {
        "should_promote": decision.should_promote,
        "consecutive_days": decision.consecutive_days,
        "reason": decision.reason,
    }
    return out


def _save_report(report: dict) -> Path:
    out_dir = Path("/app/data/rl/shadow_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_file = out_dir / f"{today}.json"
    out_file.write_text(json.dumps(report, indent=2, default=str))
    return out_file


def main() -> None:
    report = daily_report()
    print(json.dumps(report, indent=2, default=str))
    out_file = _save_report(report)
    print(f"\nSaved to {out_file}", flush=True)


if __name__ == "__main__":
    main()
