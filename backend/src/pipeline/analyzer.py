"""
Opportunity Analyzer

Integrates analysis functions into the extraction pipeline.
Detects arbitrage and value betting opportunities after odds are stored.

Architecture:
    Extraction (orchestrator.py)
        |
        v
    [Database: Events + Odds]
        |
        +---> scan_arbitrage() --> Arb opportunities
        |
        +---> scan_value() --> Value bets (5%+ edge, de-vigged sharps)
        |
        +---> scan_bonus() --> Bonus mode (anchor vs counterpart, any edge)
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import Event, Odds, Opportunity, Profile
from ..analysis.arbitrage import find_arbitrage
from ..analysis.value import find_best_value, get_fair_odds
from ..analysis.scanner import OpportunityScanner, BonusOpportunity

logger = logging.getLogger(__name__)

# Sharp providers used as "truth" sources for value detection
SHARP_PROVIDERS = {"polymarket", "pinnacle"}


class OpportunityAnalyzer:
    """
    Analyzes stored odds to detect opportunities.

    Runs after extraction to find:
    - Arbitrage: Sum of implied probabilities < 100%
    - Value: Provider odds exceed fair odds from sharp sources

    Usage:
        analyzer = OpportunityAnalyzer(session)
        results = analyzer.run()
    """

    def __init__(self, session: Session, min_arb_pct: float = None, min_edge_pct: float = None):
        """
        Initialize analyzer.

        Args:
            session: SQLAlchemy session
            min_arb_pct: Minimum arbitrage profit % (default from profile or 0.5)
            min_edge_pct: Minimum value edge % (default from profile or 2.0)
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

        self.min_arb_pct = min_arb_pct if min_arb_pct is not None else (
            getattr(profile, 'min_arb_pct', 0.5) if profile else 0.5
        )
        self.min_edge_pct = min_edge_pct if min_edge_pct is not None else (
            getattr(profile, 'min_edge_pct', 2.0) if profile else 2.0
        )

    def run(self) -> dict:
        """
        Run opportunity detection on all events with 2+ providers.

        Returns:
            Dictionary with analysis results:
            {
                "arbitrage": {"found": int, "new": int},
                "value": {"found": int, "new": int},
                "events_analyzed": int
            }
        """
        logger.info("[Analyzer] Starting opportunity detection...")

        # Deactivate all existing opportunities (will be refreshed)
        self._deactivate_existing()

        # Get events with odds from 2+ providers
        events = self._get_multi_provider_events()

        results = {
            "arbitrage": {"found": 0, "new": 0},
            "value": {"found": 0, "new": 0},
            "events_analyzed": len(events)
        }

        for event in events:
            # Group odds by market -> outcome -> providers
            odds_grouped = self._group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                # Detect arbitrage
                arb_result = self._detect_arbitrage(event.id, market, odds_by_outcome)
                if arb_result:
                    results["arbitrage"]["found"] += 1
                    if arb_result == "new":
                        results["arbitrage"]["new"] += 1

                # Detect value
                value_count = self._detect_value(event.id, market, odds_by_outcome)
                results["value"]["found"] += value_count["found"]
                results["value"]["new"] += value_count["new"]

        self.session.commit()

        logger.info(
            f"[Analyzer] Complete: {results['events_analyzed']} events analyzed, "
            f"{results['arbitrage']['found']} arbs, {results['value']['found']} value bets"
        )

        return results

    def run_bonus(
        self,
        anchor_provider: str,
        counterpart_providers: list[str] = None,
        devig: bool = True,
        blend_weight: float = 0.6,
    ) -> dict:
        """
        Run bonus-specific analysis.

        Finds ALL opportunities for clearing a bonus at the anchor provider,
        compared against sharp counterpart providers. No edge threshold -
        returns all matches sorted by edge (best first, including negative).

        Args:
            anchor_provider: Provider where bonus bet must be placed (e.g., "unibet")
            counterpart_providers: Sharp providers to compare against (default ["pinnacle", "polymarket"])
            devig: Whether to de-vig counterpart odds (default True)
            blend_weight: Pinnacle weight when blending (default 0.6)

        Returns:
            {
                "opportunities": list[BonusOpportunity],
                "count": int,
                "best_edge": float,
                "worst_edge": float,
                "positive_count": int,  # Number with edge > 0
                "anchor_provider": str,
                "counterpart_providers": list[str],
            }

        Example:
            >>> analyzer = OpportunityAnalyzer(session)
            >>> result = analyzer.run_bonus("unibet", ["pinnacle", "polymarket"])
            >>> for opp in result["opportunities"][:5]:
            ...     print(f"{opp.edge_pct:+.1f}% {opp.event_id} {opp.outcome}")
        """
        if counterpart_providers is None:
            counterpart_providers = ["pinnacle", "polymarket"]

        logger.info(
            f"[Analyzer] Running bonus scan: anchor={anchor_provider}, "
            f"counterparts={counterpart_providers}"
        )

        opportunities = self.scanner.scan_bonus(
            anchor_provider=anchor_provider,
            counterpart_providers=counterpart_providers,
            devig=devig,
            blend_weight=blend_weight,
        )

        result = {
            "opportunities": opportunities,
            "count": len(opportunities),
            "best_edge": max((o.edge_pct for o in opportunities), default=0),
            "worst_edge": min((o.edge_pct for o in opportunities), default=0),
            "positive_count": sum(1 for o in opportunities if o.edge_pct > 0),
            "anchor_provider": anchor_provider,
            "counterpart_providers": counterpart_providers,
        }

        logger.info(
            f"[Analyzer] Bonus scan complete: {result['count']} opportunities, "
            f"{result['positive_count']} positive edge, "
            f"best={result['best_edge']:+.1f}%, worst={result['worst_edge']:+.1f}%"
        )

        return result

    def _deactivate_existing(self):
        """Mark all existing active opportunities as inactive."""
        updated = self.session.query(Opportunity).filter(
            Opportunity.is_active == True
        ).update({"is_active": False})
        logger.debug(f"[Analyzer] Deactivated {updated} existing opportunities")

    def _get_multi_provider_events(self) -> list[Event]:
        """Get events that have odds from 2+ providers."""
        return (
            self.session.query(Event)
            .join(Odds)
            .group_by(Event.id)
            .having(func.count(func.distinct(Odds.provider_id)) >= 2)
            .all()
        )

    def _group_odds(self, event: Event) -> dict[str, dict[str, list[dict]]]:
        """
        Group event odds by market -> outcome -> provider list.

        Returns:
            {
                "1x2": {
                    "home": [{"provider": "unibet", "odds": 2.10, "point": None}, ...],
                    "draw": [...],
                    "away": [...]
                },
                "over_under": {
                    "over": [{"provider": "bet365", "odds": 1.95, "point": 2.5}, ...],
                    "under": [...]
                }
            }
        """
        grouped = defaultdict(lambda: defaultdict(list))

        for odds in event.odds:
            # Create market key that includes point for spread/totals
            if odds.point is not None:
                market_key = f"{odds.market}_{odds.point}"
            else:
                market_key = odds.market

            grouped[market_key][odds.outcome].append({
                "provider": odds.provider_id,
                "odds": odds.odds,
                "point": odds.point
            })

        return dict(grouped)

    def _detect_arbitrage(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]]
    ) -> Optional[str]:
        """
        Detect arbitrage opportunity for a market.

        Returns:
            "new" if new opportunity stored
            "updated" if existing opportunity reactivated
            None if no opportunity found
        """
        arb = find_arbitrage(
            event_id=event_id,
            market=market,
            odds_by_outcome=odds_by_outcome,
            min_profit_pct=self.min_arb_pct
        )

        if not arb:
            return None

        logger.info(
            f"[Analyzer] Arb found: {event_id} {market} +{arb.profit_pct}%"
        )

        # Build outcomes JSON for storage
        outcomes_json = [
            {
                "provider": o["provider"],
                "outcome": o["outcome"],
                "odds": o["odds"]
            }
            for o in arb.outcomes
        ]

        # Add stakes to outcomes
        for i, stake in enumerate(arb.stakes):
            outcomes_json[i]["stake"] = stake["stake"]
            outcomes_json[i]["return"] = stake["return"]

        # Extract point value from market key if present
        point_value = None
        if "_" in market and market.split("_")[-1].replace(".", "").replace("-", "").isdigit():
            parts = market.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    point_value = float(parts[-1])
                    market = parts[0]  # Remove point from market name
                except ValueError:
                    pass

        # Check for existing opportunity (same event/market/type)
        existing = self.session.query(Opportunity).filter(
            Opportunity.event_id == event_id,
            Opportunity.market == market,
            Opportunity.type == "arbitrage"
        ).first()

        if existing:
            # Reactivate and update
            existing.is_active = True
            existing.profit_pct = arb.profit_pct
            existing.outcomes = outcomes_json
            existing.point = point_value
            existing.total_stake = 100.0
            existing.detected_at = datetime.now(timezone.utc)

            # Update legacy fields for backwards compat
            if len(arb.outcomes) >= 2:
                existing.provider1_id = arb.outcomes[0]["provider"]
                existing.odds1 = arb.outcomes[0]["odds"]
                existing.outcome1 = arb.outcomes[0]["outcome"]
                existing.provider2_id = arb.outcomes[1]["provider"]
                existing.odds2 = arb.outcomes[1]["odds"]
                existing.outcome2 = arb.outcomes[1]["outcome"]

            return "updated"
        else:
            # Create new opportunity
            opp = Opportunity(
                type="arbitrage",
                event_id=event_id,
                market=market,
                profit_pct=arb.profit_pct,
                outcomes=outcomes_json,
                point=point_value,
                total_stake=100.0,
                is_active=True,
                detected_at=datetime.now(timezone.utc)
            )

            # Set legacy fields
            if len(arb.outcomes) >= 2:
                opp.provider1_id = arb.outcomes[0]["provider"]
                opp.odds1 = arb.outcomes[0]["odds"]
                opp.outcome1 = arb.outcomes[0]["outcome"]
                opp.provider2_id = arb.outcomes[1]["provider"]
                opp.odds2 = arb.outcomes[1]["odds"]
                opp.outcome2 = arb.outcomes[1]["outcome"]

            self.session.add(opp)
            return "new"

    def _detect_value(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]]
    ) -> dict:
        """
        Detect value betting opportunities for a market.

        Uses Polymarket as primary fair odds source, falls back to Pinnacle.

        Returns:
            {"found": int, "new": int}
        """
        result = {"found": 0, "new": 0}

        for outcome, provider_odds_list in odds_by_outcome.items():
            # Find fair odds from sharp provider
            fair_odds = None
            fair_provider = None

            # Priority: Polymarket > Pinnacle
            for sharp in ["polymarket", "pinnacle"]:
                sharp_odds = next(
                    (p for p in provider_odds_list if p["provider"] == sharp),
                    None
                )
                if sharp_odds:
                    fair_odds = sharp_odds["odds"]
                    fair_provider = sharp
                    break

            if not fair_odds:
                continue  # No sharp provider for this outcome

            # Filter to non-sharp providers only
            soft_providers = [
                p for p in provider_odds_list
                if p["provider"] not in SHARP_PROVIDERS
            ]

            if not soft_providers:
                continue  # Only sharp providers have odds

            # Find best value
            value = find_best_value(
                event_id=event_id,
                market=market,
                outcome=outcome,
                fair_odds=fair_odds,
                provider_odds_list=soft_providers,
                min_edge_pct=self.min_edge_pct
            )

            if not value:
                continue

            result["found"] += 1

            logger.info(
                f"[Analyzer] Value found: {event_id} {market} {outcome} "
                f"@ {value.provider} {value.provider_odds} (+{value.edge_pct}% vs {fair_provider})"
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
                    "provider": value.provider,
                    "outcome": outcome,
                    "odds": value.provider_odds,
                    "edge_pct": value.edge_pct
                },
                {
                    "provider": fair_provider,
                    "outcome": outcome,
                    "odds": fair_odds,
                    "is_fair_odds": True
                }
            ]

            # Check for existing
            existing = self.session.query(Opportunity).filter(
                Opportunity.event_id == event_id,
                Opportunity.market == clean_market,
                Opportunity.type == "value",
                Opportunity.outcome1 == outcome
            ).first()

            if existing:
                existing.is_active = True
                existing.edge_pct = value.edge_pct
                existing.provider1_id = value.provider
                existing.odds1 = value.provider_odds
                existing.provider2_id = fair_provider
                existing.odds2 = fair_odds
                existing.outcomes = outcomes_json
                existing.point = point_value
                existing.detected_at = datetime.now(timezone.utc)
            else:
                opp = Opportunity(
                    type="value",
                    event_id=event_id,
                    market=clean_market,
                    outcome1=outcome,
                    edge_pct=value.edge_pct,
                    provider1_id=value.provider,
                    odds1=value.provider_odds,
                    provider2_id=fair_provider,
                    odds2=fair_odds,
                    outcomes=outcomes_json,
                    point=point_value,
                    is_active=True,
                    detected_at=datetime.now(timezone.utc)
                )
                self.session.add(opp)
                result["new"] += 1

        return result
