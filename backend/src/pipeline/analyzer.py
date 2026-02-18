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
from ..services.bet_service import BetService
from ..analysis.scanner import OpportunityScanner, BonusOpportunity, DutchOpportunity
from ..constants import CANONICAL_MEMBERS, PROVIDER_CANONICAL

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

        # Snapshot closing odds for pending bets on started events
        # (must run BEFORE cleanup deletes past event odds)
        try:
            bet_service = BetService(self.session)
            clv_snapshot = bet_service.snapshot_closing_odds()
        except Exception as e:
            logger.warning(f"[Analyzer] CLV snapshot failed: {e}")
            clv_snapshot = {"processed": 0, "updated": 0}

        # Clean up stale opportunities before detection
        cleanup_stats = self.opp_repo.cleanup_stale()

        # Get events with odds from 2+ providers
        events = self.event_repo.get_multi_provider_events(min_providers=2)

        results = {
            "value": {"found": 0, "new": 0, "fanned": 0},
            "dutch": {"found": 0, "new": 0, "fanned": 0},
            "reverse": {"found": 0, "new": 0},
            "reverse_value": {"found": 0, "new": 0},
            "events_analyzed": len(events),
            "cleanup": cleanup_stats,
            "clv_snapshot": clv_snapshot,
        }

        for event in events:
            # Group odds by market -> outcome -> providers (delegates to scanner)
            odds_grouped = self.scanner.group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                # Detect value via scanner, then persist best per outcome
                value_count = self._detect_value(event.id, market, odds_by_outcome, odds_grouped)
                results["value"]["found"] += value_count["found"]
                results["value"]["new"] += value_count["new"]
                results["value"]["fanned"] += value_count.get("fanned", 0)

                # Detect dutch/reverse (cross-book opportunities)
                dutch_count = self._detect_dutch(event, market, odds_by_outcome, odds_grouped)
                results["dutch"]["found"] += dutch_count["dutch_found"]
                results["dutch"]["new"] += dutch_count["dutch_new"]
                results["dutch"]["fanned"] += dutch_count.get("dutch_fanned", 0)
                results["reverse"]["found"] += dutch_count["reverse_found"]
                results["reverse"]["new"] += dutch_count["reverse_new"]

                # Detect reverse value (Pinnacle vs soft consensus)
                rv_count = self._detect_reverse_value(event.id, market, odds_by_outcome, odds_grouped)
                results["reverse_value"]["found"] += rv_count["found"]
                results["reverse_value"]["new"] += rv_count["new"]

        self.session.commit()

        logger.info(
            f"[Analyzer] Complete: {results['events_analyzed']} events analyzed, "
            f"{results['value']['found']} value bets, "
            f"{results['dutch']['found']} dutch, "
            f"{results['reverse']['found']} reverse, "
            f"{results['reverse_value']['found']} reverse_value"
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
        # Resolve to canonical for DB query (odds stored under canonical after consolidation)
        # e.g., run_bonus("expekt") → queries DB for "unibet" odds (same platform)
        query_provider = PROVIDER_CANONICAL.get(anchor_provider, anchor_provider)
        if query_provider != anchor_provider:
            logger.info(
                f"[Analyzer] Running bonus scan: anchor={anchor_provider} "
                f"(querying as {query_provider}, same platform)"
            )
        else:
            logger.info(f"[Analyzer] Running bonus scan: anchor={anchor_provider}")

        opportunities = self.scanner.scan_bonus(
            anchor_provider=query_provider,
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
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
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
        result = {"found": 0, "new": 0, "fanned": 0}

        # Delegate to scanner (all quality gates applied here)
        value_bets = self.scanner.find_value_in_market(
            event_id=event_id,
            market=market,
            odds_by_outcome=odds_by_outcome,
            min_edge_pct=self.min_edge_pct,
            all_markets=all_markets,
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

            # Fan out to all platform members (e.g., unibet → all 8 Kambi brands)
            fan_providers = CANONICAL_MEMBERS.get(vb.provider, [vb.provider])
            result["fanned"] += len(fan_providers) - 1  # track fan-out inflation
            for fan_provider in fan_providers:
                # Build outcomes JSON per fan provider
                outcomes_json = [
                    {
                        "provider": fan_provider,
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
                    provider_id=fan_provider,
                    provider_odds=vb.provider_odds,
                    fair_odds=vb.fair_odds,
                    edge_pct=vb.edge_pct,
                    outcomes_json=outcomes_json,
                    point=point_value,
                )
                if is_new:
                    result["new"] += 1

        return result

    def _detect_reverse_value(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
    ) -> dict:
        """
        Detect reverse value opportunities: Pinnacle raw odds vs soft consensus.

        Delegates to scanner.find_reverse_value_in_market() which applies:
        - MIN_REVERSE_ODDS / MAX_REVERSE_ODDS filters (longshots only)
        - MIN_CONSENSUS_PLATFORMS (5+ independent platforms)
        - MAX_ODDS_RATIO discrepancy check
        - MAX_EDGE_PCT cap

        Returns:
            {"found": int, "new": int}
        """
        result = {"found": 0, "new": 0}

        reverse_bets = self.scanner.find_reverse_value_in_market(
            event_id=event_id,
            market=market,
            odds_by_outcome=odds_by_outcome,
            min_edge_pct=self.min_edge_pct,
            all_markets=all_markets,
        )

        if not reverse_bets:
            return result

        for vb in reverse_bets:
            result["found"] += 1

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

            outcomes_json = [
                {
                    "provider": "pinnacle",
                    "outcome": vb.outcome,
                    "odds": vb.provider_odds,
                    "edge_pct": vb.edge_pct,
                },
                {
                    "provider": "consensus",
                    "outcome": vb.outcome,
                    "odds": vb.fair_odds,
                    "is_fair_odds": True,
                }
            ]

            is_new = self.opp_repo.upsert_reverse_value(
                event_id=event_id,
                market=clean_market,
                outcome=vb.outcome,
                pinnacle_odds=vb.provider_odds,
                consensus_fair_odds=vb.fair_odds,
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
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
    ) -> dict:
        """
        Detect dutch opportunities for a market.

        Soft book legs with +EV; Pinnacle legs at fair odds (0% edge) as coverage.
        Requires at least one soft +EV leg.

        Returns:
            {"dutch_found": int, "dutch_new": int, "reverse_found": int, "reverse_new": int}
        """
        result = {"dutch_found": 0, "dutch_new": 0, "dutch_fanned": 0, "reverse_found": 0, "reverse_new": 0}

        opp = self.scanner._find_dutch_in_market(
            event=event,
            market=market,
            odds_by_outcome=odds_by_outcome,
            all_markets=all_markets,
        )

        if opp is None:
            return result

        # Require at least one +EV leg
        if not any(leg["edge_pct"] > 0 for leg in opp.legs):
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

        providers_str = ", ".join(f"{leg['provider']}({leg['outcome']})" for leg in opp.legs)
        logger.debug(
            f"[Analyzer] Dutch found: {event.id} {market} "
            f"GP={opp.guaranteed_profit_pct:+.2f}% [{providers_str}]"
        )

        result["dutch_found"] = 1

        # Fan out dutch opportunities: for each soft leg, expand to all platform members
        # Build list of fan-out provider combinations for the soft legs
        soft_leg_indices = [i for i, leg in enumerate(opp.legs) if leg.get("edge_pct", 0) > 0]

        # Collect all platform member sets for soft legs
        fan_out_sets = {}
        for i in soft_leg_indices:
            leg_provider = opp.legs[i]["provider"]
            fan_out_sets[i] = CANONICAL_MEMBERS.get(leg_provider, [leg_provider])

        if not soft_leg_indices or all(len(fan_out_sets[i]) <= 1 for i in soft_leg_indices):
            # No fan-out needed — single provider per leg
            is_new = self.opp_repo.upsert_dutch(
                event_id=event.id,
                market=clean_market,
                legs=opp.legs,
                combined_edge_pct=opp.combined_edge_pct,
                guaranteed_profit_pct=opp.guaranteed_profit_pct,
                point=point_value,
            )
            if is_new:
                result["dutch_new"] = 1
        else:
            # Fan out: create one dutch opportunity per member provider combination
            # For simplicity, fan out the first soft leg (most common case: one soft + pinnacle)
            for idx in soft_leg_indices:
                result["dutch_fanned"] += len(fan_out_sets[idx]) - 1
                for member in fan_out_sets[idx]:
                    fanned_legs = []
                    for i, leg in enumerate(opp.legs):
                        if i == idx:
                            fanned_legs.append({**leg, "provider": member})
                        else:
                            fanned_legs.append(leg)

                    is_new = self.opp_repo.upsert_dutch(
                        event_id=event.id,
                        market=clean_market,
                        legs=fanned_legs,
                        combined_edge_pct=opp.combined_edge_pct,
                        guaranteed_profit_pct=opp.guaranteed_profit_pct,
                        point=point_value,
                    )
                    if is_new:
                        result["dutch_new"] += 1

        return result
