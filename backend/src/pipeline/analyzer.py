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
from sqlalchemy.orm import Session

from ..db.models import Profile
from ..repositories import EventRepo, OpportunityRepo
from ..analysis.scanner import OpportunityScanner, BonusOpportunity, DutchOpportunity

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
        self.event_repo = EventRepo(session)
        self.opp_repo = OpportunityRepo(session)

        # Get thresholds from active profile or use defaults
        profile = None
        try:
            profile = session.query(Profile).filter(Profile.is_active == True).first()
        except Exception as e:
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
        cleanup_stats = self.opp_repo.cleanup_stale()

        # Get events with odds from 2+ providers
        events = self.event_repo.get_multi_provider_events(min_providers=2)

        results = {
            "value": {"found": 0, "new": 0},
            "dutch": {"found": 0, "new": 0},
            "reverse": {"found": 0, "new": 0},
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

                # Detect dutch/reverse (cross-book opportunities)
                dutch_count = self._detect_dutch(event, market, odds_by_outcome)
                results["dutch"]["found"] += dutch_count["dutch_found"]
                results["dutch"]["new"] += dutch_count["dutch_new"]
                results["reverse"]["found"] += dutch_count["reverse_found"]
                results["reverse"]["new"] += dutch_count["reverse_new"]

        self.session.commit()

        logger.info(
            f"[Analyzer] Complete: {results['events_analyzed']} events analyzed, "
            f"{results['value']['found']} value bets, "
            f"{results['dutch']['found']} dutch, "
            f"{results['reverse']['found']} reverse"
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

        # Keep best per (outcome, provider) — each provider gets its own opportunity
        best_by_outcome_provider: dict = {}
        for vb in value_bets:
            key = (vb.outcome, vb.provider)
            existing_best = best_by_outcome_provider.get(key)
            if existing_best is None or vb.edge_pct > existing_best.edge_pct:
                best_by_outcome_provider[key] = vb

        for (outcome, _provider), vb in best_by_outcome_provider.items():
            result["found"] += 1

            logger.debug(
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

            # Upsert to Opportunity table via repo
            is_new = self.opp_repo.upsert_value(
                event_id=event_id,
                market=clean_market,
                outcome=outcome,
                provider_id=vb.provider,
                provider_odds=vb.provider_odds,
                fair_odds=vb.fair_odds,
                edge_pct=vb.edge_pct,
                outcomes_json=outcomes_json,
                point=point_value,
            )
            if is_new:
                result["new"] += 1

        return result

    def _detect_dutch(
        self,
        event,
        market: str,
        odds_by_outcome: dict[str, list[dict]]
    ) -> dict:
        """
        Detect dutch and reverse dutch opportunities for a market.

        Dutch: ALL legs have positive edge (pure cross-book value).
        Reverse: At least one soft +EV leg, others covered at negative edge.

        Returns:
            {"dutch_found": int, "dutch_new": int, "reverse_found": int, "reverse_new": int}
        """
        result = {"dutch_found": 0, "dutch_new": 0, "reverse_found": 0, "reverse_new": 0}

        opp = self.scanner._find_dutch_in_market(
            event=event,
            market=market,
            odds_by_outcome=odds_by_outcome,
        )

        if opp is None:
            return result

        # Extract point from market key
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

        # Classify: all legs +EV = dutch, otherwise = reverse
        all_positive = all(leg["edge_pct"] > 0 for leg in opp.legs)
        opp_type = "dutch" if all_positive else "reverse"

        providers_str = ", ".join(f"{leg['provider']}({leg['outcome']})" for leg in opp.legs)
        logger.debug(
            f"[Analyzer] {opp_type.capitalize()} found: {event.id} {market} "
            f"GP={opp.guaranteed_profit_pct:+.2f}% [{providers_str}]"
        )

        result[f"{opp_type}_found"] = 1

        upsert_fn = self.opp_repo.upsert_dutch if opp_type == "dutch" else self.opp_repo.upsert_reverse
        is_new = upsert_fn(
            event_id=event.id,
            market=clean_market,
            legs=opp.legs,
            combined_edge_pct=opp.combined_edge_pct,
            guaranteed_profit_pct=opp.guaranteed_profit_pct,
            point=point_value,
        )
        if is_new:
            result[f"{opp_type}_new"] = 1

        return result
