"""ML Feature Store — read/write feature vectors and outcomes."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import CandleSnapshot, MlFeature

logger = logging.getLogger(__name__)
CURRENT_FEATURE_VERSION = 1


def log_features(
    session: Session,
    domain: str,
    source_id: str,
    source_type: str,
    features: dict,
    feature_version: int = CURRENT_FEATURE_VERSION,
) -> MlFeature:
    row = MlFeature(
        domain=domain, source_id=source_id, source_type=source_type, features=features, feature_version=feature_version
    )
    session.add(row)
    session.flush()
    return row


def resolve_outcome(session: Session, source_type: str, source_id: str, outcome: float, outcome_binary: int) -> bool:
    row = session.query(MlFeature).filter_by(source_type=source_type, source_id=source_id).first()
    if row is None:
        return False
    row.outcome = outcome
    row.outcome_binary = outcome_binary
    row.resolved_at = datetime.now(timezone.utc)
    session.flush()
    return True


def get_training_data(
    session: Session, domain: str, source_type: str, feature_version: int | None = None
) -> list[MlFeature]:
    query = session.query(MlFeature).filter(
        MlFeature.domain == domain, MlFeature.source_type == source_type, MlFeature.outcome.isnot(None)
    )
    if feature_version is not None:
        query = query.filter(MlFeature.feature_version == feature_version)
    return query.order_by(MlFeature.created_at).all()


def resolve_clv_outcomes(session: Session) -> int:
    """Backfill outcome fields for betting ml_features rows."""
    from sqlalchemy import text

    updated = 0
    unresolved = (
        session.query(MlFeature)
        .filter(
            MlFeature.source_type == "opportunity",
            MlFeature.outcome.is_(None),
        )
        .all()
    )
    for row in unresolved:
        result = session.execute(
            text("SELECT closing_line_value FROM opportunities WHERE id = :oid"),
            {"oid": row.source_id},
        ).fetchone()
        if result and result[0] is not None:
            row.outcome = float(result[0])
            row.outcome_binary = 1 if result[0] > 0 else 0
            row.resolved_at = datetime.now(timezone.utc)
            updated += 1
    session.flush()
    return updated


def resolve_trading_outcomes(session: Session) -> int:
    """Backfill trading signal outcomes from completed trades.

    Links trading_signal -> trade via trade_id, uses r_multiple as outcome.
    """
    from src.db.models import Trade, TradingSignal

    unresolved = (
        session.query(MlFeature)
        .filter(
            MlFeature.domain == "trading",
            MlFeature.source_type == "trading_signal",
            MlFeature.outcome.is_(None),
        )
        .all()
    )

    count = 0
    for feat in unresolved:
        signal = session.query(TradingSignal).filter_by(id=feat.source_id).first()
        if not signal or not signal.trade_id:
            continue
        trade = session.query(Trade).filter_by(id=signal.trade_id).first()
        if not trade or trade.r_multiple is None:
            continue
        feat.outcome = trade.r_multiple
        feat.outcome_binary = 1 if trade.r_multiple > 0 else 0
        feat.resolved_at = datetime.now(timezone.utc)
        count += 1

    session.flush()
    return count


def resolve_boost_outcomes(session: Session, boost_title: str) -> int:
    """Resolve ML feature outcomes for a settled boost bet.

    Joins ml_features (source_type='boost', source_id=boost_title) to bets
    (bet_type='boost', outcome=boost_title) to propagate settlement results.

    Returns count of resolved feature rows.
    """
    from src.db.models import Bet

    bet = (
        session.query(Bet)
        .filter(
            Bet.bet_type == "boost",
            Bet.outcome == boost_title,
            Bet.result.isnot(None),
        )
        .first()
    )
    if not bet:
        return 0

    rows = (
        session.query(MlFeature)
        .filter(
            MlFeature.source_type == "boost",
            MlFeature.source_id == boost_title,
            MlFeature.outcome.is_(None),
        )
        .all()
    )
    if not rows:
        return 0

    now = datetime.now(timezone.utc)

    if bet.result == "void":
        for row in rows:
            session.delete(row)
        session.flush()
        return 0

    outcome_val = 1.0 if bet.result == "won" else 0.0
    outcome_bin = 1 if bet.result == "won" else 0

    for row in rows:
        row.outcome = outcome_val
        row.outcome_binary = outcome_bin
        row.resolved_at = now

    session.flush()
    return len(rows)


def log_candle_snapshot(session: Session, signal_id: int, candles: list[dict], timeframe: str = "1m") -> CandleSnapshot:
    row = CandleSnapshot(signal_id=signal_id, candles=candles, timeframe=timeframe)
    session.add(row)
    session.flush()
    return row
