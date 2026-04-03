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
        AND run_id = (SELECT run_id FROM sport_run_metrics WHERE provider_id = 'pinnacle' ORDER BY id DESC LIMIT 1)
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
        AND run_id = (
            SELECT run_id FROM sport_run_metrics
            WHERE provider_id NOT IN ('pinnacle', 'polymarket')
            ORDER BY id DESC
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


def compute_scheduling_efficiency(session) -> dict:

    """Compute per-tier scheduling metrics from extraction_runs.

    Returns dict keyed by trigger name with avg duration, events, odds, and events/sec.
    """
    from sqlalchemy import text

    rows = session.execute(text("""
        SELECT trigger,
            COUNT(*) as runs,
            AVG(duration_seconds) as avg_duration,
            AVG(total_events) as avg_events,
            AVG(total_odds) as avg_odds
        FROM extraction_runs
        GROUP BY trigger
    """)).fetchall()

    results = {}
    for trigger, runs, avg_dur, avg_events, avg_odds in rows:
        avg_dur = float(avg_dur)
        avg_events = float(avg_events)
        avg_odds = float(avg_odds)
        events_per_sec = round(avg_events / avg_dur, 1) if avg_dur > 0 else 0.0
        results[trigger] = {
            "runs": runs,
            "avg_duration": round(avg_dur, 1),
            "avg_events": round(avg_events, 1),
            "avg_odds": round(avg_odds, 1),
            "events_per_sec": events_per_sec,
        }

    return results


class AnalyticsEngine:
    """Orchestrates analytics computation and recommendation generation."""

    def refresh(self, session, run_id: str) -> dict:
        """Run full analytics refresh after an extraction run.

        1. Compute provider ROI
        2. Compute coverage gaps
        3. Compute scheduling efficiency
        4. Run diagnostics on provider metrics
        5. Create/update recommendations

        Returns dict with all analytics results.
        """
        from .diagnostics import diagnose_provider
        from .recommendations import RecommendationManager
        from sqlalchemy import text

        provider_roi = compute_provider_roi(session)
        coverage_gaps = compute_coverage_gaps(session)
        scheduling = compute_scheduling_efficiency(session)

        # Build per-provider diagnostic data from recent provider_run_metrics
        # Uses last 10 runs per provider (not all-time) to avoid stale historical data
        # dragging down match rates after issues have been fixed.
        provider_metrics = session.execute(text("""
            WITH recent AS (
                SELECT provider_id, duration_seconds, events_processed,
                    events_matched, spread_count, total_count,
                    ROW_NUMBER() OVER (PARTITION BY provider_id ORDER BY start_time DESC) as rn
                FROM provider_run_metrics
                WHERE provider_id NOT IN ('pinnacle', 'polymarket')
                  AND status = 'success'
            )
            SELECT provider_id,
                AVG(duration_seconds) as avg_duration,
                AVG(events_processed) as avg_events,
                AVG(CASE WHEN events_processed > 0
                    THEN CAST(events_matched AS REAL) / events_processed
                    ELSE 0 END) as avg_match_rate,
                SUM(spread_count) as spread_count,
                SUM(total_count) as total_count
            FROM recent
            WHERE rn <= 10
            GROUP BY provider_id
        """)).fetchall()

        mgr = RecommendationManager(session)
        all_recs = []

        for pid, avg_dur, avg_events, avg_mr, spr_cnt, tot_cnt in provider_metrics:
            avg_dur = float(avg_dur) if avg_dur else 0
            avg_events = float(avg_events) if avg_events else 0
            avg_mr = float(avg_mr) if avg_mr else 0
            # Find matching ROI data
            roi_data = next((r for r in provider_roi if r["provider_id"] == pid), {})
            total_opps = roi_data.get("total_opportunities", 0)
            sec_per_vb = round(avg_dur / max(total_opps / 10, 1), 1) if avg_dur and total_opps else None

            diag_data = {
                "provider_id": pid,
                "avg_match_rate": avg_mr,
                "avg_events": avg_events,
                "avg_duration": avg_dur,
                "total_opportunities": total_opps,
                "seconds_per_value_bet": sec_per_vb,
                "spread_count": spr_cnt or 0,
                "total_count": tot_cnt or 0,
            }

            recommendations = diagnose_provider(diag_data)
            triggered_categories = {rec["category"] for rec in recommendations}

            for rec in recommendations:
                created = mgr.create(
                    provider_id=pid,
                    category=rec["category"],
                    severity=rec["severity"],
                    message=rec["message"],
                    before_metric=rec.get("diagnostic_data", {}).get("current"),
                    diagnostic_data=rec.get("diagnostic_data"),
                )
                all_recs.append(created)

            # Auto-resolve open recommendations for categories that no longer trigger
            for open_rec in mgr.get_active(provider_id=pid):
                if open_rec.category not in triggered_categories and open_rec.status == "open":
                    mgr.update_status(open_rec.id, "resolved", after_metric=avg_mr)

        # Auto-resolve recs for providers with no recent metrics (disabled/removed)
        diagnosed_providers = {row[0] for row in provider_metrics}
        for open_rec in mgr.get_active():
            if open_rec.provider_id not in diagnosed_providers and open_rec.status == "open":
                mgr.update_status(open_rec.id, "resolved")

        session.flush()

        return {
            "provider_roi": provider_roi,
            "coverage_gaps": coverage_gaps,
            "scheduling": scheduling,
            "recommendations": [
                {"id": r.id, "provider_id": r.provider_id, "category": r.category,
                 "severity": r.severity, "message": r.message, "status": r.status}
                for r in all_recs
            ],
        }
