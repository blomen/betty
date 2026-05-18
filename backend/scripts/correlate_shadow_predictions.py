"""For every shadow prediction missing a broker_trade_id, find the corresponding
broker_trade (production records only — shadow predictions don't trade)
and copy the realized R back by setting broker_trade_id.

Runs nightly via cron, similar to the existing signal correlate cron.

Idempotent — only updates rows where broker_trade_id IS NULL.

Timezone note: ShadowPrediction.ts is timezone-aware (UTC). BrokerTrade.ts is
timezone-naive (UTC implied). We strip timezone info when querying BrokerTrade
so Postgres doesn't raise "can't compare offset-naive and offset-aware datetimes".
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_

# Path fix when run as a script from any cwd
sys.path.insert(0, "/app/backend")

from src.db.models import BrokerTrade, ShadowPrediction, get_session_factory


def correlate(lookback_hours: int = 24) -> int:
    """Match shadow_predictions to broker_trades by zone touch time.

    For each production ShadowPrediction with no broker_trade_id yet, find the
    earliest closed BrokerTrade whose ts falls within 5 minutes after the
    prediction. Returns number of matches written.

    The 5-minute window is generous — typical entry happens within seconds, but
    slow fills and retries can stretch the gap. Taking the earliest matching
    trade (ORDER BY ts ASC, LIMIT 1) assumes one trade per signal, which is the
    normal case. Phantom-close rows (closed_at IS NULL) are excluded so we only
    link completed trades with known outcomes.
    """
    now_utc = datetime.now(timezone.utc)
    cutoff_aware = now_utc - timedelta(hours=lookback_hours)
    # Naive UTC equivalent for BrokerTrade comparisons (BrokerTrade.ts is naive)
    cutoff_naive = cutoff_aware.replace(tzinfo=None)

    Session = get_session_factory()
    count = 0
    with Session() as session:
        # Get production predictions with no broker_trade_id yet
        preds = (
            session.query(ShadowPrediction)
            .filter(
                and_(
                    ShadowPrediction.ts >= cutoff_aware,
                    ShadowPrediction.is_production.is_(True),
                    ShadowPrediction.broker_trade_id.is_(None),
                )
            )
            .all()
        )
        for p in preds:
            # Strip tz from prediction ts to match BrokerTrade's naive UTC column
            p_ts_naive = p.ts.replace(tzinfo=None)
            window_end_naive = p_ts_naive + timedelta(minutes=5)

            # Find the earliest closed broker_trade in the 5-minute window after
            # the prediction. Closed_at must be set (trade must be complete).
            t = (
                session.query(BrokerTrade)
                .filter(
                    and_(
                        BrokerTrade.ts >= p_ts_naive,
                        BrokerTrade.ts <= window_end_naive,
                        BrokerTrade.closed_at.isnot(None),
                    )
                )
                .order_by(BrokerTrade.ts)
                .first()
            )
            if t is not None:
                p.broker_trade_id = t.id
                count += 1
        session.commit()
    return count


if __name__ == "__main__":
    n = correlate()
    print(f"correlated {n} shadow predictions")
