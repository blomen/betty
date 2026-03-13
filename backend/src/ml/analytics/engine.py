"""Extraction analytics engine — computes provider ROI, coverage gaps, scheduling efficiency.

Queries existing tables (provider_run_metrics, sport_run_metrics, opportunities, bets)
directly. No dependency on Phase 1 ML tables.
"""
import logging
from sqlalchemy import func, case

from src.constants import PROVIDER_CANONICAL

logger = logging.getLogger(__name__)


def _canonical(provider_id: str) -> str:
    """Map provider to canonical (e.g., leovegas -> unibet)."""
    return PROVIDER_CANONICAL.get(provider_id, provider_id)


def compute_provider_roi(session, limit_runs: int = 10) -> list[dict]:
    """Compute per-provider ROI from opportunities and bets.

    Groups alias providers under their canonical provider.
    Returns list of dicts sorted by total_opportunities descending.
    """
    from src.db.models import Opportunity, Bet

    # Get all value opportunities grouped by provider
    opp_rows = (
        session.query(
            Opportunity.provider1_id,
            func.count().label("cnt"),
            func.avg(Opportunity.edge_pct).label("avg_edge"),
        )
        .filter(Opportunity.type == "value")
        .group_by(Opportunity.provider1_id)
        .all()
    )

    if not opp_rows:
        return []

    # Aggregate under canonical providers
    canonical_opps = {}
    for provider_id, cnt, avg_edge in opp_rows:
        canon = _canonical(provider_id)
        if canon not in canonical_opps:
            canonical_opps[canon] = {"total_opportunities": 0, "sum_edge": 0.0, "count": 0}
        canonical_opps[canon]["total_opportunities"] += cnt
        canonical_opps[canon]["sum_edge"] += (avg_edge or 0) * cnt
        canonical_opps[canon]["count"] += cnt

    # Get bet results grouped by provider
    bet_rows = (
        session.query(
            Bet.provider_id,
            func.count().label("total_bets"),
            func.sum(case((Bet.result == "won", 1), else_=0)).label("wins"),
            func.sum(case((Bet.result == "lost", 1), else_=0)).label("losses"),
            func.sum(case(
                (Bet.result == "won", Bet.payout - Bet.stake),
                (Bet.result == "lost", -Bet.stake),
                else_=0,
            )).label("net_pnl"),
        )
        .filter(Bet.result.in_(["won", "lost"]))
        .group_by(Bet.provider_id)
        .all()
    )

    canonical_bets = {}
    for provider_id, total, wins, losses, pnl in bet_rows:
        canon = _canonical(provider_id)
        if canon not in canonical_bets:
            canonical_bets[canon] = {"total_bets": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        canonical_bets[canon]["total_bets"] += total
        canonical_bets[canon]["wins"] += wins
        canonical_bets[canon]["losses"] += losses
        canonical_bets[canon]["net_pnl"] += float(pnl or 0)

    # Build result list
    results = []
    for canon, opp_data in canonical_opps.items():
        bet_data = canonical_bets.get(canon, {"total_bets": 0, "wins": 0, "losses": 0, "net_pnl": 0.0})
        resolved = bet_data["wins"] + bet_data["losses"]
        results.append({
            "provider_id": canon,
            "total_opportunities": opp_data["total_opportunities"],
            "avg_edge": round(opp_data["sum_edge"] / opp_data["count"], 2) if opp_data["count"] > 0 else 0.0,
            "total_bets": bet_data["total_bets"],
            "win_rate": round(bet_data["wins"] / resolved, 3) if resolved > 0 else None,
            "net_pnl": round(bet_data["net_pnl"], 2),
        })

    results.sort(key=lambda x: x["total_opportunities"], reverse=True)
    return results


def compute_coverage_gaps(session) -> list[dict]:
    """Compute per-provider per-sport coverage vs Pinnacle from sport_run_metrics.

    Uses the latest run's data per provider per sport. Compares each soft
    provider's event/market counts against Pinnacle's baseline.

    Returns list of dicts sorted by missing_events descending (biggest gaps first).
    """
    from sqlalchemy import text

    # Get Pinnacle baseline per sport (latest run)
    pin_rows = session.execute(text("""
        SELECT sport, events_extracted, ml_count, spread_count, total_count
        FROM sport_run_metrics
        WHERE provider_id = 'pinnacle'
        AND run_id = (SELECT run_id FROM sport_run_metrics WHERE provider_id = 'pinnacle' ORDER BY rowid DESC LIMIT 1)
    """)).fetchall()

    if not pin_rows:
        return []

    pinnacle_baseline = {}
    for sport, events, ml, spread, total in pin_rows:
        pinnacle_baseline[sport] = {
            "events": events, "ml": ml, "spread": spread, "total": total,
        }

    # Get soft provider data (latest run per provider)
    soft_rows = session.execute(text("""
        SELECT provider_id, sport, events_matched, ml_count, spread_count, total_count
        FROM sport_run_metrics
        WHERE provider_id NOT IN ('pinnacle', 'polymarket')
        AND run_id IN (
            SELECT DISTINCT run_id FROM sport_run_metrics
            WHERE provider_id NOT IN ('pinnacle', 'polymarket')
            ORDER BY rowid DESC
            LIMIT 1
        )
    """)).fetchall()

    results = []
    for provider_id, sport, matched, ml, spread, total in soft_rows:
        pin = pinnacle_baseline.get(sport)
        if not pin:
            continue

        pin_events = pin["events"]
        coverage_pct = round(100 * matched / pin_events, 1) if pin_events > 0 else 0.0

        results.append({
            "provider_id": provider_id,
            "sport": sport,
            "pinnacle_events": pin_events,
            "matched_events": matched,
            "event_coverage_pct": coverage_pct,
            "missing_events": pin_events - matched,
            "ml_count": ml,
            "spread_count": spread,
            "total_count": total,
            "pinnacle_ml_count": pin["ml"],
            "pinnacle_spread_count": pin["spread"],
            "pinnacle_total_count": pin["total"],
            "missing_spread": pin["spread"] - spread,
            "missing_total": pin["total"] - total,
        })

    results.sort(key=lambda x: x["missing_events"], reverse=True)
    return results
