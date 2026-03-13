"""Log Pinnacle coverage delta per provider per sport (M10d).

Reuses the same queries as extraction_report._build_pinnacle_delta()
but persists results to pinnacle_coverage_log for ML analysis.
"""
import logging
from sqlalchemy import func, distinct

logger = logging.getLogger(__name__)


def compute_coverage_delta(
    pinnacle_events: int,
    pinnacle_ml: int,
    pinnacle_spread: int,
    pinnacle_total: int,
    provider_matched: int,
    provider_ml: int,
    provider_spread: int,
    provider_total: int,
) -> dict:
    return {
        "event_coverage_pct": round(100 * provider_matched / pinnacle_events, 1) if pinnacle_events > 0 else 0.0,
        "ml_coverage_pct": round(100 * provider_ml / pinnacle_ml, 1) if pinnacle_ml > 0 else 0.0,
        "spread_coverage_pct": round(100 * provider_spread / pinnacle_spread, 1) if pinnacle_spread > 0 else 0.0,
        "total_coverage_pct": round(100 * provider_total / pinnacle_total, 1) if pinnacle_total > 0 else 0.0,
        "missing_events": pinnacle_events - provider_matched,
        "missing_spread": pinnacle_spread - provider_spread,
        "missing_total": pinnacle_total - provider_total,
    }


def log_coverage(session, run_id: str) -> int:
    from src.db.models import Odds, Event, PinnacleCoverageLog

    pin_sport_events = {}
    pin_rows = (
        session.query(Event.sport, func.count(distinct(Odds.event_id)))
        .join(Event, Odds.event_id == Event.id)
        .filter(Odds.provider_id == "pinnacle")
        .group_by(Event.sport)
        .all()
    )
    for sport, cnt in pin_rows:
        pin_sport_events[sport] = cnt

    if not pin_sport_events:
        return 0

    pin_sport_markets = {}
    pin_market_rows = (
        session.query(Event.sport, Odds.market, func.count(distinct(Odds.event_id)))
        .join(Event, Odds.event_id == Event.id)
        .filter(Odds.provider_id == "pinnacle")
        .group_by(Event.sport, Odds.market)
        .all()
    )
    for sport, market, cnt in pin_market_rows:
        if sport not in pin_sport_markets:
            pin_sport_markets[sport] = {"ml": 0, "spread": 0, "total": 0}
        mtype = "ml" if market in ("1x2", "moneyline") else market
        if mtype in pin_sport_markets[sport]:
            pin_sport_markets[sport][mtype] += cnt

    pin_event_ids_by_sport = {}
    for sport in pin_sport_events:
        ids = set(
            r[0] for r in session.query(distinct(Odds.event_id))
            .join(Event, Odds.event_id == Event.id)
            .filter(Odds.provider_id == "pinnacle", Event.sport == sport)
            .all()
        )
        pin_event_ids_by_sport[sport] = ids

    soft_providers = [
        r[0] for r in session.query(distinct(Odds.provider_id))
        .filter(Odds.provider_id.notin_(["pinnacle", "polymarket"]))
        .all()
    ]

    rows_written = 0
    for provider_id in soft_providers:
        for sport, pin_ids in pin_event_ids_by_sport.items():
            pin_total = len(pin_ids)
            if pin_total == 0:
                continue

            matched_ids = set(
                r[0] for r in session.query(distinct(Odds.event_id))
                .filter(
                    Odds.provider_id == provider_id,
                    Odds.event_id.in_(pin_ids),
                )
                .all()
            )
            matched = len(matched_ids)

            if matched == 0:
                row = PinnacleCoverageLog(
                    run_id=run_id,
                    provider_id=provider_id,
                    sport=sport,
                    pinnacle_events=pin_total,
                    pinnacle_ml_events=pin_sport_markets.get(sport, {}).get("ml", 0),
                    pinnacle_spread_events=pin_sport_markets.get(sport, {}).get("spread", 0),
                    pinnacle_total_events=pin_sport_markets.get(sport, {}).get("total", 0),
                    provider_matched_events=0,
                    event_coverage_pct=0.0,
                    ml_coverage_pct=0.0,
                    spread_coverage_pct=0.0,
                    total_coverage_pct=0.0,
                    missing_events=pin_total,
                    missing_spread=pin_sport_markets.get(sport, {}).get("spread", 0),
                    missing_total=pin_sport_markets.get(sport, {}).get("total", 0),
                )
                session.add(row)
                rows_written += 1
                continue

            shared_ids = list(matched_ids)
            pin_markets = pin_sport_markets.get(sport, {"ml": 0, "spread": 0, "total": 0})

            def _count_market(pid, market_filter, event_ids):
                return session.query(func.count(distinct(Odds.event_id))).filter(
                    Odds.provider_id == pid,
                    market_filter,
                    Odds.event_id.in_(event_ids),
                ).scalar() or 0

            p_ml = _count_market(provider_id, Odds.market.in_(["1x2", "moneyline"]), shared_ids)
            p_spr = _count_market(provider_id, Odds.market == "spread", shared_ids)
            p_tot = _count_market(provider_id, Odds.market == "total", shared_ids)

            pin_ml_shared = _count_market("pinnacle", Odds.market.in_(["1x2", "moneyline"]), shared_ids)
            pin_spr_shared = _count_market("pinnacle", Odds.market == "spread", shared_ids)
            pin_tot_shared = _count_market("pinnacle", Odds.market == "total", shared_ids)

            delta = compute_coverage_delta(
                pinnacle_events=pin_total,
                pinnacle_ml=pin_ml_shared,
                pinnacle_spread=pin_spr_shared,
                pinnacle_total=pin_tot_shared,
                provider_matched=matched,
                provider_ml=p_ml,
                provider_spread=p_spr,
                provider_total=p_tot,
            )

            row = PinnacleCoverageLog(
                run_id=run_id,
                provider_id=provider_id,
                sport=sport,
                pinnacle_events=pin_total,
                pinnacle_ml_events=pin_markets["ml"],
                pinnacle_spread_events=pin_markets["spread"],
                pinnacle_total_events=pin_markets["total"],
                provider_matched_events=matched,
                provider_ml_events=p_ml,
                provider_spread_events=p_spr,
                provider_total_events=p_tot,
                **delta,
            )
            session.add(row)
            rows_written += 1

    session.flush()
    logger.info(f"Logged {rows_written} pinnacle coverage rows for run {run_id}")
    return rows_written
