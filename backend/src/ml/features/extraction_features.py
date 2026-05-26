"""Extract features for extraction pipeline optimization (M10).

Logs per-run context (timing, health, volume) and per-provider attribution
(events, odds, value bet yield). Connects extraction decisions to downstream
value outcomes.
"""

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def extract_extraction_features(
    run_id: str,
    trigger: str,
    providers_attempted: int,
    providers_succeeded: int,
    providers_failed: int,
    total_events: int,
    total_odds: int,
    avg_match_rate: float,
    circuit_breakers_open: int = 0,
    last_sharp_run_time: datetime | None = None,
    last_soft_run_time: datetime | None = None,
    events_starting_next_2h: int | None = None,
    events_starting_next_6h: int | None = None,
) -> dict:
    now = datetime.now(UTC)

    minutes_since_sharp = None
    if last_sharp_run_time:
        if last_sharp_run_time.tzinfo is None:
            last_sharp_run_time = last_sharp_run_time.replace(tzinfo=UTC)
        minutes_since_sharp = (now - last_sharp_run_time).total_seconds() / 60

    minutes_since_soft = None
    if last_soft_run_time:
        if last_soft_run_time.tzinfo is None:
            last_soft_run_time = last_soft_run_time.replace(tzinfo=UTC)
        minutes_since_soft = (now - last_soft_run_time).total_seconds() / 60

    return {
        "run_id": run_id,
        "trigger": trigger,
        "hour_of_day": now.hour,
        "day_of_week": now.weekday(),
        "minutes_since_last_sharp": minutes_since_sharp,
        "minutes_since_last_soft": minutes_since_soft,
        "events_starting_next_2h": events_starting_next_2h,
        "events_starting_next_6h": events_starting_next_6h,
        "providers_attempted": providers_attempted,
        "providers_succeeded": providers_succeeded,
        "providers_failed": providers_failed,
        "circuit_breakers_open": circuit_breakers_open,
        "total_events": total_events,
        "total_odds": total_odds,
        "avg_match_rate": avg_match_rate,
    }


def extract_provider_value(
    run_id: str,
    provider_id: str,
    events_extracted: int,
    odds_extracted: int,
    duration_seconds: float,
    match_rate: float,
    spread_count: int = 0,
    total_count: int = 0,
    value_bets_from_provider: int | None = None,
    avg_edge_from_provider: float | None = None,
    exclusive_events: int | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "provider_id": provider_id,
        "events_extracted": events_extracted,
        "odds_extracted": odds_extracted,
        "duration_seconds": duration_seconds,
        "match_rate": match_rate,
        "spread_count": spread_count,
        "total_count": total_count,
        "value_bets_from_provider": value_bets_from_provider,
        "avg_edge_from_provider": avg_edge_from_provider,
        "exclusive_events": exclusive_events,
    }


def log_extraction_run(session, features: dict) -> None:
    from src.db.models import ExtractionFeature

    row = ExtractionFeature(**features)
    session.add(row)
    session.flush()
    logger.debug(f"Logged extraction features for run {features.get('run_id')}")


def log_provider_value(session, features: dict) -> None:
    from src.db.models import ProviderValueLog

    row = ProviderValueLog(**features)
    session.add(row)
    session.flush()


def update_extraction_outcomes(
    session,
    run_id: str,
    value_bets_found: int,
    avg_edge_pct: float | None,
    arb_opportunities_found: int = 0,
    reverse_opportunities_found: int = 0,
) -> None:
    from src.db.models import ExtractionFeature

    row = session.query(ExtractionFeature).filter_by(run_id=run_id).first()
    if row:
        row.value_bets_found = value_bets_found
        row.avg_edge_pct = avg_edge_pct
        row.arb_opportunities_found = arb_opportunities_found
        row.reverse_opportunities_found = reverse_opportunities_found
        session.flush()
