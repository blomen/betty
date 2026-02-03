"""
Opportunity Scanner

Unified scanning interface for finding betting opportunities:
- Arbitrage: Cross-provider guaranteed profit
- Value: Edge vs de-vigged sharp odds
- Bonus: Any edge for bonus clearing (no threshold)

This module queries the database and returns opportunity dataclasses.
Storage/persistence is handled by the caller (analyzer.py).
"""

from dataclasses import dataclass
from typing import Optional
from collections import defaultdict
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import Event, Odds
from .arbitrage import find_arbitrage, ArbitrageOpportunity
from .value import find_value, ValueBet
from .devig import (
    calculate_margin,
    devig_multiplicative,
    get_fair_odds_for_outcome,
)
from ..constants import SHARP_PROVIDERS, EXCLUDED_FROM_SCANS

logger = logging.getLogger(__name__)


@dataclass
class BonusOpportunity:
    """An opportunity for bonus clearing."""

    event_id: str
    market: str
    outcome: str

    # The bet at anchor provider
    anchor_provider: str
    anchor_odds: float

    # Fair odds from Pinnacle (de-vigged)
    fair_odds: float
    fair_source: str  # "pinnacle" or "pinnacle(raw)"

    # The edge (can be negative for bonus clearing)
    edge_pct: float

    # Event context
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    sport: Optional[str] = None
    league: Optional[str] = None


