"""ML Feature Store — read/write feature vectors and outcomes."""
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from src.db.models import MlFeature, CandleSnapshot

logger = logging.getLogger(__name__)
CURRENT_FEATURE_VERSION = 1


def log_features(session: Session, domain: str, source_id: str, source_type: str,
                 features: dict, feature_version: int = CURRENT_FEATURE_VERSION) -> MlFeature:
    row = MlFeature(domain=domain, source_id=source_id, source_type=source_type,
                    features=features, feature_version=feature_version)
    session.add(row)
    session.flush()
    return row


def resolve_outcome(session: Session, source_type: str, source_id: str,
                    outcome: float, outcome_binary: int) -> bool:
    row = session.query(MlFeature).filter_by(source_type=source_type, source_id=source_id).first()
    if row is None:
        return False
    row.outcome = outcome
    row.outcome_binary = outcome_binary
    row.resolved_at = datetime.now(timezone.utc)
    session.flush()
    return True


def get_training_data(session: Session, domain: str, source_type: str,
                      feature_version: int | None = None) -> list[MlFeature]:
    query = session.query(MlFeature).filter(
        MlFeature.domain == domain, MlFeature.source_type == source_type, MlFeature.outcome.isnot(None))
    if feature_version is not None:
        query = query.filter(MlFeature.feature_version == feature_version)
    return query.order_by(MlFeature.created_at).all()


def resolve_clv_outcomes(session: Session) -> int:
    """Backfill outcome fields for betting ml_features rows."""
    from sqlalchemy import text
    updated = 0
    unresolved = session.query(MlFeature).filter(
        MlFeature.source_type == "opportunity",
        MlFeature.outcome.is_(None),
    ).all()
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


def log_candle_snapshot(session: Session, signal_id: int, candles: list[dict],
                        timeframe: str = "1m") -> CandleSnapshot:
    row = CandleSnapshot(signal_id=signal_id, candles=candles, timeframe=timeframe)
    session.add(row)
    session.flush()
    return row
