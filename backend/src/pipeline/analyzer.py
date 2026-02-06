"""
Opportunity Analyzer

Integrates OpportunityScanner into the extraction pipeline.
Delegates value detection to scanner (which applies all quality gates),
then persists results to the Opportunity table.

Architecture:
    Extraction (orchestrator.py)
        |
        v
    [Database: Events + Odds]
        |
        +---> scanner.find_value_in_market() --> Value bets (de-vigged Pinnacle)
        |
        +---> scanner.scan_bonus() --> Bonus mode (anchor vs counterpart, any edge)
        |
        v
    [Opportunity table] --> UI
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import Event, Odds, Opportunity, Profile
from ..analysis.scanner import OpportunityScanner, BonusOpportunity

logger = logging.getLogger(__name__)


class OpportunityAnalyzer:
    """
    Analyzes stored odds to detect opportunities.

    Runs after extraction to find:
    - Value: Provider odds exceed fair odds from sharp sources

    Usage:
        analyzer = OpportunityAnalyzer(session)
        results = analyzer.run()
    """

    def __init__(self, session: Session, min_edge_pct: float = None):
        """
        Initialize analyzer.

        Args:
            session: SQLAlchemy session
            min_edge_pct: Minimum value edge % (default from profile or 5.0)
        """
        self.session = session
        self.scanner = OpportunityScanner(session)

        # Get thresholds from active profile or use defaults
        profile = None
        try:
            profile = session.query(Profile).filter(Profile.is_active == True).first()
        except Exception as e:
            # Profile table may have different schema - use defaults
            logger.debug(f"[Analyzer] Could not load profile: {e}")

        self.min_edge_pct = min_edge_pct if min_edge_pct is not None else (
            getattr(profile, 'min_edge_pct', 5.0) if profile else 5.0
        )

    def run(self) -> dict:
        """
        Run opportunity detection on all events with 2+ providers.

        Returns:
            Dictionary with analysis results:
            {
                "value": {"found": int, "new": int},
                "events_analyzed": int
            }
        """
        logger.info("[Analyzer] Starting opportunity detection...")

        # Clean up stale opportunities before detection
        cleanup_stats = self._cleanup_stale()

        # Get events with odds from 2+ providers
        events = self._get_multi_provider_events()

        results = {
            "value": {"found": 0, "new": 0},
            "events_analyzed": len(events),
            "cleanup": cleanup_stats
        }

        for event in events:
            # Group odds by market -> outcome -> providers (delegates to scanner)
            odds_grouped = self.scanner.group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                # Detect value via scanner, then persist best per outcome
                value_count = self._detect_value(event.id, market, odds_by_outcome)
                results["value"]["found"] += value_count["found"]
                results["value"]["new"] += value_count["new"]

        self.session.commit()

        logger.info(
            f"[Analyzer] Complete: {results['events_analyzed']} events analyzed, "
            f"{results['value']['found']} value bets"
        )

        return results

    def run_bonus(
        self,
        anchor_provider: str,
        devig: bool = True,
    ) -> dict:
        """
        Run bonus-specific analysis.

        Finds ALL opportunities for clearing a bonus at the anchor provider,
        compared against Pinnacle as the sole sharp source. No edge threshold -
        returns all matches sorted by edge (best first, including negative).

        Args:
            anchor_provider: Provider where bonus bet must be placed (e.g., "unibet")
            devig: Whether to de-vig Pinnacle odds (default True)

        Returns:
            {
                "opportunities": list[BonusOpportunity],
                "count": int,
                "best_edge": float,
                "worst_edge": float,
                "positive_count": int,  # Number with edge > 0
                "anchor_provider": str,
            }

        Example:
            >>> analyzer = OpportunityAnalyzer(session)
            >>> result = analyzer.run_bonus("unibet")
            >>> for opp in result["opportunities"][:5]:
            ...     print(f"{opp.edge_pct:+.1f}% {opp.event_id} {opp.outcome}")
        """
        logger.info(f"[Analyzer] Running bonus scan: anchor={anchor_provider}")

        opportunities = self.scanner.scan_bonus(
            anchor_provider=anchor_provider,
            devig=devig,
        )

        result = {
            "opportunities": opportunities,
            "count": len(opportunities),
            "best_edge": max((o.edge_pct for o in opportunities), default=0),
            "worst_edge": min((o.edge_pct for o in opportunities), default=0),
            "positive_count": sum(1 for o in opportunities if o.edge_pct > 0),
            "anchor_provider": anchor_provider,
        }

        logger.info(
            f"[Analyzer] Bonus scan complete: {result['count']} opportunities, "
            f"{result['positive_count']} positive edge, "
            f"best={result['best_edge']:+.1f}%, worst={result['worst_edge']:+.1f}%"
        )

        return result

    def _cleanup_stale(self) -> dict:
        """
        Clean up stale data from database.

        Deletes:
        1. Inactive opportunities (from previous runs)
        2. Orphaned opportunities (event no longer exists)
        3. Opportunities for past events (already started)
        4. Past events and their odds (cascade) — preserves events with bets

        Also deactivates current opportunities (will be refreshed).

        Returns:
            {"inactive": int, "orphaned": int, "past_events": int,
             "past_events_deleted": int, "deactivated": int}
        """
        from ..db.models import Bet

        stats = {"inactive": 0, "orphaned": 0, "past_events": 0,
                 "past_events_deleted": 0, "deactivated": 0}
        now = datetime.now(timezone.utc)

        # 1. Delete inactive opportunities
        stats["inactive"] = self.session.query(Opportunity).filter(
            Opportunity.is_active == False
        ).delete()

        # 2. Delete orphaned opportunities (event doesn't exist) — SQL subquery
        valid_event_subq = self.session.query(Event.id).subquery()
        stats["orphaned"] = self.session.query(Opportunity).filter(
            ~Opportunity.event_id.in_(self.session.query(valid_event_subq))
        ).delete(synchronize_session=False)

        # 3. Delete opportunities for past events — SQL subquery
        past_event_subq = self.session.query(Event.id).filter(
            Event.start_time < now
        ).subquery()
        stats["past_events"] = self.session.query(Opportunity).filter(
            Opportunity.event_id.in_(self.session.query(past_event_subq))
        ).delete(synchronize_session=False)

        # 4. Delete past events + their odds (cascade: all, delete-orphan)
        #    Preserve events that have bets (historical record)
        #    Must materialize IDs here — individual session.delete() needed for cascade
        past_event_ids = [
            e.id for e in self.session.query(Event.id).filter(
                Event.start_time < now
            ).all()
        ]
        if past_event_ids:
            event_ids_with_bets = set(
                row[0] for row in self.session.query(Bet.event_id).filter(
                    Bet.event_id.in_(past_event_ids)
                ).all()
                if row[0]
            )
            deletable_ids = [
                eid for eid in past_event_ids if eid not in event_ids_with_bets
            ]
            if deletable_ids:
                for i in range(0, len(deletable_ids), 500):
                    batch = deletable_ids[i:i + 500]
                    past_events = self.session.query(Event).filter(
                        Event.id.in_(batch)
                    ).all()
                    for event in past_events:
                        self.session.delete(event)  # Cascades to odds
                        stats["past_events_deleted"] += 1

        # 5. Deactivate remaining (will be refreshed during detection)
        stats["deactivated"] = self.session.query(Opportunity).filter(
            Opportunity.is_active == True
        ).update({"is_active": False})

        total_cleaned = (
            stats["inactive"] + stats["orphaned"] + stats["past_events"]
        )
        if total_cleaned > 0 or stats["past_events_deleted"] > 0:
            logger.info(
                f"[Analyzer] Cleanup: {stats['inactive']} inactive opps, "
                f"{stats['orphaned']} orphaned opps, {stats['past_events']} past opps, "
                f"{stats['past_events_deleted']} past events+odds deleted"
            )
        logger.debug(
            f"[Analyzer] Deactivated {stats['deactivated']} existing opportunities"
        )

        return stats

    def _get_multi_provider_events(self) -> list[Event]:
        """Get events that have odds from 2+ providers."""
        return (
            self.session.query(Event)
            .join(Odds)
            .group_by(Event.id)
            .having(func.count(func.distinct(Odds.provider_id)) >= 2)
            .all()
        )

    def _detect_value(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]]
    ) -> dict:
        """
        Detect value betting opportunities for a market.

        Delegates to scanner.find_value_in_market() which applies all quality gates:
        - MAX_ODDS_RATIO: rejects likely event mismatches
        - MIN_VALID_PROB_SUM: rejects incomplete soft provider markets
        - Market type mismatch: prevents 3-way vs 2-way comparison
        - Pinnacle market completeness: validates prob_sum before de-vigging

        Keeps only the best value bet per outcome (highest edge) and upserts
        to the Opportunity table.

        Returns:
            {"found": int, "new": int}
        """
        result = {"found": 0, "new": 0}

        # Delegate to scanner (all quality gates applied here)
        value_bets = self.scanner.find_value_in_market(
            event_id=event_id,
            market=market,
            odds_by_outcome=odds_by_outcome,
            min_edge_pct=self.min_edge_pct,
        )

        if not value_bets:
            return result

        # Keep best per outcome (highest edge)
        best_by_outcome: dict = {}
        for vb in value_bets:
            existing_best = best_by_outcome.get(vb.outcome)
            if existing_best is None or vb.edge_pct > existing_best.edge_pct:
                best_by_outcome[vb.outcome] = vb

        for outcome, vb in best_by_outcome.items():
            result["found"] += 1

            logger.info(
                f"[Analyzer] Value found: {event_id} {market} {outcome} "
                f"@ {vb.provider} {vb.provider_odds} (+{vb.edge_pct}% vs pinnacle)"
            )

            # Extract point value from market key if present
            point_value = None
            clean_market = market
            if "_" in market and market.split("_")[-1].replace(".", "").replace("-", "").isdigit():
                parts = market.rsplit("_", 1)
                if len(parts) == 2:
                    try:
                        point_value = float(parts[-1])
                        clean_market = parts[0]
                    except ValueError:
                        pass

            # Build outcomes JSON
            outcomes_json = [
                {
                    "provider": vb.provider,
                    "outcome": outcome,
                    "odds": vb.provider_odds,
                    "edge_pct": vb.edge_pct
                },
                {
                    "provider": "pinnacle",
                    "outcome": outcome,
                    "odds": vb.fair_odds,
                    "is_fair_odds": True
                }
            ]

            # Upsert to Opportunity table
            existing = self.session.query(Opportunity).filter(
                Opportunity.event_id == event_id,
                Opportunity.market == clean_market,
                Opportunity.type == "value",
                Opportunity.outcome1 == outcome
            ).first()

            if existing:
                existing.is_active = True
                existing.edge_pct = vb.edge_pct
                existing.provider1_id = vb.provider
                existing.odds1 = vb.provider_odds
                existing.provider2_id = "pinnacle"
                existing.odds2 = vb.fair_odds
                existing.outcomes = outcomes_json
                existing.point = point_value
                existing.detected_at = datetime.now(timezone.utc)
            else:
                opp = Opportunity(
                    type="value",
                    event_id=event_id,
                    market=clean_market,
                    outcome1=outcome,
                    edge_pct=vb.edge_pct,
                    provider1_id=vb.provider,
                    odds1=vb.provider_odds,
                    provider2_id="pinnacle",
                    odds2=vb.fair_odds,
                    outcomes=outcomes_json,
                    point=point_value,
                    is_active=True,
                    detected_at=datetime.now(timezone.utc)
                )
                self.session.add(opp)
                result["new"] += 1

        return result