class OpportunityScanner:
    """
    Scans database for betting opportunities.

    Usage:
        scanner = OpportunityScanner(session)

        # Standard analysis
        arbs = scanner.scan_arbitrage(min_profit_pct=0.5)
        values = scanner.scan_value(min_edge_pct=5.0)

        # Bonus mode (no threshold, all opportunities)
        bonus = scanner.scan_bonus("unibet", ["pinnacle", "polymarket"])
    """

    def __init__(self, session: Session):
        self.session = session

    def scan_arbitrage(self, min_profit_pct: float = 0.5) -> list[ArbitrageOpportunity]:
        """
        Find arbitrage opportunities across providers.

        Requires different providers for different outcomes (true arb).

        Args:
            min_profit_pct: Minimum profit percentage (default 0.5%)

        Returns:
            List of ArbitrageOpportunity sorted by profit (highest first)
        """
        opportunities = []

        # Get events with odds from 2+ providers
        events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self._group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                arb = find_arbitrage(
                    event_id=event.id,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    min_profit_pct=min_profit_pct,
                )
                if arb:
                    opportunities.append(arb)

        # Sort by profit (highest first)
        opportunities.sort(key=lambda x: x.profit_pct, reverse=True)

        logger.info(f"[Scanner] Found {len(opportunities)} arbitrage opportunities")
        return opportunities

    def scan_value(
        self,
        min_edge_pct: float = 5.0,
        sharp_priority: list[str] = None,
        blend_weight: float = 0.6,
    ) -> list[ValueBet]:
        """
        Find value bets against de-vigged Pinnacle odds.

        Uses Pinnacle as the sole sharp source. Their ~2.5% margin is
        removed using multiplicative de-vigging.

        Args:
            min_edge_pct: Minimum edge percentage (default 5%)
            sharp_priority: Ignored (kept for backward compatibility)
            blend_weight: Ignored (kept for backward compatibility)

        Returns:
            List of ValueBet sorted by edge (highest first)
        """
        if sharp_priority is None:
            sharp_priority = ["pinnacle"]

        opportunities = []

        # Get events with odds from 2+ providers
        events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self._group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                values = self._find_value_in_market(
                    event_id=event.id,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    min_edge_pct=min_edge_pct,
                    sharp_priority=sharp_priority,
                    blend_weight=blend_weight,
                )
                opportunities.extend(values)

        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge_pct, reverse=True)

        logger.info(f"[Scanner] Found {len(opportunities)} value bets (>={min_edge_pct}% edge)")
        return opportunities

    def scan_bonus(
        self,
        anchor_provider: str,
        counterpart_providers: list[str] = None,
        devig: bool = True,
        blend_weight: float = 0.6,
    ) -> list[BonusOpportunity]:
        """
        Find ALL opportunities for bonus clearing at anchor provider.

        Unlike value scan, this has NO edge threshold - returns all matches
        sorted by edge (best first, including negative edges).

        Uses Pinnacle as the sole sharp source for fair odds.

        Args:
            anchor_provider: Provider where bonus bet must be placed
            counterpart_providers: Ignored (kept for backward compatibility)
            devig: Whether to de-vig Pinnacle odds (default True)
            blend_weight: Ignored (kept for backward compatibility)

        Returns:
            List of BonusOpportunity sorted by edge (highest first, negatives last)
        """
        if counterpart_providers is None:
            counterpart_providers = ["pinnacle"]

        opportunities = []

        # Get events where anchor provider has odds
        events = self._get_events_with_provider(anchor_provider)

        for event in events:
            odds_grouped = self._group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                bonus_opps = self._find_bonus_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    anchor_provider=anchor_provider,
                    counterpart_providers=counterpart_providers,
                    devig=devig,
                    blend_weight=blend_weight,
                )
                opportunities.extend(bonus_opps)

        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} bonus opportunities for {anchor_provider}"
        )
        return opportunities

    def _get_multi_provider_events(self, min_providers: int = 2) -> list[Event]:
        """Get events with odds from N+ providers."""
        return (
            self.session.query(Event)
            .join(Odds)
            .group_by(Event.id)
            .having(func.count(func.distinct(Odds.provider_id)) >= min_providers)
            .all()
        )

    def _get_events_with_provider(self, provider_id: str) -> list[Event]:
        """Get events where a specific provider has odds."""
        return (
            self.session.query(Event)
            .join(Odds)
            .filter(Odds.provider_id == provider_id)
            .distinct()
            .all()
        )

    def _group_odds(
        self, event: Event, exclude_providers: set[str] = None
    ) -> dict[str, dict[str, list[dict]]]:
        """
        Group event odds by market -> outcome -> provider list.

        Args:
            event: The event to group odds for
            exclude_providers: Set of provider IDs to exclude (default: EXCLUDED_FROM_SCANS)

        Returns:
            {
                "1x2": {
                    "home": [{"provider": "unibet", "odds": 2.10}, ...],
                    "draw": [...],
                    "away": [...]
                }
            }
        """
        if exclude_providers is None:
            exclude_providers = EXCLUDED_FROM_SCANS

        grouped = defaultdict(lambda: defaultdict(list))

        for odds in event.odds:
            # Skip excluded providers (e.g., polymarket - has its own dedicated view)
            if odds.provider_id in exclude_providers:
                continue

            # Create market key (point field preserved for compatibility but not used for 1x2/moneyline)
            if odds.point is not None:
                market_key = f"{odds.market}_{odds.point}"
            else:
                market_key = odds.market

            grouped[market_key][odds.outcome].append({
                "provider": odds.provider_id,
                "odds": odds.odds,
                "point": odds.point,
            })

        return dict(grouped)

    def _count_outcomes_per_provider(
        self, odds_by_outcome: dict[str, list[dict]]
    ) -> dict[str, int]:
        """
        Count how many outcomes each provider has in this market.

        Used to detect market type mismatches (e.g., 3-way 1x2 vs 2-way moneyline).

        Returns:
            {provider_id: outcome_count}
        """
        provider_outcomes = defaultdict(set)

        for outcome, provider_list in odds_by_outcome.items():
            for po in provider_list:
                provider_outcomes[po["provider"]].add(outcome)

        return {p: len(outcomes) for p, outcomes in provider_outcomes.items()}

    def _find_value_in_market(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        min_edge_pct: float,
        sharp_priority: list[str],
        blend_weight: float,
    ) -> list[ValueBet]:
        """Find value bets in a single market."""
        values = []

        # Count outcomes per provider to detect market type mismatch
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)

        # Find which sharp has data and their outcome count
        sharp_outcome_count = 0
        for sharp in sharp_priority:
            if sharp in provider_outcome_counts:
                sharp_outcome_count = provider_outcome_counts[sharp]
                break

        for outcome, provider_odds_list in odds_by_outcome.items():
            # Get fair odds (de-vigged and/or blended)
            fair_result = self._get_fair_odds(
                outcome=outcome,
                odds_by_outcome=odds_by_outcome,
                sharp_priority=sharp_priority,
                blend_weight=blend_weight,
            )

            if fair_result is None:
                continue

            fair_odds, fair_source = fair_result

            # Check each soft provider
            for po in provider_odds_list:
                if po["provider"] in SHARP_PROVIDERS:
                    continue  # Don't compare sharp vs sharp

                # Skip if market types don't match (different outcome counts)
                soft_count = provider_outcome_counts.get(po["provider"], 0)
                if soft_count > 0 and sharp_outcome_count > 0:
                    if soft_count != sharp_outcome_count:
                        continue  # Don't compare 3-way vs 2-way markets

                vb = find_value(
                    event_id=event_id,
                    market=market,
                    outcome=outcome,
                    provider=po["provider"],
                    provider_odds=po["odds"],
                    fair_odds=fair_odds,
                    min_edge_pct=min_edge_pct,
                )
                if vb:
                    # Sanity check: edges > 100% are likely data quality issues
                    if vb.edge_pct > 100:
                        logger.debug(
                            f"Skipping suspicious value {vb.edge_pct:+.1f}% for "
                            f"{event_id} {market} {outcome}"
                        )
                        continue
                    values.append(vb)

        return values

    def _find_bonus_in_market(
        self,
        event: Event,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        anchor_provider: str,
        counterpart_providers: list[str],
        devig: bool,
        blend_weight: float,
    ) -> list[BonusOpportunity]:
        """Find bonus opportunities in a single market."""
        opportunities = []

        # Count outcomes per provider to detect market type mismatch
        # (e.g., 3-way 1x2 vs 2-way moneyline)
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)
        anchor_outcome_count = provider_outcome_counts.get(anchor_provider, 0)

        # Find which sharp has data and their outcome count
        sharp_outcome_count = 0
        for sharp in counterpart_providers:
            if sharp in provider_outcome_counts:
                sharp_outcome_count = provider_outcome_counts[sharp]
                break

        # Skip if market types don't match (different outcome counts)
        if anchor_outcome_count > 0 and sharp_outcome_count > 0:
            if anchor_outcome_count != sharp_outcome_count:
                return []  # Don't compare 3-way vs 2-way markets

        for outcome, provider_odds_list in odds_by_outcome.items():
            # Find anchor provider odds for this outcome
            anchor_odds_entry = next(
                (p for p in provider_odds_list if p["provider"] == anchor_provider),
                None,
            )
            if anchor_odds_entry is None:
                continue

            anchor_odds = anchor_odds_entry["odds"]

            # Get fair odds from counterparts
            fair_result = self._get_fair_odds(
                outcome=outcome,
                odds_by_outcome=odds_by_outcome,
                sharp_priority=counterpart_providers,
                blend_weight=blend_weight,
                devig=devig,
            )

            if fair_result is None:
                continue

            fair_odds, fair_source = fair_result

            # Calculate edge (can be negative)
            if fair_odds <= 1 or anchor_odds <= 1:
                continue

            edge = (anchor_odds / fair_odds) - 1
            edge_pct = round(edge * 100, 2)

            # Sanity check: edges > 100% are almost certainly data quality issues
            # (mismatched markets, wrong point values, stale data)
            if abs(edge_pct) > 100:
                logger.debug(
                    f"Skipping suspicious edge {edge_pct:+.1f}% for {event.id} "
                    f"{market} {outcome}: anchor={anchor_odds}, fair={fair_odds}"
                )
                continue

            opportunities.append(
                BonusOpportunity(
                    event_id=event.id,
                    market=market,
                    outcome=outcome,
                    anchor_provider=anchor_provider,
                    anchor_odds=anchor_odds,
                    fair_odds=round(fair_odds, 3),
                    fair_source=fair_source,
                    edge_pct=edge_pct,
                    home_team=event.home_team,
                    away_team=event.away_team,
                    sport=event.sport,
                    league=event.league,
                )
            )

        return opportunities

    def _get_fair_odds(
        self,
        outcome: str,
        odds_by_outcome: dict[str, list[dict]],
        sharp_priority: list[str] = None,
        blend_weight: float = 0.6,
        devig: bool = True,
    ) -> Optional[tuple[float, str]]:
        """
        Get fair odds for an outcome from Pinnacle (sole sharp source).

        Pinnacle's ~2.5% margin is removed using multiplicative de-vigging.

        Args:
            outcome: The outcome to get fair odds for
            odds_by_outcome: All market odds
            sharp_priority: Ignored (kept for backward compatibility)
            blend_weight: Ignored (kept for backward compatibility)
            devig: Whether to de-vig (default True)

        Returns:
            (fair_odds, "pinnacle") or None if Pinnacle not found
        """
        # Find Pinnacle odds for this outcome
        outcome_providers = odds_by_outcome.get(outcome, [])

        pinnacle_odds = None
        for po in outcome_providers:
            if po["provider"] == "pinnacle":
                pinnacle_odds = po["odds"]
                break

        if pinnacle_odds is None:
            return None

        # De-vig Pinnacle if requested
        if devig:
            # Need full market odds to de-vig properly
            pinnacle_market = {}
            for out, providers in odds_by_outcome.items():
                for p in providers:
                    if p["provider"] == "pinnacle":
                        pinnacle_market[out] = p["odds"]
                        break

            if len(pinnacle_market) >= 2:
                fair_odds = get_fair_odds_for_outcome(
                    outcome, pinnacle_market, method="multiplicative"
                )
                return (fair_odds, "pinnacle")
            else:
                # Single outcome, can't de-vig properly - use raw odds
                return (pinnacle_odds, "pinnacle(raw)")
        else:
            return (pinnacle_odds, "pinnacle(raw)")


# Quick test
if __name__ == "__main__":
    print("=== OpportunityScanner Test ===")
    print("Run with database session to test scanning functionality")
